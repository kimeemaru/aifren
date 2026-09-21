"""Cooperative character-lifetime leases, separate from memory truth ownership.

Runtime writers keep one shared lease until retirement. Explicit maintenance
uses a nonblocking exclusive lease for the same registry-owned directory. No
lease discovers character folders, creates continuity data, or locks another
character. Callers already holding ``registry.locked()`` must pass
``registry_locked=True`` to avoid reacquiring its file lock.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import dataclass
from aifren.runtime import file_lock as fcntl
import os
from pathlib import Path
import sqlite3
import stat
import threading
from typing import Any

from aifren.character.character_registry import CharacterStorageError


class CharacterStorageBusy(CharacterStorageError):
    """Another cooperative runtime/maintenance owner still holds this character."""


@dataclass(frozen=True)
class _Identity:
    character_id: str
    timeline_generation: str
    storage_layout: str
    directory: Path


def _file_identity(path: Path, *, directory: bool = False):
    try:
        value = path.stat(follow_symlinks=False)
    except OSError as error:
        raise CharacterStorageError("Character storage is missing or inaccessible.") from error
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected(value.st_mode) or path.resolve() != path
            or getattr(value, 'st_file_attributes', 0) & 0x400):
        raise CharacterStorageError("Character storage cannot use a symlink or special file.")
    return value.st_dev, value.st_ino


def _entry(registry, character_id, *, registry_locked):
    if not registry_locked:
        registry.refresh()
    character = registry.get(character_id)
    if character is None:
        raise CharacterStorageError("Character is absent from the registry.")
    paths = registry.runtime_paths(character.character_id)
    identity = _Identity(character.character_id, character.timeline_generation,
                         character.storage_layout, paths["directory"])
    return character, identity, paths


def _open_lock(registry, identity: _Identity):
    """Open only the registered directory; never follow a swapped lock link."""
    if os.name == 'nt':
        return _open_windows_lock(identity)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptors = []
    descriptor = None
    try:
        base = os.open(registry.application_dir, directory_flags)
        descriptors.append(base)
        parent = os.open("characters", directory_flags, dir_fd=base)
        descriptors.append(parent)
        owned = os.open(identity.directory.name, directory_flags, dir_fd=parent)
        descriptors.append(owned)
        value = os.fstat(owned)
        directory_identity = (value.st_dev, value.st_ino)
        if directory_identity != _file_identity(identity.directory, directory=True):
            raise CharacterStorageError("Character storage directory changed while opening its lease.")
        descriptor = os.open(".continuity.lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
                             0o600, dir_fd=owned)
        lock_stat = os.fstat(descriptor)
        if not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_nlink != 1:
            raise CharacterStorageError("Character continuity lock must be an owned regular file.")
        lock_identity = (lock_stat.st_dev, lock_stat.st_ino)
        if lock_identity != _file_identity(identity.directory / ".continuity.lock"):
            raise CharacterStorageError("Character continuity lock changed while opening.")
        return descriptor, directory_identity, lock_identity
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        raise CharacterStorageError("Character continuity lock is unavailable or unsafe.") from error
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        raise
    finally:
        for item in reversed(descriptors):
            os.close(item)


def _open_windows_lock(identity):
    descriptor = None
    try:
        before = _file_identity(identity.directory, directory=True)
        path = identity.directory / '.continuity.lock'
        descriptor = fcntl.open_lock_file(path)
        value = os.fstat(descriptor)
        lock_identity = (value.st_dev, value.st_ino)
        if (before != _file_identity(identity.directory, directory=True)
                or lock_identity != _file_identity(path)):
            raise CharacterStorageError('Character storage changed while opening its lease.')
        return descriptor, before, lock_identity
    except Exception as error:
        if descriptor is not None:
            os.close(descriptor)
        if isinstance(error, CharacterStorageError):
            raise
        raise CharacterStorageError('Character continuity lock is unavailable or unsafe.') from error


def _database_identity(path, expected):
    identity = _file_identity(path)
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)) as database:
            metadata = dict(database.execute(
                "SELECT key,value FROM database_meta WHERE key IN ('storage_character_id','timeline_generation')"))
    except sqlite3.Error as error:
        raise CharacterStorageError("Character-local database identity is unavailable.") from error
    if metadata != {"storage_character_id": expected.character_id,
                    "timeline_generation": expected.timeline_generation}:
        raise CharacterStorageError("Character database identity or timeline does not match its registry entry.")
    if identity != _file_identity(path):
        raise CharacterStorageError("Character database changed while opening its runtime lease.")
    return identity


class CharacterRuntimeLease:
    """One closeable runtime writer lease; assert_current is a pre-write guard."""

    def __init__(self, registry, identity, paths, descriptor, directory_identity, lock_identity):
        self.registry = registry
        self.character_id = identity.character_id
        self.timeline_generation = identity.timeline_generation
        self.storage_layout = identity.storage_layout
        self.paths = dict(paths)
        self._identity = identity
        self._descriptor = descriptor
        self._directory_identity = directory_identity
        self._lock_identity = lock_identity
        self._files: dict[str, Any] = {}
        self._canonical_present = False
        self._closed = False
        self._mutex = threading.RLock()
        self._write_depth = 0

    @property
    def closed(self):
        return self._closed

    def assert_current(self, *, registry_locked=False):
        """Reject retired identities without recreating a missing database/file."""
        with self._mutex:
            if self._closed:
                raise CharacterStorageError("Character runtime lease is retired.")
            character, identity, paths = _entry(
                self.registry, self.character_id, registry_locked=registry_locked)
            if (identity != self._identity or character.storage_status != "ready" or character.operation):
                raise CharacterStorageError("Character storage identity or operation changed; retire this runtime.")
            if paths != self.paths:
                raise CharacterStorageError("Character runtime paths changed; retire this runtime.")
            if self._directory_identity != _file_identity(identity.directory, directory=True):
                raise CharacterStorageError("Character storage directory was replaced; retire this runtime.")
            if self._lock_identity != _file_identity(identity.directory / ".continuity.lock"):
                raise CharacterStorageError("Character continuity lock was replaced; retire this runtime.")
            for kind, observed in self._files.items():
                if observed != _file_identity(paths[kind]):
                    raise CharacterStorageError("Character continuity file was replaced; retire this runtime.")
            if self._canonical_present:
                _file_identity(paths["conversation"])
            elif paths["conversation"].exists():
                _file_identity(paths["conversation"])
                self._canonical_present = True
            return self.paths

    @contextmanager
    def writing(self):
        """Keep the admitted lifetime lease through the final rename/commit.

        A preflight check alone cannot fence a save paused before its final
        file replacement. Retirement waits on this mutex; maintenance therefore
        cannot acquire the file lock while an admitted old write is unfinished.
        Nested store transactions use the same reentrant ownership.
        """
        with self._mutex:
            self.assert_current()
            self._write_depth += 1
            try:
                yield
            finally:
                self._write_depth -= 1
                if self._closed and self._write_depth == 0:
                    self._release_descriptor()

    @contextmanager
    def closing_storage(self):
        """Retire a connection after its writes, including an already stale one."""
        with self._mutex:
            yield

    def _release_descriptor(self):
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is None:
            return
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def close(self):
        with self._mutex:
            if self._closed:
                return
            self._closed = True
            # Reentrant retirement marks the owner stale immediately but may
            # not release its flock before the outer admitted write finishes.
            if self._write_depth == 0:
                self._release_descriptor()

    retire = close

    def __enter__(self):
        if self.closed:
            raise CharacterStorageError("Character runtime lease is retired.")
        return self

    def __exit__(self, *_exc):
        self.close()


def _bound_runtime_lease(guard):
    lease = getattr(guard, "__self__", None)
    if (isinstance(lease, CharacterRuntimeLease)
            and getattr(guard, "__func__", None) is CharacterRuntimeLease.assert_current):
        return lease
    return None


@contextmanager
def continuity_write_scope(owner):
    """Use the existing bound lease guard; retain legacy/mock preflight hooks."""
    guard = getattr(owner, "continuity_write_guard", None)
    lease = _bound_runtime_lease(guard)
    if lease is not None:
        with lease.writing():
            yield
    else:
        if callable(guard):
            guard()
        yield


@contextmanager
def continuity_close_scope(owner):
    """Connection close must not race a transaction or require a live epoch."""
    lease = _bound_runtime_lease(getattr(owner, "continuity_write_guard", None))
    if lease is not None:
        with lease.closing_storage():
            yield
    else:
        yield


def acquire_runtime_lease(registry, character_id, *, registry_locked=False):
    character, identity, paths = _entry(registry, character_id, registry_locked=registry_locked)
    if character.storage_status != "ready" or character.operation:
        raise CharacterStorageError("Character storage operation is incomplete; no runtime was opened.")
    descriptor, directory_identity, lock_identity = _open_lock(registry, identity)
    lease = CharacterRuntimeLease(registry, identity, paths, descriptor, directory_identity, lock_identity)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise CharacterStorageBusy("This character is undergoing maintenance. Close it before reopening.") from error
        if identity.storage_layout == "local":
            lease._files["memory_v2"] = _database_identity(paths["memory_v2"], identity)
            _file_identity(paths["conversation"])
        lease._canonical_present = paths["conversation"].is_file()
        # Canonical writes use atomic replacement, so their inode cannot be a
        # lifetime identity. Existing legacy absence/recovery policy is retained.
        lease.assert_current(registry_locked=registry_locked)
        return lease
    except Exception:
        lease.close()
        raise


@contextmanager
def maintenance_lease(registry, character_id, *, registry_locked=False):
    """Exclusive selected-character maintenance, never wait for a live writer.

    Incomplete status and missing derived/local files are allowed here so an
    explicitly selected maintenance operation can resume. The registry owns
    its operation state and expected timeline transition, not this lock helper.
    """
    _, identity, _ = _entry(registry, character_id, registry_locked=registry_locked)
    descriptor, directory_identity, lock_identity = _open_lock(registry, identity)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise CharacterStorageBusy("This character is open in another runtime. Close it before maintenance.") from error
        _, current, _ = _entry(registry, character_id, registry_locked=registry_locked)
        if (current != identity
                or directory_identity != _file_identity(identity.directory, directory=True)
                or lock_identity != _file_identity(identity.directory / ".continuity.lock")):
            raise CharacterStorageError("Character storage changed before maintenance acquired ownership.")
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
