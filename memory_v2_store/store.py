"""Transactional, synthetic-safe SQLite storage for the Memory V2 shadow store."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import sqlite3
import struct
from typing import Any, Iterator, Optional
import uuid


SCHEMA_VERSION = 4
VALID_STATUSES = {
    "active", "superseded", "expired", "cancelled", "disputed", "retracted",
    "archived", "hidden", "redacted",
}
CURRENT_EXCLUDED_STATUSES = {"superseded", "expired", "cancelled", "retracted", "archived", "hidden", "redacted"}
HISTORICAL_EXCLUDED_STATUSES = {"retracted", "archived", "hidden", "redacted"}


class StoreError(ValueError):
    """Raised when a shadow-store operation would violate its data contract."""


def utc_now_us() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1_000_000)


def parse_timestamp_us(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    normalized = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1_000_000)


def _require_uuid(value: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as error:
        raise StoreError("character_id must be a UUID.") from error


class MemoryV2Store:
    """Repository for an isolated, append-oriented SQLite shadow database."""

    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self.connection = sqlite3.connect(path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self._configure()
        self._create_schema()

    def _configure(self) -> None:
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self.connection.execute("PRAGMA synchronous = FULL")
        # SQLite uses an in-memory journal for :memory: databases; file-backed
        # shadow stores use WAL as the intended operational mode.
        if self.path != ":memory:":
            self.connection.execute("PRAGMA journal_mode = WAL")

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS database_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at_us INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS characters (
                character_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                created_at_us INTEGER NOT NULL,
                archived_at_us INTEGER,
                legacy_config_key TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS events (
                character_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                actor_kind TEXT NOT NULL,
                recorded_at_us INTEGER NOT NULL,
                occurred_from_us INTEGER,
                occurred_to_us INTEGER,
                temporal_precision TEXT NOT NULL DEFAULT 'unknown',
                content_text TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}',
                payload_schema INTEGER NOT NULL DEFAULT 1,
                source_origin TEXT NOT NULL,
                source_reference TEXT,
                content_sha256 TEXT,
                redaction_state TEXT NOT NULL DEFAULT 'active',
                redacted_at_us INTEGER,
                PRIMARY KEY (character_id, event_id),
                UNIQUE (character_id, sequence),
                FOREIGN KEY (character_id) REFERENCES characters(character_id)
            );
            CREATE TABLE IF NOT EXISTS claims (
                character_id TEXT NOT NULL,
                claim_id TEXT NOT NULL,
                claim_type TEXT NOT NULL,
                assertion_scope TEXT NOT NULL,
                subject_key TEXT,
                content TEXT NOT NULL,
                importance INTEGER NOT NULL CHECK (importance BETWEEN 1 AND 10),
                confidence REAL,
                valid_from_us INTEGER,
                valid_to_us INTEGER,
                temporal_precision TEXT NOT NULL DEFAULT 'unknown',
                temporal_expression TEXT,
                provenance_state TEXT NOT NULL,
                curator_name TEXT,
                curator_version TEXT,
                curator_policy_version TEXT,
                created_at_us INTEGER NOT NULL,
                PRIMARY KEY (character_id, claim_id),
                FOREIGN KEY (character_id) REFERENCES characters(character_id)
            );
            CREATE TABLE IF NOT EXISTS claim_evidence (
                character_id TEXT NOT NULL,
                claim_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                evidence_role TEXT NOT NULL,
                excerpt_start_cp INTEGER,
                excerpt_end_cp INTEGER,
                excerpt_hash TEXT,
                evidence_strength REAL,
                curator_confidence REAL,
                created_at_us INTEGER NOT NULL,
                PRIMARY KEY (character_id, claim_id, event_id, evidence_role),
                FOREIGN KEY (character_id, claim_id)
                    REFERENCES claims(character_id, claim_id),
                FOREIGN KEY (character_id, event_id)
                    REFERENCES events(character_id, event_id)
            );
            CREATE TABLE IF NOT EXISTS claim_relations (
                relation_id INTEGER PRIMARY KEY,
                character_id TEXT NOT NULL,
                from_claim_id TEXT NOT NULL,
                to_claim_id TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                created_at_us INTEGER NOT NULL,
                CHECK (from_claim_id <> to_claim_id),
                FOREIGN KEY (character_id, from_claim_id)
                    REFERENCES claims(character_id, claim_id),
                FOREIGN KEY (character_id, to_claim_id)
                    REFERENCES claims(character_id, claim_id)
            );
            CREATE TABLE IF NOT EXISTS claim_status_events (
                status_event_id INTEGER PRIMARY KEY,
                character_id TEXT NOT NULL,
                claim_id TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                source_event_id TEXT,
                actor_kind TEXT NOT NULL,
                created_at_us INTEGER NOT NULL,
                CHECK (status IN ('active', 'superseded', 'expired', 'cancelled', 'disputed', 'retracted', 'archived', 'hidden', 'redacted')),
                FOREIGN KEY (character_id, claim_id)
                    REFERENCES claims(character_id, claim_id),
                FOREIGN KEY (character_id, source_event_id)
                    REFERENCES events(character_id, event_id)
            );
            CREATE INDEX IF NOT EXISTS events_character_recorded
                ON events(character_id, recorded_at_us);
            CREATE INDEX IF NOT EXISTS claims_character_validity
                ON claims(character_id, valid_from_us, valid_to_us);
            CREATE INDEX IF NOT EXISTS evidence_event
                ON claim_evidence(character_id, event_id);
            CREATE INDEX IF NOT EXISTS relations_source
                ON claim_relations(character_id, from_claim_id, relation_type);
            CREATE INDEX IF NOT EXISTS status_latest
                ON claim_status_events(character_id, claim_id, status_event_id DESC);
            """
        )
        with self.transaction():
            self.connection.execute(
                "INSERT OR IGNORE INTO database_meta(key, value) VALUES ('schema_version', ?)",
                ("1",),
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at_us) VALUES (?, ?)",
                (1, utc_now_us()),
            )
        self._apply_migrations()

    def _apply_migrations(self) -> None:
        """Apply additive schema migrations; never changes canonical rows."""
        version = self.schema_version()
        if version < 2:
            # executescript manages its own transaction boundary; nesting it in
            # transaction() would make sqlite commit before the context exits.
            self.connection.executescript(
                """
                    BEGIN IMMEDIATE;
                    ALTER TABLE claims ADD COLUMN legacy_metadata_json TEXT;
                    CREATE TABLE summaries (
                        character_id TEXT NOT NULL,
                        summary_id TEXT NOT NULL,
                        summary_level TEXT NOT NULL,
                        content TEXT NOT NULL,
                        source_count INTEGER,
                        provenance_state TEXT NOT NULL,
                        generator_name TEXT,
                        generator_version TEXT,
                        legacy_metadata_json TEXT,
                        created_at_us INTEGER NOT NULL,
                        PRIMARY KEY(character_id, summary_id),
                        FOREIGN KEY(character_id) REFERENCES characters(character_id)
                    );
                    CREATE TABLE summary_source_ranges (
                        character_id TEXT NOT NULL,
                        summary_id TEXT NOT NULL,
                        start_sequence INTEGER NOT NULL,
                        end_sequence INTEGER NOT NULL,
                        PRIMARY KEY(character_id, summary_id, start_sequence, end_sequence),
                        FOREIGN KEY(character_id, summary_id)
                            REFERENCES summaries(character_id, summary_id),
                        CHECK(start_sequence >= 1 AND end_sequence >= start_sequence)
                    );
                    COMMIT;
                    """
            )
            with self.transaction():
                self.connection.execute("INSERT INTO schema_migrations(version, applied_at_us) VALUES (?, ?)", (2, utc_now_us()))
                self.connection.execute("UPDATE database_meta SET value = ? WHERE key = 'schema_version'", ("2",))
        if version < 3:
            try:
                self.connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS claims_fts USING fts5(character_id UNINDEXED, claim_id UNINDEXED, searchable_text)"
                )
            except sqlite3.OperationalError as error:
                raise StoreError("SQLite FTS5 is required for the isolated Memory V2 retrieval engine.") from error
            with self.transaction():
                self.connection.execute("INSERT INTO schema_migrations(version, applied_at_us) VALUES (?, ?)", (3, utc_now_us()))
                self.connection.execute("UPDATE database_meta SET value = ? WHERE key = 'schema_version'", ("3",))
            version = 3
        if version < 4:
            self.connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE claim_embeddings (
                    character_id TEXT NOT NULL,
                    claim_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    model_version TEXT,
                    dimensions INTEGER NOT NULL CHECK (dimensions > 0),
                    dtype TEXT NOT NULL,
                    normalized INTEGER NOT NULL CHECK (normalized IN (0, 1)),
                    preprocessing_fingerprint TEXT NOT NULL,
                    content_fingerprint TEXT NOT NULL,
                    source_content_sha256 TEXT NOT NULL,
                    vector_blob BLOB,
                    generated_at_us INTEGER NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('current', 'stale', 'failed', 'retryable')),
                    failure_reason TEXT,
                    PRIMARY KEY (character_id, claim_id, provider, model, preprocessing_fingerprint),
                    FOREIGN KEY (character_id, claim_id)
                        REFERENCES claims(character_id, claim_id)
                );
                CREATE INDEX claim_embeddings_lookup ON claim_embeddings
                    (character_id, provider, model, preprocessing_fingerprint, state);
                COMMIT;
                """
            )
            with self.transaction():
                self.connection.execute("INSERT INTO schema_migrations(version, applied_at_us) VALUES (?, ?)", (4, utc_now_us()))
                self.connection.execute("UPDATE database_meta SET value = ? WHERE key = 'schema_version'", ("4",))

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")

    def close(self) -> None:
        self.connection.close()

    def schema_version(self) -> int:
        return int(self.connection.execute("SELECT value FROM database_meta WHERE key = 'schema_version'").fetchone()[0])

    def pragma(self, name: str) -> Any:
        return self.connection.execute(f"PRAGMA {name}").fetchone()[0]

    def create_character(self, character_id: str, display_name: str, *, legacy_config_key: Optional[str] = None, metadata: Optional[dict] = None, created_at_us: Optional[int] = None) -> None:
        character_id = _require_uuid(character_id)
        if not str(display_name).strip():
            raise StoreError("display_name is required.")
        with self.transaction():
            self.connection.execute(
                "INSERT INTO characters VALUES (?, ?, ?, NULL, ?, ?)",
                (character_id, str(display_name).strip(), created_at_us or utc_now_us(), legacy_config_key, json.dumps(metadata or {}, sort_keys=True)),
            )

    def add_event(self, character_id: str, event_id: str, sequence: int, *, event_type: str = "message", actor_kind: str = "user", recorded_at_us: Optional[int] = None, occurred_from_us: Optional[int] = None, occurred_to_us: Optional[int] = None, temporal_precision: str = "unknown", content_text: Optional[str] = None, payload: Optional[dict] = None, payload_schema: int = 1, source_origin: str = "synthetic", source_reference: Optional[str] = None) -> None:
        if sequence < 1:
            raise StoreError("event sequence must be positive.")
        if occurred_from_us is not None and occurred_to_us is not None and occurred_to_us < occurred_from_us:
            raise StoreError("event valid range is invalid.")
        content_hash = hashlib.sha256(content_text.encode("utf-8")).hexdigest() if content_text is not None else None
        with self.transaction():
            self.connection.execute(
                """INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL)""",
                (_require_uuid(character_id), str(event_id), sequence, event_type, actor_kind,
                 recorded_at_us or utc_now_us(), occurred_from_us, occurred_to_us,
                 temporal_precision, content_text, json.dumps(payload or {}, sort_keys=True),
                 payload_schema, source_origin, source_reference, content_hash),
            )

    def add_claim(self, character_id: str, claim_id: str, *, claim_type: str, assertion_scope: str, content: str, importance: int = 5, confidence: Optional[float] = None, subject_key: Optional[str] = None, valid_from_us: Optional[int] = None, valid_to_us: Optional[int] = None, temporal_precision: str = "unknown", temporal_expression: Optional[str] = None, provenance_state: str = "complete", curator_name: Optional[str] = "synthetic-fixture", curator_version: Optional[str] = "1", curator_policy_version: Optional[str] = "1", legacy_metadata: Optional[dict] = None, created_at_us: Optional[int] = None) -> None:
        if not str(content).strip():
            raise StoreError("claim content is required.")
        if isinstance(importance, bool) or not isinstance(importance, int) or not 1 <= importance <= 10:
            raise StoreError("claim importance must be an integer from 1 to 10.")
        if valid_from_us is not None and valid_to_us is not None and valid_to_us < valid_from_us:
            raise StoreError("claim valid range is invalid.")
        with self.transaction():
            self.connection.execute(
                """INSERT INTO claims VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (_require_uuid(character_id), str(claim_id), claim_type, assertion_scope, subject_key,
                 str(content), importance, confidence, valid_from_us, valid_to_us,
                 temporal_precision, temporal_expression, provenance_state, curator_name,
                 curator_version, curator_policy_version, created_at_us or utc_now_us(),
                 json.dumps(legacy_metadata, ensure_ascii=False, sort_keys=True) if legacy_metadata is not None else None),
            )

    def add_summary(self, character_id: str, summary_id: str, content: str, *, source_count: Optional[int], provenance_state: str, generator_name: Optional[str], generator_version: Optional[str], legacy_metadata: Optional[dict] = None, created_at_us: Optional[int] = None) -> None:
        with self.transaction():
            self.connection.execute(
                "INSERT INTO summaries VALUES (?, ?, 'legacy_conversation_summary', ?, ?, ?, ?, ?, ?, ?)",
                (_require_uuid(character_id), summary_id, content, source_count, provenance_state,
                 generator_name, generator_version,
                 json.dumps(legacy_metadata, ensure_ascii=False, sort_keys=True) if legacy_metadata is not None else None,
                 created_at_us if created_at_us is not None else utc_now_us()),
            )

    def add_summary_source_range(self, character_id: str, summary_id: str, start_sequence: int, end_sequence: int) -> None:
        with self.transaction():
            self.connection.execute("INSERT INTO summary_source_ranges VALUES (?, ?, ?, ?)", (_require_uuid(character_id), summary_id, start_sequence, end_sequence))

    def attach_evidence(self, character_id: str, claim_id: str, event_id: str, *, evidence_role: str = "direct_user_statement", excerpt_start_cp: Optional[int] = None, excerpt_end_cp: Optional[int] = None, evidence_strength: Optional[float] = 1.0, curator_confidence: Optional[float] = None, created_at_us: Optional[int] = None) -> None:
        row = self.connection.execute(
            "SELECT content_text FROM events WHERE character_id = ? AND event_id = ?",
            (_require_uuid(character_id), str(event_id)),
        ).fetchone()
        if row is None:
            # Avoid accidentally attaching an event known under a different character.
            raise StoreError("evidence event is absent for this character.")
        content = row[0] or ""
        if excerpt_start_cp is not None or excerpt_end_cp is not None:
            if excerpt_start_cp is None or excerpt_end_cp is None or not 0 <= excerpt_start_cp <= excerpt_end_cp <= len(content):
                raise StoreError("evidence excerpt range is invalid.")
            excerpt_hash = hashlib.sha256(content[excerpt_start_cp:excerpt_end_cp].encode("utf-8")).hexdigest()
        else:
            excerpt_hash = None
        with self.transaction():
            self.connection.execute(
                "INSERT INTO claim_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_require_uuid(character_id), str(claim_id), str(event_id), evidence_role,
                 excerpt_start_cp, excerpt_end_cp, excerpt_hash, evidence_strength,
                 curator_confidence, created_at_us or utc_now_us()),
            )

    def add_relation(self, character_id: str, from_claim_id: str, to_claim_id: str, relation_type: str = "supersedes", *, created_at_us: Optional[int] = None) -> None:
        with self.transaction():
            self.connection.execute(
                "INSERT INTO claim_relations(character_id, from_claim_id, to_claim_id, relation_type, created_at_us) VALUES (?, ?, ?, ?, ?)",
                (_require_uuid(character_id), str(from_claim_id), str(to_claim_id), relation_type, created_at_us or utc_now_us()),
            )

    def add_status(self, character_id: str, claim_id: str, status: str, *, reason: Optional[str] = None, source_event_id: Optional[str] = None, actor_kind: str = "system", created_at_us: Optional[int] = None) -> None:
        if status not in VALID_STATUSES:
            raise StoreError(f"invalid claim status: {status}")
        with self.transaction():
            self.connection.execute(
                "INSERT INTO claim_status_events(character_id, claim_id, status, reason, source_event_id, actor_kind, created_at_us) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_require_uuid(character_id), str(claim_id), status, reason, source_event_id, actor_kind, created_at_us or utc_now_us()),
            )

    def effective_status(self, character_id: str, claim_id: str, *, at_us: Optional[int] = None) -> str:
        statement = "SELECT status FROM claim_status_events WHERE character_id = ? AND claim_id = ?"
        arguments: list[Any] = [_require_uuid(character_id), str(claim_id)]
        if at_us is not None:
            statement += " AND created_at_us <= ?"
            arguments.append(at_us)
        row = self.connection.execute(statement + " ORDER BY status_event_id DESC LIMIT 1", arguments).fetchone()
        return row[0] if row else "active"

    def structural_claims(self, character_id: str, at_us: int, *, historical: bool = False, exclude_claim_ids: tuple[str, ...] = (), claim_ids: tuple[str, ...] = (), claim_types: tuple[str, ...] = (), limit: Optional[int] = None) -> list[sqlite3.Row]:
        character_id = _require_uuid(character_id)
        statement = """
            SELECT c.*, COALESCE((
                SELECT status FROM claim_status_events s
                WHERE s.character_id = c.character_id AND s.claim_id = c.claim_id
                  AND s.created_at_us <= ?
                ORDER BY s.status_event_id DESC LIMIT 1
            ), 'active') AS effective_status
            FROM claims c
            WHERE c.character_id = ?
              AND EXISTS (SELECT 1 FROM claim_evidence e
                          WHERE e.character_id = c.character_id AND e.claim_id = c.claim_id)
            """
        arguments: list[Any] = [at_us, character_id]
        if claim_ids:
            statement += " AND c.claim_id IN (" + ",".join("?" for _ in claim_ids) + ")"
            arguments.extend(claim_ids)
        if claim_types:
            statement += " AND c.claim_type IN (" + ",".join("?" for _ in claim_types) + ")"
            arguments.extend(claim_types)
        statement += " ORDER BY c.importance DESC, c.claim_id"
        if limit is not None:
            statement += " LIMIT ?"
            arguments.append(limit)
        rows = self.connection.execute(statement, arguments).fetchall()
        excluded_statuses = HISTORICAL_EXCLUDED_STATUSES if historical else CURRENT_EXCLUDED_STATUSES
        results = []
        for row in rows:
            if row["claim_id"] in exclude_claim_ids or row["effective_status"] in excluded_statuses:
                continue
            if row["valid_from_us"] is not None and row["valid_from_us"] > at_us:
                continue
            if row["valid_to_us"] is not None and row["valid_to_us"] <= at_us:
                continue
            results.append(row)
        return results

    def exact_claim_ids(self, character_id: str, terms: tuple[str, ...], limit: int) -> list[str]:
        """Bound exact substring lane using SQL parameters, never FTS syntax."""
        if not terms or limit < 1:
            return []
        character_id = _require_uuid(character_id)
        predicates = " OR ".join("instr(lower(content), lower(?)) > 0" for _ in terms)
        rows = self.connection.execute(
            f"SELECT claim_id FROM claims WHERE character_id = ? AND ({predicates}) ORDER BY claim_id LIMIT ?",
            [character_id, *terms, limit],
        ).fetchall()
        return [row[0] for row in rows]

    def fts_available(self) -> bool:
        try:
            self.connection.execute("SELECT count(*) FROM claims_fts").fetchone()
            return True
        except sqlite3.OperationalError:
            return False

    def rebuild_fts(self) -> int:
        """Rebuild derived FTS rows from canonical claims; never changes claims."""
        if not self.fts_available():
            raise StoreError("SQLite FTS5 is unavailable; cannot build derived claim index.")
        with self.transaction():
            self.connection.execute("DELETE FROM claims_fts")
            rows = self.connection.execute(
                """
                SELECT c.character_id, c.claim_id, c.claim_type, c.subject_key, c.content,
                       COALESCE((SELECT status FROM claim_status_events s
                         WHERE s.character_id = c.character_id AND s.claim_id = c.claim_id
                         ORDER BY s.status_event_id DESC LIMIT 1), 'active') AS status
                FROM claims c
                WHERE EXISTS (SELECT 1 FROM claim_evidence e
                              WHERE e.character_id = c.character_id AND e.claim_id = c.claim_id)
                ORDER BY c.character_id, c.claim_id
                """
            ).fetchall()
            digest = hashlib.sha256()
            indexed = 0
            for row in rows:
                if row["status"] in {"retracted", "archived", "hidden", "redacted"}:
                    continue
                searchable = " ".join(part for part in (row["claim_type"], row["subject_key"], row["content"]) if part)
                digest.update(f"{row['character_id']}\0{row['claim_id']}\0{searchable}\0{row['status']}\n".encode("utf-8"))
                self.connection.execute(
                    "INSERT INTO claims_fts(character_id, claim_id, searchable_text) VALUES (?, ?, ?)",
                    (row["character_id"], row["claim_id"], searchable),
                )
                indexed += 1
            self.connection.execute(
                "INSERT OR REPLACE INTO database_meta(key, value) VALUES ('fts_claims_digest', ?)",
                (digest.hexdigest(),),
            )
        return indexed

    def fts_is_current(self) -> bool:
        """Detect simple derived-index drift without treating FTS as authority."""
        if not self.fts_available():
            return False
        rows = self.connection.execute(
            """
            SELECT c.character_id, c.claim_id, c.claim_type, c.subject_key, c.content,
              COALESCE((SELECT status FROM claim_status_events s
                WHERE s.character_id=c.character_id AND s.claim_id=c.claim_id
                ORDER BY s.status_event_id DESC LIMIT 1), 'active') AS status
            FROM claims c WHERE EXISTS
              (SELECT 1 FROM claim_evidence e WHERE e.character_id=c.character_id AND e.claim_id=c.claim_id)
              AND COALESCE((SELECT status FROM claim_status_events s
                WHERE s.character_id=c.character_id AND s.claim_id=c.claim_id
                ORDER BY s.status_event_id DESC LIMIT 1), 'active')
                NOT IN ('retracted', 'archived', 'hidden', 'redacted')
            ORDER BY c.character_id, c.claim_id
            """
        ).fetchall()
        digest = hashlib.sha256()
        for row in rows:
            searchable = " ".join(part for part in (row["claim_type"], row["subject_key"], row["content"]) if part)
            digest.update(f"{row['character_id']}\0{row['claim_id']}\0{searchable}\0{row['status']}\n".encode("utf-8"))
        expected = len(rows)
        actual = self.connection.execute("SELECT count(*) FROM claims_fts").fetchone()[0]
        stored = self.connection.execute("SELECT value FROM database_meta WHERE key = 'fts_claims_digest'").fetchone()
        return expected == actual and stored is not None and stored[0] == digest.hexdigest()

    def ensure_fts(self) -> int:
        return 0 if self.fts_is_current() else self.rebuild_fts()

    # The following embedding helpers exclusively maintain derived V2 state.
    # They intentionally have no path to the production JSON memory database.
    def embedding_source_claims(self, *, include_legacy_unverified: bool = False) -> list[sqlite3.Row]:
        provenance = "('complete', 'legacy_unverified')" if include_legacy_unverified else "('complete')"
        return self.connection.execute(
            f"""SELECT c.* FROM claims c
               WHERE c.provenance_state IN {provenance}
                 AND EXISTS (SELECT 1 FROM claim_evidence e
                             WHERE e.character_id=c.character_id AND e.claim_id=c.claim_id)
                 AND COALESCE((SELECT status FROM claim_status_events s
                    WHERE s.character_id=c.character_id AND s.claim_id=c.claim_id
                    ORDER BY s.status_event_id DESC LIMIT 1), 'active')
                    NOT IN ('retracted', 'archived', 'hidden', 'redacted')
               ORDER BY c.character_id, c.claim_id"""
        ).fetchall()

    @staticmethod
    def _content_sha256(content: str) -> str:
        return hashlib.sha256(str(content).encode("utf-8")).hexdigest()

    @staticmethod
    def _provider_matches(row: sqlite3.Row, provider: Any) -> bool:
        return (
            row["provider"] == provider.provider and row["model"] == provider.model
            and (row["model_version"] or "") == (getattr(provider, "model_version", "") or "")
            and row["dimensions"] == provider.dimensions and row["dtype"] == provider.dtype
            and bool(row["normalized"]) == bool(provider.normalized)
            and row["preprocessing_fingerprint"] == provider.preprocessing_fingerprint
        )

    def embedding_is_current(self, claim: sqlite3.Row, provider: Any) -> bool:
        row = self.connection.execute(
            """SELECT * FROM claim_embeddings WHERE character_id=? AND claim_id=?
               AND provider=? AND model=? AND preprocessing_fingerprint=?""",
            (claim["character_id"], claim["claim_id"], provider.provider, provider.model,
             provider.preprocessing_fingerprint),
        ).fetchone()
        return bool(row and row["state"] == "current" and row["vector_blob"] is not None
                    and self._provider_matches(row, provider)
                    and row["content_fingerprint"] == self._content_sha256(claim["content"])
                    and row["source_content_sha256"] == self._content_sha256(claim["content"]))

    def mark_incompatible_embeddings_stale(self, provider: Any) -> int:
        """Mark old model/preprocessing rows stale; they are never silently reused."""
        rows = self.connection.execute("SELECT * FROM claim_embeddings WHERE state='current'").fetchall()
        stale = [row for row in rows if not self._provider_matches(row, provider)]
        with self.transaction():
            for row in stale:
                self.connection.execute(
                    """UPDATE claim_embeddings SET state='stale', generated_at_us=?
                       WHERE character_id=? AND claim_id=? AND provider=? AND model=?
                         AND preprocessing_fingerprint=?""",
                    (utc_now_us(), row["character_id"], row["claim_id"], row["provider"],
                     row["model"], row["preprocessing_fingerprint"]),
                )
        return len(stale)

    def store_embedding(self, claim: sqlite3.Row, provider: Any, vector: list[float]) -> None:
        if len(vector) != provider.dimensions:
            raise StoreError("embedding dimensions do not match provider identity")
        try:
            values = [float(value) for value in vector]
            if not all(math.isfinite(value) for value in values):
                raise ValueError("non-finite value")
            blob = struct.pack(f"<{len(vector)}f", *values)
        except (TypeError, ValueError, struct.error) as error:
            raise StoreError("embedding vector must contain finite float values") from error
        if len(blob) != provider.dimensions * 4:
            raise StoreError("invalid serialized embedding size")
        with self.transaction():
            self.connection.execute(
                """INSERT INTO claim_embeddings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'current', NULL)
                   ON CONFLICT(character_id, claim_id, provider, model, preprocessing_fingerprint)
                   DO UPDATE SET model_version=excluded.model_version, dimensions=excluded.dimensions,
                     dtype=excluded.dtype, normalized=excluded.normalized,
                     content_fingerprint=excluded.content_fingerprint,
                     source_content_sha256=excluded.source_content_sha256, vector_blob=excluded.vector_blob,
                     generated_at_us=excluded.generated_at_us, state='current', failure_reason=NULL""",
                (claim["character_id"], claim["claim_id"], provider.provider, provider.model,
                 getattr(provider, "model_version", None), provider.dimensions, provider.dtype,
                 int(bool(provider.normalized)), provider.preprocessing_fingerprint,
                 self._content_sha256(claim["content"]), self._content_sha256(claim["content"]),
                 sqlite3.Binary(blob), utc_now_us()),
            )

    def store_embedding_failure(self, claim: sqlite3.Row, provider: Any, reason: str) -> None:
        # Do not overwrite a usable vector merely because a later rebuild failed.
        if self.embedding_is_current(claim, provider):
            return
        with self.transaction():
            self.connection.execute(
                """INSERT INTO claim_embeddings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 'retryable', ?)
                   ON CONFLICT(character_id, claim_id, provider, model, preprocessing_fingerprint)
                   DO UPDATE SET generated_at_us=excluded.generated_at_us, state='retryable',
                     failure_reason=excluded.failure_reason""",
                (claim["character_id"], claim["claim_id"], provider.provider, provider.model,
                 getattr(provider, "model_version", None), provider.dimensions, provider.dtype,
                 int(bool(provider.normalized)), provider.preprocessing_fingerprint,
                 self._content_sha256(claim["content"]), self._content_sha256(claim["content"]),
                 utc_now_us(), str(reason)[:500]),
            )

    def embedding_health(self, provider: Any, *, include_legacy_unverified: bool = False) -> dict[str, int]:
        claims = self.embedding_source_claims(include_legacy_unverified=include_legacy_unverified)
        current = stale = missing = failed = 0
        for claim in claims:
            rows = self.connection.execute(
                "SELECT * FROM claim_embeddings WHERE character_id=? AND claim_id=?",
                (claim["character_id"], claim["claim_id"]),
            ).fetchall()
            matching = [row for row in rows if self._provider_matches(row, provider)]
            if self.embedding_is_current(claim, provider):
                current += 1
            elif any(row["state"] in {"failed", "retryable"} for row in matching):
                failed += 1
            elif rows:
                stale += 1
            else:
                missing += 1
        return {"eligible": len(claims), "current": current, "stale": stale,
                "missing": missing, "failed": failed}

    def semantic_candidates(self, character_id: str, provider: Any, query_vector: list[float], limit: int) -> list[tuple[str, float]]:
        """Brute-force cosine over current, character-scoped derived vectors."""
        if limit < 1 or len(query_vector) != provider.dimensions:
            return []
        character_id = _require_uuid(character_id)
        rows = self.connection.execute(
            """SELECT e.*, c.content FROM claim_embeddings e JOIN claims c
                    ON c.character_id=e.character_id AND c.claim_id=e.claim_id
               WHERE e.character_id=? AND e.provider=? AND e.model=?
                 AND e.preprocessing_fingerprint=? AND e.state='current'
                 AND e.dimensions=? AND e.dtype=? AND e.normalized=?""",
            (character_id, provider.provider, provider.model, provider.preprocessing_fingerprint,
             provider.dimensions, provider.dtype, int(bool(provider.normalized))),
        ).fetchall()
        scores = []
        for row in rows:
            if (not self._provider_matches(row, provider)
                    or row["content_fingerprint"] != self._content_sha256(row["content"])
                    or row["source_content_sha256"] != self._content_sha256(row["content"])):
                continue
            try:
                vector = struct.unpack(f"<{provider.dimensions}f", row["vector_blob"])
            except struct.error:
                continue
            score = sum(float(left) * float(right) for left, right in zip(query_vector, vector))
            scores.append((row["claim_id"], score))
        return sorted(scores, key=lambda item: (-item[1], item[0]))[:limit]

    def search_fts(self, character_id: str, safe_query: str, limit: int) -> list[sqlite3.Row]:
        if limit < 1 or not safe_query.strip():
            return []
        character_id = _require_uuid(character_id)
        return self.connection.execute(
            """SELECT claim_id, bm25(claims_fts) AS fts_score
               FROM claims_fts WHERE claims_fts MATCH ? AND character_id = ?
               ORDER BY fts_score LIMIT ?""",
            (safe_query, character_id, limit),
        ).fetchall()

    def integrity_check(self) -> str:
        return self.connection.execute("PRAGMA quick_check").fetchone()[0]
