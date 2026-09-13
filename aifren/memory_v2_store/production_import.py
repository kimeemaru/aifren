"""Tolerant, repeatable V1 JSON import for the future production V2 store.

Unlike the strict disposable shadow importer, this importer preserves valid
legacy records while reporting malformed ones. It never alters V1 files and
does not change runtime authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

from aifren.character.character_identity import default_legacy_character_id as _default_legacy_character_id
from .repository import MemoryV2Repository, normalize_memory_type
from .store import MemoryV2Store, utc_now_us
from .v1_import import LEGACY_CHARACTER_NAMESPACE


IMPORT_SCOPE = "memory_v1_json"


@dataclass(frozen=True)
class ProductionImportResult:
    character_id: str
    imported: int
    unchanged: int
    superseded: int
    skipped: int
    errors: tuple[str, ...]


def default_legacy_character_id() -> str:
    return _default_legacy_character_id()


def v1_import_scope(character_id: str) -> str:
    """Keep V1 numeric ids isolated when later characters have their own JSON."""
    return IMPORT_SCOPE if character_id == default_legacy_character_id() else f"{IMPORT_SCOPE}:{character_id}"


def import_v1_memories(
    store: MemoryV2Store,
    source_dir: str | Path,
    *,
    character_id: str | None = None,
    display_name: str | None = None,
    source_scope: str | None = None,
) -> ProductionImportResult:
    """Import valid V1 memory rows idempotently and non-destructively."""
    source = Path(source_dir).resolve()
    path = source / "memories.json"
    character_id = character_id or default_legacy_character_id()
    source_scope = source_scope or v1_import_scope(character_id)
    name = display_name or _legacy_display_name(source) or "Default character"
    repository = MemoryV2Repository(store)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return ProductionImportResult(character_id, 0, 0, 0, 0, (f"memories.json unavailable: {type(error).__name__}",))
    if not isinstance(raw, list):
        return ProductionImportResult(character_id, 0, 0, 0, 0, ("memories.json root is not a list",))
    repository.ensure_character(character_id, name, legacy_config_key="characters/default")

    imported = unchanged = superseded = skipped = 0
    errors: list[str] = []
    for index, legacy in enumerate(raw):
        parsed, error = _parse_legacy_record(legacy)
        if error:
            skipped += 1
            errors.append(f"record {index}: {error}")
            continue
        try:
            outcome = _import_record(store, repository, character_id, parsed, source_scope=source_scope)
        except Exception as exception:
            skipped += 1
            errors.append(f"record {index}: {type(exception).__name__}")
            continue
        if outcome == "imported":
            imported += 1
        elif outcome == "superseded":
            imported += 1
            superseded += 1
        else:
            unchanged += 1
    store.ensure_fts()
    return ProductionImportResult(character_id, imported, unchanged, superseded, skipped, tuple(errors))


def export_v2_json(store: MemoryV2Store, destination: str | Path, *, character_id: str | None = None) -> Path:
    """Write a deterministic JSON export without changing SQLite or V1."""
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = MemoryV2Repository(store).export(character_id=character_id)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(destination)
    return destination


def _legacy_display_name(source: Path) -> str | None:
    try:
        character = json.loads((source / "characters/default/character.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    name = character.get("name") if isinstance(character, dict) else None
    return str(name).strip() if isinstance(name, str) and name.strip() else None


def _parse_legacy_record(value: object) -> tuple[dict | None, str | None]:
    if not isinstance(value, dict):
        return None, "not an object"
    legacy_id = value.get("id")
    content = value.get("content")
    if isinstance(legacy_id, bool) or not isinstance(legacy_id, int) or legacy_id < 1:
        return None, "invalid id"
    if not isinstance(content, str) or not content.strip():
        return None, "missing content"
    try:
        importance = int(value.get("importance", 5))
    except (TypeError, ValueError):
        importance = 5
    created, precision = _legacy_timestamp(value.get("created"))
    updated, _ = _legacy_timestamp(value.get("updated")) if value.get("updated") is not None else (created, precision)
    return {
        "legacy_id": legacy_id,
        "content": content.strip(),
        "memory_type": normalize_memory_type(value.get("category")),
        "importance": max(1, min(10, importance)),
        "created_at_us": created,
        "updated_at_us": updated,
        "temporal_precision": precision,
        "metadata": value,
    }, None


def _legacy_timestamp(value: object) -> tuple[int, str]:
    if not isinstance(value, str) or not value.strip():
        return utc_now_us(), "legacy_unknown"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return utc_now_us(), "legacy_invalid_timestamp"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1_000_000), "legacy_naive_assumed_utc"
    return int(parsed.timestamp() * 1_000_000), "instant"


def shadow_v1_mutation(
    store: MemoryV2Store,
    *,
    character_id: str,
    display_name: str,
    kind: str,
    record: dict | None,
    previous: dict | None = None,
) -> str:
    """Mirror an already-successful V1 mutation without affecting V1 authority."""
    repository = MemoryV2Repository(store)
    repository.ensure_character(character_id, display_name, legacy_config_key="characters/default")
    source_scope = v1_import_scope(character_id)
    if kind == "removed":
        source = previous or record
        if not isinstance(source, dict) or not isinstance(source.get("id"), int):
            return "ignored"
        mapped = store.connection.execute(
            "SELECT claim_id FROM v1_import_records WHERE source_scope=? AND legacy_memory_id=?",
            (source_scope, source["id"]),
        ).fetchone()
        if mapped is None:
            return "missing"
        repository.archive(character_id, mapped["claim_id"], reason="v1_removed")
        return "archived"
    parsed, error = _parse_legacy_record(record)
    if error:
        raise ValueError(f"V1 mutation is not importable: {error}")
    outcome = _import_record(store, repository, character_id, parsed, source_scope=source_scope)
    store.ensure_fts()
    return outcome


def _record_fingerprint(record: dict) -> str:
    # These are V1's mutable semantic fields. A changed record becomes a new
    # append-only V2 claim rather than altering historical V2 content.
    material = {
        "category": record["memory_type"],
        "content": record["content"],
        "importance": record["importance"],
    }
    return hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _import_record(
    store: MemoryV2Store,
    repository: MemoryV2Repository,
    character_id: str,
    record: dict,
    *,
    source_scope: str,
) -> str:
    content_hash = _record_fingerprint(record)
    previous = store.connection.execute(
        "SELECT claim_id, content_sha256 FROM v1_import_records WHERE source_scope=? AND legacy_memory_id=?",
        (source_scope, record["legacy_id"]),
    ).fetchone()
    if previous is not None and previous["content_sha256"] == content_hash:
        return "unchanged"

    claim_id = str(uuid.uuid5(LEGACY_CHARACTER_NAMESPACE, f"production:memory:{source_scope}:{record['legacy_id']}:{content_hash}"))
    event_id = str(uuid.uuid5(LEGACY_CHARACTER_NAMESPACE, f"production:memory-evidence:{source_scope}:{record['legacy_id']}:{content_hash}"))
    with store.transaction():
        existing = store.connection.execute(
            "SELECT claim_id FROM claims WHERE character_id=? AND claim_id=?", (character_id, claim_id)
        ).fetchone()
        if existing is None:
            store.connection.execute(
                """INSERT INTO claims(character_id, claim_id, claim_type, assertion_scope, subject_key, content,
                   importance, confidence, valid_from_us, valid_to_us, temporal_precision, temporal_expression,
                   provenance_state, curator_name, curator_version, curator_policy_version, created_at_us,
                   legacy_metadata_json, updated_at_us, truth_scope_id)
                   VALUES (?, ?, ?, 'user_fact', NULL, ?, ?, NULL, ?, NULL, ?, NULL,
                   'legacy_unverified', 'memory_v1_import', '2', 'legacy_tolerant_import', ?, ?, ?, ?)""",
                (character_id, claim_id, record["memory_type"], record["content"], record["importance"],
                 record["created_at_us"], record["temporal_precision"], record["created_at_us"],
                 json.dumps(record["metadata"], ensure_ascii=False, sort_keys=True), record["updated_at_us"],
                 store.default_truth_scope_id(character_id)),
            )
        sequence = store.connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE character_id=?", (character_id,)
        ).fetchone()[0]
        store.connection.execute(
            """INSERT OR IGNORE INTO events VALUES (?, ?, ?, 'legacy_memory_record', 'system', ?, NULL, NULL,
               'legacy_import', NULL, '{}', 1, 'legacy_v1_import', ?, NULL, 'active', NULL)""",
            (character_id, event_id, sequence, record["created_at_us"], f"memories.json#{record['legacy_id']}"),
        )
        store.connection.execute(
            """INSERT OR IGNORE INTO claim_evidence VALUES (?, ?, ?, 'legacy_memory_record', NULL, NULL, NULL,
               NULL, NULL, ?)""",
            (character_id, claim_id, event_id, utc_now_us()),
        )
        if previous is not None and previous["claim_id"] != claim_id:
            store.connection.execute(
                "INSERT OR IGNORE INTO claim_relations(character_id, from_claim_id, to_claim_id, relation_type, created_at_us) VALUES (?, ?, ?, 'supersedes', ?)",
                (character_id, claim_id, previous["claim_id"], utc_now_us()),
            )
            store.connection.execute(
                "INSERT INTO claim_status_events(character_id, claim_id, status, reason, source_event_id, actor_kind, created_at_us) VALUES (?, ?, 'superseded', 'legacy_v1_update', NULL, 'system', ?)",
                (character_id, previous["claim_id"], utc_now_us()),
            )
        store.connection.execute(
            """INSERT INTO v1_import_records(source_scope, legacy_memory_id, claim_id, content_sha256, imported_at_us)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(source_scope, legacy_memory_id) DO UPDATE SET claim_id=excluded.claim_id,
                 content_sha256=excluded.content_sha256, imported_at_us=excluded.imported_at_us""",
            (source_scope, record["legacy_id"], claim_id, content_hash, utc_now_us()),
        )
    return "superseded" if previous is not None else "imported"
