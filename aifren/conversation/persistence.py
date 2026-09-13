"""Atomic JSON persistence for conversation-owned records, without recovery guesses."""

import json
import os
import tempfile


class ConversationPersistenceError(RuntimeError):
    """A content-free failure with an explicit replacement/commit boundary."""

    def __init__(self, *, record_kind, stage, committed=False):
        self.record_kind = record_kind
        self.stage = stage
        self.committed = committed
        label = "conversation archive" if record_kind == "conversation" else "V1 summary"
        if stage == "load":
            message = (
                f"The {label} could not be read safely. Its data has been preserved. "
                "Restore a valid file and reload before continuing."
            )
        elif committed:
            message = (
                f"The {label} was saved, but crash durability could not be confirmed. "
                "The saved data is retained; do not resend the turn."
            )
        else:
            message = f"Could not save the {label}. The previous saved data is unchanged."
        super().__init__(message)


def validate_record(data, record_kind):
    if record_kind == "conversation":
        if not isinstance(data, list) or any(
            not isinstance(record, dict)
            or not isinstance(record.get("role"), str)
            or not isinstance(record.get("content"), str)
            for record in data
        ):
            raise ValueError("conversation must contain message objects")
    elif record_kind == "summary":
        if (not isinstance(data, dict)
                or not isinstance(data.get("summary"), str)
                or not isinstance(data.get("summarized_messages"), int)
                or isinstance(data.get("summarized_messages"), bool)):
            raise ValueError("summary must contain text and a message count")
    else:
        raise ValueError("unknown conversation record kind")


def load_json(path, default, *, record_kind, allow_missing=True):
    """Return (data, exists); only a genuinely absent first-run file is empty."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        validate_record(data, record_kind)
        return data, True
    except FileNotFoundError as error:
        # A dangling file symlink is damaged storage, not a first launch.
        if allow_missing and not os.path.lexists(path):
            return default, False
        raise ConversationPersistenceError(record_kind=record_kind, stage="load") from error
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as error:
        raise ConversationPersistenceError(record_kind=record_kind, stage="load") from error


def _sync_directory(directory):
    # Windows has no portable directory-fsync API. File fsync and same-volume
    # replacement still apply there; POSIX additionally persists the rename.
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def save_json(path, data, *, record_kind):
    """Replace one record atomically; no backup is ever populated from bad data.

    Successful os.replace is the commit point. Any subsequent sync/close error
    has committed=True: callers retain that write and must not retry the turn.
    """
    temporary = None
    descriptor = None
    committed = False
    stage = "serialize"
    try:
        validate_record(data, record_kind)
        directory = os.path.dirname(os.path.abspath(path))
        stage = "write"
        # Sync each parent after creating a directory entry, so first-run
        # nested directories are also durable on POSIX.
        missing = []
        current = directory
        while not os.path.exists(current):
            missing.append(current)
            current = os.path.dirname(current)
        for child in reversed(missing):
            os.mkdir(child, 0o700)
            _sync_directory(os.path.dirname(child))
        descriptor, temporary = tempfile.mkstemp(
            dir=directory, prefix=f".{os.path.basename(path)}.", suffix=".tmp",
        )
        # mkstemp creates an exclusive 0600 file. Replacement retains those
        # private permissions instead of inheriting a permissive old mode.
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = None  # The file object now owns closing it.
        with handle:
            stage = "serialize"
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            stage = "flush"
            handle.flush()
            os.fsync(handle.fileno())
        stage = "replace"
        os.replace(temporary, path)
        committed = True
        temporary = None
        stage = "directory_sync"
        _sync_directory(directory)
    except (OSError, UnicodeError, ValueError, TypeError, OverflowError, RecursionError) as error:
        raise ConversationPersistenceError(
            record_kind=record_kind, stage=stage, committed=committed,
        ) from error
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                # Cleanup cannot change which canonical version committed.
                pass
