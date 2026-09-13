"""Create a one-character disposable application clone for V2 maintenance.

The copier intentionally does not use SQLite's backup API or copy an
application directory.  Both operations would transiently copy unrelated
characters.  Instead it builds a new application root from an exact file and
schema allowlist, streams only rows owned by the selected character, rebuilds
derived FTS state, and writes the staged-disposable marker last.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from typing import Mapping, Sequence

from character_registry import (
    CharacterRegistry,
    CharacterRegistryError,
    REGISTRY_RELATIVE_PATH,
    REGISTRY_VERSION,
)
from memory_v2_historical_evidence import (
    STAGED_DATABASE_DIRECTORY,
    STAGED_DISPOSABLE_MARKER,
    staged_disposable_marker_payload,
    validate_staged_disposable_target,
)
from memory_v2_store.durable_contract import DURABLE_CORE_FACT
from memory_v2_store.production_import import v1_import_scope
from memory_v2_store.store import MemoryV2Store, SCHEMA_VERSION
from memory_v2_store.character_rows import (
    _TableSpec, _TABLE_SPECS, _V1_IMPORT_SPEC, _HISTORICAL_CHECKPOINT_SPEC,
    _NON_COPIED_COLUMNS, _FTS_SHADOW_TABLES, _quoted, _schema_columns,
    _validate_exact_schema as _validate_row_schema,
    _digest_value, _digest_rows, _copy_table, _target_digest,
)


DEFAULT_STAGED_DATABASE_NAME = "staged-memory-v2.sqlite3"
DEFAULT_COPY_BATCH_ROWS = 128
MAX_COPY_BATCH_ROWS = 1_024
_COPY_CHUNK_BYTES = 64 * 1024
_REGISTRY_MAX_BYTES = 4 * 1024 * 1024
MAX_SOURCE_DATABASE_BYTES = 4 * 1024 * 1024 * 1024
MAX_SELECTED_ROWS = 1_000_000
MAX_SELECTED_VALUE_BYTES = 1024 * 1024 * 1024
_FILE_LIMITS = {
    "character": 2 * 1024 * 1024,
    "personality": 8 * 1024 * 1024,
    "memory": 64 * 1024 * 1024,
    "conversation": 64 * 1024 * 1024,
    "summary": 16 * 1024 * 1024,
}


class StagedCloneError(RuntimeError):
    """Raised before a staged clone can be marked usable."""


@dataclass
class _CopyBudget:
    rows: int = 0
    value_bytes: int = 0

    def add(self, row: Sequence[object]) -> None:
        self.rows += 1
        if self.rows > MAX_SELECTED_ROWS:
            raise StagedCloneError("selected character exceeds the staged-clone row bound")
        for value in row:
            if isinstance(value, str):
                self.value_bytes += len(value.encode("utf-8"))
            elif isinstance(value, (bytes, memoryview)):
                self.value_bytes += len(value)
            elif value is not None:
                self.value_bytes += 8
        if self.value_bytes > MAX_SELECTED_VALUE_BYTES:
            raise StagedCloneError("selected character exceeds the staged-clone value bound")


@dataclass(frozen=True)
class StagedCloneReport:
    character_id: str
    schema_version: int
    target_database: Path
    marker_path: Path
    canonical_file_count: int
    fts_row_count: int
    copied_counts: tuple[tuple[str, int], ...]
    copied_digests: tuple[tuple[str, str], ...]


def _validate_exact_schema(
    connection: sqlite3.Connection,
    *,
    label: str,
    allowed_versions: tuple[int, ...] = (SCHEMA_VERSION,),
) -> int:
    return _validate_row_schema(
        connection, label=label, allowed_versions=allowed_versions,
        error_type=StagedCloneError,
    )


def _assert_safe_regular_file(root: Path, path: Path, *, label: str) -> Path:
    lexical = path.absolute()
    current = lexical
    while current != root and current != current.parent:
        if current.is_symlink():
            raise StagedCloneError(f"{label} cannot use a symlink")
        current = current.parent
    resolved = lexical.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise StagedCloneError(f"{label} is outside the selected application") from error
    if not resolved.is_file():
        raise StagedCloneError(f"{label} is missing")
    metadata = resolved.stat(follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise StagedCloneError(f"{label} is not a singly owned regular file")
    return resolved


def _ensure_private_directory(root: Path, path: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise StagedCloneError("staged clone output escaped its private root") from error
    current = root
    current.mkdir(mode=0o700, exist_ok=True)
    os.chmod(current, 0o700)
    for part in relative.parts:
        current /= part
        current.mkdir(mode=0o700, exist_ok=True)
        os.chmod(current, 0o700)


def _hash_file(path: Path, maximum_bytes: int) -> str:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while True:
            chunk = source.read(_COPY_CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            if size > maximum_bytes:
                raise StagedCloneError(f"source file exceeds the clone bound: {path.name}")
            digest.update(chunk)
    return digest.hexdigest()


def _copy_file(root: Path, source: Path, target: Path, maximum_bytes: int) -> str:
    _ensure_private_directory(root, target.parent)
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as reader, target.open("xb") as writer:
        os.fchmod(writer.fileno(), 0o600)
        while True:
            chunk = reader.read(_COPY_CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            if size > maximum_bytes:
                raise StagedCloneError(f"source file exceeds the clone bound: {source.name}")
            digest.update(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    return digest.hexdigest()


def _write_json_new(root: Path, path: Path, value: object) -> None:
    _ensure_private_directory(root, path.parent)
    encoded = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with path.open("xb") as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(encoded)
        output.flush()
        os.fsync(output.fileno())


def _write_marker_last(root: Path, payload: object) -> Path:
    marker = root / STAGED_DISPOSABLE_MARKER
    if marker.exists() or marker.is_symlink():
        raise StagedCloneError("staged-disposable marker already exists")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".staged-disposable.", suffix=".tmp", dir=root,
    )
    temporary = Path(temporary_name)
    replaced = False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=True) as output:
            json.dump(payload, output, ensure_ascii=False, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, marker)
        replaced = True
        directory = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory)
        except Exception:
            marker.unlink(missing_ok=True)
            replaced = False
            raise
        finally:
            os.close(directory)
    finally:
        if not replaced:
            temporary.unlink(missing_ok=True)
    return marker


def _read_selected_character(source_root: Path, character_id: str):
    registry_path = _assert_safe_regular_file(
        source_root, source_root / REGISTRY_RELATIVE_PATH, label="character registry",
    )
    if registry_path.stat().st_size > _REGISTRY_MAX_BYTES:
        raise StagedCloneError("character registry exceeds the clone bound")
    registry = CharacterRegistry(source_root)
    character = registry.get(character_id)
    if character is None:
        raise StagedCloneError("selected character is absent from the source registry")
    # Registry ownership supports both immutable UUID folders and the current
    # readable name--UUID form. Do not duplicate its path/identity policy here.
    try:
        registry.owned_directory(character.character_id)
    except CharacterRegistryError as error:
        raise StagedCloneError("selected character has a non-canonical config directory") from error
    if character.storage_status != "ready" or character.operation:
        raise StagedCloneError("selected character has an incomplete storage operation")
    return registry, character, registry_path


def _copy_selected_application_files(
    source_root: Path,
    target_root: Path,
    registry: CharacterRegistry,
    character,
    registry_path: Path,
) -> tuple[dict[Path, tuple[str, int]], Mapping[str, Path]]:
    runtime_paths = registry.runtime_paths(character.character_id)
    fingerprints: dict[Path, tuple[str, int]] = {
        registry_path: (_hash_file(registry_path, _REGISTRY_MAX_BYTES), _REGISTRY_MAX_BYTES),
    }
    copied: set[Path] = set()
    copied_by_key: dict[str, Path] = {}
    for key in ("character", "personality", "memory", "conversation", "summary"):
        source = _assert_safe_regular_file(
            source_root, Path(runtime_paths[key]), label=f"selected {key} file",
        )
        relative = source.relative_to(source_root)
        if relative in copied:
            raise StagedCloneError("selected canonical file paths are not distinct")
        copied.add(relative)
        copied_by_key[key] = relative
        limit = _FILE_LIMITS[key]
        digest = _copy_file(target_root, source, target_root / relative, limit)
        fingerprints[source] = (digest, limit)

    target_registry = {
        "version": REGISTRY_VERSION,
        "active_character_id": character.character_id,
        "characters": [asdict(character)],
    }
    _write_json_new(target_root, target_root / REGISTRY_RELATIVE_PATH, target_registry)
    target_runtime = CharacterRegistry(target_root).runtime_paths(character.character_id)
    for key, relative in copied_by_key.items():
        expected = (target_root / relative).resolve()
        actual = _assert_safe_regular_file(
            target_root, Path(target_runtime[key]), label=f"staged {key} file",
        )
        if actual != expected:
            raise StagedCloneError("staged character runtime path differs from its copied file")
    return fingerprints, target_runtime


def _assert_source_stable(fingerprints: Mapping[Path, tuple[str, int]]) -> None:
    for path, (expected, limit) in fingerprints.items():
        if _hash_file(path, limit) != expected:
            raise StagedCloneError("source application changed while the clone was being built")


def _assert_target_semantics(
    store: MemoryV2Store,
    character_id: str,
    expected: Mapping[str, tuple[int, str]],
    import_expected: tuple[int, str],
    checkpoint_expected: tuple[int, str] | None = None,
) -> int:
    connection = store.connection
    _validate_exact_schema(connection, label="target")
    for spec in _TABLE_SPECS:
        actual = _target_digest(
            connection, spec, where_sql="character_id=?", arguments=(character_id,),
        )
        if actual != expected[spec.name]:
            raise StagedCloneError(f"copied row verification failed for {spec.name}")

    scope = v1_import_scope(character_id)
    actual_import = _target_digest(
        connection, _V1_IMPORT_SPEC, where_sql="source_scope=?", arguments=(scope,),
    )
    if actual_import != import_expected:
        raise StagedCloneError("copied row verification failed for v1_import_records")

    character_tables = [spec.name for spec in _TABLE_SPECS] + [
        "retrieval_telemetry", "historical_evidence_checkpoints", "claims_fts",
    ]
    for table in character_tables:
        values = connection.execute(
            f"SELECT DISTINCT character_id FROM {_quoted(table)} LIMIT 2",
        ).fetchall()
        if any(str(row[0]) != character_id for row in values):
            raise StagedCloneError(f"foreign character row reached staged table {table}")

    if connection.execute("SELECT COUNT(*) FROM retrieval_telemetry").fetchone()[0] != 0:
        raise StagedCloneError("staged clone must not copy retrieval telemetry")
    checkpoint_actual = _target_digest(
        connection, _HISTORICAL_CHECKPOINT_SPEC,
        where_sql="character_id=?", arguments=(character_id,),
    )
    if checkpoint_expected is None:
        if checkpoint_actual[0] != 0:
            raise StagedCloneError("staged clone must start without an evidence checkpoint")
    elif checkpoint_actual != checkpoint_expected or checkpoint_actual[0] != 1:
        raise StagedCloneError("prepared historical checkpoint verification failed")
    if connection.execute(
        "SELECT COUNT(*) FROM claims WHERE character_id=? AND claim_type=?",
        (character_id, DURABLE_CORE_FACT),
    ).fetchone()[0] != 0:
        raise StagedCloneError("staged clone source already contains governed durable facts")

    active = connection.execute(
        """SELECT ts.scope_kind FROM characters c
             LEFT JOIN truth_scopes ts
               ON ts.character_id=c.character_id
              AND ts.truth_scope_id=c.active_truth_scope_id
            WHERE c.character_id=?""",
        (character_id,),
    ).fetchone()
    if active is None or active[0] != "real_world":
        raise StagedCloneError("staged clone requires an active real-world truth scope")

    for table in ("claims", "active_scene_subjects", "active_scene_relations", "open_threads"):
        missing = connection.execute(
            f"""SELECT COUNT(*) FROM {_quoted(table)} value
                  LEFT JOIN truth_scopes scope
                    ON scope.character_id=value.character_id
                   AND scope.truth_scope_id=value.truth_scope_id
                 WHERE value.character_id=? AND value.truth_scope_id IS NOT NULL
                   AND scope.truth_scope_id IS NULL""",
            (character_id,),
        ).fetchone()[0]
        if missing:
            raise StagedCloneError(f"{table} contains an unresolved truth scope")

    orphan_imports = connection.execute(
        """SELECT COUNT(*) FROM v1_import_records source
             LEFT JOIN claims claim
               ON claim.character_id=? AND claim.claim_id=source.claim_id
            WHERE source.source_scope=? AND claim.claim_id IS NULL""",
        (character_id, scope),
    ).fetchone()[0]
    if orphan_imports:
        raise StagedCloneError("selected V1 import bookkeeping references a missing claim")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise StagedCloneError("staged clone failed foreign-key validation")
    quick = tuple(str(row[0]) for row in connection.execute("PRAGMA quick_check").fetchall())
    if quick != ("ok",):
        raise StagedCloneError("staged clone failed SQLite quick_check")
    if not store.fts_is_current():
        raise StagedCloneError("staged clone FTS index is not current")
    return int(connection.execute("SELECT COUNT(*) FROM claims_fts").fetchone()[0])


def create_memory_v2_staged_clone(
    source_application_dir: str | Path,
    target_application_dir: str | Path,
    character_id: str,
    *,
    database_name: str = DEFAULT_STAGED_DATABASE_NAME,
    batch_rows: int = DEFAULT_COPY_BATCH_ROWS,
    source_database_path: str | Path | None = None,
    preserve_completed_historical_reconstruction: bool = False,
) -> StagedCloneReport:
    """Create one marker-gated disposable clone without reading foreign rows.

    The source application should be quiescent.  A pinned SQLite read
    transaction still guarantees a coherent database snapshot, while the
    pre/post data-version and canonical-file checks conservatively reject a
    source changed by another process during the operation.
    """
    if isinstance(batch_rows, bool) or not isinstance(batch_rows, int) or not 1 <= batch_rows <= MAX_COPY_BATCH_ROWS:
        raise StagedCloneError("copy batch size is outside the bounded range")
    if preserve_completed_historical_reconstruction and source_database_path is None:
        raise StagedCloneError(
            "historical reconstruction may be preserved only from an attested staged source"
        )
    database_component = Path(database_name)
    if (database_component.name != database_name
            or database_component.suffix != ".sqlite3"
            or not database_component.stem):
        raise StagedCloneError("staged database name must be one SQLite filename")

    source_input = Path(source_application_dir)
    if source_input.is_symlink() or not source_input.is_dir():
        raise StagedCloneError("source application root is missing or unsafe")
    source_root = source_input.resolve()
    target_input = Path(target_application_dir)
    if target_input.exists() or target_input.is_symlink():
        raise StagedCloneError("target application root must not already exist")
    target_root = target_input.resolve()
    try:
        target_root.relative_to(source_root)
    except ValueError:
        pass
    else:
        raise StagedCloneError("target application cannot be inside the source application")

    registry, character, registry_path = _read_selected_character(
        source_root, str(character_id),
    )
    if source_database_path is None:
        source_database = _assert_safe_regular_file(
            source_root, registry.runtime_paths(character.character_id)["memory_v2"],
            label="source Memory V2 database",
        )
    else:
        source_database = validate_staged_disposable_target(
            source_root,
            character.character_id,
            source_database_path,
            registry.runtime_paths(character.character_id)["conversation"],
        )
        source_database = _assert_safe_regular_file(
            source_root, source_database, label="attested prepared Memory V2 database",
        )
    target_root.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(target_root, 0o700)
    fingerprints, target_runtime = _copy_selected_application_files(
        source_root, target_root, registry, character, registry_path,
    )
    if character.storage_layout == "local":
        if database_name != DEFAULT_STAGED_DATABASE_NAME:
            raise StagedCloneError("local staged clones use their registered database filename")
        target_database = target_runtime["memory_v2"]
    else:
        target_database = target_root / STAGED_DATABASE_DIRECTORY / database_name
    _ensure_private_directory(target_root, target_database.parent)
    with target_database.open("xb") as database_file:
        os.fchmod(database_file.fileno(), 0o600)

    source_database_bytes = source_database.stat(follow_symlinks=False).st_size
    source_sidecar_sizes: dict[str, int] = {}
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(f"{source_database}{suffix}")
        if sidecar.exists() or sidecar.is_symlink():
            safe_sidecar = _assert_safe_regular_file(
                source_root, sidecar, label=f"source Memory V2 {suffix[1:]} sidecar",
            )
            sidecar_size = safe_sidecar.stat(follow_symlinks=False).st_size
            source_sidecar_sizes[suffix] = sidecar_size
            source_database_bytes += sidecar_size
    if source_database_bytes > MAX_SOURCE_DATABASE_BYTES:
        raise StagedCloneError("source Memory V2 database exceeds the staged-clone bound")

    # SQLite may create an empty WAL/SHM pair even for a mode=ro connection to
    # a WAL-configured but fully checkpointed database.  That violates the
    # clone's source-immutability promise.  With no live WAL/journal content,
    # immutable mode is an exact main-database snapshot and creates no
    # sidecars.  A non-empty WAL remains on the ordinary read-only path so
    # committed uncheckpointed rows are never silently omitted.
    immutable_snapshot = (
        source_sidecar_sizes.get("-wal", 0) == 0
        and source_sidecar_sizes.get("-journal", 0) == 0
    )
    source_uri = source_database.as_uri() + "?mode=ro"
    if immutable_snapshot:
        source_uri += "&immutable=1"
    source = sqlite3.connect(source_uri, uri=True, isolation_level=None)
    source.row_factory = sqlite3.Row
    source.execute("PRAGMA query_only=ON")
    source.execute("PRAGMA busy_timeout=5000")
    data_version_before = int(source.execute("PRAGMA data_version").fetchone()[0])
    copied: dict[str, tuple[int, str]] = {}
    imported: tuple[int, str] = (0, hashlib.sha256().hexdigest())
    checkpoint_copied: tuple[int, str] | None = None
    target_store: MemoryV2Store | None = None
    copy_budget = _CopyBudget()
    fts_rows = 0
    source_transaction = False
    try:
        source.execute("BEGIN")
        source_transaction = True
        source_version = _validate_exact_schema(
            source, label="source", allowed_versions=(17, 18, 19, 20, SCHEMA_VERSION),
        )
        if character.storage_layout == "local":
            metadata = dict(source.execute(
                "SELECT key,value FROM database_meta WHERE key IN ('storage_character_id','timeline_generation')",
            ))
            if metadata != {"storage_character_id": character.character_id,
                            "timeline_generation": character.timeline_generation}:
                raise StagedCloneError("source local database identity or timeline differs from its registry")
        character_rows = source.execute(
            "SELECT COUNT(*) FROM characters WHERE character_id=?", (character.character_id,),
        ).fetchone()[0]
        if character_rows != 1:
            raise StagedCloneError("source Memory V2 character row is missing")
        if source.execute(
            "SELECT COUNT(*) FROM claims WHERE character_id=? AND claim_type=?",
            (character.character_id, DURABLE_CORE_FACT),
        ).fetchone()[0]:
            raise StagedCloneError("source already contains governed durable facts")

        target_store = MemoryV2Store(str(target_database))
        _validate_exact_schema(target_store.connection, label="target")
        with target_store.transaction():
            if character.storage_layout == "local":
                target_store.connection.executemany(
                    "INSERT OR REPLACE INTO database_meta(key,value) VALUES (?,?)",
                    (("storage_character_id", character.character_id),
                     ("timeline_generation", character.timeline_generation)),
                )
            for spec in _TABLE_SPECS:
                if ((source_version == 17 and spec.name == "historical_evidence")
                        or (source_version < 19 and spec.name == "canonical_observation_progress")
                        or (source_version < 21 and spec.name == "canonical_observation_dispositions")):
                    copied[spec.name] = (0, hashlib.sha256().hexdigest())
                else:
                    copied[spec.name] = _copy_table(
                        source, target_store.connection, spec,
                        where_sql="character_id=?", arguments=(character.character_id,),
                        batch_rows=batch_rows, budget=copy_budget,
                    )
            scope = v1_import_scope(character.character_id)
            imported = _copy_table(
                source, target_store.connection, _V1_IMPORT_SPEC,
                where_sql="source_scope=?", arguments=(scope,), batch_rows=batch_rows,
                budget=copy_budget,
            )
            if preserve_completed_historical_reconstruction:
                checkpoint_copied = _copy_table(
                    source, target_store.connection, _HISTORICAL_CHECKPOINT_SPEC,
                    where_sql="character_id=?", arguments=(character.character_id,),
                    batch_rows=batch_rows, budget=copy_budget,
                )
                checkpoint = source.execute(
                    """SELECT state, source_record_count, next_index
                         FROM historical_evidence_checkpoints
                        WHERE character_id=?""",
                    (character.character_id,),
                ).fetchone()
                try:
                    canonical_messages = json.loads(
                        registry.runtime_paths(character.character_id)["conversation"].read_text(
                            encoding="utf-8"
                        )
                    )
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise StagedCloneError(
                        "attested source canonical conversation is malformed"
                    ) from error
                if (
                    checkpoint is None
                    or str(checkpoint[0]) != "complete"
                    or not isinstance(canonical_messages, list)
                    or int(checkpoint[1]) != len(canonical_messages)
                    or int(checkpoint[2]) != len(canonical_messages)
                ):
                    raise StagedCloneError(
                        "attested source historical reconstruction is incomplete"
                    )
            target_store.rebuild_fts()
            fts_rows = _assert_target_semantics(
                target_store, character.character_id, copied, imported,
                checkpoint_copied,
            )
        source.execute("COMMIT")
        source_transaction = False
        data_version_after = int(source.execute("PRAGMA data_version").fetchone()[0])
        if data_version_after != data_version_before:
            raise StagedCloneError("source Memory V2 database changed during cloning")
        _assert_source_stable(fingerprints)
        target_store.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        target_store.close()
        target_store = None
        os.chmod(target_database, 0o600)
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(f"{target_database}{suffix}")
            if sidecar.exists():
                os.chmod(sidecar, 0o600)
    except Exception:
        if source_transaction:
            source.execute("ROLLBACK")
        if target_store is not None:
            target_store.close()
        raise
    finally:
        source.close()

    metadata = target_database.stat(follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise StagedCloneError("staged target database has unsafe filesystem ownership")
    marker_payload = staged_disposable_marker_payload(
        target_root, character.character_id, target_database,
        target_runtime["conversation"],
    )
    marker = _write_marker_last(target_root, marker_payload)
    try:
        validate_staged_disposable_target(
            target_root, character.character_id, target_database,
            target_runtime["conversation"],
        )
    except Exception:
        # A local filesystem race after the final marker write must not leave
        # a clone that appears authorized even though final validation failed.
        marker.unlink(missing_ok=True)
        raise
    return StagedCloneReport(
        character_id=character.character_id,
        schema_version=SCHEMA_VERSION,
        target_database=target_database,
        marker_path=marker,
        canonical_file_count=5,
        fts_row_count=fts_rows,
        copied_counts=tuple((name, value[0]) for name, value in copied.items())
        + ((_V1_IMPORT_SPEC.name, imported[0]),)
        + (((_HISTORICAL_CHECKPOINT_SPEC.name, checkpoint_copied[0]),)
           if checkpoint_copied is not None else ()),
        copied_digests=tuple((name, value[1]) for name, value in copied.items())
        + ((_V1_IMPORT_SPEC.name, imported[1]),)
        + (((_HISTORICAL_CHECKPOINT_SPEC.name, checkpoint_copied[1]),)
           if checkpoint_copied is not None else ()),
    )


__all__ = [
    "DEFAULT_COPY_BATCH_ROWS",
    "DEFAULT_STAGED_DATABASE_NAME",
    "MAX_COPY_BATCH_ROWS",
    "StagedCloneError",
    "StagedCloneReport",
    "create_memory_v2_staged_clone",
]
