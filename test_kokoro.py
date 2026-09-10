"""Generate a Kokoro WAV and report model-load and synthesis timing."""
import argparse
import os
import time

from tts.tts import KokoroTextToSpeech

parser = argparse.ArgumentParser()
parser.add_argument("--voice", default=None)
parser.add_argument("--speed", type=float, default=None)
parser.add_argument("--output", default="test_kokoro.wav")
args = parser.parse_args()

started = time.perf_counter()
tts = KokoroTextToSpeech(
    **{key: value for key, value in {"voice": args.voice, "speed": args.speed}.items() if value is not None}
)
loaded = time.perf_counter()
tts.synthesize("Hello. This is a short Kokoro test for AIFren.", args.output)
finished = time.perf_counter()
print(f"model_load_seconds={loaded - started:.2f}")
print(f"synthesis_seconds={finished - loaded:.2f}")
print(f"Generated: {os.path.abspath(args.output)}")
