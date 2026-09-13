"""Selected-character copying and cleanup, without application-layout policy.

The caller must exclusively coordinate writers, journal the operation, and publish
paths only after success. The source is never backed up wholesale. A destination
must be new; original rows/IDs are copied exactly, and only its derived FTS is
rebuilt. No canonical files, imports, observers or application settings are run.

Cleanup requires a matching inventory from the completed copy. It deletes only
that character's rows, without vacuuming, deleting the shared database or reading
other characters to rebuild a global index checksum.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
from typing import Callable, Iterator, Sequence
import uuid

from .character_rows import (
    _TableSpec, _TABLE_SPECS, _V1_IMPORT_SPEC, _HISTORICAL_CHECKPOINT_SPEC,
    _NON_COPIED_COLUMNS, _quoted, _validate_exact_schema, _digest_value,
    _copy_table,
)
from .production_import import v1_import_scope
from .store import MemoryV2Store, SCHEMA_VERSION

DEFAULT_COPY_BATCH_ROWS = 128
MAX_COPY_BATCH_ROWS = 1_024
# Finite work ceilings, not semantic retention limits.
MAX_SELECTED_ROWS = 1_000_000
MAX_SELECTED_VALUE_BYTES = 1024 * 1024 * 1024
MAX_SOURCE_DATABASE_BYTES = 4 * 1024 * 1024 * 1024
_SIDECARS = ("-wal", "-shm", "-journal")
_TELEMETRY_SPEC = _TableSpec(
    "retrieval_telemetry", _NON_COPIED_COLUMNS["retrieval_telemetry"], ("telemetry_id",),
)
_CHARACTER_SPECS = (*_TABLE_SPECS, _HISTORICAL_CHECKPOINT_SPEC, _TELEMETRY_SPEC)
_ALL_SPECS = (*_CHARACTER_SPECS, _V1_IMPORT_SPEC)


class CharacterCopyError(RuntimeError):
    """A selected-character operation could not be proved safe."""


class CharacterCopyCancelled(CharacterCopyError):
    """The caller cancelled before committing the selected-character operation."""


@dataclass(frozen=True)
class SelectedCharacterInventory:
    character_id: str
    schema_version: int
    counts: tuple[tuple[str, int], ...]
    digests: tuple[tuple[str, str], ...]

    @property
    def character_exists(self) -> bool:
        return dict(self.counts)["characters"] == 1

    @property
    def total_rows(self) -> int:
        return sum(count for _, count in self.counts)

    def to_dict(self) -> dict:
        return {"character_id": self.character_id, "schema_version": self.schema_version,
                "counts": dict(self.counts), "digests": dict(self.digests)}

    @classmethod
    def from_dict(cls, value: dict) -> "SelectedCharacterInventory":
        try:
            character_id = _character_id(value["character_id"])
            names = tuple(spec.name for spec in _ALL_SPECS)
            if value["schema_version"] != SCHEMA_VERSION or set(value["counts"]) != set(names) or set(value["digests"]) != set(names):
                raise ValueError("inventory layout")
            counts = tuple((name, value["counts"][name]) for name in names)
            digests = tuple((name, value["digests"][name]) for name in names)
            if any(isinstance(count, bool) or not isinstance(count, int) or count < 0 for _, count in counts):
                raise ValueError("inventory counts")
            if any(not isinstance(digest, str) or len(digest) != 64 or
                   any(char not in "0123456789abcdef" for char in digest) for _, digest in digests):
                raise ValueError("inventory digests")
            return cls(character_id, SCHEMA_VERSION, counts, digests)
        except (KeyError, TypeError, ValueError) as error:
            raise CharacterCopyError("invalid selected-character inventory") from error


@dataclass(frozen=True)
class CharacterCopyReport:
    character_id: str
    schema_version: int
    target_database: Path
    inventory: SelectedCharacterInventory
    fts_row_count: int

    @property
    def copied_counts(self) -> tuple[tuple[str, int], ...]:
        return self.inventory.counts

    @property
    def copied_digests(self) -> tuple[tuple[str, str], ...]:
        return self.inventory.digests

    def to_dict(self) -> dict:
        return {"character_id": self.character_id, "schema_version": self.schema_version,
                "target_database": str(self.target_database), "inventory": self.inventory.to_dict(),
                "fts_row_count": self.fts_row_count}


@dataclass(frozen=True)
class CharacterCleanupReport:
    character_id: str
    removed_counts: tuple[tuple[str, int], ...]
    removed_fts_rows: int
    already_absent: bool
    global_fts_digest_invalidated: bool

    def to_dict(self) -> dict:
        return {"character_id": self.character_id, "removed_counts": dict(self.removed_counts),
                "removed_fts_rows": self.removed_fts_rows, "already_absent": self.already_absent,
                "global_fts_digest_invalidated": self.global_fts_digest_invalidated}


@dataclass
class _Budget:
    rows: int = 0
    value_bytes: int = 0

    def add(self, row: Sequence[object]) -> None:
        self.rows += 1
        for value in row:
            if isinstance(value, str):
                self.value_bytes += len(value.encode("utf-8"))
            elif isinstance(value, (bytes, memoryview)):
                self.value_bytes += len(value)
            elif value is not None:
                self.value_bytes += 8
        if self.rows > MAX_SELECTED_ROWS or self.value_bytes > MAX_SELECTED_VALUE_BYTES:
            raise CharacterCopyError("selected state exceeds the copy work ceiling")


def _character_id(value: str) -> str:
    try:
        normalized = str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise CharacterCopyError("character_id must be a canonical UUID") from error
    if normalized != value:
        raise CharacterCopyError("character_id must be a canonical UUID")
    return normalized


def _cancel_check(cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        raise CharacterCopyCancelled("selected-character operation cancelled")


def _batch_size(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_COPY_BATCH_ROWS:
        raise CharacterCopyError("copy batch size is outside its work ceiling")
    return value


def _path(value: str | Path, *, must_exist: bool) -> Path:
    candidate = Path(value).absolute()
    for part in (candidate, *candidate.parents):
        if part.is_symlink():
            raise CharacterCopyError("database paths cannot use symlinks")
    if must_exist:
        try:
            metadata = candidate.stat(follow_symlinks=False)
        except OSError as error:
            raise CharacterCopyError("source database is missing") from error
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise CharacterCopyError("database must be a singly owned regular file")
    elif not candidate.parent.is_dir():
        raise CharacterCopyError("destination parent must already exist")
    return candidate


def _source_identity(path: Path) -> tuple[tuple[str, tuple[int, ...] | None], ...]:
    result = []
    size = 0
    for suffix in ("", "-wal", "-journal"):
        item = Path(f"{path}{suffix}")
        if not item.exists() and not item.is_symlink():
            result.append((suffix, None))
            continue
        metadata = item.stat(follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise CharacterCopyError("database sidecar is not a singly owned regular file")
        size += metadata.st_size
        result.append((suffix, (metadata.st_dev, metadata.st_ino, metadata.st_size,
                                metadata.st_mtime_ns, metadata.st_ctime_ns)))
    shm = Path(f"{path}-shm")
    if shm.is_symlink():
        raise CharacterCopyError("database sidecar cannot use a symlink")
    if size > MAX_SOURCE_DATABASE_BYTES:
        raise CharacterCopyError("source database exceeds the copy work ceiling")
    return tuple(result)


@contextmanager
def _read_source(path: Path) -> Iterator[sqlite3.Connection]:
    before = _source_identity(path)
    # Other characters may have legitimate writers. Never use immutable=1 on
    # this shared source: SQLite must retain its normal snapshot/WAL semantics.
    # SQLite owns any reader-side WAL/SHM files; this owner never deletes them.
    uri = path.as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN")
        yield connection
        after = _source_identity(path)
        if dict(after)[""][:2] != dict(before)[""][:2]:
            raise CharacterCopyError("source database was replaced during selected-character copy")
    finally:
        connection.close()


def _schema_definition(connection: sqlite3.Connection) -> tuple[tuple, ...]:
    return tuple(tuple(row) for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name",
    ))


@lru_cache(maxsize=1)
def _expected_definition() -> tuple[tuple, ...]:
    store = MemoryV2Store()
    try:
        return _schema_definition(store.connection)
    finally:
        store.close()


def _validate_schema(connection: sqlite3.Connection) -> None:
    try:
        _validate_exact_schema(connection, label="selected-character", error_type=CharacterCopyError)
        # Names/columns alone do not protect cleanup from a foreign-row trigger
        # or altered FK cascade. Require the actual production schema as well.
        if _schema_definition(connection) != _expected_definition():
            raise CharacterCopyError("selected-character schema definitions differ")
    except sqlite3.DatabaseError as error:
        raise CharacterCopyError("selected-character schema cannot be validated") from error


def _predicate(spec: _TableSpec, character_id: str) -> tuple[str, tuple[str, ...]]:
    if spec.name == _V1_IMPORT_SPEC.name:
        return "source_scope=?", (v1_import_scope(character_id),)
    return "character_id=?", (character_id,)


def _inventory(
    connection: sqlite3.Connection, character_id: str, *, batch_rows: int,
    cancelled: Callable[[], bool] | None,
) -> SelectedCharacterInventory:
    counts, digests = [], []
    budget = _Budget()
    for spec in _ALL_SPECS:
        _cancel_check(cancelled)
        where, arguments = _predicate(spec, character_id)
        columns = ",".join(_quoted(column) for column in spec.columns)
        order = ",".join(_quoted(column) for column in spec.order_by)
        cursor = connection.execute(
            f"SELECT {columns} FROM {_quoted(spec.name)} WHERE {where} ORDER BY {order}", arguments,
        )
        count, digest = 0, hashlib.sha256()
        while True:
            _cancel_check(cancelled)
            rows = cursor.fetchmany(batch_rows)
            if not rows:
                break
            for row in rows:
                budget.add(row)
                digest.update(b"r")
                for value in row:
                    _digest_value(digest, value)
                count += 1
        counts.append((spec.name, count))
        digests.append((spec.name, digest.hexdigest()))
    inventory = SelectedCharacterInventory(character_id, SCHEMA_VERSION, tuple(counts), tuple(digests))
    if inventory.total_rows and not inventory.character_exists:
        raise CharacterCopyError("selected rows have no owning character")
    return inventory


def _validate_selected_links(connection: sqlite3.Connection, character_id: str) -> None:
    # These ownership references are intentionally not all SQL foreign keys.
    # Preserve their exact identity, not a guessed/imported replacement.
    references = (
        ("characters", "active_truth_scope_id", "truth_scopes", "truth_scope_id"),
        ("claims", "truth_scope_id", "truth_scopes", "truth_scope_id"),
        ("active_scene_subjects", "truth_scope_id", "truth_scopes", "truth_scope_id"),
        ("active_scene_relations", "truth_scope_id", "truth_scopes", "truth_scope_id"),
        ("open_threads", "truth_scope_id", "truth_scopes", "truth_scope_id"),
        ("canonical_observation_dispositions", "truth_scope_id", "truth_scopes", "truth_scope_id"),
        ("canonical_observation_dispositions", "witness_event_id", "events", "event_id"),
        ("active_scene_relations", "cause_subject_id", "active_scene_subjects", "scene_subject_id"),
        ("proactive_checkins", "thread_id", "open_threads", "thread_id"),
        ("proactive_attempts", "thread_id", "open_threads", "thread_id"),
    )
    for table, column, owner, key in references:
        if connection.execute(
            f"SELECT 1 FROM {_quoted(table)} s WHERE s.character_id=? "
            f"AND s.{_quoted(column)} IS NOT NULL AND NOT EXISTS "
            f"(SELECT 1 FROM {_quoted(owner)} o WHERE o.character_id=s.character_id "
            f"AND o.{_quoted(key)}=s.{_quoted(column)}) LIMIT 1", (character_id,),
        ).fetchone():
            raise CharacterCopyError(f"selected {table} has an unresolved ownership reference")
    if connection.execute(
        "SELECT 1 FROM active_scene_relations r WHERE r.character_id=? AND r.target_kind='scene' "
        "AND NOT EXISTS (SELECT 1 FROM active_scene_subjects s WHERE s.character_id=r.character_id "
        "AND s.scene_subject_id=r.target_actor) LIMIT 1", (character_id,),
    ).fetchone():
        raise CharacterCopyError("selected scene relation has no owned target")
    if connection.execute(
        "SELECT 1 FROM v1_import_records i WHERE i.source_scope=? AND NOT EXISTS "
        "(SELECT 1 FROM claims c WHERE c.character_id=? AND c.claim_id=i.claim_id) LIMIT 1",
        (v1_import_scope(character_id), character_id),
    ).fetchone():
        raise CharacterCopyError("selected V1 import receipt has no owned claim")


def _validate_local_identity(connection: sqlite3.Connection, character_id: str) -> None:
    values = dict(connection.execute(
        "SELECT key,value FROM database_meta WHERE key IN ('storage_character_id','timeline_generation')",
    ))
    owner = values.get("storage_character_id")
    if owner is not None and owner != character_id:
        raise CharacterCopyError("local database belongs to a different character")
    if "timeline_generation" in values and owner is None:
        raise CharacterCopyError("local timeline metadata has no character owner")


def validate_character_database_layout(connection: sqlite3.Connection, character_id: str) -> int:
    """Validate local schema/identity without inventorying conversation or claims.

    The caller owns the read transaction and connection lifetime, and checks its
    expected timeline generation separately. This does not migrate/repair the
    database or establish that derived retrieval state is current.
    """
    character_id = _character_id(character_id)
    _validate_schema(connection)
    _validate_local_identity(connection, character_id)
    if connection.execute("SELECT 1 FROM characters WHERE character_id=?", (character_id,)).fetchone() is None:
        raise CharacterCopyError("selected character row is missing")
    if connection.execute("SELECT 1 FROM characters WHERE character_id<>? LIMIT 1", (character_id,)).fetchone():
        raise CharacterCopyError("local database contains another character owner")
    return SCHEMA_VERSION


def selected_character_inventory(
    database_path: str | Path, character_id: str, *,
    batch_rows: int = DEFAULT_COPY_BATCH_ROWS,
    cancelled: Callable[[], bool] | None = None,
) -> SelectedCharacterInventory:
    """Count/hash selected original and derived rows without reading payloads of others."""
    character_id, batch_rows = _character_id(character_id), _batch_size(batch_rows)
    _cancel_check(cancelled)
    with _read_source(_path(database_path, must_exist=True)) as connection:
        _validate_schema(connection)
        _validate_local_identity(connection, character_id)
        result = _inventory(connection, character_id, batch_rows=batch_rows, cancelled=cancelled)
        _validate_selected_links(connection, character_id)
        return result


inspect_character_state = selected_character_inventory


def _remove_failed_destination(path: Path, identity: tuple[int, int]) -> None:
    # Only unlink names created for this operation, and only while the reserved
    # main inode is still ours. Never remove a replacement supplied by a caller.
    if path.is_symlink() or not path.exists():
        return
    metadata = path.stat(follow_symlinks=False)
    if (metadata.st_dev, metadata.st_ino) != identity:
        return
    for suffix in _SIDECARS:
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists() and not sidecar.is_symlink():
            sidecar.unlink()
    path.unlink()


def copy_character_state(
    source_path: str | Path, destination_new_path: str | Path, character_id: str, *,
    batch_rows: int = DEFAULT_COPY_BATCH_ROWS,
    cancelled: Callable[[], bool] | None = None,
) -> CharacterCopyReport:
    """Copy one character into a newly reserved database, preserving exact state.

    All checkpoint/disposition states, original corrections, V1 import receipts,
    and selected telemetry are retained. This does not run reconstruction, repair,
    history observation or any live-data migration. On error the unready database
    created by this call is removed; source changes are never rolled back.
    """
    character_id, batch_rows = _character_id(character_id), _batch_size(batch_rows)
    source, destination = _path(source_path, must_exist=True), _path(destination_new_path, must_exist=False)
    if source == destination or any(Path(f"{destination}{suffix}").exists() or
                                    Path(f"{destination}{suffix}").is_symlink()
                                    for suffix in ("", *_SIDECARS)):
        raise CharacterCopyError("destination must be a new database with no sidecars")
    _cancel_check(cancelled)
    source_inode = dict(_source_identity(source))[""][:2]
    identity, target = None, None
    try:
        with _read_source(source) as connection:
            _validate_schema(connection)
            _validate_local_identity(connection, character_id)
            original = _inventory(connection, character_id, batch_rows=batch_rows, cancelled=cancelled)
            if not original.character_exists:
                raise CharacterCopyError("selected character is absent from source database")
            _validate_selected_links(connection, character_id)
            _cancel_check(cancelled)
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                metadata = os.fstat(fd)
                identity = (metadata.st_dev, metadata.st_ino)
            finally:
                os.close(fd)
            target = MemoryV2Store(str(destination))
            _validate_schema(target.connection)
            counts, digests, budget = [], [], _Budget()
            with target.transaction():
                for spec in _ALL_SPECS:
                    where, arguments = _predicate(spec, character_id)
                    count, digest = _copy_table(
                        connection, target.connection, spec, where_sql=where,
                        arguments=arguments, batch_rows=batch_rows, budget=budget,
                        check_cancel=lambda: _cancel_check(cancelled),
                    )
                    counts.append((spec.name, count))
                    digests.append((spec.name, digest))
                if (tuple(counts), tuple(digests)) != (original.counts, original.digests):
                    raise CharacterCopyError("selected copy differs from source inventory")
                _validate_selected_links(target.connection, character_id)
                if target.connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise CharacterCopyError("selected copy has a foreign-key failure")
                if target.connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise CharacterCopyError("selected copy failed SQLite integrity check")
                fts_count = target.rebuild_fts()
                actual = _inventory(target.connection, character_id, batch_rows=batch_rows, cancelled=cancelled)
                if actual != original or not target.fts_is_current():
                    raise CharacterCopyError("selected copy failed semantic/derived validation")
                _cancel_check(cancelled)
            # The read context verifies main-file identity. Unrelated writers
            # can change the shared DB, so verify A again through a fresh SQLite
            # snapshot instead of treating B's file mtime as an A mutation.
        fresh = selected_character_inventory(
            source, character_id, batch_rows=batch_rows, cancelled=cancelled,
        )
        if fresh != original:
            raise CharacterCopyError("selected source changed during copy; coordinate its writers")
        if dict(_source_identity(source))[""][:2] != source_inode:
            raise CharacterCopyError("source database was replaced during selected-character copy")
        target.close()
        target = None
        return CharacterCopyReport(character_id, SCHEMA_VERSION, destination, original, fts_count)
    except BaseException:
        if target is not None:
            target.close()
        if identity is not None:
            _remove_failed_destination(destination, identity)
        raise


def cleanup_character_state(
    database_path: str | Path, character_id: str, *,
    expected_inventory: SelectedCharacterInventory,
    batch_rows: int = DEFAULT_COPY_BATCH_ROWS,
    cancelled: Callable[[], bool] | None = None,
) -> CharacterCleanupReport:
    """Delete the verified selected rows transactionally; require caller quiescence.

    The caller must hold the copy/journal/writer coordination boundary. A newer
    selected row refuses cleanup. Repeating a completed cleanup is a no-op. Other
    characters' FTS rows remain; the global derived digest is invalidated because
    rebuilding that shared checksum would require reading their payloads.
    """
    character_id, batch_rows = _character_id(character_id), _batch_size(batch_rows)
    if not isinstance(expected_inventory, SelectedCharacterInventory) or (
        expected_inventory.character_id != character_id or expected_inventory.schema_version != SCHEMA_VERSION
    ):
        raise CharacterCopyError("cleanup requires the matching selected copy inventory")
    path = _path(database_path, must_exist=True)
    _source_identity(path)
    _cancel_check(cancelled)
    connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, isolation_level=None, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("BEGIN IMMEDIATE")
        _validate_schema(connection)
        _validate_local_identity(connection, character_id)
        current = _inventory(connection, character_id, batch_rows=batch_rows, cancelled=cancelled)
        if current.total_rows and current != expected_inventory:
            raise CharacterCopyError("selected state changed since the verified copy; cleanup refused")
        _validate_selected_links(connection, character_id)
        removed_fts = 0
        for table in ("claims_fts", "historical_evidence_fts"):
            removed_fts += connection.execute(
                f"DELETE FROM {_quoted(table)} WHERE character_id=?", (character_id,),
            ).rowcount
        for spec in reversed(_ALL_SPECS):
            _cancel_check(cancelled)
            where, arguments = _predicate(spec, character_id)
            connection.execute(f"DELETE FROM {_quoted(spec.name)} WHERE {where}", arguments)
        invalidate = bool(current.total_rows or removed_fts)
        if invalidate:
            connection.execute("DELETE FROM database_meta WHERE key='fts_claims_digest'")
        if _inventory(connection, character_id, batch_rows=batch_rows, cancelled=cancelled).total_rows:
            raise CharacterCopyError("selected cleanup left owned rows")
        _cancel_check(cancelled)
        connection.execute("COMMIT")
        return CharacterCleanupReport(character_id, current.counts, removed_fts,
                                      not bool(current.total_rows), invalidate)
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
