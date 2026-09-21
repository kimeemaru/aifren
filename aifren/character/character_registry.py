"""Character identity, explicit storage selection and owned runtime paths.

Names are labels; immutable UUIDs and registry-owned directories authorize
access. Existing entries stay legacy-shared until a confirmed migration.
"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
import unicodedata
import uuid
from aifren.character.character_identity import default_legacy_character_id

REGISTRY_RELATIVE_PATH = Path("characters") / "registry.json"
LEGACY_CONFIG_DIRECTORY = Path("characters") / "default"
REGISTRY_VERSION = 2
_registry_mutex = threading.RLock()

@dataclass(frozen=True)
class Character:
    character_id: str
    display_name: str
    config_directory: str
    created_at: str
    legacy_default: bool = False
    storage_layout: str = "legacy_shared"
    storage_status: str = "ready"
    timeline_generation: str = "legacy"
    source_namespace: str = ""
    migration_source: dict | None = None
    operation: dict | None = None
    creation_request_id: str = ""
    creation_payload_sha256: str = ""
    @property
    def is_legacy_default(self): return self.legacy_default

class CharacterRegistryError(ValueError): pass
class CharacterStorageError(CharacterRegistryError): pass

def normalized_character_name(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()

def write_json_atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".owned-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n"); output.flush(); os.fsync(output.fileno())
        os.replace(name, path)
        from aifren.runtime.file_lock import sync_directory
        sync_directory(path.parent)
    finally: Path(name).unlink(missing_ok=True)

class CharacterRegistry:
    def __init__(self, application_dir="."):
        self.application_dir = Path(application_dir).resolve()
        self.path = self.application_dir / REGISTRY_RELATIVE_PATH
        with self.locked(reload=False): self._data = self._load_or_initialize()

    @contextmanager
    def locked(self, *, reload=True):
        # The one registry write lock also serializes create idempotency and
        # operation pointer publication across backend/maintenance processes.
        from aifren.runtime import file_lock
        with _registry_mutex:
            parent = self.application_dir / "characters"
            if parent.is_symlink(): raise CharacterRegistryError("Unsafe character registry directory")
            parent.mkdir(parents=True, exist_ok=True)
            lock = parent / ".registry.lock"
            if lock.is_symlink(): raise CharacterRegistryError("Unsafe registry lock")
            descriptor = file_lock.open_lock_file(lock)
            with os.fdopen(descriptor, "a+b") as handle:
                file_lock.flock(handle.fileno(), file_lock.LOCK_EX)
                try:
                    if reload: self._data = self._load_or_initialize()
                    yield
                finally: file_lock.flock(handle.fileno(), file_lock.LOCK_UN)

    @property
    def revision(self): return int(self._data.get("revision", 0))
    def refresh(self):
        with self.locked(): pass
        return self
    def list_characters(self): return [self._character(v) for v in self._data["characters"]]
    def get(self, character_id):
        character_id = _canonical_uuid(character_id)
        return next((self._character(v) for v in self._data["characters"] if v["character_id"] == character_id), None)
    def active_or_none(self):
        value = self._data.get("active_character_id")
        return self.get(value) if value else None
    def active(self):
        result = self.active_or_none()
        if result is None: raise CharacterStorageError("No character selected. Create or select a character in Settings.")
        return result
    def select(self, character_id):
        with self.locked():
            character = self.get(character_id)
            if character is None: raise CharacterRegistryError("Character is absent from the registry")
            self._data["active_character_id"] = character.character_id
            self._save()
            return character

    def owned_directory(self, character_id):
        character = self.get(character_id)
        if character is None: raise CharacterRegistryError("Character is absent from the registry")
        relative = Path(character.config_directory)
        if relative.is_absolute() or len(relative.parts) != 2 or relative.parts[0] != "characters" or ".." in relative.parts:
            raise CharacterStorageError("Unsafe character-owned directory")
        directory = self.application_dir / relative
        if directory.is_symlink() or directory.parent.is_symlink() or directory.resolve() != directory:
            raise CharacterStorageError("Character directory cannot be a symlink")
        return directory

    def runtime_paths(self, character_id=None):
        character = self.get(character_id) if character_id else self.active()
        if character is None: raise CharacterRegistryError("Character is absent from the registry")
        return self.runtime_paths_for(character)

    def runtime_paths_for(self, character):
        current = self.get(character.character_id)
        if current is None or (character.config_directory, character.legacy_default) != (current.config_directory, current.legacy_default):
            raise CharacterStorageError("Operation identity does not own this directory")
        if character.legacy_default and (character.character_id != default_legacy_character_id() or character.config_directory != LEGACY_CONFIG_DIRECTORY.as_posix()):
            raise CharacterStorageError("Only the legacy default identity owns root compatibility files")
        directory = self.owned_directory(character.character_id)
        canonical = self.application_dir if character.legacy_default and character.storage_layout == "legacy_shared" else directory
        database = (directory / "memory_v2.sqlite3" if character.storage_layout == "local"
                    else self.application_dir / "memory_v2" / "memory_v2.sqlite3")
        paths = dict(character=directory / "character.json", personality=directory / "personality.md",
                     conversation=canonical / "conversation.json", memory=canonical / "memories.json",
                     summary=canonical / "conversation_summary.json", memory_v2=database,
                     attention=directory / "transient_impulses.sqlite3", presentation=directory / "presentation.json",
                     cache=directory / "derived", voice_profile=directory / "voice_profile.json",
                     voice_assets=directory / "voice", directory=directory)
        for value in paths.values():
            if value.is_symlink() or value.resolve() != value:
                raise CharacterStorageError("A character-owned path escapes its directory")
        return paths

    def retained_source_paths(self, character_id, descriptor):
        """A retained reference cannot grant ownership of another character."""
        if not descriptor: return None, ()
        c = self.get(character_id)
        if c is None or c.storage_layout != "local" or not isinstance(descriptor, dict):
            raise CharacterStorageError("Invalid retained migration source")
        original = Character(**{**asdict(c), "storage_layout": "legacy_shared"})
        paths = self.runtime_paths_for(original)
        expected_database = paths["memory_v2"].relative_to(self.application_dir).as_posix()
        allowed = {paths[k].relative_to(self.application_dir).as_posix() for k in ("conversation", "memory", "summary")
                   if not paths[k].is_relative_to(paths["directory"])}
        files = descriptor.get("files", [])
        if (descriptor.get("database") != expected_database or not isinstance(files, list)
                or any(not isinstance(v, str) or v not in allowed for v in files) or len(files) != len(set(files))):
            raise CharacterStorageError("Retained migration paths do not belong to this character's original layout")
        return paths["memory_v2"], tuple(self.application_dir / v for v in files)

    def canonical_namespace(self, character_id):
        c = self.get(character_id)
        return c.source_namespace or self.runtime_paths(character_id)["conversation"].relative_to(self.application_dir).as_posix()

    def assert_storage_ready(self, character_id):
        from contextlib import closing
        from aifren.memory_v2_store.character_copy import CharacterCopyError, validate_character_database_layout

        c = self.get(character_id)
        paths = self.runtime_paths(character_id)
        if c.storage_status != "ready" or c.operation:
            raise CharacterStorageError("Character storage operation is incomplete. Use Settings > Character to resume it.")
        if c.storage_layout != "local":
            self._assert_legacy_files_consistent(c, paths)
            return paths
        for key in ("character", "personality", "conversation", "memory_v2"):
            if not paths[key].is_file():
                raise CharacterStorageError("Character-local storage is incomplete. No legacy fallback was loaded; use Character management.")
        try:
            # A finite profile read prevents damaged/local replacement files
            # from reaching the character loader's generic compatibility path.
            # Names remain labels; migrated profiles need not acquire new IDs.
            with paths["character"].open("rb") as source:
                raw = source.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise CharacterStorageError("Character profile exceeds its read bound")
            profile = json.loads(raw.decode("utf-8"))
            name = profile.get("name") if isinstance(profile, dict) else None
            if not isinstance(name, str) or not name.strip() or any(ord(ch) < 32 for ch in name):
                raise CharacterStorageError("Character profile has no readable name")
            for key in ("character_id", "_character_id"):
                if key in profile and profile[key] != c.character_id:
                    raise CharacterStorageError("Character profile identity does not match its registry entry")
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CharacterStorageError("Character profile is unreadable. No replacement profile was loaded.") from error
        try:
            with closing(sqlite3.connect(paths["memory_v2"].as_uri() + "?mode=ro", uri=True)) as db:
                db.execute("PRAGMA query_only=ON")
                db.execute("BEGIN")
                validate_character_database_layout(db, c.character_id)
                meta = dict(db.execute("SELECT key,value FROM database_meta WHERE key IN ('storage_character_id','timeline_generation')"))
                if meta != {"storage_character_id": c.character_id, "timeline_generation": c.timeline_generation}:
                    raise CharacterStorageError("Character database identity or timeline does not match its registry entry")
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise CharacterStorageError("Character database is damaged")
        except CharacterCopyError as error:
            raise CharacterStorageError("Character-local schema or ownership is invalid. No legacy fallback was loaded.") from error
        except sqlite3.Error as error:
            raise CharacterStorageError("Character database is unreadable. No legacy fallback was loaded.") from error
        return paths

    def _assert_legacy_files_consistent(self, c, paths):
        """Missing legacy files are not permission to revive external state.

        Only inspect the selected UUID's existence, never another character's
        payload. An untouched empty legacy installation keeps its compatibility
        initialization. Existing external continuity plus erased files blocks
        ordinary binding and leaves the explicit manager reachable.
        """
        if paths["conversation"].is_file() and paths["character"].is_file(): return
        if not paths["memory_v2"].exists(): return
        from contextlib import closing
        try:
            with closing(sqlite3.connect(paths["memory_v2"].as_uri()+"?mode=ro",uri=True)) as db:
                db.execute("PRAGMA query_only=ON")
                tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                retained=False
                for table in ("events","claims","active_scene_relations","open_threads"):
                    if table in tables and db.execute(f'SELECT 1 FROM "{table}" WHERE character_id=? LIMIT 1',(c.character_id,)).fetchone():
                        retained=True;break
                if retained:
                    raise CharacterStorageError("Character files are missing while legacy continuity still exists. Nothing was restored or reset. Use Character management to review recovery or an explicit reset/delete.")
        except sqlite3.Error as error:
            raise CharacterStorageError("Legacy storage cannot be checked safely. No replacement timeline was created.") from error

    def create(self, display_name, *, description="", personality=None, request_id=""):
        name = str(display_name or "").strip()
        if not name or len(name) > 100 or any(ord(ch) < 32 for ch in name):
            raise CharacterRegistryError("Enter a readable character name (1–100 characters)")
        request_id = str(request_id or "")
        if len(request_id) > 128: raise CharacterRegistryError("Invalid create request identity")
        payload_digest=hashlib.sha256(json.dumps([normalized_character_name(name),str(description or ""),personality],ensure_ascii=False).encode()).hexdigest()
        with self.locked():
            if request_id:
                prior = next((c for c in self.list_characters() if c.creation_request_id == request_id), None)
                if prior:
                    if prior.storage_status != "ready":
                        raise CharacterStorageError("The earlier create operation is incomplete. Use Character management.")
                    if normalized_character_name(prior.display_name) != normalized_character_name(name):
                        raise CharacterRegistryError("Create request was already used for a different name")
                    if prior.creation_payload_sha256 and prior.creation_payload_sha256 != payload_digest:
                        raise CharacterRegistryError("Create request was already used for a different profile")
                    return prior
            if any(normalized_character_name(c.display_name) == normalized_character_name(name) for c in self.list_characters()):
                raise CharacterRegistryError("A character with that name already exists. Choose a different name.")
            identity = str(uuid.uuid4())
            slug = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()).strip("-")[:40] or "character"
            relative = Path("characters") / (slug + "--" + identity)
            generation = str(uuid.uuid4())
            value = asdict(Character(identity, name, relative.as_posix(), _now(), storage_layout="local",
                                     timeline_generation=generation, source_namespace=(relative / "conversation.json").as_posix(),
                                     creation_request_id=request_id, creation_payload_sha256=payload_digest, storage_status="creating"))
            self._data["characters"].append(value)
            if not self._data.get("active_character_id"): self._data["active_character_id"] = identity
            self._save()  # A crash leaves a visible incomplete identity, never a hidden orphan.
            directory = self.owned_directory(identity)
            created_directory = False
            try:
                directory.mkdir(mode=0o700, exist_ok=False)
                created_directory = True
                write_json_atomic(directory / "character.json", {
                    "name": name, "description": str(description or ""), "version": "1.0",
                    "voice": {"provider": None, "voice_id": None},
                    "avatar": {"enabled": True, "model": None, "path": None}})
                (directory / "personality.md").write_text(personality if personality is not None else "You are a friendly AI companion.\n", encoding="utf-8")
                self.initialize_empty_continuity(identity)
                value["storage_status"] = "ready"; self._save()
            except Exception:
                if created_directory and directory.is_dir() and not directory.is_symlink(): shutil.rmtree(directory)
                self._data["characters"] = [v for v in self._data["characters"] if v["character_id"] != identity]
                if self._data["active_character_id"] == identity:
                    self._data["active_character_id"] = self._data["characters"][0]["character_id"] if self._data["characters"] else None
                self._save(); raise
            return self._character(value)

    def initialize_empty_continuity(self, character_id):
        c = self.get(character_id); paths = self.runtime_paths(character_id)
        if c.storage_layout != "local": raise CharacterStorageError("Empty initialization requires explicit local selection")
        if paths["memory_v2"].exists(): raise CharacterStorageError("Refusing to overwrite a continuity database")
        for key in ("conversation", "memory"): write_json_atomic(paths[key], [])
        write_json_atomic(paths["summary"], {"summary": "", "summarized_messages": 0})
        from aifren.memory_v2_store import MemoryV2Repository, MemoryV2Store
        store = MemoryV2Store(str(paths["memory_v2"]))
        try:
            MemoryV2Repository(store).ensure_character(c.character_id, c.display_name)
            with store.transaction():
                store.connection.executemany("INSERT OR REPLACE INTO database_meta(key,value) VALUES (?,?)",
                    (("storage_character_id", c.character_id), ("timeline_generation", c.timeline_generation)))
        finally: store.close()

    def update(self, character_id, *, display_name=None):
        with self.locked():
            value = self._entry(character_id)
            if display_name is not None:
                name = str(display_name).strip()
                if not name or len(name) > 100 or any(ord(c) < 32 for c in name):
                    raise CharacterRegistryError("Use a readable character name of 1–100 characters")
                if any(c.character_id != character_id and normalized_character_name(c.display_name) == normalized_character_name(name)
                       for c in self.list_characters()):
                    raise CharacterRegistryError("A character with that name already exists. Choose a different name.")
                value["display_name"] = name
            self._save(); return self._character(value)
    def _entry(self, identity):
        identity = _canonical_uuid(identity)
        for value in self._data["characters"]:
            if value["character_id"] == identity: return value
        raise CharacterRegistryError("Character is absent from the registry")
    def _load_or_initialize(self):
        if self.path.is_symlink(): raise CharacterStorageError("Registry cannot be a symlink")
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8")); self._validate(data)
                data["version"] = REGISTRY_VERSION; data.setdefault("revision", 0)
                return data
            except (OSError, json.JSONDecodeError) as error:
                raise CharacterRegistryError("Character registry is unreadable") from error
        # Compatibility adoption describes known legacy locations; it performs
        # no memory import or migration and never scans character directories.
        legacy = self.application_dir / LEGACY_CONFIG_DIRECTORY
        name = "AIFren"
        try:
            raw = json.loads((legacy / "character.json").read_text(encoding="utf-8"))
            if isinstance(raw.get("name"), str) and raw["name"].strip(): name = raw["name"].strip()
        except (OSError, json.JSONDecodeError): pass
        value = asdict(Character(default_legacy_character_id(), name, LEGACY_CONFIG_DIRECTORY.as_posix(), _now(), True,
                                 source_namespace="conversation.json"))
        self._data = {"version": REGISTRY_VERSION, "revision": 0, "active_character_id": value["character_id"], "characters": [value]}
        self._save(); return self._data
    def _save(self):
        self._data["version"] = REGISTRY_VERSION
        self._data["revision"] = self.revision + 1
        write_json_atomic(self.path, self._data)
    @staticmethod
    def _character(value): return Character(**value)
    @staticmethod
    def _validate(data):
        if not isinstance(data, dict) or data.get("version") not in (1, REGISTRY_VERSION):
            raise CharacterRegistryError("Unsupported registry version")
        if not isinstance(data.get("characters"), list): raise CharacterRegistryError("Invalid character list")
        ids=set(); directories=set()
        for value in data["characters"]:
            try: c=Character(**value)
            except (TypeError, ValueError) as error: raise CharacterRegistryError("Invalid character record") from error
            identity=_canonical_uuid(c.character_id)
            if identity in ids or c.config_directory in directories: raise CharacterRegistryError("Duplicate character ownership")
            ids.add(identity); directories.add(c.config_directory)
            if not c.display_name.strip() or c.storage_layout not in ("local", "legacy_shared"):
                raise CharacterRegistryError("Invalid character identity/storage")
            if c.storage_layout == "local" and not c.timeline_generation: raise CharacterRegistryError("Missing timeline identity")
            if c.storage_status not in {"ready", "creating", "incomplete"}:
                raise CharacterRegistryError("Unknown character storage status")
            if c.legacy_default and (identity != default_legacy_character_id() or c.config_directory != LEGACY_CONFIG_DIRECTORY.as_posix()):
                raise CharacterRegistryError("Invalid legacy default ownership")
        if data.get("active_character_id") is not None and data.get("active_character_id") not in ids:
            raise CharacterRegistryError("Active character is absent")

def _canonical_uuid(value: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as error:
        raise CharacterRegistryError("character_id must be a UUID") from error


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description="AIFren character identity registry helper.")
    parser.add_argument("command", choices=("list", "active", "select"))
    parser.add_argument("character_id", nargs="?")
    parser.add_argument("--application-dir", default=".")
    args = parser.parse_args()
    registry = CharacterRegistry(args.application_dir)
    if args.command == "list":
        print(json.dumps([asdict(character) for character in registry.list_characters()], indent=2, sort_keys=True))
    elif args.command == "active":
        print(json.dumps(asdict(registry.active()), indent=2, sort_keys=True))
    else:
        if not args.character_id:
            raise SystemExit("select requires character_id")
        print(json.dumps(asdict(registry.select(args.character_id)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
