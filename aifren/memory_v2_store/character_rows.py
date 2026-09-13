"""Closed SQLite row layouts shared by production copying and disposable QA.

This module has no application, registry or disposable-fixture policy. Callers
own allowed versions, row ownership predicates, transaction/paths and work bounds.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import sqlite3
import struct
from typing import Callable, Iterable, Mapping, Protocol, Sequence

from .store import SCHEMA_VERSION


class SchemaLayoutError(RuntimeError):
    """The database does not match a reviewed row layout."""


class RowBudget(Protocol):
    def add(self, row: Sequence[object]) -> None: ...


@dataclass(frozen=True)
class _TableSpec:
    name: str
    columns: tuple[str, ...]
    order_by: tuple[str, ...]


_TABLE_SPECS: tuple[_TableSpec, ...] = (
    _TableSpec("characters", (
        "character_id", "display_name", "created_at_us", "archived_at_us",
        "legacy_config_key", "metadata_json", "active_truth_scope_id",
    ), ("character_id",)),
    _TableSpec("truth_scopes", (
        "character_id", "truth_scope_id", "scope_kind", "label", "status",
        "created_at_us", "last_active_at_us",
    ), ("character_id", "truth_scope_id")),
    _TableSpec("events", (
        "character_id", "event_id", "sequence", "event_type", "actor_kind",
        "recorded_at_us", "occurred_from_us", "occurred_to_us", "temporal_precision",
        "content_text", "payload_json", "payload_schema", "source_origin",
        "source_reference", "content_sha256", "redaction_state", "redacted_at_us",
    ), ("character_id", "sequence", "event_id")),
    _TableSpec("claims", (
        "character_id", "claim_id", "claim_type", "assertion_scope", "subject_key",
        "content", "importance", "confidence", "valid_from_us", "valid_to_us",
        "temporal_precision", "temporal_expression", "provenance_state", "curator_name",
        "curator_version", "curator_policy_version", "created_at_us",
        "legacy_metadata_json", "updated_at_us", "truth_scope_id",
    ), ("character_id", "claim_id")),
    _TableSpec("historical_evidence", (
        "character_id", "claim_id", "event_id", "canonical_index",
        "canonical_record_id", "speaker_role", "speech_act", "source_class",
        "scope_state", "truth_scope_id", "source_content_sha256",
        "projection_version", "retrieval_eligible",
    ), ("character_id", "canonical_index", "claim_id")),
    _TableSpec("summaries", (
        "character_id", "summary_id", "summary_level", "content", "source_count",
        "provenance_state", "generator_name", "generator_version",
        "legacy_metadata_json", "created_at_us",
    ), ("character_id", "summary_id")),
    _TableSpec("active_scene_subjects", (
        "character_id", "scene_subject_id", "introduced_at_us", "retired_at_us",
        "introduced_event_id", "introduced_excerpt_start_cp", "introduced_excerpt_end_cp",
        "introduced_excerpt_hash", "retired_event_id", "retired_excerpt_start_cp",
        "retired_excerpt_end_cp", "retired_excerpt_hash", "truth_scope_id",
        "last_referenced_at_us", "identity_strength",
    ), ("character_id", "scene_subject_id")),
    _TableSpec("active_scene_relations", (
        "character_id", "relation_id", "truth_scope_id", "target_kind", "target_actor",
        "facet", "side", "predicate", "cause_kind", "cause", "cause_subject_id",
        "semantic_family", "quantity", "effect_state", "valid_from_us", "valid_to_us", "locus",
    ), ("character_id", "relation_id")),
    _TableSpec("claim_evidence", (
        "character_id", "claim_id", "event_id", "evidence_role", "excerpt_start_cp",
        "excerpt_end_cp", "excerpt_hash", "evidence_strength", "curator_confidence",
        "created_at_us",
    ), ("character_id", "claim_id", "event_id", "evidence_role")),
    _TableSpec("claim_relations", (
        "relation_id", "character_id", "from_claim_id", "to_claim_id",
        "relation_type", "created_at_us",
    ), ("relation_id",)),
    _TableSpec("claim_status_events", (
        "status_event_id", "character_id", "claim_id", "status", "reason",
        "source_event_id", "actor_kind", "created_at_us",
    ), ("status_event_id",)),
    _TableSpec("claim_embeddings", (
        "character_id", "claim_id", "provider", "model", "model_version", "dimensions",
        "dtype", "normalized", "preprocessing_fingerprint", "content_fingerprint",
        "source_content_sha256", "vector_blob", "generated_at_us", "state",
        "failure_reason",
    ), ("character_id", "claim_id", "provider", "model", "preprocessing_fingerprint")),
    _TableSpec("summary_source_ranges", (
        "character_id", "summary_id", "start_sequence", "end_sequence",
    ), ("character_id", "summary_id", "start_sequence", "end_sequence")),
    _TableSpec("truth_scope_events", (
        "scope_event_id", "character_id", "truth_scope_id", "operation", "event_id",
        "excerpt_start_cp", "excerpt_end_cp", "excerpt_hash", "created_at_us",
    ), ("scope_event_id",)),
    _TableSpec("open_threads", (
        "character_id", "thread_id", "thread_kind", "participant_scope", "description",
        "temporal_anchor", "status", "opened_at_us", "last_mentioned_at_us",
        "closed_at_us", "opened_event_id", "last_event_id", "last_excerpt_start_cp",
        "last_excerpt_end_cp", "last_excerpt_hash", "closed_event_id", "truth_scope_id",
    ), ("character_id", "thread_id")),
    _TableSpec("active_scene_relation_events", (
        "relation_event_id", "character_id", "relation_id", "operation", "event_id",
        "excerpt_start_cp", "excerpt_end_cp", "excerpt_hash", "created_at_us",
    ), ("relation_event_id",)),
    _TableSpec("active_scene_subject_lifecycle_events", (
        "lifecycle_event_id", "character_id", "scene_subject_id", "operation",
        "event_id", "created_at_us",
    ), ("lifecycle_event_id",)),
    _TableSpec("proactive_checkins", (
        "character_id", "checkin_id", "reason_kind", "thread_id", "displayed_at_us",
        "conversation_index", "assistant_content_sha256", "responded_at_us",
    ), ("character_id", "checkin_id")),
    _TableSpec("proactive_attempts", (
        "character_id", "attempt_id", "reason_kind", "thread_id", "attempted_at_us",
        "outcome",
    ), ("character_id", "attempt_id")),
    _TableSpec("canonical_observation_progress", (
        "character_id", "source_key", "consumer", "policy_version", "next_index",
        "prefix_digest", "state", "reason", "updated_at_us",
    ), ("character_id", "source_key", "consumer", "policy_version")),
    _TableSpec("canonical_observation_dispositions", (
        "character_id", "source_key", "consumer", "policy_version", "source_index",
        "source_digest", "truth_scope_id", "reason", "witness_event_id", "decided_at_us",
    ), ("character_id", "source_key", "consumer", "policy_version", "source_index")),
)

_V1_IMPORT_SPEC = _TableSpec("v1_import_records", (
    "source_scope", "legacy_memory_id", "claim_id", "content_sha256", "imported_at_us",
), ("source_scope", "legacy_memory_id"))

_HISTORICAL_CHECKPOINT_SPEC = _TableSpec(
    "historical_evidence_checkpoints",
    (
        "character_id", "policy_version", "source_key", "next_index",
        "source_prefix_digest", "source_archive_digest", "source_record_count",
        "state", "updated_at_us",
    ),
    ("character_id",),
)

_NON_COPIED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "database_meta": ("key", "value"),
    "schema_migrations": ("version", "applied_at_us"),
    "claims_fts": ("character_id", "claim_id", "searchable_text"),
    "historical_evidence_fts": ("character_id", "claim_id", "searchable_text"),
    "retrieval_telemetry": (
        "telemetry_id", "recorded_at_us", "character_id", "query_sha256",
        "v1_ids_json", "v2_ids_json", "overlap_count", "v1_abstained",
        "v2_abstained", "v1_latency_ms", "v2_latency_ms", "error_kind",
        "retrieval_strategy",
    ),
    "historical_evidence_checkpoints": (
        "character_id", "policy_version", "source_key", "next_index",
        "source_prefix_digest", "source_archive_digest", "source_record_count",
        "state", "updated_at_us",
    ),
}

_FTS_SHADOW_TABLES = {
    "claims_fts_config", "claims_fts_content", "claims_fts_data",
    "claims_fts_docsize", "claims_fts_idx",
    "historical_evidence_fts_config", "historical_evidence_fts_content",
    "historical_evidence_fts_data", "historical_evidence_fts_docsize",
    "historical_evidence_fts_idx",
}


def _quoted(identifier: str) -> str:
    # Identifiers come exclusively from the constants above. Quoting keeps the
    # SQL readable and makes accidental future keyword collisions harmless.
    return '"' + identifier.replace('"', '""') + '"'


def _schema_columns(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    return tuple(str(row[1]) for row in connection.execute(
        f"PRAGMA table_info({_quoted(table)})",
    ).fetchall())


def _validate_exact_schema(
    connection: sqlite3.Connection,
    *,
    label: str,
    allowed_versions: tuple[int, ...] = (SCHEMA_VERSION,),
    error_type: type[RuntimeError] = SchemaLayoutError,
) -> int:
    row = connection.execute(
        "SELECT value FROM database_meta WHERE key='schema_version'",
    ).fetchone()
    try:
        version = int(row[0]) if row is not None else -1
    except (TypeError, ValueError):
        version = -1
    if version not in allowed_versions:
        expected = "/".join(str(value) for value in allowed_versions)
        raise error_type(f"{label} Memory V2 schema is not version {expected}")

    expected_columns = {spec.name: spec.columns for spec in _TABLE_SPECS}
    expected_columns[_V1_IMPORT_SPEC.name] = _V1_IMPORT_SPEC.columns
    expected_columns.update(_NON_COPIED_COLUMNS)
    fts_shadow_tables = set(_FTS_SHADOW_TABLES)
    if version < 21:
        expected_columns.pop("canonical_observation_dispositions")
    if version < 20:
        expected_columns["active_scene_relations"] = expected_columns["active_scene_relations"][:-1]
    if version < 19:
        expected_columns.pop("canonical_observation_progress")
    if version == 17:
        # Schema 18 adds the historical occurrence lane and its checkpoint.
        # Reading exact schema 17 avoids migrating the live source merely to
        # build a disposable clone.
        expected_columns.pop("historical_evidence")
        expected_columns.pop("historical_evidence_checkpoints")
        expected_columns.pop("historical_evidence_fts")
        fts_shadow_tables = {
            value for value in fts_shadow_tables
            if not value.startswith("historical_evidence_fts_")
        }
    expected_tables = set(expected_columns) | fts_shadow_tables
    actual_tables = {
        str(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'",
        ).fetchall()
        if not str(row[0]).startswith("sqlite_")
    }
    if actual_tables != expected_tables:
        raise error_type(f"{label} Memory V2 schema has an unknown or missing table")
    for table, expected in expected_columns.items():
        if _schema_columns(connection, table) != expected:
            raise error_type(f"{label} Memory V2 table layout differs for {table}")
    latest_migration = connection.execute(
        "SELECT MAX(version) FROM schema_migrations",
    ).fetchone()[0]
    if int(latest_migration or -1) != version:
        raise error_type(f"{label} Memory V2 migration history is inconsistent")
    return version


def _digest_value(digest: "hashlib._Hash", value: object) -> None:
    if value is None:
        digest.update(b"n")
        return
    if isinstance(value, bytes):
        encoded = value
        marker = b"b"
    elif isinstance(value, str):
        encoded = value.encode("utf-8")
        marker = b"s"
    elif isinstance(value, bool):
        encoded = b"1" if value else b"0"
        marker = b"o"
    elif isinstance(value, int):
        encoded = str(value).encode("ascii")
        marker = b"i"
    elif isinstance(value, float):
        encoded = struct.pack(">d", value)
        marker = b"f"
    else:
        encoded = bytes(value) if isinstance(value, memoryview) else repr(value).encode("utf-8")
        marker = b"x"
    digest.update(marker)
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)


def _digest_rows(rows: Iterable[Sequence[object]]) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        digest.update(b"r")
        for value in row:
            _digest_value(digest, value)
        count += 1
    return count, digest.hexdigest()


def _copy_table(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    spec: _TableSpec,
    *,
    where_sql: str,
    arguments: Sequence[object],
    batch_rows: int,
    budget: RowBudget,
    check_cancel: Callable[[], None] | None = None,
) -> tuple[int, str]:
    columns = ", ".join(_quoted(value) for value in spec.columns)
    order = ", ".join(_quoted(value) for value in spec.order_by)
    source_columns = columns
    if spec.name == "active_scene_relations" and "locus" not in _schema_columns(source, spec.name):
        # Only exact validated schemas 17-19 reach this compatibility path.
        source_columns = ", ".join(_quoted(value) if value != "locus" else 'NULL AS "locus"'
                                   for value in spec.columns)
    cursor = source.execute(
        f"SELECT {source_columns} FROM {_quoted(spec.name)} WHERE {where_sql} ORDER BY {order}",
        tuple(arguments),
    )
    placeholders = ",".join("?" for _ in spec.columns)
    insert = f"INSERT INTO {_quoted(spec.name)} ({columns}) VALUES ({placeholders})"
    digest = hashlib.sha256()
    count = 0
    while True:
        if check_cancel is not None:
            check_cancel()
        rows = cursor.fetchmany(batch_rows)
        if not rows:
            break
        values = [tuple(row[index] for index in range(len(spec.columns))) for row in rows]
        for row in values:
            budget.add(row)
            digest.update(b"r")
            for value in row:
                _digest_value(digest, value)
        target.executemany(insert, values)
        count += len(values)
    return count, digest.hexdigest()


def _target_digest(
    connection: sqlite3.Connection,
    spec: _TableSpec,
    *,
    where_sql: str,
    arguments: Sequence[object],
) -> tuple[int, str]:
    columns = ", ".join(_quoted(value) for value in spec.columns)
    order = ", ".join(_quoted(value) for value in spec.order_by)
    rows = connection.execute(
        f"SELECT {columns} FROM {_quoted(spec.name)} WHERE {where_sql} ORDER BY {order}",
        tuple(arguments),
    )
    return _digest_rows(tuple(row) for row in rows)
