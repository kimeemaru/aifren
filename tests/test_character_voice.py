import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock
import wave

import numpy as np

from aifren.character.character_registry import CharacterRegistry
from aifren.character.voice_profile import CharacterVoiceProfiles, VoiceProfile, reference_bytes
from aifren.tts.character_voice import CharacterVoiceTTS
from aifren.tts.clone_runtime import CloneCancelled, CloneRuntime


def recording(path):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000)
        wav.writeframes(b"\0\0" * 32000)


class CharacterVoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = CharacterRegistry(self.root)
        self.a = self.registry.create("Harbor", personality="Synthetic test companion.")
        self.b = self.registry.create("Cedar", personality="Another synthetic companion.")
        self.profiles = CharacterVoiceProfiles(self.registry)
        self.reference = self.root / "external.wav"
        recording(self.reference)

    def draft(self):
        return self.profiles.draft(self.a.character_id, engine="gpt_sovits", language="en",
            transcript="This is an independently synthetic transcript.", reference_path=str(self.reference), revision="initial")

    def save(self):
        profile, data = self.draft()
        return self.profiles.save(self.a.character_id, self.a.timeline_generation, profile, data, guard=lambda: None)

    def test_save_restart_and_other_character_default(self):
        original = self.reference.read_bytes()
        saved = self.save()
        reopened = CharacterVoiceProfiles(CharacterRegistry(self.root))
        self.assertEqual(reopened.load(self.a.character_id), saved)
        self.assertEqual(reopened.load(self.b.character_id).engine, "kokoro")
        self.assertEqual(reopened.reference_path(self.a.character_id, saved).read_bytes(), original)
        self.assertEqual(self.reference.read_bytes(), original)

    def test_draft_and_cancel_write_no_profile_or_reference(self):
        self.draft()
        paths = self.registry.runtime_paths(self.a.character_id)
        self.assertFalse(paths["voice_profile"].exists())
        self.assertFalse(paths["voice_assets"].exists())

    def test_old_revision_and_timeline_cannot_save(self):
        old, data = self.draft()
        self.save()
        for timeline in (self.a.timeline_generation, "retired"):
            with self.assertRaises(ValueError):
                self.profiles.save(self.a.character_id, timeline, old, data, guard=lambda: None)

    def test_tampered_reference_and_wrong_owner_fail_closed(self):
        saved = self.save()
        path = self.profiles.reference_path(self.a.character_id, saved)
        path.write_bytes(path.read_bytes()[:-2] + b"xx")
        with self.assertRaises(ValueError):
            self.profiles.reference_path(self.a.character_id, saved)
        raw = self.registry.runtime_paths(self.a.character_id)["voice_profile"].read_text()
        self.registry.runtime_paths(self.b.character_id)["voice_profile"].write_text(raw)
        with self.assertRaises(ValueError):
            self.profiles.load(self.b.character_id)

    def test_symlink_and_invalid_duration_rejected(self):
        link = self.root / "link.wav"; link.symlink_to(self.reference)
        with self.assertRaises(ValueError):
            reference_bytes(link)
        with wave.open(str(self.root / "short.wav"), "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000); wav.writeframes(b"\0\0" * 800)
        with self.assertRaisesRegex(ValueError, "3–10"):
            reference_bytes(self.root / "short.wav")

    def test_truncated_pcm_is_not_a_valid_reference(self):
        self.reference.write_bytes(self.reference.read_bytes()[:-400])
        with self.assertRaisesRegex(ValueError, "incomplete"):
            reference_bytes(self.reference)

    def test_damaged_profile_never_silently_falls_back_but_can_be_replaced_explicitly(self):
        paths = self.registry.runtime_paths(self.a.character_id)
        paths["voice_profile"].write_text("broken")
        kokoro = Mock()
        tts = CharacterVoiceTTS(kokoro, self.registry, self.a.character_id, runtime=Mock())
        self.assertEqual(tts.snapshot()["state"], "failed")
        with self.assertRaisesRegex(RuntimeError, "saved voice"):
            tts.prepare_stream_chunk("A synthetic reply.")
        kokoro.prepare_cancellable_stream_chunk.assert_not_called()
        revision = tts.snapshot()["revision"]
        profile, data = self.profiles.draft(self.a.character_id, engine="kokoro", revision=revision)
        paths["voice_profile"].write_text("changed broken profile")
        with self.assertRaises(ValueError):
            self.profiles.save(self.a.character_id, self.a.timeline_generation, profile, data, guard=lambda: None)
        tts.bind_character(self.a.character_id)
        job = tts.begin_voice_job()
        result = tts.edit_voice("save", dict(engine="kokoro", revision=tts.snapshot()["revision"]),
                                job=job, guard=lambda: None)
        self.assertEqual(result["state"], "ready")
        tts.prepare_stream_chunk("A synthetic reply.")
        kokoro.prepare_cancellable_stream_chunk.assert_called_once()

    def test_failed_draft_does_not_disable_saved_voice(self):
        kokoro = Mock()
        tts = CharacterVoiceTTS(kokoro, self.registry, self.a.character_id, runtime=Mock())
        job = tts.begin_voice_job()
        result = tts.edit_voice("prepare", dict(engine="gpt_sovits", revision="initial"), job=job, guard=lambda: None)
        self.assertEqual(result["state"], "failed")
        tts.prepare_stream_chunk("The saved voice still works.")
        kokoro.prepare_cancellable_stream_chunk.assert_called_once()

    def test_clone_unit_policy_keeps_all_words_and_default_kokoro_policy(self):
        from aifren.tts.streaming import StreamingSpeechQueue
        tts = CharacterVoiceTTS(Mock(), self.registry, self.a.character_id, runtime=Mock())
        self.assertIsNone(tts.committed_unit_policy)
        self.save(); tts.bind_character(self.a.character_id)
        text = "This is an independently synthetic sentence with enough words to test chunking. " * 18
        units = StreamingSpeechQueue.committed_units(text, policy=tts.committed_unit_policy)
        self.assertGreater(len(units), 1)
        self.assertEqual("".join(units).split(), text.split())

    def test_shared_playback_outcome_is_available_for_selected_provider(self):
        tts = CharacterVoiceTTS(Mock(), self.registry, self.a.character_id, runtime=Mock())
        tts._continuous_results.append((42, {"completed": False, "error": "late_chunk"}))
        self.assertEqual(tts.continuous_stream_outcome(42), {"completed": False, "error": "late_chunk"})

    def test_runtime_close_does_not_wait_for_native_lane(self):
        runtime = CloneRuntime(self.root / "not-installed.json")
        runtime._lock.acquire()
        started = time.monotonic()
        runtime.close()
        self.assertLess(time.monotonic() - started, .5)
        with self.assertRaises(CloneCancelled):
            runtime.request(VoiceProfile(), self.reference)
        runtime._lock.release()

    def test_condition_identity_covers_transcript_language_and_model(self):
        from dataclasses import replace
        profile, _ = self.draft()
        for changed in (replace(profile, transcript="Different"), replace(profile, language="all_ja"),
                        replace(profile, model="other"), replace(profile, reference_sha256="a"*64)):
            self.assertNotEqual(profile.conditioning_key, changed.conditioning_key)

    def test_reset_preserves_voice_delete_removes_only_owned_copy(self):
        from aifren.character.character_operations import CharacterOperationService
        saved = self.save()
        operations = CharacterOperationService(self.registry)
        # Production confirmation path, with no live runtime writers.
        for action in ("reset", "delete"):
            preview = operations.preview(self.a.character_id, action)
            operations.execute(character_id=self.a.character_id, token=preview["token"], revision=preview["revision"])
            if action == "reset":
                self.assertEqual(self.profiles.load(self.a.character_id), saved)
        self.assertTrue(self.reference.exists())
        self.assertEqual(self.profiles.load(self.b.character_id).engine, "kokoro")

    def test_speech_projection_is_not_reparsed_by_voice(self):
        kokoro = Mock()
        kokoro.prepare_cancellable_stream_chunk.return_value = (np.zeros((10,1)), 24000, [])
        tts = CharacterVoiceTTS(kokoro, self.registry, self.a.character_id, runtime=Mock())
        tts.prepare_stream_chunk("I really liked that.")
        kokoro.prepare_cancellable_stream_chunk.assert_called_once_with("I really liked that.", cancelled=None)

    def test_late_prepare_cannot_update_switched_character_or_play(self):
        entered, release = threading.Event(), threading.Event()
        runtime = Mock()
        def prepare(*args, **kwargs):
            entered.set(); release.wait(3)
            if kwargs["cancelled"].is_set(): raise CloneCancelled()
        runtime.request.side_effect = prepare
        tts = CharacterVoiceTTS(Mock(), self.registry, self.a.character_id, runtime=runtime)
        job = tts.begin_voice_job()
        payload = dict(engine="gpt_sovits", language="en", transcript="Synthetic reference.",
                       reference_path=str(self.reference), revision="initial")
        result = []
        worker = threading.Thread(target=lambda: result.append(tts.edit_voice("preview", payload, job=job, guard=lambda: None)))
        worker.start(); self.assertTrue(entered.wait(2))
        tts.bind_character(self.b.character_id); release.set(); worker.join(4)
        self.assertEqual(result, [None])
        self.assertEqual(tts.character_id, self.b.character_id)
        self.assertEqual(tts.profile.engine, "kokoro")
        self.assertIsNone(tts.playback_thread)
        self.assertFalse(list(self.registry.owned_directory(self.a.character_id).glob(".voice-preview-*")))


if __name__ == "__main__":
    unittest.main()
