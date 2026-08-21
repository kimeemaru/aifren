import os
import wave

from piper import PiperVoice

from config import TTS_VOICE


# ============================================================
# Paths
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

VOICE_FILE = os.path.join(
    BASE_DIR,
    TTS_VOICE
)


# ============================================================
# Test
# ============================================================

print(
    "Loading Piper voice..."
)

if not os.path.isfile(
    VOICE_FILE
):

    raise FileNotFoundError(
        "\nVoice model not found:\n"
        f"{VOICE_FILE}\n"
    )

voice = PiperVoice.load(
    VOICE_FILE
)

print(
    "Piper voice loaded."
)

output_file = os.path.join(
    BASE_DIR,
    "test_tts.wav"
)

text = (
    "Hello. This is a test of the "
    "local Piper text to speech system."
)

print(
    "Generating speech..."
)

with wave.open(
    output_file,
    "wb"
) as wav_file:

    voice.synthesize_wav(
        text,
        wav_file
    )

print(
    f"Generated:\n{output_file}"
)