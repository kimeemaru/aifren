import unittest
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch
import wave

import numpy as np

from assistant_service import AssistantService
from tts import tts
from tts.kokoro_assets import require_local_assets


class ProviderSelectionTests(unittest.TestCase):
    def test_configured_default_selects_kokoro(self):
        provider = object()
        with patch.object(tts, "TTS_PROVIDER", "kokoro"), patch.object(
            tts, "KokoroTextToSpeech", return_value=provider
        ):
            self.assertIs(tts.create_tts_provider(), provider)

    def test_piper_selection_keeps_the_existing_provider(self):
        provider = object()
        with patch.object(tts, "PiperTextToSpeech", return_value=provider):
            self.assertIs(tts.create_tts_provider("piper"), provider)

    def test_kokoro_failure_falls_back_to_piper(self):
        fallback = object()
        with patch.object(tts, "KokoroTextToSpeech", side_effect=RuntimeError("missing")), patch.object(tts, "PiperTextToSpeech", return_value=fallback):
            self.assertIs(tts.create_tts_provider("kokoro"), fallback)

    def test_unknown_provider_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported TTS_PROVIDER"):
            tts.create_tts_provider("not-a-provider")


class ProviderIndependentTtsBehaviorTests(unittest.TestCase):
    def test_emote_filtering_is_independent_of_provider(self):
        self.assertEqual(
            AssistantService.clean_text_for_tts("*waves* Hello, *smiles* friend!"),
            "Hello,  friend!",
        )

    def test_inline_asterisk_emphasis_is_preserved_for_tts(self):
        self.assertEqual(
            AssistantService.clean_text_for_tts("*nods* I *really* mean *that*?"),
            "I really mean that?",
        )

    def test_stage_direction_vocabulary_is_not_spoken_as_emphasis(self):
        self.assertEqual(AssistantService.clean_text_for_tts("*smiles* Fine."), "Fine.")
        self.assertEqual(AssistantService.clean_text_for_tts("*blinks* Fine."), "Fine.")
        self.assertEqual(AssistantService.clean_text_for_tts("*pauses* I suppose so."), "I suppose so.")
        self.assertEqual(AssistantService.clean_text_for_tts("*smiling* Hello."), "Hello.")
        self.assertEqual(AssistantService.clean_text_for_tts("*AIFren waves* Hello."), "Hello.")

    def test_double_asterisk_emphasis_is_normalized_before_tts(self):
        self.assertEqual(AssistantService.clean_text_for_tts("I *not* kidding."), "I not kidding.")
        self.assertEqual(AssistantService.clean_text_for_tts("I **really** mean it."), "I really mean it.")
        self.assertEqual(
            AssistantService.clean_text_for_tts("*smiles* I **really** am *not* kidding. *nods*"),
            "I really am not kidding.",
        )

    def test_long_single_marker_roleplay_beats_are_not_spoken(self):
        self.assertEqual(
            AssistantService.clean_text_for_tts("*let out a soft, teasing huff and lean back on my heels*"),
            "",
        )
        self.assertEqual(AssistantService.clean_text_for_tts("*I cross my arms*"), "")
        self.assertEqual(AssistantService.clean_text_for_tts("*very close indeed*"), "very close indeed")
        self.assertEqual(AssistantService.clean_text_for_tts("*I really mean this*"), "")
        self.assertEqual(
            AssistantService.clean_text_for_tts("I **really mean this very strongly**."),
            "I really mean this very strongly.",
        )

    def test_shared_playback_controls_clamp_volume_and_stop(self):
        provider = object.__new__(tts.PiperTextToSpeech)
        provider._initialize_playback_state()
        provider.set_volume(5)
        self.assertEqual(provider.get_volume(), 1.0)
        provider.set_volume(-1)
        self.assertEqual(provider.get_volume(), 0.0)
        provider.stop()
        self.assertTrue(provider.playback_finished.is_set())

    def test_piper_speak_uses_in_memory_wav_and_starts_playback(self):
        provider = object.__new__(tts.PiperTextToSpeech)
        provider._initialize_playback_state()
        provider.voice = MagicMock()
        provider._start_playback = MagicMock(return_value=True)

        def synthesize_wav(text, wav_file):
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22050)
            wav_file.writeframes(b"\x00\x00\x10\x00")

        provider.voice.synthesize_wav.side_effect = synthesize_wav

        self.assertTrue(provider.speak("Fallback speech works."))
        provider.voice.synthesize_wav.assert_called_once()
        audio, sample_rate, generation = provider._start_playback.call_args.args
        self.assertEqual(sample_rate, 22050)
        self.assertGreater(generation, 0)
        self.assertEqual(audio.dtype, np.float32)
        self.assertEqual(audio.shape[1], 1)

    def test_interrupted_synthesis_cannot_start_stale_playback(self):
        provider = object.__new__(tts.PiperTextToSpeech)
        provider._initialize_playback_state()
        synthesis_generation = provider._next_playback_generation()
        provider.stop()

        self.assertFalse(provider._start_playback(
            np.zeros((8, 1), dtype=np.float32), 24000, synthesis_generation
        ))

    def test_interrupt_invalidates_active_audio_without_waiting_for_worker_cleanup(self):
        provider = object.__new__(tts.PiperTextToSpeech)
        provider._initialize_playback_state()

        class FakeStream:
            def __init__(self):
                self.aborted = False
                self.active = True

            def abort(self):
                self.aborted = True
                self.active = False

        generation = provider._next_playback_generation()
        provider._mark_playback_active(generation)
        stream = FakeStream()
        provider.stream = stream

        interrupted = provider.stop()

        self.assertEqual(generation, interrupted)
        self.assertTrue(stream.aborted)
        self.assertIsNone(provider.playback_debug_state()["playing"])
        # A stale synthesis/playback completion cannot restart the audio.
        self.assertFalse(provider._start_playback(
            np.zeros((8, 1), dtype=np.float32), 24000, generation
        ))

    def test_natural_completion_retires_active_playback_and_notifies_once(self):
        provider = object.__new__(tts.PiperTextToSpeech)
        provider._initialize_playback_state()
        generation = provider._next_playback_generation()
        provider._mark_playback_active(generation)
        callbacks = []
        provider.set_playback_finished_callback(callbacks.append)

        class FakeStream:
            active = True

            def start(self):
                pass

            def stop(self):
                self.active = False

            def close(self):
                pass

            def abort(self):
                self.active = False

        stream = FakeStream()

        class FakeSoundDevice:
            def OutputStream(self, **_kwargs):
                return stream

            @staticmethod
            def sleep(_milliseconds):
                stream.active = False

        with patch.object(tts, "sd", FakeSoundDevice()):
            provider._play_audio(
                np.zeros((8, 1), dtype=np.float32), 24000, 8 / 24000,
                [], [], generation,
            )

        self.assertEqual([generation], callbacks)
        state = provider.playback_debug_state()
        self.assertIsNone(state["playing"])
        self.assertFalse(state["stream"])

    def test_kokoro_interruption_prevents_late_synthesis_chunks_from_playing(self):
        provider = object.__new__(tts.KokoroTextToSpeech)
        provider._initialize_playback_state()
        generation = provider._next_playback_generation()
        provider._mark_synthesis_active(generation)
        provider.pipeline = lambda *_args, **_kwargs: iter((
            SimpleNamespace(audio=np.zeros(24, dtype=np.float32), tokens=[]),
        ))
        provider.voice_path = "unused"
        provider.speed = 1.0
        provider.stop()

        self.assertIsNone(provider._generate_audio("late synthesis", generation))
        self.assertIsNone(provider.playback_debug_state()["synthesizing"])

    def test_lip_sync_envelope_tracks_audio_energy_without_text_timing(self):
        silent_then_loud = np.concatenate((np.zeros(2400), np.ones(2400) * .5)).reshape(-1, 1)

        envelope = tts.PiperTextToSpeech.build_lip_sync_envelope(
            silent_then_loud, 24000, samples_per_second=24
        )

        self.assertEqual(5, len(envelope))
        self.assertEqual(0.0, envelope[0])
        self.assertGreater(envelope[-1], .8)


class KokoroLocalAssetsTests(unittest.TestCase):
    def test_local_asset_validation_requires_config_model_and_selected_voice(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "voices").mkdir()
            for path in (root / "config.json", root / "kokoro-v1_0.pth", root / "voices" / "af_heart.pt"):
                path.write_bytes(b"local")

            config, model, voice = require_local_assets(root, "af_heart")
            self.assertTrue(config.is_file())
            self.assertTrue(model.is_file())
            self.assertTrue(voice.is_file())

    def test_kokoro_token_starts_keep_only_lexical_timestamped_tokens(self):
        tokens = [
            SimpleNamespace(text="Hello", start_ts=0.1),
            SimpleNamespace(text=",", start_ts=0.3),
            SimpleNamespace(text="friend", start_ts=0.4),
            SimpleNamespace(text="missing", start_ts=None),
        ]
        self.assertEqual(
            [1.1, 1.4],
            tts.KokoroTextToSpeech._result_word_starts(tokens, 1.0),
        )
