import os

CHARACTER_DIR = "characters/default"

# Provider/model selection is user-owned local configuration. These defaults
# are provider-neutral fallbacks used only before a user saves settings.
LLM_PROVIDER = "online"
ONLINE_PROVIDER = "gemini"
ONLINE_MODEL = "gemini-3.5-flash-lite"
ONLINE_BASE_URL = ""
LOCAL_LLM_ENDPOINT = "http://127.0.0.1:8000/v1"
LOCAL_LLM_MODEL = ""
# Managed GGUF discovery is intentionally generic.  The directory is ignored
# with the rest of local models and can be overridden per development machine.
LOCAL_LLM_MODEL_DIR = os.environ.get("AIFREN_LOCAL_LLM_MODEL_DIR", "models/llama")
# This is a runtime capacity, not a separate local-memory mode.  It is large
# enough for the normal generous context assembly on current development
# hardware; deployments may lower it explicitly when a selected model cannot
# support it.  The context builder then trims only expendable oldest raw turns.
LOCAL_LLM_CONTEXT_SIZE = int(os.environ.get("AIFREN_LOCAL_LLM_CONTEXT_SIZE", "16384"))
LOCAL_LLM_CONTEXT_CHAR_BUDGET = int(os.environ.get("AIFREN_LOCAL_LLM_CONTEXT_CHAR_BUDGET", "48000"))

# Kokoro is the public local speech provider. Voice recordings and transcripts
# are local user data and never belong in repository configuration.
TTS_PROVIDER = os.environ.get("AIFREN_TTS_PROVIDER", "kokoro").strip().lower()
TTS_CHUNK_MIN_CHARS = 48
# Conservative sentence/clause bounds keep early Kokoro speech responsive.
TTS_CHUNK_PREFERRED_MAX_CHARS = 80
TTS_CHUNK_HARD_MAX_CHARS = 96
TTS_CHUNK_QUEUE_MAX = 4

# Kokoro-82M provider settings.  Pitch post-processing is intentionally not
# part of the runtime path; use the model's native voice at normal speed.
KOKORO_VOICE = "af_heart"
KOKORO_SPEED = 1.0
KOKORO_DEVICE = "auto"
KOKORO_MODEL_DIR = "models/kokoro-82m"
# Optional developer override for the persisted Kokoro Early speech setting.
# Normal launches leave this unset and use the user-visible saved preference.
_KOKORO_EARLY_SPEECH_ENV = os.environ.get("AIFREN_KOKORO_EARLY_SPEECH")
KOKORO_EARLY_SPEECH_OVERRIDE = (
    None if _KOKORO_EARLY_SPEECH_ENV is None
    else _KOKORO_EARLY_SPEECH_ENV.strip().lower() in {"1", "true", "yes", "on"}
)
# Best-effort mutation mirroring into a separate V2 SQLite store. V1 JSON
# remains canonical for general memory; a separately verified, narrow V2
# durable-fact path may supply background context when explicitly admitted.
MEMORY_V2_SHADOW_WRITE_ENABLED = True

# Provider-neutral prompt assembly defaults.  These are character budgets, not
# tokenizer estimates: provider/model profiles may override them later without
# changing conversation or Memory V2 semantics.
RECENT_CONTEXT_MAX_MESSAGES = 100
RECENT_CONTEXT_MAX_CHARS = 60000
