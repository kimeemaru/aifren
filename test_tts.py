"""Offline-safe TTS integration smoke check (no model download or audio device)."""

from unittest.mock import MagicMock, patch

from tts import tts


def main() -> None:
    kokoro = MagicMock()
    with patch.object(tts, "KokoroTextToSpeech", return_value=kokoro):
        daily_driver = tts.create_tts_provider("kokoro")
    assert daily_driver is kokoro

    print("Kokoro provider selection is configured.")


if __name__ == "__main__":
    main()
