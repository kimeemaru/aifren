"""Authored character voice choices, separate from learned continuity.

External recordings are read-only import sources. Only verified managed copies
under the registry's voice directory are saved; timeline reset preserves them.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import io
import json
from pathlib import Path
import re
import uuid
import wave

from aifren.character.character_registry import CharacterStorageError, write_json_atomic

ENGINE_MODEL = "gpt-sovits-v2ProPlus:48b1a0169a28582a8984402f82cf438d3bfa6aca"
MAX_REFERENCE_BYTES = 12 * 1024 * 1024
MAX_PROFILE_BYTES = 32 * 1024
LANGUAGES = ("en", "all_zh", "all_ja", "all_ko", "all_yue")


@dataclass(frozen=True)
class VoiceProfile:
    version: int = 1
    engine: str = "kokoro"
    language: str = "en"
    transcript: str = ""
    reference: str = ""
    reference_sha256: str = ""
    model: str = ENGINE_MODEL
    revision: str = "initial"

    @property
    def conditioning_key(self):
        payload = (self.reference_sha256, self.transcript, self.language, self.model)
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def reference_bytes(path: Path) -> bytes:
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError("Choose a regular WAV recording, not a link or directory.")
    if not 44 <= path.stat().st_size <= MAX_REFERENCE_BYTES:
        raise ValueError("The reference WAV must be smaller than 12 MiB.")
    with path.open("rb") as stream:
        data = stream.read(MAX_REFERENCE_BYTES + 1)
    if len(data) > MAX_REFERENCE_BYTES:
        raise ValueError("The reference recording changed or is too large.")
    try:
        with wave.open(io.BytesIO(data)) as audio:
            duration = audio.getnframes() / audio.getframerate()
            if audio.getnchannels() not in (1, 2) or audio.getsampwidth() not in (1, 2, 3, 4):
                raise ValueError()
            if not 3 <= duration <= 10:
                raise ValueError("Use a 3–10 second WAV excerpt and its matching transcript.")
            expected = audio.getnframes() * audio.getnchannels() * audio.getsampwidth()
            if len(audio.readframes(audio.getnframes())) != expected:
                raise ValueError("The WAV recording is incomplete. Choose the original complete excerpt.")
    except (wave.Error, EOFError, ZeroDivisionError) as error:
        raise ValueError("Use an uncompressed PCM WAV reference.") from error
    return data


class CharacterVoiceProfiles:
    def __init__(self, registry):
        self.registry = registry

    def load(self, character_id):
        path = self.registry.runtime_paths(character_id)["voice_profile"]
        if not path.exists():
            return VoiceProfile()
        try:
            if path.stat().st_size > MAX_PROFILE_BYTES:
                raise ValueError()
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.pop("character_id") != character_id:
                raise ValueError()
            profile = VoiceProfile(**value)
            self.validate(profile)
            return profile
        except (OSError, TypeError, KeyError, ValueError) as error:
            raise CharacterStorageError("This character's saved voice profile is unavailable; choose or save a voice in Settings.") from error

    def revision(self, character_id):
        try:
            return self.load(character_id).revision
        except CharacterStorageError:
            path = self.registry.runtime_paths(character_id)["voice_profile"]
            with path.open("rb") as source:
                return "damaged-" + hashlib.file_digest(source, "sha256").hexdigest()

    @staticmethod
    def validate(profile):
        if (profile.version != 1 or profile.engine not in ("kokoro", "gpt_sovits")
                or profile.language not in LANGUAGES or profile.model != ENGINE_MODEL
                or not isinstance(profile.transcript, str) or len(profile.transcript) > 2000
                or not isinstance(profile.revision, str) or len(profile.revision) > 128
                or not isinstance(profile.reference, str)
                or not isinstance(profile.reference_sha256, str)):
            raise ValueError("Unsupported voice profile settings.")
        if profile.reference and (not re.fullmatch(r"voice/[0-9a-f]{64}\.wav", profile.reference)
                                  or profile.reference != f"voice/{profile.reference_sha256}.wav"):
            raise ValueError("Invalid managed voice reference.")
        if profile.engine == "gpt_sovits" and (not profile.reference or not profile.transcript.strip()):
            raise ValueError("A cloned voice needs a WAV reference and its exact transcript.")

    def reference_path(self, character_id, profile):
        self.validate(profile)
        path = self.registry.owned_directory(character_id) / profile.reference
        data = reference_bytes(path)
        if hashlib.sha256(data).hexdigest() != profile.reference_sha256:
            raise ValueError("The saved reference changed. Import and prepare the intended recording again.")
        return path

    def draft(self, character_id, *, engine, language="en", transcript="", reference_path="", revision):
        try:
            current = self.load(character_id)
        except CharacterStorageError:
            # Explicit Save of Kokoro replaces only this selected damaged
            # authored profile, with a fingerprint CAS; no fallback during speech.
            if engine != "kokoro":
                raise
            current = VoiceProfile(revision=self.revision(character_id))
        if revision != current.revision:
            raise ValueError("The saved voice changed. Refresh before saving this draft.")
        data = None
        if reference_path:
            data = reference_bytes(Path(reference_path))
            digest = hashlib.sha256(data).hexdigest()
            reference = f"voice/{digest}.wav"
        else:
            digest, reference = current.reference_sha256, current.reference
        profile = VoiceProfile(engine=engine, language=language, transcript=transcript.strip(),
                               reference=reference, reference_sha256=digest, revision=revision)
        self.validate(profile)
        if engine == "gpt_sovits" and data is None:
            data = reference_bytes(self.reference_path(character_id, profile))
        return profile, data

    def save(self, character_id, timeline_generation, profile, data, *, guard):
        self.validate(profile)
        with self.registry.locked():
            character = self.registry.get(character_id)
            guard()
            if (character is None or character.timeline_generation != timeline_generation
                    or character.storage_status != "ready"
                    or self.revision(character_id) != profile.revision):
                raise CharacterStorageError("The character or saved voice changed; refresh before saving.")
            paths = self.registry.runtime_paths(character_id)
            if data is not None:
                if hashlib.sha256(data).hexdigest() != profile.reference_sha256:
                    raise ValueError("Reference verification failed.")
                paths["voice_assets"].mkdir(exist_ok=True)
                destination = paths["directory"] / profile.reference
                if destination.is_symlink():
                    raise CharacterStorageError("Unsafe voice reference destination.")
                if destination.exists():
                    if destination.read_bytes() != data:
                        raise CharacterStorageError("Managed voice reference changed.")
                else:
                    # Content addressed, exclusive creation. No external original is moved.
                    with destination.open("xb") as output:
                        output.write(data)
                        output.flush()
                        import os
                        os.fsync(output.fileno())
            saved = VoiceProfile(**{**asdict(profile), "revision": uuid.uuid4().hex})
            guard()
            write_json_atomic(paths["voice_profile"], {**asdict(saved), "character_id": character_id})
            return saved
