"""Bounded canonical-conversation evidence indexing for disposable V2 clones.

This lane records that a canonical message occurred. It does not assert that
the message's proposition is currently true, does not consult Memory V1, and
does not relabel legacy claims. Unknown historical Truth Scope remains an
explicit unknown state.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import stat
from typing import Mapping, Sequence
import uuid

from character_registry import CharacterRegistry, CharacterRegistryError, REGISTRY_RELATIVE_PATH
from conversation.truth_scope import parse_canonical_truth_scope
from memory_v2_episode_compaction import canonical_record_id
from memory_v2_shadow_writer import MemoryV2ShadowWriter
from memory_v2_store.store import parse_timestamp_us, utc_now_us


HISTORICAL_EVIDENCE_POLICY_VERSION = "canonical_historical_occurrence_v2"
HISTORICAL_EVIDENCE_PROJECTION_VERSION = 1
DEFAULT_PAGE_RECORDS = 128
MAX_PAGE_RECORDS = 512
MAX_ARCHIVE_RECORDS = 200_000
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_SOURCE_CONTENT_CHARACTERS = 100_000
MAX_SEARCHABLE_CONTENT_CHARACTERS = 1_200
STAGED_DISPOSABLE_SCHEMA = "aifren.memory_v2.staged_disposable"
STAGED_DISPOSABLE_VERSION = 1
STAGED_DISPOSABLE_MARKER = ".aifren-memory-v2-staged-disposable.json"
STAGED_DATABASE_DIRECTORY = Path("data") / "memory_v2_staging"
_HISTORICAL_NAMESPACE = uuid.UUID("0d1fa3c4-6679-5c9e-b0a8-729dc7ab8ad1")

_LEGACY_KEYS = frozenset({"role", "content", "timestamp"})
_SCOPED_KEYS = frozenset({"role", "content", "timestamp", "truth_scope"})
_HEARING_KEYS = frozenset({"role", "content", "timestamp", "truth_scope", "semantic_admission"})
_SCENE_UI_KEYS = frozenset({"role", "content", "timestamp", "truth_scope", "origin"})


class HistoricalEvidenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class CanonicalHistoricalEvidence:
    canonical_index: int
    canonical_record_id: str
    speaker_role: str
    speech_act: str
    source_class: str
    scope_state: str
    truth_scope_id: str | None
    source_content_sha256: str
    recorded_at_us: int
    timestamp: str
    searchable_text: str
    retrieval_eligible: bool


@dataclass(frozen=True)
class HistoricalEvidenceDecision:
    evidence: CanonicalHistoricalEvidence | None
    reason: str

    @property
    def accepted(self) -> bool:
        return self.evidence is not None


@dataclass(frozen=True)
class HistoricalEvidencePage:
    state: str
    start_index: int
    next_index: int
    source_record_count: int
    processed_record_count: int
    indexed_record_count: int
    created_claim_count: int
    unchanged_claim_count: int
    searchable_user_count: int
    unknown_scope_count: int
    rejection_counts: tuple[tuple[str, int], ...]
    source_prefix_digest: str

    @property
    def complete(self) -> bool:
        return self.state == "complete"


def staged_disposable_marker_payload(
    application_dir: str | Path,
    character_id: str,
    database_path: str | Path,
    conversation_file: str | Path,
) -> dict[str, object]:
    root = Path(application_dir).resolve()
    target = Path(database_path).resolve()
    source = Path(conversation_file).resolve()
    try:
        relative = target.relative_to(root).as_posix()
        source_relative = source.relative_to(root).as_posix()
    except ValueError as error:
        raise HistoricalEvidenceError(
            "staged evidence database is outside the disposable application root",
        ) from error
    return {
        "schema": STAGED_DISPOSABLE_SCHEMA,
        "version": STAGED_DISPOSABLE_VERSION,
        "character_id": str(character_id),
        "database_relative_path": relative,
        "conversation_relative_path": source_relative,
    }


def validate_staged_disposable_target(
    application_dir: str | Path,
    character_id: str,
    database_path: str | Path,
    conversation_file: str | Path,
) -> Path:
    """Prove positive clone ownership before opening SQLite sidecars."""
    root = Path(application_dir).resolve()
    lexical_stage = root / STAGED_DATABASE_DIRECTORY
    target_input = Path(database_path)
    if target_input.is_symlink():
        raise HistoricalEvidenceError("staged evidence database cannot be a symlink")
    target = target_input.resolve()
    if target.suffix != ".sqlite3":
        raise HistoricalEvidenceError("staged evidence database must be a SQLite file")
    source_input = Path(conversation_file)
    if source_input.is_symlink():
        raise HistoricalEvidenceError("canonical archive cannot be a symlink")
    source = source_input.resolve()
    try:
        source.relative_to(root)
    except ValueError as error:
        raise HistoricalEvidenceError("canonical archive is outside the staged clone") from error
    if not source.is_file():
        raise HistoricalEvidenceError("canonical archive is missing from the staged clone")
    source_metadata = source.stat(follow_symlinks=False)
    if not stat.S_ISREG(source_metadata.st_mode) or source_metadata.st_nlink != 1:
        raise HistoricalEvidenceError("canonical archive has unsafe filesystem ownership")

    registry_path = root / REGISTRY_RELATIVE_PATH
    if registry_path.is_symlink() or not registry_path.is_file():
        raise HistoricalEvidenceError("staged clone character registry is missing or unsafe")
    registry_metadata = registry_path.stat(follow_symlinks=False)
    if not stat.S_ISREG(registry_metadata.st_mode) or registry_metadata.st_nlink != 1:
        raise HistoricalEvidenceError("staged clone character registry is unsafe")
    try:
        registry = CharacterRegistry(root)
        character = registry.get(character_id)
        registered_paths = registry.runtime_paths(character_id) if character is not None else None
        registered_source = (
            registered_paths["conversation"].resolve() if registered_paths is not None else None
        )
    except (CharacterRegistryError, OSError) as error:
        raise HistoricalEvidenceError("staged clone character registry is malformed") from error
    if character is None or registered_source != source:
        raise HistoricalEvidenceError("canonical archive is not owned by the selected staged character")
    local_target = character.storage_layout == "local" and target == registered_paths["memory_v2"].resolve()
    if character.storage_layout == "local":
        if not local_target or character.storage_status != "ready" or character.operation:
            raise HistoricalEvidenceError("local staged evidence must use its ready registered database")
        if (len(registry.list_characters()) != 1 or registry.active().character_id != character_id):
            raise HistoricalEvidenceError("local staged clone must contain only its selected active character")
    elif (target.parent != lexical_stage or not lexical_stage.is_dir()
          or lexical_stage.is_symlink() or lexical_stage.resolve() != lexical_stage):
        raise HistoricalEvidenceError("owned staged evidence directory is missing or unsafe")

    if target.exists():
        metadata = target.stat(follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise HistoricalEvidenceError("staged evidence database has unsafe filesystem ownership")
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(f"{target}{suffix}")
        if sidecar.is_symlink():
            raise HistoricalEvidenceError("staged evidence database sidecar cannot be a symlink")
        if sidecar.exists():
            metadata = sidecar.stat(follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise HistoricalEvidenceError("staged evidence sidecar has unsafe ownership")

    marker = root / STAGED_DISPOSABLE_MARKER
    if marker.is_symlink() or not marker.is_file():
        raise HistoricalEvidenceError("staged disposable clone marker is missing or unsafe")
    marker_metadata = marker.stat(follow_symlinks=False)
    if not stat.S_ISREG(marker_metadata.st_mode) or marker_metadata.st_nlink != 1:
        raise HistoricalEvidenceError("staged disposable clone marker is unsafe")
    try:
        marker_bytes = marker.read_bytes()
        if len(marker_bytes) > 4_096:
            raise ValueError("marker exceeds bound")
        marker_value = json.loads(marker_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise HistoricalEvidenceError("staged disposable clone marker is malformed") from error
    expected = staged_disposable_marker_payload(root, character_id, target, source)
    if marker_value != expected:
        raise HistoricalEvidenceError(
            "staged disposable marker does not bind this character and database",
        )

    live_database = registered_paths["memory_v2"].resolve()
    # An exactly marked one-character local clone uses the ordinary local path.
    # Legacy QA remains separate from its live shared DB even with a marker.
    if not local_target and (target == live_database or (
        target.exists() and live_database.exists() and target.samefile(live_database)
    )):
        raise HistoricalEvidenceError("historical evidence indexing refuses the live V2 database")
    return target


def open_staged_historical_evidence_writer(
    application_dir: str | Path,
    character_id: str,
    database_path: str | Path,
) -> MemoryV2ShadowWriter:
    root = Path(application_dir).resolve()
    try:
        registry = CharacterRegistry(root)
        character = registry.get(character_id)
        if character is None:
            raise HistoricalEvidenceError("character is absent from the staged clone registry")
        paths = registry.runtime_paths(character.character_id)
    except (CharacterRegistryError, OSError) as error:
        raise HistoricalEvidenceError("staged clone character registry is malformed") from error
    target = validate_staged_disposable_target(
        root, character.character_id, database_path, paths["conversation"],
    )
    writer = MemoryV2ShadowWriter(
        root, character_id=character.character_id,
        display_name=character.display_name, memory_file=paths["memory"],
        database_path=target,
    )
    writer._historical_evidence_attestation = (
        str(root), character.character_id, str(target), str(paths["conversation"].resolve()),
    )
    return writer


def _speech_act(role: str, content: str) -> str:
    stripped = content.strip()
    if role != "user" or (stripped.startswith("*") and stripped.endswith("*")):
        return "other"
    if "?" in stripped or re.match(
        r"^(?:who|what|when|where|why|how|which|do|does|did|can|could|should|would|is|are|am)\b",
        stripped, re.IGNORECASE,
    ):
        return "question"
    return "assertion"


def _source_class(record: Mapping[str, object]) -> tuple[str, bool] | None:
    keys = frozenset(record)
    if keys in {_LEGACY_KEYS, _SCOPED_KEYS}:
        return "ordinary_conversation", True
    if keys == _HEARING_KEYS:
        admission = record.get("semantic_admission")
        if admission == {"channel": "hearing", "state": "unavailable", "understood": False}:
            return "hearing_unavailable", False
        return None
    if keys == _SCENE_UI_KEYS:
        from scene_ui_event import valid_scene_ui_origin
        if valid_scene_ui_origin(record.get("origin")):
            return "generated_scene_ui", False
        return None
    return None


def _searchable_projection(role: str, speech_act: str, content: str) -> str:
    compact = " ".join(content.split())
    if len(compact) > MAX_SEARCHABLE_CONTENT_CHARACTERS:
        compact = compact[: MAX_SEARCHABLE_CONTENT_CHARACTERS - 1].rstrip() + "…"
    kind = "question" if speech_act == "question" else (
        "statement" if speech_act == "assertion" else "interaction"
    )
    return f"Historical {role} {kind}: {compact}"


def resolve_historical_evidence(
    records: Sequence[object],
    index: int,
    *,
    valid_scope_ids: set[str] | frozenset[str],
) -> HistoricalEvidenceDecision:
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(records):
        return HistoricalEvidenceDecision(None, "invalid_canonical_index")
    record = records[index]
    if not isinstance(record, Mapping):
        return HistoricalEvidenceDecision(None, "malformed_record")
    role = record.get("role")
    content = record.get("content")
    timestamp = record.get("timestamp")
    if role not in {"user", "assistant"}:
        return HistoricalEvidenceDecision(None, "unsupported_speaker")
    if not isinstance(content, str) or not content.strip() or len(content) > MAX_SOURCE_CONTENT_CHARACTERS:
        return HistoricalEvidenceDecision(None, "invalid_source_content")
    if not isinstance(timestamp, str) or not timestamp.strip():
        return HistoricalEvidenceDecision(None, "invalid_source_timestamp")
    try:
        recorded_at_us = parse_timestamp_us(timestamp)
    except (TypeError, ValueError, OverflowError):
        recorded_at_us = None
    if recorded_at_us is None:
        return HistoricalEvidenceDecision(None, "invalid_source_timestamp")
    source = _source_class(record)
    if source is None:
        return HistoricalEvidenceDecision(None, "unsupported_source_class")
    source_class, semantically_available = source

    scope = parse_canonical_truth_scope(record, valid_scope_ids=valid_scope_ids)
    if scope.kind == "legacy_untagged":
        scope_state, truth_scope_id = "unknown_scope", None
    elif scope.is_valid and scope.kind in {"real_world", "scenario"}:
        scope_state, truth_scope_id = scope.kind, scope.scope_id
    else:
        return HistoricalEvidenceDecision(None, "invalid_truth_scope")
    speech_act = _speech_act(str(role), content)
    record_id = canonical_record_id(index, record)
    return HistoricalEvidenceDecision(CanonicalHistoricalEvidence(
        canonical_index=index,
        canonical_record_id=record_id,
        speaker_role=str(role),
        speech_act=speech_act,
        source_class=source_class,
        scope_state=scope_state,
        truth_scope_id=truth_scope_id,
        source_content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        recorded_at_us=int(recorded_at_us),
        timestamp=timestamp,
        searchable_text=_searchable_projection(str(role), speech_act, content),
        retrieval_eligible=bool(role == "user" and semantically_available),
    ), "accepted")


def _records_digest(records: Sequence[object], end: int) -> str:
    payload = json.dumps(
        list(records[:end]), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def persist_historical_occurrence(store, character_id, evidence):
    """Persist one resolved exact occurrence with the established stable IDs."""
    index = evidence.canonical_index
    source_reference = f"conversation.json#{index}"
    event_id = str(uuid.uuid5(
        _HISTORICAL_NAMESPACE,
        f"event:{character_id}:{evidence.canonical_record_id}",
    ))
    claim_id = str(uuid.uuid5(
        _HISTORICAL_NAMESPACE,
        f"claim:{character_id}:{evidence.canonical_record_id}",
    ))
    was_created = store.add_historical_evidence(
        character_id, claim_id,
        event_id=event_id,
        canonical_index=index,
        canonical_record_id=evidence.canonical_record_id,
        speaker_role=evidence.speaker_role,
        speech_act=evidence.speech_act,
        source_class=evidence.source_class,
        scope_state=evidence.scope_state,
        truth_scope_id=evidence.truth_scope_id,
        source_content_sha256=evidence.source_content_sha256,
        searchable_text=evidence.searchable_text,
        recorded_at_us=evidence.recorded_at_us,
        source_reference=source_reference,
        retrieval_eligible=evidence.retrieval_eligible,
        projection_version=HISTORICAL_EVIDENCE_PROJECTION_VERSION,
    )
    return claim_id, was_created


class HistoricalEvidenceIndexer:
    """Index one selected canonical archive in bounded, atomic pages."""

    def __init__(
        self,
        writer: MemoryV2ShadowWriter,
        conversation_file: str | Path,
        *,
        source_key: str = "conversation.json",
        confirm_staged_disposable: bool = False,
    ) -> None:
        if confirm_staged_disposable is not True:
            raise HistoricalEvidenceError(
                "historical evidence indexing requires staged-disposable confirmation",
            )
        self.writer = writer
        self.store = writer.store
        self.character_id = writer.character_id
        target = validate_staged_disposable_target(
            writer.application_dir, self.character_id, writer.database_path, conversation_file,
        )
        self.conversation_file = Path(conversation_file).resolve()
        expected_attestation = (
            str(writer.application_dir), self.character_id, str(target),
            str(self.conversation_file),
        )
        if getattr(writer, "_historical_evidence_attestation", None) != expected_attestation:
            raise HistoricalEvidenceError("staged evidence writer was not opened through its factory")
        if not source_key or len(source_key) > 120 or any(value in source_key for value in "\r\n\t"):
            raise ValueError("historical evidence source key is invalid")
        self.source_key = source_key

    def _read_archive(self) -> tuple[bytes, list[object]]:
        raw = self.conversation_file.read_bytes()
        if len(raw) > MAX_ARCHIVE_BYTES:
            raise HistoricalEvidenceError("canonical archive exceeds evidence indexing bound")
        try:
            records = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise HistoricalEvidenceError("canonical archive is malformed") from error
        if not isinstance(records, list) or len(records) > MAX_ARCHIVE_RECORDS:
            raise HistoricalEvidenceError("canonical archive shape exceeds evidence indexing bound")
        return raw, records

    def _checkpoint(self):
        return self.store.connection.execute(
            "SELECT * FROM historical_evidence_checkpoints WHERE character_id=?",
            (self.character_id,),
        ).fetchone()

    def run_page(self, *, maximum_records: int = DEFAULT_PAGE_RECORDS) -> HistoricalEvidencePage:
        if (
            isinstance(maximum_records, bool) or not isinstance(maximum_records, int)
            or not 1 <= maximum_records <= MAX_PAGE_RECORDS
        ):
            raise ValueError("maximum_records must be between 1 and 512")
        source_bytes, records = self._read_archive()
        archive_digest = hashlib.sha256(source_bytes).hexdigest()
        checkpoint = self._checkpoint()
        if checkpoint is None:
            start, expected_prefix = 0, _records_digest(records, 0)
        else:
            if (
                str(checkpoint["policy_version"]) != HISTORICAL_EVIDENCE_POLICY_VERSION
                or str(checkpoint["source_key"]) != self.source_key
            ):
                raise HistoricalEvidenceError("historical evidence checkpoint policy/source mismatch")
            start = int(checkpoint["next_index"])
            expected_prefix = str(checkpoint["source_prefix_digest"])
        if start > len(records) or _records_digest(records, start) != expected_prefix:
            raise HistoricalEvidenceError("canonical source prefix changed after evidence indexing")
        if checkpoint is not None and (
            int(checkpoint["source_record_count"]) != len(records)
            or str(checkpoint["source_archive_digest"]) != archive_digest
        ):
            raise HistoricalEvidenceError("canonical archive changed after evidence indexing began")
        if checkpoint is not None and start == len(records) and checkpoint["state"] == "complete":
            return HistoricalEvidencePage(
                "complete", start, start, len(records), 0, 0, 0, 0, 0, 0, (), expected_prefix,
            )

        end = min(len(records), start + maximum_records)
        scope_rows = self.store.connection.execute(
            "SELECT truth_scope_id FROM truth_scopes WHERE character_id=?",
            (self.character_id,),
        ).fetchall()
        valid_scope_ids = {str(row[0]) for row in scope_rows}
        counts: Counter[str] = Counter()
        indexed = created = unchanged = searchable = unknown = 0
        with self.store.transaction():
            locked = self._checkpoint()
            if checkpoint is None:
                if locked is not None:
                    raise HistoricalEvidenceError("historical evidence checkpoint advanced concurrently")
            elif locked is None or int(locked["next_index"]) != start:
                raise HistoricalEvidenceError("historical evidence checkpoint advanced concurrently")
            for index in range(start, end):
                decision = resolve_historical_evidence(
                    records, index, valid_scope_ids=valid_scope_ids,
                )
                if not decision.accepted:
                    counts[decision.reason] += 1
                    continue
                evidence = decision.evidence
                assert evidence is not None
                indexed += 1
                searchable += int(evidence.retrieval_eligible)
                unknown += int(evidence.scope_state == "unknown_scope")
                _claim_id, was_created = persist_historical_occurrence(
                    self.store, self.character_id, evidence,
                )
                created += int(was_created)
                unchanged += int(not was_created)
            next_prefix = _records_digest(records, end)
            state = "complete" if end == len(records) else "running"
            self.store.connection.execute(
                """INSERT INTO historical_evidence_checkpoints(
                       character_id, policy_version, source_key, next_index,
                       source_prefix_digest, source_archive_digest,
                       source_record_count, state, updated_at_us)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(character_id) DO UPDATE SET
                       next_index=excluded.next_index,
                       source_prefix_digest=excluded.source_prefix_digest,
                       state=excluded.state,
                       updated_at_us=excluded.updated_at_us""",
                (
                    self.character_id, HISTORICAL_EVIDENCE_POLICY_VERSION,
                    self.source_key, end, next_prefix, archive_digest,
                    len(records), state, utc_now_us(),
                ),
            )
        return HistoricalEvidencePage(
            state, start, end, len(records), end - start, indexed, created,
            unchanged, searchable, unknown, tuple(sorted(counts.items())), next_prefix,
        )
