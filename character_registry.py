"""Durable, local-first character identity and selection registry.

The legacy installation remains character ``characters/default`` and keeps its
existing canonical JSON files in their historical locations.  New characters
receive independent configuration, V1 memory, and conversation paths.  Visual
asset files are intentionally absent from this registry: Unity owns one global
reusable library, while its local presentation settings associate only a
stable selected-avatar identifier with each character.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import tempfile
import uuid

from character_identity import default_legacy_character_id


REGISTRY_RELATIVE_PATH = Path("characters") / "registry.json"
LEGACY_CONFIG_DIRECTORY = Path("characters") / "default"
REGISTRY_VERSION = 1


@dataclass(frozen=True)
class Character:
    character_id: str
    display_name: str
    config_directory: str
    created_at: str
    legacy_default: bool = False

    @property
    def is_legacy_default(self) -> bool:
        return self.legacy_default


class CharacterRegistryError(ValueError):
    pass


class CharacterRegistry:
    """Owns durable character identity and active-character selection only."""

    def __init__(self, application_dir: str | Path = ".") -> None:
        self.application_dir = Path(application_dir).resolve()
        self.path = self.application_dir / REGISTRY_RELATIVE_PATH
        self._data = self._load_or_initialize()

    def list_characters(self) -> list[Character]:
        return [self._character(value) for value in self._data["characters"]]

    def get(self, character_id: str) -> Character | None:
        character_id = _canonical_uuid(character_id)
        for value in self._data["characters"]:
            if value["character_id"] == character_id:
                return self._character(value)
        return None

    def active(self) -> Character:
        character = self.get(self._data["active_character_id"])
        if character is None:  # Defensive repair of a manually edited registry.
            raise CharacterRegistryError("active character is absent from the registry")
        return character

    def select(self, character_id: str) -> Character:
        character = self.get(character_id)
        if character is None:
            raise CharacterRegistryError("character is absent from the registry")
        self._data["active_character_id"] = character.character_id
        self._save()
        return character

    def create(self, display_name: str, *, description: str = "", personality: str | None = None) -> Character:
        name = str(display_name or "").strip()
        if not name:
            raise CharacterRegistryError("character display name is required")
        character_id = str(uuid.uuid4())
        directory = Path("characters") / character_id
        absolute_directory = self.application_dir / directory
        absolute_directory.mkdir(parents=True, exist_ok=False)
        character_json = {
            "name": name,
            "description": str(description or ""),
            "version": "1.0",
            "voice": {"provider": None, "voice_id": None},
            "avatar": {"enabled": True, "model": None, "path": None},
        }
        (absolute_directory / "character.json").write_text(
            json.dumps(character_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (absolute_directory / "personality.md").write_text(
            personality if personality is not None else "You are a friendly AI companion.\n", encoding="utf-8"
        )
        value = {
            "character_id": character_id,
            "display_name": name,
            "config_directory": directory.as_posix(),
            "created_at": _now(),
            "legacy_default": False,
        }
        self._data["characters"].append(value)
        self._save()
        return self._character(value)

    def update(self, character_id: str, *, display_name: str | None = None) -> Character:
        character_id = _canonical_uuid(character_id)
        for value in self._data["characters"]:
            if value["character_id"] != character_id:
                continue
            if display_name is not None:
                name = str(display_name).strip()
                if not name:
                    raise CharacterRegistryError("character display name is required")
                value["display_name"] = name
            self._save()
            return self._character(value)
        raise CharacterRegistryError("character is absent from the registry")

    def runtime_paths(self, character_id: str | None = None) -> dict[str, Path]:
        """Resolve a selected character without moving legacy canonical data."""
        character = self.get(character_id) if character_id is not None else self.active()
        if character is None:
            raise CharacterRegistryError("character is absent from the registry")
        config_directory = self.application_dir / character.config_directory
        if character.legacy_default:
            return {
                "character": config_directory / "character.json",
                "personality": config_directory / "personality.md",
                "memory": self.application_dir / "memories.json",
                "conversation": self.application_dir / "conversation.json",
                "summary": self.application_dir / "conversation_summary.json",
            }
        return {
            "character": config_directory / "character.json",
            "personality": config_directory / "personality.md",
            "memory": config_directory / "memories.json",
            "conversation": config_directory / "conversation.json",
            "summary": config_directory / "conversation_summary.json",
        }

    def _load_or_initialize(self) -> dict:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._validate(data)
                return data
            except (OSError, json.JSONDecodeError, CharacterRegistryError) as error:
                raise CharacterRegistryError(f"character registry is malformed: {type(error).__name__}") from error

        legacy_directory = self.application_dir / LEGACY_CONFIG_DIRECTORY
        legacy_name = "AIFren"
        try:
            raw = json.loads((legacy_directory / "character.json").read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("name"), str) and raw["name"].strip():
                legacy_name = raw["name"].strip()
        except (OSError, json.JSONDecodeError):
            pass
        legacy_id = default_legacy_character_id()
        data = {
            "version": REGISTRY_VERSION,
            "active_character_id": legacy_id,
            "characters": [{
                "character_id": legacy_id,
                "display_name": legacy_name,
                "config_directory": LEGACY_CONFIG_DIRECTORY.as_posix(),
                "created_at": _now(),
                "legacy_default": True,
            }],
        }
        self._data = data
        self._save()
        return data

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".registry.", suffix=".tmp", dir=self.path.parent)
        try:
            with open(descriptor, "w", encoding="utf-8", closefd=True) as file:
                json.dump(self._data, file, ensure_ascii=False, indent=2, sort_keys=True)
                file.write("\n")
                file.flush()
            Path(temporary).replace(self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    @staticmethod
    def _character(value: dict) -> Character:
        return Character(**value)

    @staticmethod
    def _validate(data: object) -> None:
        if not isinstance(data, dict) or data.get("version") != REGISTRY_VERSION:
            raise CharacterRegistryError("unsupported registry version")
        characters = data.get("characters")
        if not isinstance(characters, list) or not characters:
            raise CharacterRegistryError("registry requires at least one character")
        ids = set()
        for value in characters:
            if not isinstance(value, dict):
                raise CharacterRegistryError("invalid character record")
            character_id = _canonical_uuid(value.get("character_id"))
            if character_id in ids:
                raise CharacterRegistryError("duplicate character id")
            ids.add(character_id)
            if not isinstance(value.get("display_name"), str) or not value["display_name"].strip():
                raise CharacterRegistryError("invalid character display name")
            if not isinstance(value.get("config_directory"), str) or not value["config_directory"].strip():
                raise CharacterRegistryError("invalid character config directory")
        if _canonical_uuid(data.get("active_character_id")) not in ids:
            raise CharacterRegistryError("active character is absent")


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
