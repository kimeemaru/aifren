"""Transactional, synthetic-safe SQLite storage for the Memory V2 shadow store."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import sqlite3
import struct
from typing import Any, Iterable, Iterator, Optional
import uuid

from aifren.character.character_storage_runtime import continuity_close_scope, continuity_write_scope
from .durable_contract import (
    DURABLE_ASSERTION_SCOPE,
    DURABLE_CORE_FACT,
    DURABLE_LEGACY_BRIDGE_EVIDENCE_ROLE,
    DURABLE_USER_EVIDENCE_ROLES,
    is_singleton_durable_key,
    validate_durable_subject_key,
)
from .active_state_contract import (
    ACTIVE_STATE, ACTIVE_STATE_ASSERTION_SCOPE, ACTIVE_STATE_EVIDENCE_ROLES,
    MAX_ACTIVE_SCENE_ATTRIBUTES_PER_SUBJECT, MAX_ACTIVE_SCENE_SUBJECTS,
    ActiveSceneSubjectIntroduction, ActiveSceneSubjectRetirement, ActiveSceneSubjectReactivation,
    ActiveStateCorrectionProposal, ActiveStateProposal, ActiveStateProposalUpdate, active_state_slot,
    parse_active_state_subject_key, scene_state_subject_key, validate_active_state_proposal, validate_active_state_subject_key,
    validate_active_state_correction, validate_active_state_value,
)
from .open_thread_contract import (
    MAX_CURRENT_OPEN_THREADS, OPEN_THREAD, OPEN_THREAD_ASSERTION_SCOPE,
    OPEN_THREAD_EVIDENCE_ROLES, MAX_OPEN_THREAD_EVIDENCE_RECORDS, OpenThreadProposal,
    OpenThreadProposalOperation, validate_open_thread_proposal,
)
from .truth_scope_contract import (
    MAX_SCENARIO_SCOPES_PER_CHARACTER, REAL_WORLD_SCOPE, SCENARIO_SCOPE,
    default_real_world_scope_id, normalize_truth_scope_label,
    validate_truth_scope_id,
)
from .scene_relation_contract import (
    MAX_CURRENT_SCENE_RELATIONS,
    SceneRelationProposal,
    validate_scene_relation_proposal,
)


SCHEMA_VERSION = 21
HISTORICAL_EVIDENCE = "historical_evidence"
HISTORICAL_EVIDENCE_ASSERTION_SCOPE = "historical_occurrence"
VALID_STATUSES = {
    "active", "superseded", "expired", "cancelled", "disputed", "retracted",
    "archived", "hidden", "redacted",
}
CURRENT_EXCLUDED_STATUSES = {"superseded", "expired", "cancelled", "retracted", "archived", "hidden", "redacted"}
HISTORICAL_EXCLUDED_STATUSES = {"retracted", "archived", "hidden", "redacted"}
MAX_DORMANT_SCENE_SUBJECTS = 32
GENERIC_DORMANT_RETIRE_AFTER_US = 30 * 24 * 60 * 60 * 1_000_000
DISTINCT_DORMANT_RETIRE_AFTER_US = 180 * 24 * 60 * 60 * 1_000_000


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
        # AssistantService construction/reconciliation happens off the
        # WebSocket event loop, while character-switch retirement is finalized
        # on that loop.  A service serializes turns and is only retired once it
        # is idle, so this connection is never used concurrently; allowing its
        # owner to close it from the lifecycle thread prevents a successful
        # character switch from failing solely because SQLite recorded the
        # factory worker as its creation thread.
        #
        # This is deliberately not a promise that MemoryV2Store is generally
        # safe for concurrent callers.  Higher-level service serialization
        # remains the concurrency boundary.
        self.connection = sqlite3.connect(
            path,
            isolation_level=None,
            check_same_thread=False,
        )
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
            version = 4
        if version < 5:
            # Production-foundation import bookkeeping. This records a legacy
            # V1 record's currently imported claim without making V1 data an
            # authority or mutating its JSON source.
            self.connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE v1_import_records (
                    source_scope TEXT NOT NULL,
                    legacy_memory_id INTEGER NOT NULL,
                    claim_id TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    imported_at_us INTEGER NOT NULL,
                    PRIMARY KEY (source_scope, legacy_memory_id)
                );
                CREATE INDEX claims_character_created
                    ON claims(character_id, created_at_us DESC, claim_id);
                CREATE INDEX claims_character_type_created
                    ON claims(character_id, claim_type, created_at_us DESC, claim_id);
                COMMIT;
                """
            )
            with self.transaction():
                self.connection.execute("INSERT INTO schema_migrations(version, applied_at_us) VALUES (?, ?)", (5, utc_now_us()))
                self.connection.execute("UPDATE database_meta SET value = ? WHERE key = 'schema_version'", ("5",))
            version = 5
        if version < 6:
            self.connection.executescript(
                """
                BEGIN IMMEDIATE;
                ALTER TABLE claims ADD COLUMN updated_at_us INTEGER;
                UPDATE claims SET updated_at_us = created_at_us WHERE updated_at_us IS NULL;
                COMMIT;
                """
            )
            with self.transaction():
                self.connection.execute("INSERT INTO schema_migrations(version, applied_at_us) VALUES (?, ?)", (6, utc_now_us()))
                self.connection.execute("UPDATE database_meta SET value = ? WHERE key = 'schema_version'", ("6",))
            version = 6
        if version < 7:
            # Bounded, local-only dual-read diagnostics. These rows contain
            # IDs and a query digest, never memory or conversation text.
            self.connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE retrieval_telemetry (
                    telemetry_id INTEGER PRIMARY KEY,
                    recorded_at_us INTEGER NOT NULL,
                    character_id TEXT NOT NULL,
                    query_sha256 TEXT NOT NULL,
                    v1_ids_json TEXT NOT NULL,
                    v2_ids_json TEXT NOT NULL,
                    overlap_count INTEGER NOT NULL,
                    v1_abstained INTEGER NOT NULL,
                    v2_abstained INTEGER NOT NULL,
                    v1_latency_ms REAL,
                    v2_latency_ms REAL,
                    error_kind TEXT,
                    FOREIGN KEY(character_id) REFERENCES characters(character_id)
                );
                CREATE INDEX retrieval_telemetry_character_time
                    ON retrieval_telemetry(character_id, recorded_at_us DESC);
                COMMIT;
                """
            )
            with self.transaction():
                self.connection.execute("INSERT INTO schema_migrations(version, applied_at_us) VALUES (?, ?)", (7, utc_now_us()))
                self.connection.execute("UPDATE database_meta SET value = ? WHERE key = 'schema_version'", ("7",))
            version = 7
        if version < 8:
            # Distinguish bounded FTS telemetry from the small-corpus hybrid
            # evaluator without persisting any extra user content.
            with self.transaction():
                self.connection.execute(
                    "ALTER TABLE retrieval_telemetry ADD COLUMN retrieval_strategy TEXT NOT NULL DEFAULT 'unknown'"
                )
                self.connection.execute("INSERT INTO schema_migrations(version, applied_at_us) VALUES (?, ?)", (8, utc_now_us()))
                self.connection.execute("UPDATE database_meta SET value = ? WHERE key = 'schema_version'", ("8",))
            version = 8
        if version < 9:
            # Durable-core claims are a small, separately governed subset of
            # claims.  This index keeps exact-slot lookup bounded without
            # changing canonical claim/evidence rows.
            with self.transaction():
                self.connection.execute(
                    "CREATE INDEX IF NOT EXISTS claims_durable_lookup "
                    "ON claims(character_id, claim_type, subject_key, created_at_us DESC, claim_id)"
                )
                self.connection.execute("INSERT INTO schema_migrations(version, applied_at_us) VALUES (?, ?)", (9, utc_now_us()))
                self.connection.execute("UPDATE database_meta SET value = ? WHERE key = 'schema_version'", ("9",))
            version = 9
        if version < 10:
            # Current-scene subject IDs are small, character-local roster
            # records. Attribute values remain append-oriented Active State
            # claims, so this table does not duplicate state or evidence.
            self.connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE active_scene_subjects (
                    character_id TEXT NOT NULL,
                    scene_subject_id TEXT NOT NULL,
                    introduced_at_us INTEGER NOT NULL,
                    retired_at_us INTEGER,
                    introduced_event_id TEXT NOT NULL,
                    introduced_excerpt_start_cp INTEGER,
                    introduced_excerpt_end_cp INTEGER,
                    introduced_excerpt_hash TEXT,
                    retired_event_id TEXT,
                    retired_excerpt_start_cp INTEGER,
                    retired_excerpt_end_cp INTEGER,
                    retired_excerpt_hash TEXT,
                    PRIMARY KEY(character_id, scene_subject_id),
                    FOREIGN KEY(character_id) REFERENCES characters(character_id),
                    FOREIGN KEY(character_id, introduced_event_id) REFERENCES events(character_id, event_id),
                    FOREIGN KEY(character_id, retired_event_id) REFERENCES events(character_id, event_id)
                );
                CREATE INDEX active_scene_subjects_current_lookup
                    ON active_scene_subjects(character_id, retired_at_us, introduced_at_us DESC, scene_subject_id);
                COMMIT;
                """
            )
            with self.transaction():
                self.connection.execute("INSERT INTO schema_migrations(version, applied_at_us) VALUES (?, ?)", (10, utc_now_us()))
                self.connection.execute("UPDATE database_meta SET value = ? WHERE key = 'schema_version'", ("10",))
            version = 10
        if version < 11:
            # Open Threads are compact derived continuity records. Canonical
            # evidence remains in the normal claim/evidence tables; this table
            # only carries thread lifecycle and bounded current-read metadata.
            self.connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE open_threads (
                    character_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    thread_kind TEXT NOT NULL,
                    participant_scope TEXT NOT NULL,
                    description TEXT NOT NULL,
                    temporal_anchor TEXT,
                    status TEXT NOT NULL CHECK (status IN ('open', 'resolved', 'cancelled')),
                    opened_at_us INTEGER NOT NULL,
                    last_mentioned_at_us INTEGER NOT NULL,
                    closed_at_us INTEGER,
                    opened_event_id TEXT NOT NULL,
                    last_event_id TEXT NOT NULL,
                    last_excerpt_start_cp INTEGER,
                    last_excerpt_end_cp INTEGER,
                    last_excerpt_hash TEXT,
                    closed_event_id TEXT,
                    PRIMARY KEY(character_id, thread_id),
                    FOREIGN KEY(character_id) REFERENCES characters(character_id),
                    FOREIGN KEY(character_id, thread_id) REFERENCES claims(character_id, claim_id),
                    FOREIGN KEY(character_id, opened_event_id) REFERENCES events(character_id, event_id),
                    FOREIGN KEY(character_id, last_event_id) REFERENCES events(character_id, event_id),
                    FOREIGN KEY(character_id, closed_event_id) REFERENCES events(character_id, event_id)
                );
                CREATE INDEX open_threads_current_lookup
                    ON open_threads(character_id, status, last_mentioned_at_us DESC, thread_id);
                COMMIT;
                """
            )
            with self.transaction():
                self.connection.execute("INSERT INTO schema_migrations(version, applied_at_us) VALUES (?, ?)", (11, utc_now_us()))
                self.connection.execute("UPDATE database_meta SET value = ? WHERE key = 'schema_version'", ("11",))
            version = 11
        if version < 12:
            # Scope is a character-local truth boundary, not a second archive.
            # Existing unscoped V2 rows become the stable default real-world
            # scope so migration cannot silently discard or reinterpret them.
            self.connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE truth_scopes (
                    character_id TEXT NOT NULL,
                    truth_scope_id TEXT NOT NULL,
                    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('real_world', 'scenario')),
                    label TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('active', 'inactive')),
                    created_at_us INTEGER NOT NULL,
                    last_active_at_us INTEGER NOT NULL,
                    PRIMARY KEY(character_id, truth_scope_id),
                    FOREIGN KEY(character_id) REFERENCES characters(character_id)
                );
                CREATE UNIQUE INDEX truth_scopes_one_real_world
                    ON truth_scopes(character_id) WHERE scope_kind='real_world';
                CREATE TABLE truth_scope_events (
                    scope_event_id INTEGER PRIMARY KEY,
                    character_id TEXT NOT NULL,
                    truth_scope_id TEXT NOT NULL,
                    operation TEXT NOT NULL CHECK (operation IN ('created', 'activated', 'deactivated')),
                    event_id TEXT,
                    excerpt_start_cp INTEGER,
                    excerpt_end_cp INTEGER,
                    excerpt_hash TEXT,
                    created_at_us INTEGER NOT NULL,
                    FOREIGN KEY(character_id, truth_scope_id) REFERENCES truth_scopes(character_id, truth_scope_id),
                    FOREIGN KEY(character_id, event_id) REFERENCES events(character_id, event_id)
                );
                ALTER TABLE characters ADD COLUMN active_truth_scope_id TEXT;
                ALTER TABLE claims ADD COLUMN truth_scope_id TEXT;
                ALTER TABLE active_scene_subjects ADD COLUMN truth_scope_id TEXT;
                ALTER TABLE open_threads ADD COLUMN truth_scope_id TEXT;
                CREATE INDEX claims_scope_active_lookup
                    ON claims(character_id, truth_scope_id, claim_type, subject_key, created_at_us DESC, claim_id);
                CREATE INDEX active_scene_subjects_scope_current_lookup
                    ON active_scene_subjects(character_id, truth_scope_id, retired_at_us, introduced_at_us DESC, scene_subject_id);
                CREATE INDEX open_threads_scope_current_lookup
                    ON open_threads(character_id, truth_scope_id, status, last_mentioned_at_us DESC, thread_id);
                COMMIT;
                """
            )
            with self.transaction():
                rows = self.connection.execute("SELECT character_id, created_at_us FROM characters").fetchall()
                for row in rows:
                    character_id = str(row["character_id"])
                    scope_id = default_real_world_scope_id(character_id)
                    created_at = int(row["created_at_us"])
                    self.connection.execute(
                        "INSERT OR IGNORE INTO truth_scopes VALUES (?, ?, 'real_world', 'Real world', 'active', ?, ?)",
                        (character_id, scope_id, created_at, created_at),
                    )
                    self.connection.execute(
                        "UPDATE characters SET active_truth_scope_id=? WHERE character_id=?",
                        (scope_id, character_id),
                    )
                    self.connection.execute("UPDATE claims SET truth_scope_id=? WHERE character_id=? AND truth_scope_id IS NULL", (scope_id, character_id))
                    self.connection.execute("UPDATE active_scene_subjects SET truth_scope_id=? WHERE character_id=? AND truth_scope_id IS NULL", (scope_id, character_id))
                    self.connection.execute("UPDATE open_threads SET truth_scope_id=? WHERE character_id=? AND truth_scope_id IS NULL", (scope_id, character_id))
                    self.connection.execute(
                        "INSERT OR IGNORE INTO truth_scope_events(character_id, truth_scope_id, operation, event_id, excerpt_start_cp, excerpt_end_cp, excerpt_hash, created_at_us) VALUES (?, ?, 'created', NULL, NULL, NULL, NULL, ?)",
                        (character_id, scope_id, created_at),
                    )
                self.connection.execute("INSERT INTO schema_migrations(version, applied_at_us) VALUES (?, ?)", (12, utc_now_us()))
                self.connection.execute("UPDATE database_meta SET value = ? WHERE key = 'schema_version'", ("12",))
            version = 12
        if version < 13:
            # Proactive lifecycle stores only structural reason identity and a
            # content digest. Canonical displayed prose remains in conversation.json.
            self.connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE proactive_checkins (
                    character_id TEXT NOT NULL,
                    checkin_id TEXT NOT NULL,
                    reason_kind TEXT NOT NULL CHECK (reason_kind IN ('open_thread')),
                    thread_id TEXT,
                    displayed_at_us INTEGER NOT NULL,
                    conversation_index INTEGER NOT NULL,
                    assistant_content_sha256 TEXT NOT NULL,
                    responded_at_us INTEGER,
                    PRIMARY KEY(character_id, checkin_id),
                    FOREIGN KEY(character_id) REFERENCES characters(character_id)
                );
                CREATE INDEX proactive_checkins_character_time
                    ON proactive_checkins(character_id, displayed_at_us DESC, checkin_id);
                COMMIT;
                """
            )
            with self.transaction():
                self.connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at_us) VALUES (?, ?)",
                    (13, utc_now_us()),
                )
                self.connection.execute(
                    "UPDATE database_meta SET value = ? WHERE key = 'schema_version'", ("13",),
                )
            version = 13
        if version < 14:
            self.connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE active_scene_relations (
                    character_id TEXT NOT NULL,
                    relation_id TEXT NOT NULL,
                    truth_scope_id TEXT NOT NULL,
                    target_actor TEXT NOT NULL CHECK(target_actor IN ('user','companion')),
                    facet TEXT NOT NULL CHECK(facet IN ('eyes','mouth','hands','feet')),
                    predicate TEXT NOT NULL CHECK(predicate IN ('covered_by','obstructed_by','occupied_by','worn_by')),
                    cause_kind TEXT NOT NULL CHECK(cause_kind IN ('scene','actor_part')),
                    cause TEXT NOT NULL,
                    valid_from_us INTEGER NOT NULL,
                    valid_to_us INTEGER,
                    PRIMARY KEY(character_id, relation_id),
                    FOREIGN KEY(character_id) REFERENCES characters(character_id)
                );
                CREATE UNIQUE INDEX active_scene_relation_current_slot
                    ON active_scene_relations(character_id, truth_scope_id, target_actor, facet)
                    WHERE valid_to_us IS NULL;
                CREATE TABLE active_scene_relation_events (
                    relation_event_id INTEGER PRIMARY KEY,
                    character_id TEXT NOT NULL,
                    relation_id TEXT NOT NULL,
                    operation TEXT NOT NULL CHECK(operation IN ('set','confirm','clear')),
                    event_id TEXT NOT NULL,
                    excerpt_start_cp INTEGER,
                    excerpt_end_cp INTEGER,
                    excerpt_hash TEXT,
                    created_at_us INTEGER NOT NULL,
                    FOREIGN KEY(character_id, relation_id) REFERENCES active_scene_relations(character_id, relation_id),
                    FOREIGN KEY(character_id, event_id) REFERENCES events(character_id, event_id)
                );
                CREATE INDEX active_scene_relation_lookup
                    ON active_scene_relations(character_id, truth_scope_id, valid_from_us DESC, relation_id);
                COMMIT;
                """
            )
            with self.transaction():
                self.connection.execute("INSERT INTO schema_migrations(version, applied_at_us) VALUES (?, ?)", (14, utc_now_us()))
                self.connection.execute("UPDATE database_meta SET value = ? WHERE key = 'schema_version'", ("14",))
            version = 14
        if version < 15:
            # Preserve every v14 relation/event row while replacing the
            # one-relation-per-facet index with sparse multi-cause identity.
            # New nullable region/side/subject/family fields remain bounded;
            # this is still a mention-driven relation list, not an entity graph.
            self.connection.executescript(
                """
                PRAGMA foreign_keys=OFF;
                BEGIN IMMEDIATE;
                ALTER TABLE active_scene_relation_events RENAME TO active_scene_relation_events_v14;
                ALTER TABLE active_scene_relations RENAME TO active_scene_relations_v14;
                DROP INDEX active_scene_relation_current_slot;
                DROP INDEX active_scene_relation_lookup;
                CREATE TABLE active_scene_relations (
                    character_id TEXT NOT NULL,
                    relation_id TEXT NOT NULL,
                    truth_scope_id TEXT NOT NULL,
                    target_kind TEXT NOT NULL CHECK(target_kind IN ('actor','scene')),
                    target_actor TEXT NOT NULL,
                    facet TEXT CHECK(facet IS NULL OR facet IN
                        ('head','eyes','mouth','neck','torso','full_outfit','arms','hands','waist','legs','feet','full_body')),
                    side TEXT CHECK(side IS NULL OR side IN ('left','right','both')),
                    predicate TEXT NOT NULL CHECK(predicate IN
                        ('covered_by','obstructed_by','occupied_by','worn_by','wearing','holding','carrying',
                         'covering','obstructing','occupying','riding','driving','seated_in','using','supported_by',
                         'located_on','located_in')),
                    cause_kind TEXT NOT NULL CHECK(cause_kind IN ('scene','actor','actor_part','state','location')),
                    cause TEXT NOT NULL,
                    cause_subject_id TEXT,
                    semantic_family TEXT CHECK(semantic_family IS NULL OR semantic_family IN
                        ('vision_obstruction','speech_obstruction','hand_occupancy','walking','rolling','skating',
                         'cycling','driving','riding','assisted','swimming','other')),
                    quantity INTEGER CHECK(quantity IS NULL OR quantity BETWEEN 1 AND 16),
                    valid_from_us INTEGER NOT NULL,
                    valid_to_us INTEGER,
                    PRIMARY KEY(character_id, relation_id),
                    FOREIGN KEY(character_id) REFERENCES characters(character_id)
                );
                CREATE UNIQUE INDEX active_scene_relation_current_identity
                    ON active_scene_relations(
                        character_id, truth_scope_id, target_kind, target_actor,
                        COALESCE(facet,''), COALESCE(side,''), predicate, cause_kind,
                        cause, COALESCE(cause_subject_id,''))
                    WHERE valid_to_us IS NULL;
                CREATE TABLE active_scene_relation_events (
                    relation_event_id INTEGER PRIMARY KEY,
                    character_id TEXT NOT NULL,
                    relation_id TEXT NOT NULL,
                    operation TEXT NOT NULL CHECK(operation IN ('set','confirm','clear')),
                    event_id TEXT NOT NULL,
                    excerpt_start_cp INTEGER,
                    excerpt_end_cp INTEGER,
                    excerpt_hash TEXT,
                    created_at_us INTEGER NOT NULL,
                    FOREIGN KEY(character_id, relation_id) REFERENCES active_scene_relations(character_id, relation_id),
                    FOREIGN KEY(character_id, event_id) REFERENCES events(character_id, event_id)
                );
                CREATE INDEX active_scene_relation_lookup
                    ON active_scene_relations(character_id, truth_scope_id, valid_from_us DESC, relation_id);
                INSERT INTO active_scene_relations(
                    character_id, relation_id, truth_scope_id, target_kind, target_actor, facet, side,
                    predicate, cause_kind, cause, cause_subject_id, semantic_family, quantity,
                    valid_from_us, valid_to_us)
                SELECT character_id, relation_id, truth_scope_id, 'actor', target_actor, facet, NULL,
                    predicate, cause_kind, cause, NULL,
                    CASE WHEN facet='eyes' AND predicate IN ('covered_by','obstructed_by')
                         THEN 'vision_obstruction' ELSE NULL END,
                    1, valid_from_us, valid_to_us
                FROM active_scene_relations_v14;
                INSERT INTO active_scene_relation_events
                    SELECT * FROM active_scene_relation_events_v14;
                DROP TABLE active_scene_relation_events_v14;
                DROP TABLE active_scene_relations_v14;
                COMMIT;
                PRAGMA foreign_keys=ON;
                """
            )
            foreign_key_errors = self.connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_errors:
                raise StoreError("schema v15 relation migration failed foreign-key validation")
            with self.transaction():
                self.connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at_us) VALUES (?, ?)",
                    (15, utc_now_us()),
                )
                self.connection.execute(
                    "UPDATE database_meta SET value = ? WHERE key = 'schema_version'", ("15",),
                )
            version = 15
        if version < 16:
            # A rejected proactive draft is a scheduler attempt, not a
            # displayed check-in. Persist only structural cooldown state so a
            # restart cannot immediately hammer the same eligible thread.
            self.connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE proactive_attempts (
                    character_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    reason_kind TEXT NOT NULL,
                    thread_id TEXT,
                    attempted_at_us INTEGER NOT NULL,
                    outcome TEXT NOT NULL,
                    PRIMARY KEY(character_id, attempt_id),
                    FOREIGN KEY(character_id) REFERENCES characters(character_id)
                );
                CREATE INDEX proactive_attempts_character_time
                    ON proactive_attempts(character_id, attempted_at_us DESC, attempt_id);
                COMMIT;
                """
            )
            with self.transaction():
                self.connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at_us) VALUES (?, ?)",
                    (16, utc_now_us()),
                )
                self.connection.execute(
                    "UPDATE database_meta SET value = ? WHERE key = 'schema_version'", ("16",),
                )
            version = 16
        if version < 17:
            # Extend the same sparse relation lane with semantic severity and
            # retain an append-only subject lifecycle trail. Canonical events
            # and historical relation rows are preserved byte-for-byte.
            self.connection.executescript(
                """
                PRAGMA foreign_keys=OFF;
                BEGIN IMMEDIATE;
                ALTER TABLE active_scene_relation_events RENAME TO active_scene_relation_events_v16;
                ALTER TABLE active_scene_relations RENAME TO active_scene_relations_v16;
                DROP INDEX active_scene_relation_current_identity;
                DROP INDEX active_scene_relation_lookup;
                CREATE TABLE active_scene_relations (
                    character_id TEXT NOT NULL,
                    relation_id TEXT NOT NULL,
                    truth_scope_id TEXT NOT NULL,
                    target_kind TEXT NOT NULL CHECK(target_kind IN ('actor','scene')),
                    target_actor TEXT NOT NULL,
                    facet TEXT CHECK(facet IS NULL OR facet IN
                        ('head','eyes','ears','nose','mouth','tongue','neck','torso','full_outfit',
                         'arms','wrists','hands','skin','waist','legs','feet','full_body')),
                    side TEXT CHECK(side IS NULL OR side IN ('left','right','both')),
                    predicate TEXT NOT NULL CHECK(predicate IN
                        ('covered_by','obstructed_by','occupied_by','worn_by','wearing','holding','carrying',
                         'covering','obstructing','occupying','riding','driving','seated_in','using','supported_by',
                         'located_on','located_in','near','attached_to','tethered_to','restrained_by',
                         'unavailable_due_to')),
                    cause_kind TEXT NOT NULL CHECK(cause_kind IN
                        ('scene','actor','actor_part','state','location','environment','body_state')),
                    cause TEXT NOT NULL,
                    cause_subject_id TEXT,
                    semantic_family TEXT CHECK(semantic_family IS NULL OR semantic_family IN
                        ('vision_obstruction','hearing_obstruction','smell_obstruction','taste_obstruction',
                         'touch_obstruction','speech_obstruction','hand_occupancy','body_unavailable',
                         'body_assistance','restraint','mobility_constraint','walking','rolling','skating','cycling',
                         'driving','riding','assisted','swimming','other')),
                    quantity INTEGER CHECK(quantity IS NULL OR quantity BETWEEN 1 AND 16),
                    effect_state TEXT CHECK(effect_state IS NULL OR effect_state IN ('constrained','unavailable')),
                    valid_from_us INTEGER NOT NULL,
                    valid_to_us INTEGER,
                    PRIMARY KEY(character_id, relation_id),
                    FOREIGN KEY(character_id) REFERENCES characters(character_id)
                );
                CREATE UNIQUE INDEX active_scene_relation_current_identity
                    ON active_scene_relations(
                        character_id, truth_scope_id, target_kind, target_actor,
                        COALESCE(facet,''), COALESCE(side,''), predicate, cause_kind,
                        cause, COALESCE(cause_subject_id,''))
                    WHERE valid_to_us IS NULL;
                CREATE TABLE active_scene_relation_events (
                    relation_event_id INTEGER PRIMARY KEY,
                    character_id TEXT NOT NULL,
                    relation_id TEXT NOT NULL,
                    operation TEXT NOT NULL CHECK(operation IN ('set','confirm','clear')),
                    event_id TEXT NOT NULL,
                    excerpt_start_cp INTEGER,
                    excerpt_end_cp INTEGER,
                    excerpt_hash TEXT,
                    created_at_us INTEGER NOT NULL,
                    FOREIGN KEY(character_id, relation_id) REFERENCES active_scene_relations(character_id, relation_id),
                    FOREIGN KEY(character_id, event_id) REFERENCES events(character_id, event_id)
                );
                CREATE INDEX active_scene_relation_lookup
                    ON active_scene_relations(character_id, truth_scope_id, valid_from_us DESC, relation_id);
                INSERT INTO active_scene_relations(
                    character_id, relation_id, truth_scope_id, target_kind, target_actor, facet, side,
                    predicate, cause_kind, cause, cause_subject_id, semantic_family, quantity, effect_state,
                    valid_from_us, valid_to_us)
                SELECT character_id, relation_id, truth_scope_id, target_kind, target_actor, facet, side,
                    predicate, cause_kind, cause, cause_subject_id, semantic_family, quantity, NULL,
                    valid_from_us, valid_to_us
                FROM active_scene_relations_v16;
                INSERT INTO active_scene_relation_events SELECT * FROM active_scene_relation_events_v16;
                DROP TABLE active_scene_relation_events_v16;
                DROP TABLE active_scene_relations_v16;

                ALTER TABLE active_scene_subjects ADD COLUMN last_referenced_at_us INTEGER;
                ALTER TABLE active_scene_subjects ADD COLUMN identity_strength TEXT NOT NULL DEFAULT 'generic'
                    CHECK(identity_strength IN ('generic','distinct'));
                UPDATE active_scene_subjects SET last_referenced_at_us=COALESCE(retired_at_us, introduced_at_us);
                CREATE TABLE active_scene_subject_lifecycle_events (
                    lifecycle_event_id INTEGER PRIMARY KEY,
                    character_id TEXT NOT NULL,
                    scene_subject_id TEXT NOT NULL,
                    operation TEXT NOT NULL CHECK(operation IN ('introduced','referenced','dormant','retired','reactivated')),
                    event_id TEXT,
                    created_at_us INTEGER NOT NULL,
                    FOREIGN KEY(character_id, scene_subject_id)
                        REFERENCES active_scene_subjects(character_id, scene_subject_id),
                    FOREIGN KEY(character_id, event_id) REFERENCES events(character_id, event_id)
                );
                CREATE INDEX active_scene_subject_lifecycle_lookup
                    ON active_scene_subject_lifecycle_events(character_id, scene_subject_id, created_at_us DESC);
                CREATE INDEX active_scene_subject_salience_lookup
                    ON active_scene_subjects(character_id, truth_scope_id, retired_at_us,
                                             last_referenced_at_us DESC, scene_subject_id);
                INSERT INTO active_scene_subject_lifecycle_events(
                    character_id, scene_subject_id, operation, event_id, created_at_us)
                SELECT character_id, scene_subject_id, 'introduced', introduced_event_id, introduced_at_us
                FROM active_scene_subjects;
                INSERT INTO active_scene_subject_lifecycle_events(
                    character_id, scene_subject_id, operation, event_id, created_at_us)
                SELECT character_id, scene_subject_id, 'retired', retired_event_id, retired_at_us
                FROM active_scene_subjects WHERE retired_at_us IS NOT NULL;
                COMMIT;
                PRAGMA foreign_keys=ON;
                """
            )
            foreign_key_errors = self.connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_errors:
                raise StoreError("schema v17 scene migration failed foreign-key validation")
            with self.transaction():
                self.connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at_us) VALUES (?, ?)",
                    (17, utc_now_us()),
                )
                self.connection.execute(
                    "UPDATE database_meta SET value = ? WHERE key = 'schema_version'", ("17",),
                )
            version = 17
        if version < 18:
            # Historical conversation evidence records only that a canonical
            # source occurred. It is deliberately a different claim type from
            # present-tense durable truth, and an unknown historical scope is
            # represented explicitly with a NULL truth_scope_id.
            self.connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE historical_evidence (
                    character_id TEXT NOT NULL,
                    claim_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    canonical_index INTEGER NOT NULL CHECK(canonical_index >= 0),
                    canonical_record_id TEXT NOT NULL,
                    speaker_role TEXT NOT NULL CHECK(speaker_role IN ('user','assistant')),
                    speech_act TEXT NOT NULL CHECK(speech_act IN ('assertion','question','other')),
                    source_class TEXT NOT NULL,
                    scope_state TEXT NOT NULL CHECK(scope_state IN ('real_world','scenario','unknown_scope')),
                    truth_scope_id TEXT,
                    source_content_sha256 TEXT NOT NULL,
                    projection_version INTEGER NOT NULL CHECK(projection_version >= 1),
                    retrieval_eligible INTEGER NOT NULL CHECK(retrieval_eligible IN (0,1)),
                    PRIMARY KEY(character_id, claim_id),
                    UNIQUE(character_id, canonical_record_id),
                    FOREIGN KEY(character_id, claim_id) REFERENCES claims(character_id, claim_id),
                    FOREIGN KEY(character_id, event_id) REFERENCES events(character_id, event_id),
                    FOREIGN KEY(character_id, truth_scope_id)
                        REFERENCES truth_scopes(character_id, truth_scope_id),
                    CHECK(
                        (scope_state='unknown_scope' AND truth_scope_id IS NULL)
                        OR (scope_state IN ('real_world','scenario') AND truth_scope_id IS NOT NULL)
                    )
                );
                CREATE INDEX historical_evidence_source_lookup
                    ON historical_evidence(character_id, canonical_index, claim_id);
                CREATE INDEX historical_evidence_retrieval_lookup
                    ON historical_evidence(character_id, retrieval_eligible, scope_state,
                                           truth_scope_id, claim_id);
                CREATE VIRTUAL TABLE historical_evidence_fts USING fts5(
                    character_id UNINDEXED,
                    claim_id UNINDEXED,
                    searchable_text
                );
                CREATE TABLE historical_evidence_checkpoints (
                    character_id TEXT PRIMARY KEY,
                    policy_version TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    next_index INTEGER NOT NULL CHECK(next_index >= 0),
                    source_prefix_digest TEXT NOT NULL,
                    source_archive_digest TEXT NOT NULL,
                    source_record_count INTEGER NOT NULL CHECK(source_record_count >= 0),
                    state TEXT NOT NULL CHECK(state IN ('running','complete')),
                    updated_at_us INTEGER NOT NULL,
                    FOREIGN KEY(character_id) REFERENCES characters(character_id)
                );
                INSERT INTO schema_migrations(version, applied_at_us)
                    VALUES (18, CAST((julianday('now') - 2440587.5) * 86400000000 AS INTEGER));
                UPDATE database_meta SET value = '18' WHERE key = 'schema_version';
                COMMIT;
                """
            )

        if self.connection.execute("SELECT 1 FROM schema_migrations WHERE version=19").fetchone() is None:
            self.connection.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE canonical_observation_progress (
                    character_id TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    consumer TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    next_index INTEGER NOT NULL CHECK(next_index >= 0),
                    prefix_digest TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('pending','complete','unresolved','failed')),
                    reason TEXT NOT NULL,
                    updated_at_us INTEGER NOT NULL,
                    PRIMARY KEY(character_id, source_key, consumer, policy_version),
                    FOREIGN KEY(character_id) REFERENCES characters(character_id)
                );
                INSERT INTO schema_migrations(version, applied_at_us)
                    VALUES (19, CAST((julianday('now') - 2440587.5) * 86400000000 AS INTEGER));
                UPDATE database_meta SET value='19' WHERE key='schema_version';
                COMMIT;
            """)

        if self.connection.execute("SELECT 1 FROM schema_migrations WHERE version=20").fetchone() is None:
            self.connection.executescript("""
                BEGIN IMMEDIATE;
                ALTER TABLE active_scene_relations ADD COLUMN locus TEXT
                    CHECK(locus IS NULL OR length(locus) BETWEEN 1 AND 96);
                DROP INDEX active_scene_relation_current_identity;
                CREATE UNIQUE INDEX active_scene_relation_current_identity
                    ON active_scene_relations(character_id,truth_scope_id,target_kind,target_actor,
                        COALESCE(facet,''),COALESCE(side,''),predicate,cause_kind,cause,
                        COALESCE(cause_subject_id,''),COALESCE(locus,'')) WHERE valid_to_us IS NULL;
                INSERT INTO schema_migrations(version,applied_at_us)
                    VALUES (20,CAST((julianday('now')-2440587.5)*86400000000 AS INTEGER));
                UPDATE database_meta SET value='20' WHERE key='schema_version';
                COMMIT;
            """)

        if self.connection.execute("SELECT 1 FROM schema_migrations WHERE version=21").fetchone() is None:
            self.connection.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE canonical_observation_dispositions (
                    character_id TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    consumer TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    source_index INTEGER NOT NULL CHECK(source_index >= 0),
                    source_digest TEXT NOT NULL,
                    truth_scope_id TEXT NOT NULL,
                    reason TEXT NOT NULL CHECK(reason IN ('non_mutating_question','superseded_actor_activity')),
                    witness_event_id TEXT,
                    decided_at_us INTEGER NOT NULL,
                    PRIMARY KEY(character_id,source_key,consumer,policy_version,source_index),
                    FOREIGN KEY(character_id) REFERENCES characters(character_id)
                );
                INSERT INTO schema_migrations(version,applied_at_us)
                    VALUES (21,CAST((julianday('now')-2440587.5)*86400000000 AS INTEGER));
                UPDATE database_meta SET value='21' WHERE key='schema_version';
                COMMIT;
            """)

    def _close_exclusive_relation_conflicts(
        self,
        character_id: str,
        scope_id: str,
        proposal: SceneRelationProposal,
        *,
        target: str,
        cause_subject_id: str | None,
        effective_at_us: int,
        evidence_event_id: str,
        excerpt_start_cp: int,
        excerpt_end_cp: int,
        excerpt_hash: str,
    ) -> tuple[str, ...]:
        """Close impossible current ownership/location peers before one set."""
        if proposal.locus is not None:
            return ()  # No implicit exclusive occupancy for explicit loci.
        if proposal.target_kind == "scene" and proposal.predicate in {"located_on", "located_in"}:
            rows = self.connection.execute(
                """SELECT * FROM active_scene_relations
                     WHERE character_id=? AND truth_scope_id=? AND target_kind='scene'
                       AND target_actor=? AND predicate IN ('located_on','located_in')
                       AND valid_to_us IS NULL ORDER BY relation_id LIMIT 33""",
                (character_id, scope_id, target),
            ).fetchall()
        elif cause_subject_id is not None and proposal.predicate in {"holding", "carrying"}:
            rows = self.connection.execute(
                """SELECT * FROM active_scene_relations
                     WHERE character_id=? AND truth_scope_id=? AND cause_subject_id=?
                       AND predicate IN ('holding','carrying') AND valid_to_us IS NULL
                     ORDER BY relation_id LIMIT 33""",
                (character_id, scope_id, cause_subject_id),
            ).fetchall()
        elif proposal.predicate in {"wearing", "worn_by"} and (
            cause_subject_id is not None
            or proposal.target_kind == "actor" and proposal.facet in {"head", "feet", "full_outfit"}
        ):
            clauses = []
            parameters: list[object] = [character_id, scope_id]
            if cause_subject_id is not None:
                clauses.append("cause_subject_id=?")
                parameters.append(cause_subject_id)
            if (proposal.target_kind == "actor"
                    and proposal.facet in {"head", "feet", "full_outfit"}):
                clauses.append("(target_kind='actor' AND target_actor=? AND facet=?)")
                parameters.extend((target, proposal.facet))
            rows = self.connection.execute(
                """SELECT * FROM active_scene_relations
                     WHERE character_id=? AND truth_scope_id=?
                       AND (""" + " OR ".join(clauses) + """ )
                       AND predicate IN ('wearing','worn_by') AND valid_to_us IS NULL
                     ORDER BY relation_id LIMIT 33""",
                parameters,
            ).fetchall()
        else:
            return ()
        if len(rows) > 32:
            raise StoreError("exclusive scene relation set exceeds the governed bound")
        desired = (
            proposal.target_kind, target, proposal.facet, proposal.side,
            proposal.predicate, proposal.cause_kind, proposal.cause, cause_subject_id,
        )
        closed: list[str] = []
        for row in rows:
            if int(row["valid_from_us"]) > effective_at_us:
                raise StoreError("scene relation evidence predates an exclusive current relation")
            current = (
                str(row["target_kind"]), str(row["target_actor"]), row["facet"], row["side"],
                str(row["predicate"]), str(row["cause_kind"]), str(row["cause"]),
                str(row["cause_subject_id"]) if row["cause_subject_id"] is not None else None,
            )
            if current == desired:
                continue
            relation_id = str(row["relation_id"])
            self.connection.execute(
                "UPDATE active_scene_relations SET valid_to_us=? WHERE character_id=? AND relation_id=?",
                (effective_at_us, character_id, relation_id),
            )
            self.connection.execute(
                """INSERT INTO active_scene_relation_events(character_id, relation_id, operation,
                   event_id, excerpt_start_cp, excerpt_end_cp, excerpt_hash, created_at_us)
                   VALUES (?, ?, 'clear', ?, ?, ?, ?, ?)""",
                (character_id, relation_id, evidence_event_id, excerpt_start_cp,
                 excerpt_end_cp, excerpt_hash, effective_at_us),
            )
            closed.append(relation_id)
        return tuple(closed)

    def _mark_scene_subject_referenced(
        self, character_id: str, scene_subject_id: str, *, event_id: str, at_us: int,
    ) -> None:
        updated = self.connection.execute(
            """UPDATE active_scene_subjects SET last_referenced_at_us=MAX(
                       COALESCE(last_referenced_at_us, 0), ?)
                 WHERE character_id=? AND scene_subject_id=? AND retired_at_us IS NULL""",
            (at_us, character_id, scene_subject_id),
        ).rowcount
        if updated:
            self.connection.execute(
                """INSERT INTO active_scene_subject_lifecycle_events(
                       character_id, scene_subject_id, operation, event_id, created_at_us)
                   VALUES (?, ?, 'referenced', ?, ?)""",
                (character_id, scene_subject_id, event_id, at_us),
            )

    def _mark_scene_subject_dormant_if_eligible(
        self, character_id: str, scene_subject_id: str, *, scope_id: str, event_id: str, at_us: int,
    ) -> None:
        active = self.connection.execute(
            """SELECT 1 FROM active_scene_relations
                 WHERE character_id=? AND truth_scope_id=? AND valid_to_us IS NULL
                   AND ((target_kind='scene' AND target_actor=?) OR cause_subject_id=?) LIMIT 1""",
            (character_id, scope_id, scene_subject_id, scene_subject_id),
        ).fetchone()
        if active is None and self.connection.execute(
            """SELECT 1 FROM active_scene_subjects
                 WHERE character_id=? AND truth_scope_id=? AND scene_subject_id=? AND retired_at_us IS NULL""",
            (character_id, scope_id, scene_subject_id),
        ).fetchone() is not None:
            self.connection.execute(
                """INSERT INTO active_scene_subject_lifecycle_events(
                       character_id, scene_subject_id, operation, event_id, created_at_us)
                   VALUES (?, ?, 'dormant', ?, ?)""",
                (character_id, scene_subject_id, event_id, at_us),
            )

    def apply_scene_relation_proposal(
        self, character_id: str, proposal: SceneRelationProposal, *, evidence_event_id: str,
        truth_scope_id: str | None = None, evidence_role: str = "direct_user_statement",
    ) -> str | None:
        """Compatibility wrapper for one relation mutation."""
        changed = self.apply_scene_relation_proposals(
            character_id, (proposal,), evidence_event_id=evidence_event_id,
            truth_scope_id=truth_scope_id, evidence_role=evidence_role,
        )
        return changed[0] if changed else None

    def apply_scene_relation_proposals(
        self,
        character_id: str,
        proposals: tuple[SceneRelationProposal, ...],
        *,
        evidence_event_id: str,
        truth_scope_id: str | None = None,
        local_scene_refs: dict[str, str] | None = None,
        evidence_role: str = "direct_user_statement",
    ) -> tuple[str, ...]:
        """Atomically apply a bounded sparse relation batch from one user event."""
        if not isinstance(proposals, tuple) or not 1 <= len(proposals) <= 32:
            raise StoreError("scene relation proposal batch exceeds the governed bound")
        try:
            validated = tuple(validate_scene_relation_proposal(item) for item in proposals)
        except ValueError as error:
            raise StoreError(str(error)) from error
        character_id, effective_at_us, content = self._active_state_evidence_time(
            character_id, evidence_event_id, evidence_role,
        )
        for proposal in validated:
            start, end = proposal.excerpt_start_cp, proposal.excerpt_end_cp
            if start is None or end is None or end > len(content):
                raise StoreError("scene relation evidence span exceeds canonical evidence")
        refs = dict(local_scene_refs or {})
        changed: list[str] = []
        seen_set_identities: set[tuple[object, ...]] = set()
        with self.transaction():
            scope_id = self._write_truth_scope_id(character_id, truth_scope_id)
            # A relabel is a clear/set pair for one already located object.
            # Prove retained loci before the first mutation closes that row;
            # a later operation may not borrow a locus created by this batch.
            # Identity includes character, scope, actor/scene target, object,
            # literal locus and predicate, so another relation cannot supply it.
            for proposal in validated:
                if proposal.locus is None:
                    continue
                target = refs.get(proposal.target, proposal.target)
                cause_subject_id = (
                    refs.get(proposal.cause_subject_ref, proposal.cause_subject_ref)
                    if proposal.cause_subject_ref is not None else None
                )
                prior = self.connection.execute(
                    """SELECT 1 FROM active_scene_relations WHERE character_id=? AND truth_scope_id=?
                       AND target_kind=? AND target_actor=? AND cause_subject_id=? AND locus=?
                       AND predicate=? AND valid_to_us IS NULL LIMIT 1""",
                    (character_id, scope_id, proposal.target_kind, target, cause_subject_id,
                     proposal.locus, proposal.predicate),
                ).fetchone()
                if prior is None and proposal.locus not in content[proposal.excerpt_start_cp:proposal.excerpt_end_cp]:
                    raise StoreError("relation locus is not literal source evidence")
            touched_subjects: set[str] = set()
            for proposal_index, proposal in enumerate(validated):
                target = refs.get(proposal.target, proposal.target)
                cause_subject_id = (
                    refs.get(proposal.cause_subject_ref, proposal.cause_subject_ref)
                    if proposal.cause_subject_ref is not None else None
                )
                if proposal.target_kind == "scene" and not self._scene_subject_is_active(
                    character_id, target, effective_at_us, scope_id,
                ):
                    raise StoreError("scene relation target is not a current scoped subject")
                if cause_subject_id is not None and not self._scene_subject_is_active(
                    character_id, cause_subject_id, effective_at_us, scope_id,
                ):
                    raise StoreError("scene relation object is not a current scoped subject")
                if proposal.target_kind == "scene":
                    touched_subjects.add(target)
                if cause_subject_id is not None:
                    touched_subjects.add(cause_subject_id)
                start, end = proposal.excerpt_start_cp, proposal.excerpt_end_cp
                assert start is not None and end is not None
                excerpt_hash = hashlib.sha256(content[start:end].encode("utf-8")).hexdigest()

                if proposal.operation == "clear":
                    clauses = [
                        "character_id=?", "truth_scope_id=?", "target_kind=?", "target_actor=?",
                        "valid_to_us IS NULL",
                    ]
                    parameters: list[object] = [character_id, scope_id, proposal.target_kind, target]
                    for column, value in (
                        ("facet", proposal.facet), ("side", proposal.side),
                        ("predicate", proposal.predicate), ("cause_kind", proposal.cause_kind),
                        ("cause", proposal.cause), ("cause_subject_id", cause_subject_id),
                        ("locus", proposal.locus),
                    ):
                        if value is not None:
                            clauses.append(f"{column}=?")
                            parameters.append(value)
                    rows = self.connection.execute(
                        "SELECT relation_id, valid_from_us FROM active_scene_relations WHERE " + " AND ".join(clauses)
                        + " ORDER BY valid_from_us, relation_id LIMIT 33",
                        parameters,
                    ).fetchall()
                    if len(rows) > 32:
                        raise StoreError("scene relation clear exceeds the governed bound")
                    for row in rows:
                        if int(row["valid_from_us"]) > effective_at_us:
                            raise StoreError("scene relation clear predates the current relation")
                        relation_id = str(row["relation_id"])
                        self.connection.execute(
                            "UPDATE active_scene_relations SET valid_to_us=? WHERE character_id=? AND relation_id=?",
                            (effective_at_us, character_id, relation_id),
                        )
                        self.connection.execute(
                            """INSERT INTO active_scene_relation_events(character_id, relation_id, operation,
                               event_id, excerpt_start_cp, excerpt_end_cp, excerpt_hash, created_at_us)
                               VALUES (?, ?, 'clear', ?, ?, ?, ?, ?)""",
                            (character_id, relation_id, evidence_event_id, start, end, excerpt_hash, effective_at_us),
                        )
                        changed.append(relation_id)
                    continue

                identity = (
                    proposal.target_kind, target, proposal.facet, proposal.side,
                    proposal.predicate, proposal.cause_kind, proposal.cause, cause_subject_id, proposal.locus,
                )
                if identity in seen_set_identities:
                    raise StoreError("one relation batch cannot establish the same current identity twice")
                seen_set_identities.add(identity)
                self._close_exclusive_relation_conflicts(
                    character_id, scope_id, proposal, target=target,
                    cause_subject_id=cause_subject_id, effective_at_us=effective_at_us,
                    evidence_event_id=evidence_event_id, excerpt_start_cp=start,
                    excerpt_end_cp=end, excerpt_hash=excerpt_hash,
                )
                current = self.connection.execute(
                    """SELECT relation_id, semantic_family, quantity, effect_state, valid_from_us FROM active_scene_relations
                         WHERE character_id=? AND truth_scope_id=? AND target_kind=? AND target_actor=?
                           AND facet IS ? AND side IS ? AND predicate=? AND cause_kind=? AND cause=?
                           AND cause_subject_id IS ? AND locus IS ? AND valid_to_us IS NULL LIMIT 2""",
                    (character_id, scope_id, proposal.target_kind, target, proposal.facet,
                     proposal.side, proposal.predicate, proposal.cause_kind, proposal.cause,
                     cause_subject_id, proposal.locus),
                ).fetchall()
                if len(current) > 1:
                    raise StoreError("duplicate current scene relation identity exists")
                if current:
                    current_row = current[0]
                    if int(current_row["valid_from_us"]) > effective_at_us:
                        raise StoreError("scene relation evidence predates the current identity")
                    same_metadata = (
                        current_row["semantic_family"] == proposal.semantic_family
                        and (current_row["quantity"] or 1) == (proposal.quantity or 1)
                        and current_row["effect_state"] == proposal.effect_state
                    )
                    if same_metadata:
                        relation_id = str(current_row["relation_id"])
                        operation = "confirm"
                    else:
                        # Quantity and semantic family are part of the current
                        # relation meaning even though they are not identity
                        # selectors. Supersede the old row non-destructively so
                        # a later explicit "two boxes" cannot remain the old
                        # one-box capability cause merely because it resolves
                        # to the same stable scene subject.
                        prior_relation_id = str(current_row["relation_id"])
                        self.connection.execute(
                            """UPDATE active_scene_relations SET valid_to_us=?
                                 WHERE character_id=? AND relation_id=?""",
                            (effective_at_us, character_id, prior_relation_id),
                        )
                        self.connection.execute(
                            """INSERT INTO active_scene_relation_events(character_id, relation_id,
                               operation, event_id, excerpt_start_cp, excerpt_end_cp, excerpt_hash,
                               created_at_us) VALUES (?, ?, 'clear', ?, ?, ?, ?, ?)""",
                            (character_id, prior_relation_id, evidence_event_id, start, end,
                             excerpt_hash, effective_at_us),
                        )
                        current = []
                if not current:
                    current_count = int(self.connection.execute(
                        """SELECT COUNT(*) FROM active_scene_relations
                             WHERE character_id=? AND truth_scope_id=? AND valid_to_us IS NULL""",
                        (character_id, scope_id),
                    ).fetchone()[0])
                    if current_count >= MAX_CURRENT_SCENE_RELATIONS:
                        raise StoreError("current scene relation set exceeds the governed bound")
                    identity_text = ":".join(str(item or "-") for item in identity)
                    relation_id = str(uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"aifren:scene-relation:{character_id}:{scope_id}:{evidence_event_id}:{proposal_index}:{identity_text}",
                    ))
                    self.connection.execute(
                        """INSERT INTO active_scene_relations(
                               character_id, relation_id, truth_scope_id, target_kind, target_actor,
                               facet, side, predicate, cause_kind, cause, cause_subject_id,
                               semantic_family, quantity, effect_state, valid_from_us, valid_to_us, locus)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)""",
                        (character_id, relation_id, scope_id, proposal.target_kind, target,
                         proposal.facet, proposal.side, proposal.predicate, proposal.cause_kind,
                         proposal.cause, cause_subject_id, proposal.semantic_family,
                         proposal.quantity, proposal.effect_state, effective_at_us, proposal.locus),
                    )
                    operation = "set"
                self.connection.execute(
                    """INSERT INTO active_scene_relation_events(character_id, relation_id, operation, event_id,
                       excerpt_start_cp, excerpt_end_cp, excerpt_hash, created_at_us)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (character_id, relation_id, operation, evidence_event_id, start, end,
                     excerpt_hash, effective_at_us),
                )
                changed.append(relation_id)
            for scene_subject_id in sorted(touched_subjects):
                self._mark_scene_subject_referenced(
                    character_id, scene_subject_id, event_id=evidence_event_id, at_us=effective_at_us,
                )
                self._mark_scene_subject_dormant_if_eligible(
                    character_id, scene_subject_id, scope_id=scope_id,
                    event_id=evidence_event_id, at_us=effective_at_us,
                )
        return tuple(changed)

    def synchronize_scene_ownership_mirrors(
        self,
        character_id: str,
        *,
        evidence_event_id: str,
        truth_scope_id: str | None = None,
        evidence_role: str = "direct_user_statement",
    ) -> tuple[dict[str, object], ...]:
        """Project authoritative ownership relations into compatibility attributes.

        ``wearing``/``holding`` relations own current truth. ``worn_by`` and
        ``held_by`` Active-State attributes are bounded snapshot compatibility
        mirrors and are repaired inside the same governed transaction.
        """
        character_id, effective_at_us, evidence_content = self._active_state_evidence_time(
            character_id, evidence_event_id, evidence_role,
        )
        scope_id = self._write_truth_scope_id(character_id, truth_scope_id)
        from .repository import MemoryV2Repository
        repository = MemoryV2Repository(self)
        relations = repository.list_scene_relations(
            character_id, historical_at_us=effective_at_us,
            truth_scope_id=scope_id, limit=MAX_CURRENT_SCENE_RELATIONS,
        )
        desired: dict[tuple[str, str], str] = {}
        for relation in relations:
            if relation.target_kind != "actor" or relation.cause_subject_id is None:
                continue
            attribute = (
                "held_by" if relation.predicate in {"holding", "carrying"}
                else "worn_by" if relation.predicate in {"wearing", "worn_by"}
                else None
            )
            if attribute is None:
                continue
            key = (relation.cause_subject_id, attribute)
            prior = desired.get(key)
            if prior is not None and prior != relation.target:
                raise StoreError("scene ownership relations disagree for one subject")
            desired[key] = relation.target

        changed: list[dict[str, object]] = []
        for subject in repository.list_scene_subjects(
            character_id, historical_at_us=effective_at_us,
            truth_scope_id=scope_id, limit=MAX_ACTIVE_SCENE_SUBJECTS,
        ):
            attributes = {
                record.subject_key.rsplit(".", 1)[-1]: record.value
                for record in repository.lookup_scene_attributes(
                    character_id, subject.scene_subject_id,
                    historical_at_us=effective_at_us, truth_scope_id=scope_id,
                )
            }
            for attribute in ("held_by", "worn_by"):
                expected = desired.get((subject.scene_subject_id, attribute))
                current = attributes.get(attribute)
                if current == expected:
                    continue
                subject_key = scene_state_subject_key(subject.scene_subject_id, attribute)
                if expected is None:
                    cleared = self.clear_active_state(
                        character_id, subject_key=subject_key,
                        evidence_event_id=evidence_event_id,
                        evidence_role=evidence_role,
                        truth_scope_id=scope_id,
                    )
                    changed.append({"slot": subject_key, "operation": "clear", "cleared": cleared})
                else:
                    state_id = self.set_active_state(
                        character_id,
                        str(uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"aifren:scene-ownership-mirror:{character_id}:{scope_id}:"
                            f"{evidence_event_id}:{subject.scene_subject_id}:{attribute}",
                        )),
                        subject_key=subject_key, value=expected,
                        evidence_event_id=evidence_event_id,
                        evidence_role=evidence_role,
                        evidence_excerpt_start_cp=0,
                        evidence_excerpt_end_cp=len(evidence_content),
                        truth_scope_id=scope_id,
                    )
                    changed.append({"slot": subject_key, "operation": "set", "state_id": state_id})
        return tuple(changed)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with continuity_write_scope(self):
            # Active-state proposals validate multiple slot updates up front and
            # apply them as one transaction. Existing single-operation methods may
            # therefore be safely reused inside that outer transaction.
            if self.connection.in_transaction:
                yield
                return
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield
            except Exception:
                self.connection.execute("ROLLBACK")
                raise
            else:
                self.connection.execute("COMMIT")

    def close(self) -> None:
        with continuity_close_scope(self):
            self.connection.close()

    def schema_version(self) -> int:
        return int(self.connection.execute("SELECT value FROM database_meta WHERE key = 'schema_version'").fetchone()[0])

    def pragma(self, name: str) -> Any:
        return self.connection.execute(f"PRAGMA {name}").fetchone()[0]

    def create_character(self, character_id: str, display_name: str, *, legacy_config_key: Optional[str] = None, metadata: Optional[dict] = None, created_at_us: Optional[int] = None) -> None:
        character_id = _require_uuid(character_id)
        if not str(display_name).strip():
            raise StoreError("display_name is required.")
        created_at = created_at_us or utc_now_us()
        with self.transaction():
            self.connection.execute(
                "INSERT INTO characters(character_id, display_name, created_at_us, archived_at_us, legacy_config_key, metadata_json) VALUES (?, ?, ?, NULL, ?, ?)",
                (character_id, str(display_name).strip(), created_at, legacy_config_key, json.dumps(metadata or {}, sort_keys=True)),
            )
            self._ensure_default_truth_scope(character_id, created_at_us=created_at)

    def _ensure_default_truth_scope(self, character_id: str, *, created_at_us: int | None = None) -> str:
        """Create the one governed real-world scope for a character if absent."""
        character_id = _require_uuid(character_id)
        scope_id = default_real_world_scope_id(character_id)
        created_at = utc_now_us() if created_at_us is None else int(created_at_us)
        self.connection.execute(
            "INSERT OR IGNORE INTO truth_scopes VALUES (?, ?, 'real_world', 'Real world', 'active', ?, ?)",
            (character_id, scope_id, created_at, created_at),
        )
        self.connection.execute(
            "INSERT OR IGNORE INTO truth_scope_events(character_id, truth_scope_id, operation, event_id, excerpt_start_cp, excerpt_end_cp, excerpt_hash, created_at_us) VALUES (?, ?, 'created', NULL, NULL, NULL, NULL, ?)",
            (character_id, scope_id, created_at),
        )
        self.connection.execute(
            "UPDATE characters SET active_truth_scope_id=COALESCE(active_truth_scope_id, ?) WHERE character_id=?",
            (scope_id, character_id),
        )
        return scope_id

    def default_truth_scope_id(self, character_id: str) -> str:
        character_id = _require_uuid(character_id)
        scope_id = default_real_world_scope_id(character_id)
        row = self.connection.execute(
            "SELECT truth_scope_id FROM truth_scopes "
            "WHERE character_id=? AND truth_scope_id=?",
            (character_id, scope_id),
        ).fetchone()
        if row is not None:
            return scope_id
        with self.transaction():
            return self._ensure_default_truth_scope(character_id)

    def active_truth_scope_id(self, character_id: str) -> str:
        character_id = _require_uuid(character_id)
        row = self.connection.execute(
            "SELECT active_truth_scope_id FROM characters WHERE character_id=?",
            (character_id,),
        ).fetchone()
        if row is None:
            raise StoreError("character is absent from the Memory V2 store.")
        if row["active_truth_scope_id"]:
            return str(row["active_truth_scope_id"])
        with self.transaction():
            default_scope = self._ensure_default_truth_scope(character_id)
            row = self.connection.execute(
                "SELECT active_truth_scope_id FROM characters WHERE character_id=?", (character_id,),
            ).fetchone()
            return str(row["active_truth_scope_id"] or default_scope)

    def _require_truth_scope(self, character_id: str, truth_scope_id: str, *, require_active: bool = False) -> str:
        character_id = _require_uuid(character_id)
        try:
            truth_scope_id = validate_truth_scope_id(truth_scope_id)
        except ValueError as error:
            raise StoreError(str(error)) from error
        row = self.connection.execute(
            "SELECT truth_scope_id FROM truth_scopes WHERE character_id=? AND truth_scope_id=?",
            (character_id, truth_scope_id),
        ).fetchone()
        if row is None:
            raise StoreError("truth scope is absent for this character.")
        if require_active and self.active_truth_scope_id(character_id) != truth_scope_id:
            raise StoreError("truth scope is not currently active for this character.")
        return truth_scope_id

    def _retrieval_scope_sql(
        self,
        character_id: str,
        truth_scope_id: str | None = None,
        *,
        include_historical_evidence: bool = False,
    ) -> tuple[str, tuple[object, ...]]:
        """Return the one generic-retrieval truth boundary and its SQL values.

        Current scoped continuity comes only from the requested (normally
        active) scope. Governed durable facts remain stored in the real-world
        scope and may still supply stable identity inside a scenario.
        """
        character_id = _require_uuid(character_id)
        requested_scope_id = self._require_truth_scope(
            character_id,
            (
                self.active_truth_scope_id(character_id)
                if truth_scope_id is None else truth_scope_id
            ),
            require_active=False,
        )
        real_scope_id = self.default_truth_scope_id(character_id)
        ordinary = (
            "(c.claim_type<>? AND "
            "(c.truth_scope_id=? OR (c.claim_type=? AND c.truth_scope_id=?)))"
        )
        ordinary_arguments: tuple[object, ...] = (
            HISTORICAL_EVIDENCE, requested_scope_id, DURABLE_CORE_FACT, real_scope_id,
        )
        if include_historical_evidence:
            historical = (
                "(c.claim_type=? AND EXISTS ("
                "SELECT 1 FROM historical_evidence h "
                "WHERE h.character_id=c.character_id AND h.claim_id=c.claim_id "
                # User occurrences retain their explicit retrieval-eligible
                # bit. Assistant records may be generated as bounded callback
                # candidates, but the retriever must still prove an explicit
                # assistant/shared-conversation intent before selecting them.
                "AND (h.retrieval_eligible=1 OR h.speaker_role='assistant') "
                "AND (h.truth_scope_id=? "
                "OR (h.scope_state='unknown_scope' AND ?=1))))"
            )
            return (
                f"({ordinary} OR {historical})",
                (
                    *ordinary_arguments, HISTORICAL_EVIDENCE, requested_scope_id,
                    int(requested_scope_id == real_scope_id),
                ),
            )
        return (
            ordinary,
            ordinary_arguments,
        )

    def _write_truth_scope_id(self, character_id: str, truth_scope_id: str | None) -> str:
        character_id = _require_uuid(character_id)
        return self._require_truth_scope(
            character_id, truth_scope_id or self.active_truth_scope_id(character_id), require_active=True,
        )

    def create_scenario_truth_scope(
        self, character_id: str, label: str, *, evidence_event_id: str,
        evidence_excerpt_start_cp: int, evidence_excerpt_end_cp: int,
    ) -> str:
        """Create an inactive persistent scenario from exact canonical user evidence."""
        try:
            label = normalize_truth_scope_label(label)
        except ValueError as error:
            raise StoreError(str(error)) from error
        character_id, at_us, content = self._active_state_evidence_time(
            character_id, evidence_event_id, "direct_user_statement",
        )
        if not 0 <= evidence_excerpt_start_cp < evidence_excerpt_end_cp <= len(content):
            raise StoreError("truth-scope evidence excerpt range is invalid.")
        with self.transaction():
            count = int(self.connection.execute(
                "SELECT COUNT(*) FROM truth_scopes WHERE character_id=? AND scope_kind='scenario'", (character_id,),
            ).fetchone()[0])
            if count >= MAX_SCENARIO_SCOPES_PER_CHARACTER:
                raise StoreError("truth-scope scenario capacity is reached.")
            scope_id = f"scope-{uuid.uuid5(uuid.NAMESPACE_URL, f'aifren:scenario-scope:{character_id}:{evidence_event_id}:{label}')}"
            existing = self.connection.execute(
                "SELECT truth_scope_id FROM truth_scopes WHERE character_id=? AND truth_scope_id=?", (character_id, scope_id),
            ).fetchone()
            if existing is not None:
                return scope_id
            excerpt_hash = hashlib.sha256(content[evidence_excerpt_start_cp:evidence_excerpt_end_cp].encode("utf-8")).hexdigest()
            self.connection.execute(
                "INSERT INTO truth_scopes VALUES (?, ?, 'scenario', ?, 'inactive', ?, ?)",
                (character_id, scope_id, label, at_us, at_us),
            )
            self.connection.execute(
                "INSERT INTO truth_scope_events(character_id, truth_scope_id, operation, event_id, excerpt_start_cp, excerpt_end_cp, excerpt_hash, created_at_us) VALUES (?, ?, 'created', ?, ?, ?, ?, ?)",
                (character_id, scope_id, str(evidence_event_id), evidence_excerpt_start_cp, evidence_excerpt_end_cp, excerpt_hash, at_us),
            )
        return scope_id

    def activate_truth_scope(
        self, character_id: str, truth_scope_id: str, *, evidence_event_id: str,
        evidence_excerpt_start_cp: int, evidence_excerpt_end_cp: int,
    ) -> None:
        """Switch the one active truth scope with append-only canonical evidence."""
        character_id, at_us, content = self._active_state_evidence_time(
            character_id, evidence_event_id, "direct_user_statement",
        )
        if not 0 <= evidence_excerpt_start_cp < evidence_excerpt_end_cp <= len(content):
            raise StoreError("truth-scope evidence excerpt range is invalid.")
        excerpt_hash = hashlib.sha256(content[evidence_excerpt_start_cp:evidence_excerpt_end_cp].encode("utf-8")).hexdigest()
        with self.transaction():
            self._ensure_default_truth_scope(character_id, created_at_us=at_us)
            truth_scope_id = self._require_truth_scope(character_id, truth_scope_id)
            previous = self.active_truth_scope_id(character_id)
            if previous == truth_scope_id:
                self.connection.execute(
                    "UPDATE truth_scopes SET last_active_at_us=MAX(last_active_at_us, ?), status='active' WHERE character_id=? AND truth_scope_id=?",
                    (at_us, character_id, truth_scope_id),
                )
                return
            self.connection.execute("UPDATE truth_scopes SET status='inactive' WHERE character_id=? AND truth_scope_id=?", (character_id, previous))
            self.connection.execute(
                "INSERT INTO truth_scope_events(character_id, truth_scope_id, operation, event_id, excerpt_start_cp, excerpt_end_cp, excerpt_hash, created_at_us) VALUES (?, ?, 'deactivated', ?, ?, ?, ?, ?)",
                (character_id, previous, str(evidence_event_id), evidence_excerpt_start_cp, evidence_excerpt_end_cp, excerpt_hash, at_us),
            )
            self.connection.execute(
                "UPDATE truth_scopes SET status='active', last_active_at_us=MAX(last_active_at_us, ?) WHERE character_id=? AND truth_scope_id=?",
                (at_us, character_id, truth_scope_id),
            )
            self.connection.execute("UPDATE characters SET active_truth_scope_id=? WHERE character_id=?", (truth_scope_id, character_id))
            self.connection.execute(
                "INSERT INTO truth_scope_events(character_id, truth_scope_id, operation, event_id, excerpt_start_cp, excerpt_end_cp, excerpt_hash, created_at_us) VALUES (?, ?, 'activated', ?, ?, ?, ?, ?)",
                (character_id, truth_scope_id, str(evidence_event_id), evidence_excerpt_start_cp, evidence_excerpt_end_cp, excerpt_hash, at_us),
            )

    def deactivate_to_real_world(self, character_id: str, *, evidence_event_id: str,
                                  evidence_excerpt_start_cp: int, evidence_excerpt_end_cp: int) -> None:
        self.activate_truth_scope(
            character_id, self.default_truth_scope_id(character_id), evidence_event_id=evidence_event_id,
            evidence_excerpt_start_cp=evidence_excerpt_start_cp, evidence_excerpt_end_cp=evidence_excerpt_end_cp,
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
                 recorded_at_us if recorded_at_us is not None else utc_now_us(),
                 occurred_from_us, occurred_to_us,
                 temporal_precision, content_text, json.dumps(payload or {}, sort_keys=True),
                 payload_schema, source_origin, source_reference, content_hash),
            )

    def add_claim(self, character_id: str, claim_id: str, *, claim_type: str, assertion_scope: str, content: str, importance: int = 5, confidence: Optional[float] = None, subject_key: Optional[str] = None, valid_from_us: Optional[int] = None, valid_to_us: Optional[int] = None, temporal_precision: str = "unknown", temporal_expression: Optional[str] = None, provenance_state: str = "complete", curator_name: Optional[str] = "synthetic-fixture", curator_version: Optional[str] = "1", curator_policy_version: Optional[str] = "1", legacy_metadata: Optional[dict] = None, created_at_us: Optional[int] = None, updated_at_us: Optional[int] = None) -> None:
        if claim_type == DURABLE_CORE_FACT:
            raise StoreError("durable_core_fact must be created with add_durable_claim.")
        if claim_type == ACTIVE_STATE:
            raise StoreError("active_state must be created with set_active_state.")
        if claim_type == OPEN_THREAD:
            raise StoreError("open_thread must be created with apply_open_thread_proposal.")
        self._validate_claim_values(content, importance, valid_from_us, valid_to_us)
        with self.transaction():
            truth_scope_id = self.default_truth_scope_id(character_id)
            self._insert_claim(
                character_id, claim_id, claim_type=claim_type, assertion_scope=assertion_scope,
                subject_key=subject_key, content=content, importance=importance, confidence=confidence,
                valid_from_us=valid_from_us, valid_to_us=valid_to_us,
                temporal_precision=temporal_precision, temporal_expression=temporal_expression,
                provenance_state=provenance_state, curator_name=curator_name,
                curator_version=curator_version, curator_policy_version=curator_policy_version,
                legacy_metadata=legacy_metadata, created_at_us=created_at_us, updated_at_us=updated_at_us,
                truth_scope_id=truth_scope_id,
            )

    def add_historical_evidence(
        self,
        character_id: str,
        claim_id: str,
        *,
        event_id: str,
        canonical_index: int,
        canonical_record_id: str,
        speaker_role: str,
        speech_act: str,
        source_class: str,
        scope_state: str,
        truth_scope_id: str | None,
        source_content_sha256: str,
        searchable_text: str,
        recorded_at_us: int,
        source_reference: str,
        retrieval_eligible: bool,
        projection_version: int = 1,
    ) -> bool:
        """Append one source-owned historical occurrence projection.

        The row says only that the canonical message occurred. It cannot be
        created through ``add_durable_claim`` and an unknown scope remains
        NULL rather than being coerced into the real-world truth scope.
        """
        character_id = _require_uuid(character_id)
        if isinstance(canonical_index, bool) or canonical_index < 0:
            raise StoreError("historical canonical index is invalid")
        if speaker_role not in {"user", "assistant"}:
            raise StoreError("historical speaker role is invalid")
        if speech_act not in {"assertion", "question", "other"}:
            raise StoreError("historical speech act is invalid")
        if scope_state not in {"real_world", "scenario", "unknown_scope"}:
            raise StoreError("historical scope state is invalid")
        if scope_state == "unknown_scope":
            if truth_scope_id is not None:
                raise StoreError("unknown historical scope cannot name a truth scope")
            stored_scope_id = None
        else:
            if truth_scope_id is None:
                raise StoreError("known historical scope requires an identity")
            stored_scope_id = self._require_truth_scope(
                character_id, truth_scope_id, require_active=False,
            )
            scope_kind = self.connection.execute(
                "SELECT scope_kind FROM truth_scopes WHERE character_id=? AND truth_scope_id=?",
                (character_id, stored_scope_id),
            ).fetchone()
            if scope_kind is None or str(scope_kind[0]) != scope_state:
                raise StoreError("historical scope identity and kind disagree")
        if not re.fullmatch(r"[0-9a-f]{64}", str(source_content_sha256)):
            raise StoreError("historical source content hash is invalid")
        if not str(canonical_record_id).strip() or len(str(canonical_record_id)) > 160:
            raise StoreError("historical canonical record identity is invalid")
        if not str(source_class).strip() or len(str(source_class)) > 64:
            raise StoreError("historical source class is invalid")
        if not str(source_reference).strip() or len(str(source_reference)) > 500:
            raise StoreError("historical source reference is invalid")
        if not str(searchable_text).strip() or len(str(searchable_text)) > 1_400:
            raise StoreError("historical searchable projection is invalid")
        if isinstance(recorded_at_us, bool) or not isinstance(recorded_at_us, int):
            raise StoreError("historical source timestamp is invalid")
        if isinstance(projection_version, bool) or projection_version < 1:
            raise StoreError("historical projection version is invalid")

        existing = self.connection.execute(
            """SELECT h.*, c.content, e.source_reference, e.recorded_at_us
                 FROM historical_evidence h
                 JOIN claims c ON c.character_id=h.character_id AND c.claim_id=h.claim_id
                 JOIN events e ON e.character_id=h.character_id AND e.event_id=h.event_id
                WHERE h.character_id=? AND h.canonical_record_id=?""",
            (character_id, str(canonical_record_id)),
        ).fetchone()
        if existing is not None:
            expected = (
                str(claim_id), str(event_id), canonical_index, speaker_role, speech_act,
                source_class, scope_state, stored_scope_id, source_content_sha256,
                int(projection_version), int(bool(retrieval_eligible)), str(searchable_text),
                str(source_reference), recorded_at_us,
            )
            actual = (
                str(existing["claim_id"]), str(existing["event_id"]),
                int(existing["canonical_index"]), str(existing["speaker_role"]),
                str(existing["speech_act"]), str(existing["source_class"]),
                str(existing["scope_state"]), existing["truth_scope_id"],
                str(existing["source_content_sha256"]), int(existing["projection_version"]),
                int(existing["retrieval_eligible"]), str(existing["content"]),
                str(existing["source_reference"]), int(existing["recorded_at_us"]),
            )
            if actual != expected:
                raise StoreError("historical canonical identity conflicts with indexed evidence")
            return False

        with self.transaction():
            sequence = int(self.connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE character_id=?",
                (character_id,),
            ).fetchone()[0])
            self.add_event(
                character_id, event_id, sequence,
                event_type="canonical_historical_message",
                actor_kind=speaker_role, recorded_at_us=recorded_at_us,
                temporal_precision="instant", content_text=str(searchable_text),
                source_origin="canonical_conversation",
                source_reference=str(source_reference),
                payload={
                    "schema": "aifren.memory_v2.historical_evidence",
                    "version": int(projection_version),
                    "canonical_index": canonical_index,
                    "canonical_record_id": str(canonical_record_id),
                    "speaker_role": speaker_role,
                    "speech_act": speech_act,
                    "source_class": source_class,
                    "scope_state": scope_state,
                    "source_content_sha256": source_content_sha256,
                },
            )
            self._insert_claim(
                character_id, claim_id,
                claim_type=HISTORICAL_EVIDENCE,
                assertion_scope=HISTORICAL_EVIDENCE_ASSERTION_SCOPE,
                subject_key=f"canonical_record:{canonical_record_id}",
                content=str(searchable_text), importance=3, confidence=1.0,
                valid_from_us=recorded_at_us, valid_to_us=None,
                temporal_precision="instant", temporal_expression=None,
                provenance_state="complete", curator_name="canonical_historical_evidence",
                curator_version=str(projection_version),
                curator_policy_version="source_occurrence_v1",
                legacy_metadata=None, created_at_us=recorded_at_us,
                updated_at_us=recorded_at_us, truth_scope_id=stored_scope_id,
                allow_unknown_scope=True,
            )
            self.connection.execute(
                """INSERT INTO claim_evidence(
                       character_id, claim_id, event_id, evidence_role,
                       excerpt_start_cp, excerpt_end_cp, excerpt_hash,
                       evidence_strength, curator_confidence, created_at_us)
                   VALUES (?, ?, ?, 'canonical_historical_source', NULL, NULL, NULL, 1.0, 1.0, ?)""",
                (character_id, str(claim_id), str(event_id), recorded_at_us),
            )
            self.connection.execute(
                """INSERT INTO historical_evidence(
                       character_id, claim_id, event_id, canonical_index,
                       canonical_record_id, speaker_role, speech_act, source_class,
                       scope_state, truth_scope_id, source_content_sha256,
                       projection_version, retrieval_eligible)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    character_id, str(claim_id), str(event_id), canonical_index,
                    str(canonical_record_id), speaker_role, speech_act, source_class,
                    scope_state, stored_scope_id, source_content_sha256,
                    int(projection_version), int(bool(retrieval_eligible)),
                ),
            )
        return True

    @staticmethod
    def _validate_claim_values(content, importance, valid_from_us, valid_to_us) -> None:
        if not str(content).strip():
            raise StoreError("claim content is required.")
        if isinstance(importance, bool) or not isinstance(importance, int) or not 1 <= importance <= 10:
            raise StoreError("claim importance must be an integer from 1 to 10.")
        if valid_from_us is not None and valid_to_us is not None and valid_to_us < valid_from_us:
            raise StoreError("claim valid range is invalid.")

    def _insert_claim(self, character_id: str, claim_id: str, *, claim_type: str, assertion_scope: str,
                      subject_key: Optional[str], content: str, importance: int, confidence: Optional[float],
                      valid_from_us: Optional[int], valid_to_us: Optional[int], temporal_precision: str,
                      temporal_expression: Optional[str], provenance_state: str, curator_name: Optional[str],
                      curator_version: Optional[str], curator_policy_version: Optional[str],
                      legacy_metadata: Optional[dict], created_at_us: Optional[int], updated_at_us: Optional[int],
                      truth_scope_id: str | None = None,
                      allow_unknown_scope: bool = False) -> None:
        if truth_scope_id is None and allow_unknown_scope:
            validated_scope_id = None
        else:
            validated_scope_id = self._require_truth_scope(
                character_id, truth_scope_id or self.default_truth_scope_id(character_id),
            )
        self.connection.execute(
            """INSERT INTO claims(character_id, claim_id, claim_type, assertion_scope, subject_key, content,
                   importance, confidence, valid_from_us, valid_to_us, temporal_precision, temporal_expression,
                   provenance_state, curator_name, curator_version, curator_policy_version, created_at_us,
                   legacy_metadata_json, updated_at_us, truth_scope_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_require_uuid(character_id), str(claim_id), claim_type, assertion_scope, subject_key,
             str(content), importance, confidence, valid_from_us, valid_to_us,
             temporal_precision, temporal_expression, provenance_state, curator_name,
             curator_version, curator_policy_version,
             created_at_us if created_at_us is not None else utc_now_us(),
             json.dumps(legacy_metadata, ensure_ascii=False, sort_keys=True) if legacy_metadata is not None else None,
             updated_at_us if updated_at_us is not None else (
                 created_at_us if created_at_us is not None else utc_now_us()
             ), validated_scope_id),
        )

    def add_durable_claim(self, character_id: str, claim_id: str, *, subject_key: str, content: str,
                          evidence_event_id: str, evidence_role: str, importance: int = 5,
                          confidence: Optional[float] = None, valid_from_us: Optional[int] = None,
                          valid_to_us: Optional[int] = None, temporal_precision: str = "unknown",
                          temporal_expression: Optional[str] = None, curator_name: Optional[str] = None,
                          curator_version: Optional[str] = None, curator_policy_version: Optional[str] = None,
                          created_at_us: Optional[int] = None, updated_at_us: Optional[int] = None,
                          evidence_excerpt_start_cp: Optional[int] = None,
                          evidence_excerpt_end_cp: Optional[int] = None,
                          supersedes_claim_id: Optional[str] = None) -> None:
        """Append a verified durable fact and, optionally, supersede its predecessor.

        This is deliberately the only supported writer for ``durable_core_fact``.
        It atomically binds the claim to active, same-character user evidence.
        """
        try:
            subject_key = validate_durable_subject_key(subject_key)
        except ValueError as error:
            raise StoreError(str(error)) from error
        if evidence_role not in DURABLE_USER_EVIDENCE_ROLES:
            raise StoreError("durable evidence role must be a direct user statement or user confirmation.")
        self._validate_claim_values(content, importance, valid_from_us, valid_to_us)
        character_id = _require_uuid(character_id)
        event = self.connection.execute(
            """SELECT event_id, recorded_at_us, content_text FROM events WHERE character_id=? AND event_id=?
               AND actor_kind='user' AND redaction_state='active' AND content_text IS NOT NULL""",
            (character_id, str(evidence_event_id)),
        ).fetchone()
        if event is None:
            raise StoreError("durable claim requires active same-character user-authored evidence.")
        if self.active_truth_scope_id(character_id) != self.default_truth_scope_id(character_id):
            raise StoreError("durable facts may only be written in the active real-world truth scope.")
        evidence_content = event["content_text"]
        if evidence_excerpt_start_cp is not None or evidence_excerpt_end_cp is not None:
            if (evidence_excerpt_start_cp is None or evidence_excerpt_end_cp is None
                    or not 0 <= evidence_excerpt_start_cp <= evidence_excerpt_end_cp <= len(evidence_content)):
                raise StoreError("durable evidence excerpt range is invalid.")
            excerpt_hash = hashlib.sha256(
                evidence_content[evidence_excerpt_start_cp:evidence_excerpt_end_cp].encode("utf-8")
            ).hexdigest()
        else:
            excerpt_hash = None
        with self.transaction():
            truth_scope_id = self.default_truth_scope_id(character_id)
            source_at_us = int(event["recorded_at_us"])
            watermark = self.durable_subject_watermark_us(character_id, subject_key)
            if watermark is not None and source_at_us < watermark:
                raise StoreError("durable evidence predates the subject lifecycle watermark.")
            if supersedes_claim_id is None and is_singleton_durable_key(subject_key):
                existing = self.connection.execute(
                    """SELECT 1 FROM claims c WHERE c.character_id=? AND c.claim_type=? AND c.subject_key=?
                       AND COALESCE((SELECT status FROM claim_status_events s
                         WHERE s.character_id=c.character_id AND s.claim_id=c.claim_id
                         ORDER BY s.status_event_id DESC LIMIT 1), 'active') NOT IN ('superseded', 'expired', 'cancelled', 'retracted', 'archived', 'hidden', 'redacted')
                       AND c.valid_to_us IS NULL
                       LIMIT 1""",
                    (character_id, DURABLE_CORE_FACT, subject_key),
                ).fetchone()
                if existing is not None:
                    raise StoreError("current durable singleton already exists; create a correction with supersedes_claim_id.")
            if supersedes_claim_id is not None:
                predecessor = self.connection.execute(
                    """SELECT c.claim_type, c.subject_key, c.valid_to_us,
                              c.provenance_state, c.truth_scope_id,
                              COALESCE((SELECT status FROM claim_status_events s
                                WHERE s.character_id=c.character_id AND s.claim_id=c.claim_id
                                ORDER BY s.status_event_id DESC LIMIT 1), 'active') AS effective_status
                         FROM claims c WHERE c.character_id=? AND c.claim_id=?""",
                    (character_id, str(supersedes_claim_id)),
                ).fetchone()
                if (predecessor is None
                        or predecessor["claim_type"] != DURABLE_CORE_FACT
                        or predecessor["subject_key"] != subject_key
                        or predecessor["valid_to_us"] is not None
                        or predecessor["provenance_state"] != "complete"
                        or predecessor["truth_scope_id"] != truth_scope_id
                        or predecessor["effective_status"] in CURRENT_EXCLUDED_STATUSES):
                    raise StoreError("durable correction must supersede the same-character claim with the same subject_key.")
            self._insert_claim(
                character_id, claim_id, claim_type=DURABLE_CORE_FACT, assertion_scope=DURABLE_ASSERTION_SCOPE,
                subject_key=subject_key, content=content, importance=importance, confidence=confidence,
                valid_from_us=valid_from_us, valid_to_us=valid_to_us,
                temporal_precision=temporal_precision, temporal_expression=temporal_expression,
                provenance_state="complete", curator_name=curator_name, curator_version=curator_version,
                curator_policy_version=curator_policy_version, legacy_metadata=None,
                created_at_us=created_at_us, updated_at_us=updated_at_us,
                truth_scope_id=truth_scope_id,
            )
            self.connection.execute(
                "INSERT INTO claim_evidence VALUES (?, ?, ?, ?, ?, ?, ?, 1.0, NULL, ?)",
                (character_id, str(claim_id), str(evidence_event_id), evidence_role,
                 evidence_excerpt_start_cp, evidence_excerpt_end_cp, excerpt_hash,
                 int(event["recorded_at_us"])),
            )
            if supersedes_claim_id is not None:
                now = source_at_us
                self.connection.execute(
                    "INSERT INTO claim_relations(character_id, from_claim_id, to_claim_id, relation_type, created_at_us) VALUES (?, ?, ?, 'supersedes', ?)",
                    (character_id, str(claim_id), str(supersedes_claim_id), now),
                )
                self.connection.execute(
                    "INSERT INTO claim_status_events(character_id, claim_id, status, reason, source_event_id, actor_kind, created_at_us) VALUES (?, ?, 'superseded', 'durable_correction', ?, 'user', ?)",
                    (character_id, str(supersedes_claim_id), str(evidence_event_id), now),
                )
                # Status history remains append-only, while the closed
                # validity interval makes time-point historical lookup select
                # the predecessor only before this correction.
                self.connection.execute(
                    """UPDATE claims SET valid_to_us=?
                         WHERE character_id=? AND claim_id=?
                           AND (valid_to_us IS NULL OR valid_to_us > ?)""",
                    (now, character_id, str(supersedes_claim_id), now),
                )

    def durable_subject_watermark_us(
        self,
        character_id: str,
        subject_key: str,
    ) -> int | None:
        """Return the latest source-owned lifecycle time for one governed slot."""
        try:
            subject_key = validate_durable_subject_key(subject_key)
        except ValueError as error:
            raise StoreError(str(error)) from error
        character_id = _require_uuid(character_id)
        scope_id = self.default_truth_scope_id(character_id)
        row = self.connection.execute(
            """WITH subject_claims AS (
                   SELECT claim_id, valid_from_us, valid_to_us, created_at_us
                     FROM claims WHERE character_id=? AND claim_type=?
                       AND assertion_scope=? AND subject_key=?
                       AND truth_scope_id=? AND provenance_state='complete'
                 ), lifecycle_times(authoritative_at_us) AS (
                   SELECT COALESCE(valid_from_us, created_at_us) FROM subject_claims
                   UNION ALL
                   SELECT valid_to_us FROM subject_claims WHERE valid_to_us IS NOT NULL
                   UNION ALL
                   SELECT e.recorded_at_us FROM claim_evidence ce
                     JOIN subject_claims c ON c.claim_id=ce.claim_id
                     JOIN events e ON e.character_id=? AND e.event_id=ce.event_id
                    WHERE ce.character_id=?
                   UNION ALL
                   SELECT s.created_at_us FROM claim_status_events s
                     JOIN subject_claims c ON c.claim_id=s.claim_id
                    WHERE s.character_id=?
                 )
                 SELECT MAX(authoritative_at_us) FROM lifecycle_times""",
            (
                character_id, DURABLE_CORE_FACT, DURABLE_ASSERTION_SCOPE,
                subject_key, scope_id, character_id, character_id, character_id,
            ),
        ).fetchone()
        return int(row[0]) if row is not None and row[0] is not None else None

    def retire_durable_claim(
        self,
        character_id: str,
        claim_id: str,
        *,
        subject_key: str,
        expected_content: str,
        evidence_event_id: str,
    ) -> None:
        """Close one exact governed value from direct canonical user evidence."""
        try:
            subject_key = validate_durable_subject_key(subject_key)
        except ValueError as error:
            raise StoreError(str(error)) from error
        character_id = _require_uuid(character_id)
        event = self.connection.execute(
            """SELECT recorded_at_us FROM events WHERE character_id=? AND event_id=?
                 AND actor_kind='user' AND redaction_state='active'
                 AND content_text IS NOT NULL""",
            (character_id, str(evidence_event_id)),
        ).fetchone()
        if event is None:
            raise StoreError("durable retirement requires active same-character user evidence.")
        if self.active_truth_scope_id(character_id) != self.default_truth_scope_id(character_id):
            raise StoreError("durable facts may only be retired in the active real-world truth scope.")
        at_us = int(event["recorded_at_us"])
        claim = self.connection.execute(
            """SELECT c.valid_from_us, c.valid_to_us,
                      COALESCE((SELECT status FROM claim_status_events s
                        WHERE s.character_id=c.character_id AND s.claim_id=c.claim_id
                        ORDER BY s.status_event_id DESC LIMIT 1), 'active') AS effective_status
                 FROM claims c WHERE c.character_id=? AND c.claim_id=?
                   AND c.claim_type=? AND c.assertion_scope=?
                   AND c.subject_key=? AND c.content=?
                   AND c.truth_scope_id=? AND c.provenance_state='complete'""",
            (
                character_id, str(claim_id), DURABLE_CORE_FACT,
                DURABLE_ASSERTION_SCOPE, subject_key, str(expected_content),
                self.default_truth_scope_id(character_id),
            ),
        ).fetchone()
        if (claim is None or claim["valid_to_us"] is not None
                or claim["effective_status"] in CURRENT_EXCLUDED_STATUSES):
            raise StoreError("durable retirement requires one exact current governed fact.")
        watermark = self.durable_subject_watermark_us(character_id, subject_key)
        if watermark is not None and at_us < watermark:
            raise StoreError("durable retirement predates the subject lifecycle watermark.")
        with self.transaction():
            self.connection.execute(
                """INSERT INTO claim_status_events(
                       character_id, claim_id, status, reason, source_event_id,
                       actor_kind, created_at_us)
                     VALUES (?, ?, 'expired', 'durable_user_retirement', ?, 'user', ?)""",
                (character_id, str(claim_id), str(evidence_event_id), at_us),
            )
            updated = self.connection.execute(
                """UPDATE claims SET valid_to_us=?,
                       updated_at_us=MAX(COALESCE(updated_at_us, ?), ?)
                     WHERE character_id=? AND claim_id=? AND valid_to_us IS NULL""",
                (at_us, at_us, at_us, character_id, str(claim_id)),
            ).rowcount
            if updated != 1:
                raise StoreError("durable retirement raced with another lifecycle update.")

    def add_legacy_favorite_color_predecessor(
        self,
        character_id: str,
        claim_id: str,
        *,
        content: str,
        legacy_evidence_event_id: str,
        valid_from_us: int,
    ) -> None:
        """Bootstrap only a governed favorite-color predecessor from V1.

        The replacement still requires separately persisted canonical user
        correction evidence through ``add_durable_claim``. No other V1 fact
        type can enter the governed durable lane through this method.
        """
        character_id = _require_uuid(character_id)
        self._validate_claim_values(content, 5, valid_from_us, None)
        if not re.fullmatch(
            r"The user's favorite color is [A-Za-z][A-Za-z -]{0,31}\.", content,
        ):
            raise StoreError("favorite-color bridge content is outside the governed shape.")
        event = self.connection.execute(
            """SELECT event_id FROM events WHERE character_id=? AND event_id=?
                 AND event_type='legacy_memory_record' AND actor_kind='system'
                 AND source_origin='legacy_v1_import' AND redaction_state='active'""",
            (character_id, str(legacy_evidence_event_id)),
        ).fetchone()
        if event is None:
            raise StoreError("favorite-color bridge requires same-character imported V1 evidence.")
        current = self.connection.execute(
            """SELECT 1 FROM claims c WHERE c.character_id=? AND c.claim_type=?
                 AND c.subject_key='preference.color'
                 AND COALESCE((SELECT status FROM claim_status_events s
                   WHERE s.character_id=c.character_id AND s.claim_id=c.claim_id
                   ORDER BY s.status_event_id DESC LIMIT 1), 'active')
                   NOT IN ('superseded','expired','cancelled','retracted','archived','hidden','redacted')
                 LIMIT 1""",
            (character_id, DURABLE_CORE_FACT),
        ).fetchone()
        if current is not None:
            raise StoreError("favorite-color bridge cannot replace a governed current value.")
        with self.transaction():
            self._insert_claim(
                character_id, claim_id, claim_type=DURABLE_CORE_FACT,
                assertion_scope=DURABLE_ASSERTION_SCOPE, subject_key="preference.color",
                content=content, importance=5, confidence=None,
                valid_from_us=valid_from_us, valid_to_us=None,
                temporal_precision="legacy_unknown", temporal_expression=None,
                provenance_state="complete", curator_name="favorite_color_v1_bridge",
                curator_version="1", curator_policy_version="favorite_color_v1_bridge_v1",
                legacy_metadata=None, created_at_us=valid_from_us,
                updated_at_us=valid_from_us,
                truth_scope_id=self.default_truth_scope_id(character_id),
            )
            self.connection.execute(
                "INSERT INTO claim_evidence VALUES (?, ?, ?, ?, NULL, NULL, NULL, 1.0, NULL, ?)",
                (character_id, str(claim_id), str(legacy_evidence_event_id),
                 DURABLE_LEGACY_BRIDGE_EVIDENCE_ROLE, utc_now_us()),
            )

    def reconfirm_durable_claim(
        self,
        character_id: str,
        claim_id: str,
        *,
        evidence_event_id: str,
        evidence_excerpt_start_cp: int,
        evidence_excerpt_end_cp: int,
    ) -> None:
        """Attach bounded user confirmation to one still-current durable claim."""
        character_id = _require_uuid(character_id)
        if self.active_truth_scope_id(character_id) != self.default_truth_scope_id(character_id):
            raise StoreError("durable facts may only be reconfirmed in the active real-world truth scope.")
        claim = self.connection.execute(
            """SELECT c.subject_key, c.valid_from_us, c.valid_to_us, c.truth_scope_id,
                      COALESCE((SELECT status FROM claim_status_events s
                        WHERE s.character_id=c.character_id AND s.claim_id=c.claim_id
                        ORDER BY s.status_event_id DESC LIMIT 1), 'active') AS effective_status
                 FROM claims c WHERE c.character_id=? AND c.claim_id=? AND c.claim_type=?
                   AND c.assertion_scope=? AND c.provenance_state='complete'""",
            (character_id, str(claim_id), DURABLE_CORE_FACT, DURABLE_ASSERTION_SCOPE),
        ).fetchone()
        if (claim is None or claim["truth_scope_id"] != self.default_truth_scope_id(character_id)
                or claim["effective_status"] in CURRENT_EXCLUDED_STATUSES or claim["valid_to_us"] is not None):
            raise StoreError("durable confirmation requires one current governed claim.")
        event = self.connection.execute(
            """SELECT recorded_at_us, content_text FROM events WHERE character_id=? AND event_id=?
                 AND actor_kind='user' AND redaction_state='active' AND content_text IS NOT NULL""",
            (character_id, str(evidence_event_id)),
        ).fetchone()
        if event is None:
            raise StoreError("durable confirmation requires active same-character user evidence.")
        recorded_at_us = int(event["recorded_at_us"])
        watermark = self.durable_subject_watermark_us(
            character_id, str(claim["subject_key"]),
        )
        if watermark is not None and recorded_at_us < watermark:
            raise StoreError("durable confirmation predates the subject lifecycle watermark.")
        content = event["content_text"]
        start, end = evidence_excerpt_start_cp, evidence_excerpt_end_cp
        if (isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int)
                or not isinstance(end, int) or not 0 <= start <= end <= len(content)):
            raise StoreError("durable evidence excerpt range is invalid.")
        excerpt_hash = hashlib.sha256(content[start:end].encode("utf-8")).hexdigest()
        with self.transaction():
            self.connection.execute(
                """INSERT OR IGNORE INTO claim_evidence(character_id, claim_id, event_id, evidence_role,
                   excerpt_start_cp, excerpt_end_cp, excerpt_hash, evidence_strength,
                   curator_confidence, created_at_us)
                   VALUES (?, ?, ?, 'user_confirmation', ?, ?, ?, 1.0, NULL, ?)""",
                (character_id, str(claim_id), str(evidence_event_id), start, end, excerpt_hash, recorded_at_us),
            )
            self.connection.execute(
                """UPDATE claims SET updated_at_us=MAX(COALESCE(updated_at_us, 0), ?)
                     WHERE character_id=? AND claim_id=?""",
                (recorded_at_us, character_id, str(claim_id)),
            )

    def _active_state_evidence_time(
        self,
        character_id: str,
        evidence_event_id: str,
        evidence_role: str,
    ) -> tuple[str, int, str]:
        if evidence_role not in ACTIVE_STATE_EVIDENCE_ROLES:
            raise StoreError("active state evidence role is not governed.")
        character_id = _require_uuid(character_id)
        actor_kind = "assistant" if evidence_role == "governed_companion_action" else "user"
        event = self.connection.execute(
            """SELECT event_id, recorded_at_us, content_text FROM events WHERE character_id=? AND event_id=?
               AND actor_kind=? AND redaction_state='active' AND content_text IS NOT NULL""",
            (character_id, str(evidence_event_id), actor_kind),
        ).fetchone()
        if event is None:
            raise StoreError("active state requires active same-character governed evidence.")
        return character_id, int(event["recorded_at_us"]), str(event["content_text"])

    def _current_active_state_rows(self, character_id: str, subject_key: str, at_us: int, truth_scope_id: str):
        return self.connection.execute(
            """SELECT c.claim_id, c.content, c.valid_from_us FROM claims c
                 WHERE c.character_id=? AND c.truth_scope_id=? AND c.claim_type=? AND c.subject_key=?
                   AND c.assertion_scope=? AND c.provenance_state='complete'
                   AND (c.valid_from_us IS NULL OR c.valid_from_us <= ?)
                   AND (c.valid_to_us IS NULL OR c.valid_to_us > ?)
                   AND COALESCE((SELECT status FROM claim_status_events s
                         WHERE s.character_id=c.character_id AND s.claim_id=c.claim_id
                           AND s.created_at_us <= ?
                         ORDER BY s.status_event_id DESC LIMIT 1), 'active')
                       NOT IN ('superseded', 'expired', 'cancelled', 'retracted', 'archived', 'hidden', 'redacted')
                   AND EXISTS (
                       SELECT 1 FROM claim_evidence ce JOIN events e
                         ON e.character_id=ce.character_id AND e.event_id=ce.event_id
                        WHERE ce.character_id=c.character_id AND ce.claim_id=c.claim_id
                          AND ((ce.evidence_role IN ('direct_user_statement', 'user_confirmation', 'immediate_user_event_consequence')
                                AND e.actor_kind='user')
                               OR (ce.evidence_role='governed_companion_action' AND e.actor_kind='assistant'))
                          AND e.redaction_state='active' AND e.content_text IS NOT NULL
                   )
                 ORDER BY c.created_at_us DESC, c.claim_id
                 LIMIT 2""",
            (character_id, truth_scope_id, ACTIVE_STATE, subject_key, ACTIVE_STATE_ASSERTION_SCOPE, at_us, at_us, at_us),
        ).fetchall()

    def set_active_state(
        self,
        character_id: str,
        state_id: str,
        *,
        subject_key: str,
        value: str,
        evidence_event_id: str,
        evidence_role: str = "direct_user_statement",
        evidence_excerpt_start_cp: Optional[int] = None,
        evidence_excerpt_end_cp: Optional[int] = None,
        truth_scope_id: str | None = None,
        replacement_reason: str = "active_state_replaced",
    ) -> str:
        """Set one exact active-state slot, atomically closing its predecessor."""
        try:
            subject_key = validate_active_state_subject_key(subject_key)
            value = validate_active_state_value(subject_key, value)
        except ValueError as error:
            raise StoreError(str(error)) from error
        character_id, effective_at_us, evidence_content = self._active_state_evidence_time(
            character_id, evidence_event_id, evidence_role,
        )
        if evidence_excerpt_start_cp is not None or evidence_excerpt_end_cp is not None:
            if (evidence_excerpt_start_cp is None or evidence_excerpt_end_cp is None
                    or not 0 <= evidence_excerpt_start_cp <= evidence_excerpt_end_cp <= len(evidence_content)):
                raise StoreError("active-state evidence excerpt range is invalid.")
            excerpt_hash = hashlib.sha256(
                evidence_content[evidence_excerpt_start_cp:evidence_excerpt_end_cp].encode("utf-8")
            ).hexdigest()
        else:
            excerpt_hash = None
        with self.transaction():
            truth_scope_id = self._write_truth_scope_id(character_id, truth_scope_id)
            target_kind, target_ref, _ = parse_active_state_subject_key(subject_key)
            if target_kind == "scene" and not self._scene_subject_is_active(character_id, str(target_ref), effective_at_us, truth_scope_id):
                raise StoreError("active-state scene subject is not current for this character.")
            existing = self._current_active_state_rows(character_id, subject_key, effective_at_us, truth_scope_id)
            if len(existing) > 1:
                raise StoreError("multiple current active-state values exist for this singleton slot.")
            predecessor = existing[0] if existing else None
            if predecessor is not None and predecessor["valid_from_us"] is not None and predecessor["valid_from_us"] > effective_at_us:
                raise StoreError("active-state evidence predates the current slot value.")
            if predecessor is not None and predecessor["content"] == value:
                # A same-value explicit assertion reconfirms the existing
                # state.  It adds source evidence without resetting the
                # interval start or manufacturing a replacement claim.
                self.connection.execute(
                    "INSERT OR IGNORE INTO claim_evidence VALUES (?, ?, ?, ?, ?, ?, ?, 1.0, NULL, ?)",
                    (character_id, str(predecessor["claim_id"]), str(evidence_event_id), evidence_role,
                     evidence_excerpt_start_cp, evidence_excerpt_end_cp, excerpt_hash, effective_at_us),
                )
                self.connection.execute(
                    "UPDATE claims SET updated_at_us=MAX(COALESCE(updated_at_us, 0), ?) WHERE character_id=? AND claim_id=?",
                    (effective_at_us, character_id, str(predecessor["claim_id"])),
                )
                return str(predecessor["claim_id"])
            self._insert_claim(
                character_id, state_id, claim_type=ACTIVE_STATE,
                assertion_scope=ACTIVE_STATE_ASSERTION_SCOPE, subject_key=subject_key,
                content=value, importance=5, confidence=None, valid_from_us=effective_at_us,
                valid_to_us=None, temporal_precision="instant", temporal_expression=None,
                provenance_state="complete", curator_name="active_state_api", curator_version="1",
                curator_policy_version=(
                    "immediate_consequence_v1" if evidence_role == "immediate_user_event_consequence"
                    else "governed_companion_action_v1" if evidence_role == "governed_companion_action"
                    else "explicit_v1"
                ),
                legacy_metadata=None,
                created_at_us=effective_at_us, updated_at_us=effective_at_us,
                truth_scope_id=truth_scope_id,
            )
            self.connection.execute(
                "INSERT INTO claim_evidence VALUES (?, ?, ?, ?, ?, ?, ?, 1.0, NULL, ?)",
                (character_id, str(state_id), str(evidence_event_id), evidence_role,
                 evidence_excerpt_start_cp, evidence_excerpt_end_cp, excerpt_hash, effective_at_us),
            )
            if predecessor is not None:
                self.connection.execute(
                    "INSERT INTO claim_relations(character_id, from_claim_id, to_claim_id, relation_type, created_at_us) VALUES (?, ?, ?, 'supersedes', ?)",
                    (character_id, str(state_id), predecessor["claim_id"], effective_at_us),
                )
                self.connection.execute(
                    "INSERT INTO claim_status_events(character_id, claim_id, status, reason, source_event_id, actor_kind, created_at_us) VALUES (?, ?, 'superseded', ?, ?, ?, ?)",
                    (character_id, predecessor["claim_id"], str(replacement_reason)[:64],
                     str(evidence_event_id),
                     "assistant" if evidence_role == "governed_companion_action" else "user",
                     effective_at_us),
                )
                self.connection.execute(
                    """UPDATE claims SET valid_to_us=? WHERE character_id=? AND claim_id=?
                         AND (valid_to_us IS NULL OR valid_to_us > ?)""",
                    (effective_at_us, character_id, predecessor["claim_id"], effective_at_us),
                )
        return str(state_id)

    def _scene_subject_is_active(self, character_id: str, scene_subject_id: str, at_us: int, truth_scope_id: str) -> bool:
        return self.connection.execute(
            """SELECT 1 FROM active_scene_subjects WHERE character_id=? AND truth_scope_id=? AND scene_subject_id=?
                 AND introduced_at_us <= ? AND (retired_at_us IS NULL OR retired_at_us > ?)""",
            (character_id, truth_scope_id, scene_subject_id, at_us, at_us),
        ).fetchone() is not None

    def _scene_subject_count(self, character_id: str, at_us: int, truth_scope_id: str) -> int:
        return int(self.connection.execute(
            """SELECT COUNT(*) FROM active_scene_subjects WHERE character_id=? AND truth_scope_id=? AND introduced_at_us <= ?
                 AND (retired_at_us IS NULL OR retired_at_us > ?)""",
            (character_id, truth_scope_id, at_us, at_us),
        ).fetchone()[0])

    def _current_scene_attribute_rows(self, character_id: str, scene_subject_id: str, at_us: int, truth_scope_id: str):
        prefix = f"active.scene.{scene_subject_id}."
        return self.connection.execute(
            """SELECT c.claim_id FROM claims c WHERE c.character_id=? AND c.truth_scope_id=? AND c.claim_type=?
                 AND c.subject_key >= ? AND c.subject_key < ? AND c.assertion_scope=?
                 AND c.provenance_state='complete' AND (c.valid_from_us IS NULL OR c.valid_from_us <= ?)
                 AND (c.valid_to_us IS NULL OR c.valid_to_us > ?)
                 AND COALESCE((SELECT status FROM claim_status_events s
                       WHERE s.character_id=c.character_id AND s.claim_id=c.claim_id
                         AND s.created_at_us <= ? ORDER BY s.status_event_id DESC LIMIT 1), 'active')
                     NOT IN ('superseded', 'expired', 'cancelled', 'retracted', 'archived', 'hidden', 'redacted')
                 ORDER BY c.subject_key, c.created_at_us DESC, c.claim_id LIMIT ?""",
            (character_id, truth_scope_id, ACTIVE_STATE, prefix, prefix + "\uffff", ACTIVE_STATE_ASSERTION_SCOPE,
             at_us, at_us, at_us, MAX_ACTIVE_SCENE_ATTRIBUTES_PER_SUBJECT + 1),
        ).fetchall()

    def _introduce_scene_subject(
        self,
        character_id: str,
        introduction: ActiveSceneSubjectIntroduction,
        *,
        evidence_event_id: str,
        effective_at_us: int,
        evidence_content: str,
        truth_scope_id: str,
    ) -> str:
        if self._scene_subject_count(character_id, effective_at_us, truth_scope_id) >= MAX_ACTIVE_SCENE_SUBJECTS:
            raise StoreError("active current-scene subject capacity is reached.")
        subject_uuid = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"aifren:scene-subject:{character_id}:{evidence_event_id}:{introduction.reference}",
        )
        scene_subject_id = f"scene-{subject_uuid}"
        if self.connection.execute(
            "SELECT 1 FROM active_scene_subjects WHERE character_id=? AND truth_scope_id=? AND scene_subject_id=?",
            (character_id, truth_scope_id, scene_subject_id),
        ).fetchone() is not None:
            raise StoreError("active-scene subject introduction already exists for this canonical event.")
        start, end = introduction.excerpt_start_cp, introduction.excerpt_end_cp
        if end > len(evidence_content):
            raise StoreError("active-scene subject introduction source span exceeds canonical evidence.")
        excerpt_hash = hashlib.sha256(evidence_content[start:end].encode("utf-8")).hexdigest()
        self.connection.execute(
            """INSERT INTO active_scene_subjects(character_id, scene_subject_id, introduced_at_us,
                   retired_at_us, introduced_event_id, introduced_excerpt_start_cp,
                   introduced_excerpt_end_cp, introduced_excerpt_hash, retired_event_id,
                   retired_excerpt_start_cp, retired_excerpt_end_cp, retired_excerpt_hash, truth_scope_id,
                   last_referenced_at_us, identity_strength)
                   VALUES (?, ?, ?, NULL, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?, ?)""",
            (character_id, scene_subject_id, effective_at_us, str(evidence_event_id), start, end, excerpt_hash,
             truth_scope_id, effective_at_us, introduction.identity_strength),
        )
        self.connection.execute(
            """INSERT INTO active_scene_subject_lifecycle_events(
                   character_id, scene_subject_id, operation, event_id, created_at_us)
               VALUES (?, ?, 'introduced', ?, ?)""",
            (character_id, scene_subject_id, evidence_event_id, effective_at_us),
        )
        self.set_active_state(
            character_id,
            str(uuid.uuid5(uuid.NAMESPACE_URL, f"aifren:active-state:{character_id}:{truth_scope_id}:{evidence_event_id}:{scene_subject_id}:kind")),
            subject_key=scene_state_subject_key(scene_subject_id, "kind"), value=introduction.kind,
            evidence_event_id=evidence_event_id, evidence_role="direct_user_statement",
            evidence_excerpt_start_cp=start, evidence_excerpt_end_cp=end,
            truth_scope_id=truth_scope_id,
        )
        return scene_subject_id

    def _reactivate_scene_subject(
        self,
        character_id: str,
        reactivation: ActiveSceneSubjectReactivation,
        *,
        evidence_event_id: str,
        effective_at_us: int,
        evidence_content: str,
        truth_scope_id: str,
    ) -> bool:
        """Reactivate only one explicitly referenced distinct retired subject."""
        row = self.connection.execute(
            """SELECT retired_at_us, identity_strength FROM active_scene_subjects
                 WHERE character_id=? AND truth_scope_id=? AND scene_subject_id=?""",
            (character_id, truth_scope_id, reactivation.reference),
        ).fetchone()
        if row is None or row["retired_at_us"] is None:
            return False
        if str(row["identity_strength"]) != "distinct":
            raise StoreError("generic retired scene subjects cannot be implicitly reactivated")
        if self._scene_subject_count(character_id, effective_at_us, truth_scope_id) >= MAX_ACTIVE_SCENE_SUBJECTS:
            raise StoreError("active current-scene subject capacity is reached.")
        start, end = reactivation.excerpt_start_cp, reactivation.excerpt_end_cp
        if end > len(evidence_content):
            raise StoreError("active-scene subject reactivation span exceeds canonical evidence")
        # Restore identity metadata only. Ownership, location, capability
        # causes, and transient condition do not silently resurrect.
        prefix = f"active.scene.{reactivation.reference}."
        rows = self.connection.execute(
            """SELECT c.subject_key, c.content FROM claims c
                 WHERE c.character_id=? AND c.truth_scope_id=? AND c.claim_type=?
                   AND c.subject_key>=? AND c.subject_key<?
                 ORDER BY c.created_at_us DESC, c.claim_id""",
            (character_id, truth_scope_id, ACTIVE_STATE, prefix, prefix + "\uffff"),
        ).fetchall()
        latest: dict[str, str] = {}
        allowed = {"kind", "color", "region", "side", "quantity", "set_label"}
        for metadata in rows:
            attribute = str(metadata["subject_key"]).rsplit(".", 1)[-1]
            if attribute in allowed and attribute not in latest:
                latest[attribute] = str(metadata["content"])
        if "kind" not in latest:
            raise StoreError("retired scene subject lacks stable identity metadata")
        self.connection.execute(
            """UPDATE active_scene_subjects SET retired_at_us=NULL, retired_event_id=NULL,
                   retired_excerpt_start_cp=NULL, retired_excerpt_end_cp=NULL,
                   retired_excerpt_hash=NULL, last_referenced_at_us=?
                 WHERE character_id=? AND truth_scope_id=? AND scene_subject_id=?""",
            (effective_at_us, character_id, truth_scope_id, reactivation.reference),
        )
        self.connection.execute(
            """INSERT INTO active_scene_subject_lifecycle_events(
                   character_id, scene_subject_id, operation, event_id, created_at_us)
               VALUES (?, ?, 'reactivated', ?, ?)""",
            (character_id, reactivation.reference, evidence_event_id, effective_at_us),
        )
        for attribute, value in latest.items():
            self.set_active_state(
                character_id,
                str(uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"aifren:scene-reactivation:{character_id}:{truth_scope_id}:"
                    f"{evidence_event_id}:{reactivation.reference}:{attribute}",
                )),
                subject_key=scene_state_subject_key(reactivation.reference, attribute),
                value=value, evidence_event_id=evidence_event_id,
                evidence_role="direct_user_statement",
                evidence_excerpt_start_cp=start, evidence_excerpt_end_cp=end,
                truth_scope_id=truth_scope_id,
            )
        return True

    def _retire_scene_subject(
        self,
        character_id: str,
        retirement: ActiveSceneSubjectRetirement,
        *,
        evidence_event_id: str,
        effective_at_us: int,
        evidence_content: str,
        truth_scope_id: str,
    ) -> bool:
        subject_id = retirement.reference
        if not self._scene_subject_is_active(character_id, subject_id, effective_at_us, truth_scope_id):
            return False
        start, end = retirement.excerpt_start_cp, retirement.excerpt_end_cp
        if end is not None and end > len(evidence_content):
            raise StoreError("active-scene subject retirement source span exceeds canonical evidence.")
        excerpt_hash = (hashlib.sha256(evidence_content[start:end].encode("utf-8")).hexdigest()
                        if start is not None and end is not None else None)
        rows = self._current_scene_attribute_rows(character_id, subject_id, effective_at_us, truth_scope_id)
        if len(rows) > MAX_ACTIVE_SCENE_ATTRIBUTES_PER_SUBJECT:
            raise StoreError("active-scene subject has too many current attributes.")
        for row in rows:
            self.connection.execute(
                "INSERT INTO claim_status_events(character_id, claim_id, status, reason, source_event_id, actor_kind, created_at_us) VALUES (?, ?, 'cancelled', 'active_scene_subject_retired', ?, 'user', ?)",
                (character_id, row["claim_id"], str(evidence_event_id), effective_at_us),
            )
            self.connection.execute(
                "UPDATE claims SET valid_to_us=? WHERE character_id=? AND claim_id=? AND (valid_to_us IS NULL OR valid_to_us > ?)",
                (effective_at_us, character_id, row["claim_id"], effective_at_us),
            )
        relation_rows = self.connection.execute(
            """SELECT relation_id, valid_from_us FROM active_scene_relations
                 WHERE character_id=? AND truth_scope_id=? AND valid_to_us IS NULL
                   AND ((target_kind='scene' AND target_actor=?) OR cause_subject_id=?)
                 ORDER BY relation_id LIMIT 33""",
            (character_id, truth_scope_id, subject_id, subject_id),
        ).fetchall()
        if len(relation_rows) > 32:
            raise StoreError("active-scene retirement relation set exceeds the governed bound")
        for relation_row in relation_rows:
            if int(relation_row["valid_from_us"]) > effective_at_us:
                raise StoreError("scene retirement evidence predates a current relation")
            relation_id = str(relation_row["relation_id"])
            self.connection.execute(
                "UPDATE active_scene_relations SET valid_to_us=? WHERE character_id=? AND relation_id=?",
                (effective_at_us, character_id, relation_id),
            )
            self.connection.execute(
                """INSERT INTO active_scene_relation_events(character_id, relation_id, operation,
                   event_id, excerpt_start_cp, excerpt_end_cp, excerpt_hash, created_at_us)
                   VALUES (?, ?, 'clear', ?, ?, ?, ?, ?)""",
                (character_id, relation_id, evidence_event_id, start, end, excerpt_hash, effective_at_us),
            )
        self.connection.execute(
            """UPDATE active_scene_subjects SET retired_at_us=?, retired_event_id=?,
                   retired_excerpt_start_cp=?, retired_excerpt_end_cp=?, retired_excerpt_hash=?
                 WHERE character_id=? AND truth_scope_id=? AND scene_subject_id=? AND retired_at_us IS NULL""",
            (effective_at_us, str(evidence_event_id), start, end, excerpt_hash, character_id, truth_scope_id, subject_id),
        )
        self.connection.execute(
            """INSERT INTO active_scene_subject_lifecycle_events(
                   character_id, scene_subject_id, operation, event_id, created_at_us)
               VALUES (?, ?, 'retired', ?, ?)""",
            (character_id, subject_id, evidence_event_id, effective_at_us),
        )
        return True

    def sweep_scene_subject_budget(
        self,
        character_id: str,
        *,
        evidence_event_id: str,
        truth_scope_id: str | None = None,
    ) -> tuple[str, ...]:
        """Retire only relation-free dormant subjects by age/count salience."""
        character_id, effective_at_us, evidence_content = self._active_state_evidence_time(
            character_id, evidence_event_id, "direct_user_statement",
        )
        with self.transaction():
            scope_id = self._write_truth_scope_id(character_id, truth_scope_id)
            rows = self.connection.execute(
                """SELECT s.scene_subject_id, s.last_referenced_at_us, s.identity_strength
                     FROM active_scene_subjects s
                    WHERE s.character_id=? AND s.truth_scope_id=? AND s.retired_at_us IS NULL
                      AND NOT EXISTS(
                          SELECT 1 FROM active_scene_relations r
                           WHERE r.character_id=s.character_id AND r.truth_scope_id=s.truth_scope_id
                             AND r.valid_to_us IS NULL
                             AND ((r.target_kind='scene' AND r.target_actor=s.scene_subject_id)
                                  OR r.cause_subject_id=s.scene_subject_id)
                      )
                    ORDER BY COALESCE(s.last_referenced_at_us, s.introduced_at_us) DESC,
                             s.scene_subject_id LIMIT ?""",
                (character_id, scope_id, MAX_ACTIVE_SCENE_SUBJECTS + 1),
            ).fetchall()
            retired: list[str] = []
            for index, row in enumerate(rows):
                last_reference = int(row["last_referenced_at_us"] or 0)
                threshold = (
                    DISTINCT_DORMANT_RETIRE_AFTER_US
                    if str(row["identity_strength"]) == "distinct"
                    else GENERIC_DORMANT_RETIRE_AFTER_US
                )
                if index < MAX_DORMANT_SCENE_SUBJECTS and effective_at_us - last_reference < threshold:
                    continue
                scene_subject_id = str(row["scene_subject_id"])
                if self._retire_scene_subject(
                    character_id, ActiveSceneSubjectRetirement(scene_subject_id),
                    evidence_event_id=evidence_event_id, effective_at_us=effective_at_us,
                    evidence_content=evidence_content, truth_scope_id=scope_id,
                ):
                    retired.append(scene_subject_id)
            return tuple(retired)

    def clear_active_state(
        self,
        character_id: str,
        *,
        subject_key: str,
        evidence_event_id: str,
        evidence_role: str = "direct_user_statement",
        truth_scope_id: str | None = None,
    ) -> bool:
        """Explicitly unset one slot without resurrecting an older value."""
        try:
            subject_key = validate_active_state_subject_key(subject_key)
            if not active_state_slot(subject_key).clear_supported:
                raise ValueError("active-state slot does not support clear")
        except ValueError as error:
            raise StoreError(str(error)) from error
        character_id, effective_at_us, _ = self._active_state_evidence_time(
            character_id, evidence_event_id, evidence_role,
        )
        with self.transaction():
            truth_scope_id = self._write_truth_scope_id(character_id, truth_scope_id)
            existing = self._current_active_state_rows(character_id, subject_key, effective_at_us, truth_scope_id)
            if len(existing) > 1:
                raise StoreError("multiple current active-state values exist for this singleton slot.")
            if not existing:
                return False
            predecessor = existing[0]
            self.connection.execute(
                "INSERT INTO claim_status_events(character_id, claim_id, status, reason, source_event_id, actor_kind, created_at_us) VALUES (?, ?, 'cancelled', 'active_state_cleared', ?, ?, ?)",
                (character_id, predecessor["claim_id"], str(evidence_event_id),
                 "assistant" if evidence_role == "governed_companion_action" else "user",
                 effective_at_us),
            )
            self.connection.execute(
                """UPDATE claims SET valid_to_us=? WHERE character_id=? AND claim_id=?
                     AND (valid_to_us IS NULL OR valid_to_us > ?)""",
                (effective_at_us, character_id, predecessor["claim_id"], effective_at_us),
            )
        return True

    def apply_active_state_proposal(
        self,
        character_id: str,
        proposal: ActiveStateProposal,
        *,
        evidence_event_id: str,
        evidence_role: str = "direct_user_statement",
        truth_scope_id: str | None = None,
    ) -> tuple[dict[str, object], ...]:
        """Atomically apply validated, bounded updates from one user event.

        Every update is validated before mutation. Any lifecycle conflict or
        write error rolls back the entire proposal, so a bad update cannot
        partially alter unrelated current slots.
        """
        try:
            updates = validate_active_state_proposal(proposal)
        except ValueError as error:
            raise StoreError(str(error)) from error
        if evidence_role not in {
            "direct_user_statement", "user_confirmation", "governed_companion_action",
        }:
            raise StoreError("active-state proposals require governed evidence.")
        if evidence_role == "governed_companion_action":
            if proposal.introductions or proposal.retirements or proposal.reactivations or len(updates) != 1:
                raise StoreError("a governed companion action may modify exactly one actor slot")
            action_update = updates[0]
            if (action_update.target_kind != "actor" or action_update.target_ref != "companion"
                    or action_update.attribute not in {"activity", "posture"}
                    or action_update.basis != "explicit"):
                raise StoreError("governed companion action target is not permitted")
        character_id, effective_at_us, evidence_content = self._active_state_evidence_time(
            character_id, evidence_event_id, evidence_role,
        )
        for update in updates:
            if update.operation == "set":
                assert update.excerpt_start_cp is not None and update.excerpt_end_cp is not None
                if update.excerpt_end_cp > len(evidence_content):
                    raise StoreError("active-state proposal source span exceeds canonical evidence.")
        applied: list[dict[str, object]] = []
        with self.transaction():
            truth_scope_id = self._write_truth_scope_id(character_id, truth_scope_id)
            local_refs: dict[str, str] = {}
            for reactivation in proposal.reactivations:
                reactivated = self._reactivate_scene_subject(
                    character_id, reactivation, evidence_event_id=evidence_event_id,
                    effective_at_us=effective_at_us, evidence_content=evidence_content,
                    truth_scope_id=truth_scope_id,
                )
                applied.append({"operation": "reactivate", "scene_subject_id": reactivation.reference,
                                "reactivated": reactivated})
            for introduction in proposal.introductions:
                scene_subject_id = self._introduce_scene_subject(
                    character_id, introduction, evidence_event_id=evidence_event_id,
                    effective_at_us=effective_at_us, evidence_content=evidence_content, truth_scope_id=truth_scope_id,
                )
                local_refs[introduction.reference] = scene_subject_id
                applied.append({"operation": "introduce", "reference": introduction.reference,
                                "scene_subject_id": scene_subject_id})

            resolved: list[tuple[ActiveStateProposalUpdate, str]] = []
            for update in updates:
                if update.target_kind == "slot":
                    assert update.slot is not None
                    subject_key = update.slot
                elif update.target_kind == "actor":
                    from .active_state_contract import actor_state_subject_key
                    assert update.target_ref is not None and update.attribute is not None
                    subject_key = actor_state_subject_key(update.target_ref, update.attribute)
                else:
                    assert update.target_ref is not None and update.attribute is not None
                    subject_id = local_refs.get(update.target_ref, update.target_ref)
                    if not self._scene_subject_is_active(character_id, subject_id, effective_at_us, truth_scope_id):
                        raise StoreError("active-state proposal references a non-current scene subject.")
                    subject_key = scene_state_subject_key(subject_id, update.attribute)
                resolved.append((update, subject_key))
            retired_subjects = {item.reference for item in proposal.retirements}
            if any(parse_active_state_subject_key(key)[1] in retired_subjects for _, key in resolved):
                raise StoreError("active-state proposal cannot update and retire one scene subject together.")
            for update, subject_key in resolved:
                update_evidence_role = ("immediate_user_event_consequence"
                                        if update.basis == "immediate_consequence" else evidence_role)
                if update.operation == "set":
                    requested_state_id = str(uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"aifren:active-state:{character_id}:{truth_scope_id}:{evidence_event_id}:{subject_key}",
                    ))
                    state_id = self.set_active_state(
                        character_id, requested_state_id, subject_key=subject_key, value=str(update.value),
                        evidence_event_id=evidence_event_id, evidence_role=update_evidence_role,
                        evidence_excerpt_start_cp=update.excerpt_start_cp,
                        evidence_excerpt_end_cp=update.excerpt_end_cp,
                        truth_scope_id=truth_scope_id,
                    )
                    applied.append({"slot": subject_key, "operation": "set", "state_id": state_id,
                                    "basis": update.basis})
                else:
                    cleared = self.clear_active_state(
                        character_id, subject_key=subject_key, evidence_event_id=evidence_event_id,
                        evidence_role=update_evidence_role,
                        truth_scope_id=truth_scope_id,
                    )
                    applied.append({"slot": subject_key, "operation": "clear", "cleared": cleared,
                                    "basis": update.basis})
            for retirement in proposal.retirements:
                retired = self._retire_scene_subject(
                    character_id, retirement, evidence_event_id=evidence_event_id,
                    effective_at_us=effective_at_us, evidence_content=evidence_content, truth_scope_id=truth_scope_id,
                )
                applied.append({"operation": "retire", "scene_subject_id": retirement.reference,
                                "retired": retired})
        return tuple(applied)

    def apply_active_state_correction(
        self,
        character_id: str,
        proposal: ActiveStateCorrectionProposal,
        *,
        evidence_event_id: str,
        truth_scope_id: str | None = None,
    ) -> dict[str, object]:
        """Verify and non-destructively replace exactly one governed slot."""
        try:
            correction, subject_key = validate_active_state_correction(proposal)
        except ValueError as error:
            raise StoreError(str(error)) from error
        character_id, effective_at_us, evidence_content = self._active_state_evidence_time(
            character_id, evidence_event_id, "direct_user_statement",
        )
        if correction.excerpt_end_cp > len(evidence_content):
            raise StoreError("active-state correction evidence span exceeds canonical evidence")
        with self.transaction():
            scope_id = self._write_truth_scope_id(character_id, truth_scope_id)
            current = self._current_active_state_rows(
                character_id, subject_key, effective_at_us, scope_id,
            )
            if len(current) > 1:
                raise StoreError("active-state correction target is not a singleton")
            predecessor = current[0] if current else None
            if correction.expected_value is not None and (
                predecessor is None or predecessor["content"] != correction.expected_value
            ):
                raise StoreError("active-state correction exact expectation does not match current state")
            if correction.expected_family is not None:
                if predecessor is None:
                    raise StoreError("active-state correction family expectation has no current state")
                first = re.search(r"[a-z]+", str(predecessor["content"]).casefold())
                family = first.group(0) if first is not None else "activity"
                family = {
                    "sleeping": "sleep", "working": "work", "playing": "play",
                    "watching": "watch", "installing": "install",
                }.get(family, family)
                if family != correction.expected_family:
                    raise StoreError("active-state correction family expectation does not match current state")
            requested_id = str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"aifren:active-state-correction:{character_id}:{scope_id}:{evidence_event_id}:{subject_key}",
            ))
            state_id = self.set_active_state(
                character_id, requested_id, subject_key=subject_key,
                value=correction.corrected_value, evidence_event_id=evidence_event_id,
                evidence_role="direct_user_statement",
                evidence_excerpt_start_cp=correction.excerpt_start_cp,
                evidence_excerpt_end_cp=correction.excerpt_end_cp,
                truth_scope_id=scope_id,
                replacement_reason="active_state_corrected",
            )
            return {
                "operation": "correct", "slot": subject_key, "state_id": state_id,
                "replaced": predecessor is not None,
            }

    def _open_thread_evidence_time(self, character_id: str, evidence_event_id: str) -> tuple[str, int, str]:
        """Return only active, same-character canonical user evidence."""
        character_id = _require_uuid(character_id)
        event = self.connection.execute(
            """SELECT recorded_at_us, content_text FROM events WHERE character_id=? AND event_id=?
               AND actor_kind='user' AND redaction_state='active' AND content_text IS NOT NULL""",
            (character_id, str(evidence_event_id)),
        ).fetchone()
        if event is None:
            raise StoreError("open thread requires active same-character user-authored evidence.")
        return character_id, int(event["recorded_at_us"]), str(event["content_text"])

    @staticmethod
    def _open_thread_evidence_hash(content: str, start: int, end: int) -> str:
        if not 0 <= start < end <= len(content):
            raise StoreError("open-thread proposal source span exceeds canonical evidence.")
        return hashlib.sha256(content[start:end].encode("utf-8")).hexdigest()

    def _current_open_thread_row(self, character_id: str, thread_id: str, at_us: int, truth_scope_id: str):
        return self.connection.execute(
            """SELECT * FROM open_threads WHERE character_id=? AND truth_scope_id=? AND thread_id=? AND status='open'
                 AND opened_at_us <= ? AND (closed_at_us IS NULL OR closed_at_us > ?)""",
            (character_id, truth_scope_id, thread_id, at_us, at_us),
        ).fetchone()

    def _current_open_thread_count(self, character_id: str, at_us: int, truth_scope_id: str) -> int:
        return int(self.connection.execute(
            """SELECT COUNT(*) FROM open_threads WHERE character_id=? AND truth_scope_id=? AND status='open'
                 AND opened_at_us <= ? AND (closed_at_us IS NULL OR closed_at_us > ?)""",
            (character_id, truth_scope_id, at_us, at_us),
        ).fetchone()[0])

    def _current_open_thread_by_identity(
        self, character_id: str, kind: str, participant_scope: str, description: str, at_us: int, truth_scope_id: str,
    ):
        """Exact compact duplicate prevention; this is not semantic matching."""
        return self.connection.execute(
            """SELECT * FROM open_threads WHERE character_id=? AND truth_scope_id=? AND status='open' AND thread_kind=?
                 AND participant_scope=? AND description=? AND opened_at_us <= ?
                 AND (closed_at_us IS NULL OR closed_at_us > ?) LIMIT 2""",
            (character_id, truth_scope_id, kind, participant_scope, description, at_us, at_us),
        ).fetchall()

    def _attach_open_thread_evidence(
        self,
        character_id: str,
        thread_id: str,
        event_id: str,
        role: str,
        start: int,
        end: int,
        excerpt_hash: str,
        at_us: int,
    ) -> None:
        """Keep a bounded evidence sample; the canonical archive remains complete."""
        if role not in OPEN_THREAD_EVIDENCE_ROLES:
            raise StoreError("open-thread evidence role is not governed.")
        count = int(self.connection.execute(
            "SELECT COUNT(*) FROM claim_evidence WHERE character_id=? AND claim_id=?",
            (character_id, thread_id),
        ).fetchone()[0])
        if count >= MAX_OPEN_THREAD_EVIDENCE_RECORDS:
            return
        self.connection.execute(
            "INSERT OR IGNORE INTO claim_evidence VALUES (?, ?, ?, ?, ?, ?, ?, 1.0, NULL, ?)",
            (character_id, thread_id, str(event_id), role, start, end, excerpt_hash, at_us),
        )

    def apply_open_thread_proposal(
        self,
        character_id: str,
        proposal: OpenThreadProposal,
        *,
        evidence_event_id: str,
        truth_scope_id: str | None = None,
    ) -> tuple[dict[str, object], ...]:
        """Atomically apply compact Open Thread lifecycle updates.

        The method is deliberately generic and provider-agnostic. It accepts
        only the governed proposal contract and canonical user evidence; a
        model or provider must never write the table directly.
        """
        try:
            operations = validate_open_thread_proposal(proposal)
        except ValueError as error:
            raise StoreError(str(error)) from error
        character_id, effective_at_us, evidence_content = self._open_thread_evidence_time(
            character_id, evidence_event_id,
        )
        for operation in operations:
            self._open_thread_evidence_hash(
                evidence_content, operation.excerpt_start_cp, operation.excerpt_end_cp,
            )
        applied: list[dict[str, object]] = []
        with self.transaction():
            truth_scope_id = self._write_truth_scope_id(character_id, truth_scope_id)
            for operation in operations:
                excerpt_hash = self._open_thread_evidence_hash(
                    evidence_content, operation.excerpt_start_cp, operation.excerpt_end_cp,
                )
                if operation.operation == "open":
                    assert operation.kind is not None and operation.participant_scope is not None
                    assert operation.description is not None
                    duplicate = self._current_open_thread_by_identity(
                        character_id, operation.kind, operation.participant_scope,
                        operation.description, effective_at_us, truth_scope_id,
                    )
                    if len(duplicate) > 1:
                        raise StoreError("multiple current open threads share one exact compact identity.")
                    if duplicate:
                        thread_id = str(duplicate[0]["thread_id"])
                        self._attach_open_thread_evidence(
                            character_id, thread_id, str(evidence_event_id), "thread_reconfirm",
                            operation.excerpt_start_cp, operation.excerpt_end_cp, excerpt_hash, effective_at_us,
                        )
                        self.connection.execute(
                            """UPDATE open_threads SET last_mentioned_at_us=MAX(last_mentioned_at_us, ?),
                                   last_event_id=?, last_excerpt_start_cp=?, last_excerpt_end_cp=?,
                                   last_excerpt_hash=?, temporal_anchor=COALESCE(?, temporal_anchor)
                                 WHERE character_id=? AND thread_id=?""",
                            (effective_at_us, str(evidence_event_id), operation.excerpt_start_cp,
                             operation.excerpt_end_cp, excerpt_hash, operation.temporal_anchor,
                             character_id, thread_id),
                        )
                        self.connection.execute(
                            "UPDATE claims SET updated_at_us=MAX(COALESCE(updated_at_us, 0), ?) WHERE character_id=? AND claim_id=?",
                            (effective_at_us, character_id, thread_id),
                        )
                        applied.append({"operation": "reconfirm", "reference": operation.reference, "thread_id": thread_id})
                        continue
                    if self._current_open_thread_count(character_id, effective_at_us, truth_scope_id) >= MAX_CURRENT_OPEN_THREADS:
                        raise StoreError("open-thread current capacity is reached.")
                    thread_id = f"thread-{uuid.uuid5(uuid.NAMESPACE_URL, f'aifren:open-thread:{character_id}:{truth_scope_id}:{evidence_event_id}:{operation.reference}')}"
                    if self.connection.execute(
                        "SELECT 1 FROM open_threads WHERE character_id=? AND thread_id=?",
                        (character_id, thread_id),
                    ).fetchone() is not None:
                        raise StoreError("open-thread introduction already exists for this canonical event.")
                    self._insert_claim(
                        character_id, thread_id, claim_type=OPEN_THREAD,
                        assertion_scope=OPEN_THREAD_ASSERTION_SCOPE, subject_key=thread_id,
                        content=operation.description, importance=5, confidence=None,
                        valid_from_us=effective_at_us, valid_to_us=None,
                        temporal_precision="instant", temporal_expression=operation.temporal_anchor,
                        provenance_state="complete", curator_name="open_thread_api",
                        curator_version="1", curator_policy_version="explicit_v1",
                        legacy_metadata=None, created_at_us=effective_at_us, updated_at_us=effective_at_us,
                        truth_scope_id=truth_scope_id,
                    )
                    self._attach_open_thread_evidence(
                        character_id, thread_id, str(evidence_event_id), "thread_open",
                        operation.excerpt_start_cp, operation.excerpt_end_cp, excerpt_hash, effective_at_us,
                    )
                    self.connection.execute(
                        """INSERT INTO open_threads(character_id, thread_id, thread_kind, participant_scope,
                               description, temporal_anchor, status, opened_at_us, last_mentioned_at_us,
                               closed_at_us, opened_event_id, last_event_id, last_excerpt_start_cp,
                               last_excerpt_end_cp, last_excerpt_hash, closed_event_id, truth_scope_id)
                               VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, NULL, ?, ?, ?, ?, ?, NULL, ?)""",
                        (character_id, thread_id, operation.kind, operation.participant_scope,
                         operation.description, operation.temporal_anchor, effective_at_us, effective_at_us,
                         str(evidence_event_id), str(evidence_event_id), operation.excerpt_start_cp,
                         operation.excerpt_end_cp, excerpt_hash, truth_scope_id),
                    )
                    applied.append({"operation": "open", "reference": operation.reference, "thread_id": thread_id})
                    continue
                thread_id = operation.reference
                row = self._current_open_thread_row(character_id, thread_id, effective_at_us, truth_scope_id)
                if row is None:
                    raise StoreError("open-thread proposal references a non-current same-character thread.")
                if operation.operation == "reconfirm":
                    self._attach_open_thread_evidence(
                        character_id, thread_id, str(evidence_event_id), "thread_reconfirm",
                        operation.excerpt_start_cp, operation.excerpt_end_cp, excerpt_hash, effective_at_us,
                    )
                    self.connection.execute(
                        """UPDATE open_threads SET last_mentioned_at_us=MAX(last_mentioned_at_us, ?),
                               last_event_id=?, last_excerpt_start_cp=?, last_excerpt_end_cp=?,
                               last_excerpt_hash=?, temporal_anchor=COALESCE(?, temporal_anchor)
                             WHERE character_id=? AND thread_id=?""",
                        (effective_at_us, str(evidence_event_id), operation.excerpt_start_cp,
                         operation.excerpt_end_cp, excerpt_hash, operation.temporal_anchor, character_id, thread_id),
                    )
                    self.connection.execute(
                        "UPDATE claims SET updated_at_us=MAX(COALESCE(updated_at_us, 0), ?) WHERE character_id=? AND claim_id=?",
                        (effective_at_us, character_id, thread_id),
                    )
                    applied.append({"operation": "reconfirm", "thread_id": thread_id})
                    continue
                evidence_role = "thread_resolve" if operation.operation == "resolve" else "thread_cancel"
                final_status = "resolved" if operation.operation == "resolve" else "cancelled"
                self._attach_open_thread_evidence(
                    character_id, thread_id, str(evidence_event_id), evidence_role,
                    operation.excerpt_start_cp, operation.excerpt_end_cp, excerpt_hash, effective_at_us,
                )
                self.connection.execute(
                    """UPDATE open_threads SET status=?, last_mentioned_at_us=MAX(last_mentioned_at_us, ?),
                           last_event_id=?, last_excerpt_start_cp=?, last_excerpt_end_cp=?,
                           last_excerpt_hash=?, closed_at_us=?, closed_event_id=?
                         WHERE character_id=? AND thread_id=? AND status='open'""",
                    (final_status, effective_at_us, str(evidence_event_id), operation.excerpt_start_cp,
                     operation.excerpt_end_cp, excerpt_hash, effective_at_us, str(evidence_event_id),
                     character_id, thread_id),
                )
                self.connection.execute(
                    """UPDATE claims SET valid_to_us=?, updated_at_us=MAX(COALESCE(updated_at_us, 0), ?)
                         WHERE character_id=? AND claim_id=? AND (valid_to_us IS NULL OR valid_to_us > ?)""",
                    (effective_at_us, effective_at_us, character_id, thread_id, effective_at_us),
                )
                applied.append({"operation": operation.operation, "thread_id": thread_id})
        return tuple(applied)

    def add_summary(self, character_id: str, summary_id: str, content: str, *, source_count: Optional[int], provenance_state: str, generator_name: Optional[str], generator_version: Optional[str], legacy_metadata: Optional[dict] = None, created_at_us: Optional[int] = None, summary_level: str = "legacy_conversation_summary") -> None:
        summary_level = str(summary_level or "").strip()
        if not summary_level:
            raise StoreError("summary_level is required.")
        with self.transaction():
            self.connection.execute(
                "INSERT INTO summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_require_uuid(character_id), summary_id, summary_level, content, source_count, provenance_state,
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
        if status == "superseded":
            claim = self.connection.execute(
                "SELECT claim_type FROM claims WHERE character_id=? AND claim_id=?",
                (_require_uuid(character_id), str(claim_id)),
            ).fetchone()
            if claim is not None and claim["claim_type"] == DURABLE_CORE_FACT:
                raise StoreError("durable corrections must use add_durable_claim with supersedes_claim_id.")
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

    def structural_claims(self, character_id: str, at_us: int, *, historical: bool = False, exclude_claim_ids: tuple[str, ...] = (), claim_ids: tuple[str, ...] = (), claim_types: tuple[str, ...] = (), limit: Optional[int] = None, truth_scope_id: str | None = None, include_historical_evidence: bool = False) -> list[sqlite3.Row]:
        character_id = _require_uuid(character_id)
        scope_sql, scope_arguments = self._retrieval_scope_sql(
            character_id, truth_scope_id,
            include_historical_evidence=include_historical_evidence,
        )
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
              AND """ + scope_sql
        arguments: list[Any] = [at_us, character_id, *scope_arguments]
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

    def exact_claim_ids(
        self,
        character_id: str,
        terms: tuple[str, ...],
        limit: int,
        *,
        truth_scope_id: str | None = None,
        include_historical_evidence: bool = False,
    ) -> list[str]:
        """Bound exact substring lane using SQL parameters, never FTS syntax."""
        if not terms or limit < 1:
            return []
        character_id = _require_uuid(character_id)
        scope_sql, scope_arguments = self._retrieval_scope_sql(
            character_id, truth_scope_id,
            include_historical_evidence=include_historical_evidence,
        )
        predicates = " OR ".join("instr(lower(content), lower(?)) > 0" for _ in terms)
        rows = self.connection.execute(
            f"SELECT c.claim_id FROM claims c WHERE c.character_id = ? AND {scope_sql} "
            f"AND ({predicates}) ORDER BY c.claim_id LIMIT ?",
            [character_id, *scope_arguments, *terms, limit],
        ).fetchall()
        return [row[0] for row in rows]

    def retrieval_scope_claim_ids(
        self,
        character_id: str,
        claim_ids: Iterable[str],
        *,
        truth_scope_id: str | None = None,
        include_historical_evidence: bool = False,
    ) -> set[str]:
        """Filter bounded derived candidates through canonical truth scope."""
        values = tuple(dict.fromkeys(str(claim_id) for claim_id in claim_ids))
        if not values:
            return set()
        character_id = _require_uuid(character_id)
        scope_sql, scope_arguments = self._retrieval_scope_sql(
            character_id, truth_scope_id,
            include_historical_evidence=include_historical_evidence,
        )
        rows = self.connection.execute(
            f"SELECT c.claim_id FROM claims c WHERE c.character_id=? AND {scope_sql} "
            f"AND c.claim_id IN ({','.join('?' for _ in values)})",
            [character_id, *scope_arguments, *values],
        ).fetchall()
        return {str(row[0]) for row in rows}

    def historical_evidence_metadata(
        self,
        character_id: str,
        claim_ids: Iterable[str],
        *,
        limit: int = 256,
    ) -> dict[str, sqlite3.Row]:
        """Return bounded structural metadata, never canonical source text."""
        if isinstance(limit, bool) or not 1 <= int(limit) <= 256:
            raise StoreError("historical evidence metadata limit is invalid")
        values = tuple(dict.fromkeys(str(value) for value in claim_ids))[: int(limit)]
        if not values:
            return {}
        character_id = _require_uuid(character_id)
        rows = self.connection.execute(
            """SELECT h.* FROM historical_evidence h
                WHERE h.character_id=? AND h.claim_id IN ("""
            + ",".join("?" for _ in values)
            + ") ORDER BY h.claim_id LIMIT ?",
            (character_id, *values, int(limit)),
        ).fetchall()
        return {str(row["claim_id"]): row for row in rows}

    def fts_available(self) -> bool:
        try:
            self.connection.execute("SELECT count(*) FROM claims_fts").fetchone()
            return True
        except sqlite3.OperationalError:
            return False

    def rebuild_fts(self) -> int:
        """Rebuild derived FTS rows from canonical claims; never changes claims."""
        if not self.fts_available() or self.connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='historical_evidence_fts'",
        ).fetchone()[0] != 1:
            raise StoreError("SQLite FTS5 is unavailable; cannot build derived claim index.")
        with self.transaction():
            self.connection.execute("DELETE FROM claims_fts")
            self.connection.execute("DELETE FROM historical_evidence_fts")
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
                target = (
                    "historical_evidence_fts"
                    if row["claim_type"] == HISTORICAL_EVIDENCE
                    else "claims_fts"
                )
                self.connection.execute(
                    f"INSERT INTO {target}(character_id, claim_id, searchable_text) VALUES (?, ?, ?)",
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
        expected_claims = sum(row["claim_type"] != HISTORICAL_EVIDENCE for row in rows)
        expected_historical = len(rows) - expected_claims
        actual_claims = self.connection.execute("SELECT count(*) FROM claims_fts").fetchone()[0]
        try:
            actual_historical = self.connection.execute(
                "SELECT count(*) FROM historical_evidence_fts",
            ).fetchone()[0]
        except sqlite3.OperationalError:
            return False
        stored = self.connection.execute("SELECT value FROM database_meta WHERE key = 'fts_claims_digest'").fetchone()
        return (
            expected_claims == actual_claims
            and expected_historical == actual_historical
            and stored is not None
            and stored[0] == digest.hexdigest()
        )

    def ensure_fts(self) -> int:
        return 0 if self.fts_is_current() else self.rebuild_fts()

    def refresh_fts_sources(self, character_id: str, source_references: Sequence[str]) -> int:
        """Refresh only claims touched by a bounded canonical observation page.

        Original events, claims and correction lifecycle are never deleted.
        The existing drift digest is recomputed, not the full FTS projection.
        Its validation is linear; changed row writes are bounded independently.
        """
        character_id = _require_uuid(character_id)
        references = tuple(dict.fromkeys(source_references))
        if not references:
            return 0
        if len(references) > 256 or any(not isinstance(v, str) or len(v) > 500 for v in references):
            raise StoreError("canonical FTS page is out of bounds")
        placeholders = ",".join("?" for _ in references)
        identifiers = tuple(row[0] for row in self.connection.execute(
            f"""SELECT ce.claim_id FROM claim_evidence ce JOIN events e
                   ON e.character_id=ce.character_id AND e.event_id=ce.event_id
                 WHERE e.character_id=? AND e.source_reference IN ({placeholders})
                 UNION SELECT s.claim_id FROM claim_status_events s JOIN events e
                   ON e.character_id=s.character_id AND e.event_id=s.source_event_id
                 WHERE e.character_id=? AND e.source_reference IN ({placeholders}) LIMIT 513""",
            (character_id, *references, character_id, *references),
        ))
        if len(identifiers) > 512:
            raise StoreError("canonical FTS claim page is out of bounds")
        with self.transaction():
            for claim_id in identifiers:
                for table in ("claims_fts", "historical_evidence_fts"):
                    self.connection.execute(f"DELETE FROM {table} WHERE character_id=? AND claim_id=?",
                                            (character_id, claim_id))
                row = self.connection.execute(
                    """SELECT c.*, COALESCE((SELECT status FROM claim_status_events s
                         WHERE s.character_id=c.character_id AND s.claim_id=c.claim_id
                         ORDER BY status_event_id DESC LIMIT 1),'active') AS status
                       FROM claims c WHERE c.character_id=? AND c.claim_id=? AND EXISTS
                       (SELECT 1 FROM claim_evidence e WHERE e.character_id=c.character_id
                        AND e.claim_id=c.claim_id)""", (character_id, claim_id),
                ).fetchone()
                if row is not None and row["status"] not in {"retracted", "archived", "hidden", "redacted"}:
                    searchable = " ".join(part for part in (row["claim_type"], row["subject_key"], row["content"]) if part)
                    table = "historical_evidence_fts" if row["claim_type"] == HISTORICAL_EVIDENCE else "claims_fts"
                    self.connection.execute(f"INSERT INTO {table} VALUES (?,?,?)", (character_id, claim_id, searchable))
            # Do not claim unrelated existing drift was fixed. Compare retained
            # rows with the canonical claim projection before stamping it current.
            digest = hashlib.sha256()
            expected_count = 0
            for row in self.connection.execute(
                """SELECT c.character_id,c.claim_id,c.claim_type,c.subject_key,c.content,
                   COALESCE((SELECT status FROM claim_status_events s
                     WHERE s.character_id=c.character_id AND s.claim_id=c.claim_id
                     ORDER BY status_event_id DESC LIMIT 1),'active') AS status
                   FROM claims c WHERE EXISTS (SELECT 1 FROM claim_evidence e
                     WHERE e.character_id=c.character_id AND e.claim_id=c.claim_id)
                   ORDER BY c.character_id,c.claim_id""",
            ):
                if row["status"] in {"retracted", "archived", "hidden", "redacted"}:
                    continue
                searchable = " ".join(part for part in (row["claim_type"], row["subject_key"], row["content"]) if part)
                expected_count += 1
                digest.update(f"{row['character_id']}\0{row['claim_id']}\0{searchable}\0{row['status']}\n".encode())
            actual_digest = hashlib.sha256()
            actual_count = 0
            for row in self.connection.execute(
                """SELECT f.character_id,f.claim_id,f.searchable_text,f.historical,c.claim_type,
                   COALESCE((SELECT status FROM claim_status_events s
                     WHERE s.character_id=c.character_id AND s.claim_id=c.claim_id
                     ORDER BY status_event_id DESC LIMIT 1),'active') AS status
                   FROM (SELECT *,0 AS historical FROM claims_fts UNION ALL
                         SELECT *,1 AS historical FROM historical_evidence_fts) f
                   LEFT JOIN claims c ON c.character_id=f.character_id AND c.claim_id=f.claim_id
                   ORDER BY f.character_id,f.claim_id""",
            ):
                if (row["claim_type"] == HISTORICAL_EVIDENCE) != bool(row["historical"]):
                    return len(identifiers)
                actual_count += 1
                actual_digest.update(f"{row['character_id']}\0{row['claim_id']}\0{row['searchable_text']}\0{row['status']}\n".encode())
            if actual_count != expected_count or actual_digest.digest() != digest.digest():
                return len(identifiers)
            self.connection.execute("INSERT OR REPLACE INTO database_meta VALUES ('fts_claims_digest',?)", (digest.hexdigest(),))
        return len(identifiers)

    # The following embedding helpers exclusively maintain derived V2 state.
    # They intentionally have no path to the production JSON memory database.
    def embedding_source_claims(
        self,
        *,
        include_legacy_unverified: bool = False,
        claim_ids: Iterable[str] | None = None,
        character_id: str | None = None,
    ) -> list[sqlite3.Row]:
        provenance = "('complete', 'legacy_unverified')" if include_legacy_unverified else "('complete')"
        statement = f"""SELECT c.* FROM claims c
               WHERE c.provenance_state IN {provenance}
                 AND (c.claim_type<>? OR EXISTS (
                       SELECT 1 FROM historical_evidence h
                        WHERE h.character_id=c.character_id AND h.claim_id=c.claim_id
                          AND h.retrieval_eligible=1))
                 AND EXISTS (SELECT 1 FROM claim_evidence e
                             WHERE e.character_id=c.character_id AND e.claim_id=c.claim_id)
                 AND COALESCE((SELECT status FROM claim_status_events s
                    WHERE s.character_id=c.character_id AND s.claim_id=c.claim_id
                    ORDER BY s.status_event_id DESC LIMIT 1), 'active')
                    NOT IN ('retracted', 'archived', 'hidden', 'redacted')"""
        arguments: list[object] = [HISTORICAL_EVIDENCE]
        if character_id is not None:
            statement += " AND c.character_id=?"
            arguments.append(_require_uuid(character_id))
        values = tuple(str(value) for value in claim_ids) if claim_ids is not None else ()
        if claim_ids is not None:
            if not values:
                return []
            statement += " AND c.claim_id IN (" + ",".join("?" for _ in values) + ")"
            arguments.extend(values)
        statement += " ORDER BY c.character_id, c.claim_id"
        return self.connection.execute(statement, arguments).fetchall()

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
                 utc_now_us(), "embedding_failed"),
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

    def semantic_candidates(
        self,
        character_id: str,
        provider: Any,
        query_vector: list[float],
        limit: int,
        *,
        truth_scope_id: str | None = None,
        include_historical_evidence: bool = False,
    ) -> list[tuple[str, float]]:
        """Brute-force cosine over current, character-scoped derived vectors."""
        if limit < 1 or len(query_vector) != provider.dimensions:
            return []
        character_id = _require_uuid(character_id)
        scope_sql, scope_arguments = self._retrieval_scope_sql(
            character_id, truth_scope_id,
            include_historical_evidence=include_historical_evidence,
        )
        rows = self.connection.execute(
            f"""SELECT e.*, c.content FROM claim_embeddings e JOIN claims c
                    ON c.character_id=e.character_id AND c.claim_id=e.claim_id
               WHERE e.character_id=? AND e.provider=? AND e.model=?
                 AND e.preprocessing_fingerprint=? AND e.state='current'
                 AND e.dimensions=? AND e.dtype=? AND e.normalized=?
                 AND ((?=1 AND c.claim_type=?) OR (?=0 AND c.claim_type<>?))
                 AND {scope_sql}""",
            (character_id, provider.provider, provider.model, provider.preprocessing_fingerprint,
             provider.dimensions, provider.dtype, int(bool(provider.normalized)),
             int(bool(include_historical_evidence)), HISTORICAL_EVIDENCE,
             int(bool(include_historical_evidence)), HISTORICAL_EVIDENCE,
             *scope_arguments),
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

    def ann_embedding_rows(
        self,
        character_id: str,
        provider: Any,
        *,
        include_historical_evidence: bool = False,
    ) -> list[sqlite3.Row]:
        """Return current provider vectors for rebuilding a derived ANN index.

        This intentionally includes lifecycle tombstones; retrieval performs
        the authoritative character/status validation after ANN candidate
        lookup, so a stale derived index can never resurrect a claim.
        """
        character_id = _require_uuid(character_id)
        return self.connection.execute(
            """SELECT e.claim_id, e.vector_blob FROM claim_embeddings e
               JOIN claims c ON c.character_id=e.character_id AND c.claim_id=e.claim_id
               WHERE e.character_id=? AND e.provider=? AND e.model=?
                 AND e.preprocessing_fingerprint=? AND e.state='current'
                 AND e.dimensions=? AND e.dtype=? AND e.normalized=?
                 AND ((?=1 AND c.claim_type=?) OR (?=0 AND c.claim_type<>?))
               ORDER BY e.claim_id""",
            (character_id, provider.provider, provider.model,
             provider.preprocessing_fingerprint, provider.dimensions,
             provider.dtype, int(bool(provider.normalized)),
             int(bool(include_historical_evidence)), HISTORICAL_EVIDENCE,
             int(bool(include_historical_evidence)), HISTORICAL_EVIDENCE),
        ).fetchall()

    def iter_ann_embedding_rows(
        self,
        character_id: str,
        provider: Any,
        *,
        include_historical_evidence: bool = False,
    ):
        """Stream derived vectors for large rebuilds without materializing text."""
        character_id = _require_uuid(character_id)
        cursor = self.connection.execute(
            """SELECT e.claim_id, e.vector_blob FROM claim_embeddings e
               JOIN claims c ON c.character_id=e.character_id AND c.claim_id=e.claim_id
               WHERE e.character_id=? AND e.provider=? AND e.model=?
                 AND e.preprocessing_fingerprint=? AND e.state='current'
                 AND e.dimensions=? AND e.dtype=? AND e.normalized=?
                 AND ((?=1 AND c.claim_type=?) OR (?=0 AND c.claim_type<>?))
               ORDER BY e.claim_id""",
            (character_id, provider.provider, provider.model,
             provider.preprocessing_fingerprint, provider.dimensions,
             provider.dtype, int(bool(provider.normalized)),
             int(bool(include_historical_evidence)), HISTORICAL_EVIDENCE,
             int(bool(include_historical_evidence)), HISTORICAL_EVIDENCE),
        )
        yield from cursor

    def ann_embedding_count(
        self,
        character_id: str,
        provider: Any,
        *,
        include_historical_evidence: bool = False,
    ) -> int:
        character_id = _require_uuid(character_id)
        return int(self.connection.execute(
            """SELECT COUNT(*) FROM claim_embeddings e
               JOIN claims c ON c.character_id=e.character_id AND c.claim_id=e.claim_id
               WHERE e.character_id=? AND e.provider=? AND e.model=?
                 AND e.preprocessing_fingerprint=? AND e.state='current'
                 AND e.dimensions=? AND e.dtype=? AND e.normalized=?
                 AND ((?=1 AND c.claim_type=?) OR (?=0 AND c.claim_type<>?))""",
            (character_id, provider.provider, provider.model, provider.preprocessing_fingerprint,
             provider.dimensions, provider.dtype, int(bool(provider.normalized)),
             int(bool(include_historical_evidence)), HISTORICAL_EVIDENCE,
             int(bool(include_historical_evidence)), HISTORICAL_EVIDENCE),
        ).fetchone()[0])

    def search_fts(
        self,
        character_id: str,
        safe_query: str,
        limit: int,
        *,
        truth_scope_id: str | None = None,
        include_historical_evidence: bool = False,
    ) -> list[sqlite3.Row]:
        if limit < 1 or not safe_query.strip():
            return []
        character_id = _require_uuid(character_id)
        scope_sql, scope_arguments = self._retrieval_scope_sql(
            character_id, truth_scope_id,
            include_historical_evidence=include_historical_evidence,
        )
        table = "historical_evidence_fts" if include_historical_evidence else "claims_fts"
        return self.connection.execute(
            f"""SELECT f.claim_id, bm25({table}) AS fts_score
                  FROM {table} f JOIN claims c
                    ON c.character_id=f.character_id AND c.claim_id=f.claim_id
                 WHERE {table} MATCH ? AND f.character_id = ? AND {scope_sql}
                 ORDER BY fts_score LIMIT ?""",
            (safe_query, character_id, *scope_arguments, limit),
        ).fetchall()

    def integrity_check(self) -> str:
        return self.connection.execute("PRAGMA quick_check").fetchone()[0]
