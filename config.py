CHARACTER_DIR = "characters/default"

LLM_PROVIDER = "gemini"

GEMINI_MODEL = "gemini-3.5-flash-lite"

TTS_VOICE = "models/piper/en_US-hfc_female-medium.onnx"

# Kokoro is the selected AIFren 1.0 voice provider in the reproducible
# .venv-aifren runtime. Piper remains the automatic fallback if Kokoro cannot
# initialize.
TTS_PROVIDER = "kokoro"

# Kokoro-82M provider settings.  Pitch post-processing is intentionally not
# part of the runtime path; use the model's native voice at normal speed.
KOKORO_VOICE = "af_heart"
KOKORO_SPEED = 1.0
KOKORO_DEVICE = "auto"
KOKORO_MODEL_DIR = "models/kokoro-82m"
# Development-only, non-authoritative Memory V2 comparison.  When false, no
# V2 shadow database is opened and normal AIFren behavior is unchanged.
MEMORY_V2_SHADOW_ENABLED = True
