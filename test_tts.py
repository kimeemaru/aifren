"""Offline-safe TTS integration smoke check (no model download or audio device)."""

from unittest.mock import MagicMock, patch

from tts import tts


def main() -> None:
    kokoro = MagicMock()
    with patch.object(tts, "KokoroTextToSpeech", return_value=kokoro):
        daily_driver = tts.create_tts_provider("kokoro")
    assert daily_driver is kokoro

    primary, fallback = MagicMock(), MagicMock()
    primary.speak.return_value = True
    with patch.object(tts, "Audio8TextToSpeech", return_value=primary), patch.object(
        tts, "KokoroTextToSpeech", return_value=fallback
    ):
        provider = tts.create_tts_provider("audio8")
    assert provider.primary is primary and provider.fallback is fallback
    print("Kokoro daily-driver and opt-in Audio8/Kokoro fallback contracts are configured.")


if __name__ == "__main__":
    main()
