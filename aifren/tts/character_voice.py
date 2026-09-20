"""Character-selected synthesis through the existing continuous playback owner."""
from __future__ import annotations
from dataclasses import asdict
import threading
import tempfile
from pathlib import Path

from aifren.character.voice_profile import CharacterVoiceProfiles, VoiceProfile
from aifren.tts.clone_runtime import CloneRuntime, CloneCancelled
from aifren.tts.tts import ContinuousPlaybackTTS

PREVIEW_TEXT = "Hello. This is a preview of my voice."

class CharacterVoiceTTS(ContinuousPlaybackTTS):
    # This provider never speaks uncommitted generation deltas.
    @property
    def supports_early_speech(self):
        return self.profile.engine == "kokoro" and bool(getattr(self.kokoro, "supports_early_speech", False))

    def __init__(self, kokoro, registry, character_id, *, runtime=None):
        self.kokoro = kokoro
        self.registry = registry
        self.profiles = CharacterVoiceProfiles(registry)
        self.runtime = runtime or CloneRuntime()
        self._profile_lock = threading.RLock()
        self._voice_job_cancel = threading.Event()
        self._voice_job_id = 0
        self._voice_status = "ready"
        self._voice_error = ""
        self._initialize_playback_state()
        self._initialize_continuous_state()
        self.bind_character(character_id)

    @property
    def voice(self):
        return "Reference voice" if self.profile.engine == "gpt_sovits" else getattr(self.kokoro, "voice", "default")

    @property
    def device(self):
        return "cpu" if self.profile.engine == "gpt_sovits" else getattr(self.kokoro, "device", "cpu")

    @property
    def effective_provider(self):
        return self.profile.engine

    @property
    def synthesis_strategy(self):
        return getattr(self.kokoro, "synthesis_strategy", "whole_response") if self.profile.engine == "kokoro" else "whole_response"

    @property
    def committed_unit_policy(self):
        if self.profile.engine != "gpt_sovits":
            return None
        from aifren.tts.streaming import CommittedSpeechUnitPolicy
        # Substantial sentence groups amortize reference-conditioned inference.
        # These bound native work, not the words in the committed reply.
        return CommittedSpeechUnitPolicy(90, 220, 300, 90, 180, 240)

    def bind_character(self, character_id):
        self.cancel_voice_job()
        self.stop()
        with self._profile_lock:
            self.registry.refresh()
            self.character_id = character_id
            self._voice_error = ""
            self._profile_error = ""
            try:
                self.profile = self.profiles.load(character_id)
                self._voice_status = ("ready" if self.profile.engine == "kokoro" else
                                      "unavailable" if not self.runtime.available() else "saved")
            except Exception:
                # Never silently use Kokoro for a saved but damaged clone profile.
                self.profile = VoiceProfile(revision=self.profiles.revision(character_id))
                self._voice_status = "failed"
                self._voice_error = "The saved voice profile is unavailable. Select and save a voice to recover."
                self._profile_error = self._voice_error

    def snapshot(self):
        with self._profile_lock:
            value = asdict(self.profile)
            value.update(state=self._voice_status, message=self._voice_error,
                         installed=self.runtime.available(), device=self.device,
                         active_engine=self.effective_provider,
                         reference_name=Path(self.profile.reference).name if self.profile.reference else "",
                         job_id=self._voice_job_id)
            # Full transcript belongs only to the local settings response, never
            # recorder/default log fields. Reference is managed-relative, not external.
            return value

    def cancel_voice_job(self):
        with self._profile_lock:
            self._voice_job_cancel.set()
            self._voice_job_id += 1
            if self._voice_status == "preparing":
                self._voice_status = "cancelled"

    def begin_voice_job(self):
        self.cancel_voice_job()
        self.stop()
        with self._profile_lock:
            cancel = self._voice_job_cancel = threading.Event()
            self._voice_status, self._voice_error = "preparing", ""
            return self._voice_job_id, cancel, self.character_id

    def _job_current(self, job):
        identity, cancel, character = job
        return identity == self._voice_job_id and not cancel.is_set() and character == self.character_id

    def owns_voice_preview(self, job_id):
        with self._profile_lock:
            return job_id == self._voice_job_id and not self._voice_job_cancel.is_set()

    def stop_voice_preview(self, job):
        # Retire the job and capture its PCM owner under the same lock used
        # before a conversational replacement claims synthesis. The subsequent
        # stop is conditional; a new reply may start after this lock releases.
        with self._profile_lock:
            if not self._job_current(job):
                return None
            generation = self.playback_generation
            self.cancel_voice_job()
            self._voice_status = "cancelled"
        return self.stop_if_generation(generation)

    def edit_voice(self, action, payload, *, job, guard):
        identity, cancel, character = job
        def current():
            guard()
            if not self._job_current(job):
                raise CloneCancelled()
        try:
            current()
            self.registry.refresh()
            generation = self.registry.get(character).timeline_generation
            profile, data = self.profiles.draft(character, **payload)
            if action in {"prepare", "preview"}:
                if profile.engine == "kokoro":
                    prepared = (self.kokoro.prepare_cancellable_stream_chunk(
                        PREVIEW_TEXT, cancelled=cancel) if action == "preview" else None)
                else:
                    # Temporary conditioning input is character-owned and removed
                    # after this job. Save publishes a separate verified copy.
                    directory = self.registry.owned_directory(character)
                    with tempfile.TemporaryDirectory(prefix=".voice-preview-", dir=directory) as temporary:
                        reference = Path(temporary) / "reference.wav"
                        reference.write_bytes(data)
                        self.runtime.request(profile, reference, cancelled=cancel)
                        prepared = (self.runtime.request(profile, reference,
                            text=PREVIEW_TEXT, cancelled=cancel) if action == "preview" else None)
                current()
                if action == "preview":
                    # Dispatch uses the very same cancellation/PCM owner as speech.
                    with self._profile_lock:
                        current()
                        self.begin_prepared_stream(prepared, cancelled=cancel, metadata={
                            "voice_preview": True, "preview_job_id": identity,
                            "content": PREVIEW_TEXT, "subtitle_content": PREVIEW_TEXT,
                        })
                        self.finish_prepared_stream()
            elif action == "save":
                with self._profile_lock:
                    current()
                    self.profile = self.profiles.save(character, generation, profile, data, guard=current)
                    self._profile_error = ""
            else:
                raise ValueError("Unsupported voice operation.")
            with self._profile_lock:
                current()
                self._voice_status = ("saved" if action == "save" and profile.engine == "gpt_sovits" else "ready")
                self._voice_error = ""
            return self.snapshot()
        except CloneCancelled:
            return None
        except Exception as error:
            with self._profile_lock:
                if self._job_current(job):
                    self._voice_status = "failed"
                    self._voice_error = str(error) if isinstance(error, (ValueError, RuntimeError)) else "Voice operation failed. Check the recording and retry."
            return self.snapshot() if self._job_current(job) else None

    def _generate_audio(self, text, generation=None, *, cancelled=None):
        with self._profile_lock:
            profile, character = self.profile, self.character_id
            if self._profile_error:
                raise RuntimeError(self._profile_error)
        if profile.engine == "kokoro":
            return self.kokoro.prepare_cancellable_stream_chunk(text, cancelled=cancelled)
        reference = self.profiles.reference_path(character, profile)
        try:
            result = self.runtime.request(profile, reference, text=text, cancelled=cancelled)
        except CloneCancelled:
            return None
        with self._profile_lock:
            if profile != self.profile or character != self.character_id:
                return None
            self._voice_status = "ready"
        return result

    def fallback_to_cpu_after_resource_failure(self):
        return self.profile.engine == "kokoro" and self.kokoro.fallback_to_cpu_after_resource_failure()

    def close(self):
        self.cancel_voice_job()
        self.stop()
        self.runtime.close()
        close = getattr(self.kokoro, "close", None)
        if callable(close):
            close()
