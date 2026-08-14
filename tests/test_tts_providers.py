import unittest
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
        audio, sample_rate = provider._start_playback.call_args.args
        self.assertEqual(sample_rate, 22050)
        self.assertEqual(audio.dtype, np.float32)
        self.assertEqual(audio.shape[1], 1)

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
