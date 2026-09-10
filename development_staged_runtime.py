"""Attested Development-only runtime access to one disposable character clone."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
import stat
from typing import Iterator, Mapping

from character_registry import CharacterRegistry
from memory_v2_episode_compaction import (
    EPISODE_PURPOSE_HISTORICAL,
    EpisodeCompactionCache,
)
from memory_v2_historical_evidence import (
    HISTORICAL_EVIDENCE_POLICY_VERSION,
    STAGED_DATABASE_DIRECTORY,
    _records_digest,
    validate_staged_disposable_target,
)
from memory_v2_staged_clone import DEFAULT_STAGED_DATABASE_NAME
from memory_v2_store import MemoryV2Store
from runtime_layout import (
    DEVELOPMENT_STAGED_CHARACTER_ID_ENV,
    DEVELOPMENT_STAGED_DATA_ROOT_ENV,
    absolute_path,
)


DEVELOPMENT_QA_ENV = "AIFREN_ENABLE_DEVELOPMENT_QA"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


class DevelopmentStagedRuntimeError(RuntimeError):
    """Raised before an untrusted staged path can become runtime state."""


@dataclass(frozen=True)
class DevelopmentStagedRuntime:
    data_root: Path
    character_id: str
    database_path: Path
    historical_evidence_count: int
    validated_episode_count: int


def staged_runtime_requested(environment: Mapping[str, str] | None = None) -> bool:
    values = os.environ if environment is None else environment
    return bool(str(values.get(DEVELOPMENT_STAGED_DATA_ROOT_ENV) or "").strip())


def _require_regular_private_root(value: str | os.PathLike[str]) -> Path:
    candidate = Path(value)
    if candidate.is_symlink() or not candidate.is_dir():
        raise DevelopmentStagedRuntimeError(
            "Development staged data root is missing or unsafe."
        )
    root = candidate.resolve()
    metadata = root.stat(follow_symlinks=False)
    if not stat.S_ISDIR(metadata.st_mode):
        raise DevelopmentStagedRuntimeError(
            "Development staged data root is not a directory."
        )
    return root


def _runtime_history_attested(progress, messages, *, character_id, relative_path):
    """Validate a production observer's exact completed canonical prefix.

    Runtime appends may follow that prefix; they do not rewrite its identity.
    This is an attestation of the existing owner, never a fabricated checkpoint.
    """
    import hashlib
    from memory_v2_runtime_observation import EMPTY_DIGEST, extend_digest
    source_key = hashlib.sha256(relative_path.as_posix().encode()).hexdigest()
    policy = HISTORICAL_EVIDENCE_POLICY_VERSION + ":runtime_append_v1"
    for row in progress:
        owner, source, consumer, version, count, digest, state, reason, _ = row
        if (owner != character_id or source != source_key or consumer != 'history'
                or version != policy or state != 'complete' or reason not in ('', 'up_to_date')
                or not 0 < count <= len(messages)):
            continue
        expected = EMPTY_DIGEST
        for record in messages[:count]:
            expected = extend_digest(expected, record)
        if digest == expected:
            return True
    return False


def attest_development_staged_runtime(
    application_dir: str | os.PathLike[str],
    *,
    character_id: str | None = None,
    environment: Mapping[str, str] | None = None,
    require_rebuilt_history: bool = True,
) -> DevelopmentStagedRuntime | None:
    """Validate the exact marker-bound clone selected by ephemeral env state.

    Merely setting ``AIFREN_DATA_ROOT`` is never enough. The special staged
    path requires the explicit Development gate, an exact character identity,
    the existing disposable marker, SQLite integrity, a completed occurrence
    checkpoint, and (for runtime use) a shared-validator-accepted historical
    episode generation.
    """
    values = os.environ if environment is None else environment
    requested = str(values.get(DEVELOPMENT_STAGED_DATA_ROOT_ENV) or "").strip()
    if not requested:
        return None
    if str(values.get(DEVELOPMENT_QA_ENV) or "").strip().casefold() not in _TRUE_VALUES:
        raise DevelopmentStagedRuntimeError(
            "Development staged data requires AIFREN_ENABLE_DEVELOPMENT_QA."
        )
    expected_character = str(
        character_id or values.get(DEVELOPMENT_STAGED_CHARACTER_ID_ENV) or ""
    ).strip()
    configured_character = str(
        values.get(DEVELOPMENT_STAGED_CHARACTER_ID_ENV) or ""
    ).strip()
    if not expected_character or configured_character != expected_character:
        raise DevelopmentStagedRuntimeError(
            "Development staged character identity is missing or inconsistent."
        )
    root = _require_regular_private_root(requested)
    if absolute_path(application_dir) != root:
        raise DevelopmentStagedRuntimeError(
            "Runtime data root does not equal the attested staged clone."
        )

    registry = CharacterRegistry(root)
    characters = registry.list_characters()
    active = registry.active()
    if (
        len(characters) != 1
        or characters[0].character_id != expected_character
        or active.character_id != expected_character
    ):
        raise DevelopmentStagedRuntimeError(
            "Staged runtime must contain exactly the selected active character."
        )
    paths = registry.runtime_paths(expected_character)
    database = (
        root / STAGED_DATABASE_DIRECTORY / DEFAULT_STAGED_DATABASE_NAME
    ).resolve()
    try:
        validate_staged_disposable_target(
            root, expected_character, database, paths["conversation"],
        )
    except Exception as error:
        raise DevelopmentStagedRuntimeError(
            "Development staged marker or path attestation failed."
        ) from error

    connection = sqlite3.connect(
        database.as_uri() + "?mode=ro", uri=True, isolation_level=None,
    )
    try:
        connection.execute("PRAGMA query_only=ON")
        if tuple(str(row[0]) for row in connection.execute("PRAGMA quick_check")) != ("ok",):
            raise DevelopmentStagedRuntimeError("Staged SQLite quick_check failed.")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise DevelopmentStagedRuntimeError("Staged SQLite foreign-key check failed.")
        owners = {
            str(row[0]) for row in connection.execute(
                "SELECT character_id FROM characters LIMIT 2",
            ).fetchall()
        }
        if owners != {expected_character}:
            raise DevelopmentStagedRuntimeError(
                "Staged SQLite character ownership is inconsistent."
            )
        evidence_count = int(connection.execute(
            "SELECT COUNT(*) FROM historical_evidence WHERE character_id=?",
            (expected_character,),
        ).fetchone()[0])
        checkpoint = connection.execute(
            """SELECT state, source_record_count, next_index, source_prefix_digest,
                      policy_version, source_key
                 FROM historical_evidence_checkpoints WHERE character_id=?""",
            (expected_character,),
        ).fetchone()
        progress = connection.execute(
            "SELECT * FROM canonical_observation_progress WHERE character_id=? AND consumer='history'",
            (expected_character,),
        ).fetchall()
    finally:
        connection.close()

    try:
        messages = json.loads(paths["conversation"].read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DevelopmentStagedRuntimeError(
            "Staged canonical conversation is malformed."
        ) from error
    if not isinstance(messages, list):
        raise DevelopmentStagedRuntimeError(
            "Staged canonical conversation is not a record list."
        )
    frozen_valid = not (
        evidence_count <= 0
        or checkpoint is None
        or str(checkpoint[0]) != "complete"
        or int(checkpoint[1]) <= 0
        or int(checkpoint[1]) > len(messages)
        or int(checkpoint[2]) != int(checkpoint[1])
        or str(checkpoint[4]) != HISTORICAL_EVIDENCE_POLICY_VERSION
        or str(checkpoint[5]) != "conversation.json"
        or _records_digest(messages, int(checkpoint[1])) != str(checkpoint[3])
    )
    runtime_valid = evidence_count > 0 and _runtime_history_attested(
        progress, messages, character_id=expected_character,
        relative_path=paths['conversation'].relative_to(root))
    if require_rebuilt_history and not (frozen_valid or runtime_valid):
        raise DevelopmentStagedRuntimeError(
            "Staged historical occurrence reconstruction is incomplete."
        )

    # The frozen rebuild still owns exactly its original completed prefix.
    # Runtime appends are original canonical evidence, possibly awaiting the
    # independent durable observers after a crash. Never relabel/rebuild that
    # frozen checkpoint merely to restart a legitimate disposable session.

    validated_episodes = 0
    if require_rebuilt_history:
        store = MemoryV2Store(str(database))
        try:
            scope_id = store.active_truth_scope_id(expected_character)
            scope_kind = store.connection.execute(
                """SELECT scope_kind FROM truth_scopes
                     WHERE character_id=? AND truth_scope_id=?""",
                (expected_character, scope_id),
            ).fetchone()
            validation = EpisodeCompactionCache(
                store, expected_character,
            ).validate_for_context(
                messages,
                active_truth_scope={
                    "kind": str(scope_kind[0]) if scope_kind else "",
                    "scope_id": scope_id,
                },
                allow_historical_recall=True,
            )
            validated_episodes = sum(
                1 for record in validation.lower_records
                if record.accepted
                and record.generation_purpose == EPISODE_PURPOSE_HISTORICAL
            )
            if not validation.accepted or validated_episodes <= 0:
                raise DevelopmentStagedRuntimeError(
                    "Staged historical episode generation is not valid."
                )
        finally:
            store.close()

    return DevelopmentStagedRuntime(
        root, expected_character, database, evidence_count, validated_episodes,
    )


def development_staged_database_path(
    application_dir: str | os.PathLike[str], character_id: str,
) -> Path | None:
    runtime = attest_development_staged_runtime(
        application_dir,
        character_id=character_id,
        # The command/backend startup performs the one-time reconstructed-history
        # attestation.  A disposable evaluation then appends ordinary canonical
        # turns, so subsequent service instances must recheck ownership, marker,
        # and SQLite integrity without treating that legitimate append as a
        # failed frozen-history checkpoint.
        require_rebuilt_history=False,
    )
    return runtime.database_path if runtime is not None else None


@contextmanager
def development_staged_environment(
    data_root: str | os.PathLike[str],
    character_id: str,
    *,
    resource_root: str | os.PathLike[str] | None = None,
    require_rebuilt_history: bool = True,
) -> Iterator[DevelopmentStagedRuntime]:
    """Activate one attested clone for the lifetime of a Development command.

    Normal staged-player/evaluation use requires reconstructed history.  The
    explicit false value exists only for source-state regression checks that
    must reproduce a pre-reconstruction V2 store while retaining every other
    marker, ownership, path, and SQLite-integrity guard.
    """
    keys = (
        DEVELOPMENT_QA_ENV,
        DEVELOPMENT_STAGED_DATA_ROOT_ENV,
        DEVELOPMENT_STAGED_CHARACTER_ID_ENV,
        "AIFREN_RESOURCE_ROOT",
    )
    previous = {key: os.environ.get(key) for key in keys}
    old_cwd = Path.cwd()
    try:
        os.environ[DEVELOPMENT_QA_ENV] = "1"
        os.environ[DEVELOPMENT_STAGED_DATA_ROOT_ENV] = str(Path(data_root).resolve())
        os.environ[DEVELOPMENT_STAGED_CHARACTER_ID_ENV] = str(character_id)
        os.environ["AIFREN_RESOURCE_ROOT"] = str(
            absolute_path(resource_root or Path(__file__).resolve().parent)
        )
        runtime = attest_development_staged_runtime(
            data_root, character_id=character_id,
            require_rebuilt_history=bool(require_rebuilt_history),
        )
        assert runtime is not None
        os.chdir(runtime.data_root)
        yield runtime
    finally:
        os.chdir(old_cwd)
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


__all__ = [
    "DEVELOPMENT_QA_ENV",
    "DevelopmentStagedRuntime",
    "DevelopmentStagedRuntimeError",
    "attest_development_staged_runtime",
    "development_staged_database_path",
    "development_staged_environment",
    "staged_runtime_requested",
]
