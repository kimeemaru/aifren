import os

from runtime_layout import resource_path

CHARACTER_DIR = "characters/default"

# Provider/model selection is user-owned local configuration. These defaults
# are provider-neutral fallbacks used only before a user saves settings.
LLM_PROVIDER = "online"
ONLINE_PROVIDER = "gemini"
ONLINE_MODEL = "gemini-3.5-flash-lite"
ONLINE_BASE_URL = ""
# Compatibility import for benchmark fixtures; never a primary settings label.
GEMINI_MODEL = ONLINE_MODEL
LOCAL_LLM_ENDPOINT = "http://127.0.0.1:8000/v1"
LOCAL_LLM_MODEL = ""
# Managed GGUF discovery is intentionally generic.  The directory is ignored
# with the rest of local models and can be overridden per development machine.
LOCAL_LLM_MODEL_DIR = str(resource_path(
    os.environ.get("AIFREN_LOCAL_LLM_MODEL_DIR", "models/llama")
))
# This is a runtime capacity, not a separate local-memory mode.  It is large
# enough for the normal generous context assembly on current development
# hardware; deployments may lower it explicitly when a selected model cannot
# support it.  The context builder then trims only expendable oldest raw turns.
LOCAL_LLM_CONTEXT_SIZE = int(os.environ.get("AIFREN_LOCAL_LLM_CONTEXT_SIZE", "16384"))
LOCAL_LLM_CONTEXT_CHAR_BUDGET = int(os.environ.get("AIFREN_LOCAL_LLM_CONTEXT_CHAR_BUDGET", "48000"))
# Governor deployment ceiling when an adapter cannot report model capacity.
# This is not a claim about an unknown online model's advertised context size.
CONTEXT_GOVERNOR_FALLBACK_CAPACITY = int(os.environ.get("AIFREN_CONTEXT_CAPACITY_TOKENS", "16384"))
# Complete-request performance target, independent of model allocation/output.
# Initial deployment profile: prior responsive requests were about 4.1k tokens
# including conservative framing. 4.5k adds modest margin; not an optimality claim.
CONTEXT_OPERATING_TARGET_TOKENS = int(os.environ.get("AIFREN_CONTEXT_OPERATING_TARGET_TOKENS", "4608"))
# Normal V2 composition after the matched responsive-profile validation.
# AIFREN_CONTEXT_GOVERNOR=0 is explicit process-local composition rollback;
# it never selects V1 or changes model allocation/output limits.
CONTEXT_GOVERNOR_DEFAULT_ENABLED = True

# Kokoro is the practical daily-driver provider. Audio8 remains an explicitly
# selectable diagnostic persistent service, but its chunking constraints must
# not shape Kokoro's synthesis strategy.
# The runtime root and voice inputs are intentionally local-only environment
# configuration: do not put voice recordings or transcripts in repository data.
# When unavailable, Kokoro is the deterministic local fallback.
TTS_PROVIDER = os.environ.get("AIFREN_TTS_PROVIDER", "kokoro").strip().lower()
AUDIO8_BASE_URL = os.environ.get("AIFREN_AUDIO8_BASE_URL", "http://127.0.0.1:8024/v1")
AUDIO8_MODEL = os.environ.get("AIFREN_AUDIO8_MODEL", "arktts")
AUDIO8_RUNTIME_ROOT = os.environ.get("AIFREN_AUDIO8_RUNTIME_ROOT", "")
AUDIO8_VOICE_PROFILE = os.environ.get("AIFREN_AUDIO8_VOICE_PROFILE", "")
AUDIO8_REFERENCE_AUDIO = os.environ.get("AIFREN_AUDIO8_REFERENCE_AUDIO", "")
AUDIO8_REFERENCE_TEXT = os.environ.get("AIFREN_AUDIO8_REFERENCE_TEXT", "")
AUDIO8_TIMEOUT_SECONDS = 45
AUDIO8_STARTUP_TIMEOUT_SECONDS = 75
# Exercise a production-shaped acoustic generation, not just a tiny token
# path. The preview runtime's first substantial request initializes additional
# decoder/vocoder state; that work must be disposable rather than user-facing.
AUDIO8_WARMUP_TEXT = "The voice system is warmed up and ready for conversation."
TTS_CHUNK_MIN_CHARS = 48
# Audio8's validated quality window is at most roughly 150 characters; longer
# generations can develop a repeatable click near five seconds. These bounds
# remain sentence/clause based and are also safe for the Kokoro fallback.
TTS_CHUNK_PREFERRED_MAX_CHARS = 80
TTS_CHUNK_HARD_MAX_CHARS = 96
TTS_CHUNK_QUEUE_MAX = 4

# Kokoro-82M provider settings.  Pitch post-processing is intentionally not
# part of the runtime path; use the model's native voice at normal speed.
KOKORO_VOICE = "af_heart"
KOKORO_SPEED = 1.0
KOKORO_DEVICE = os.environ.get("AIFREN_KOKORO_DEVICE", "auto").strip().lower() or "auto"
KOKORO_MODEL_DIR = str(resource_path(
    os.environ.get("AIFREN_KOKORO_MODEL_DIR", "models/kokoro-82m")
))
# Optional developer override for the persisted Kokoro Early speech setting.
# Normal launches leave this unset and use the user-visible saved preference.
_KOKORO_EARLY_SPEECH_ENV = os.environ.get("AIFREN_KOKORO_EARLY_SPEECH")
KOKORO_EARLY_SPEECH_OVERRIDE = (
    None if _KOKORO_EARLY_SPEECH_ENV is None
    else _KOKORO_EARLY_SPEECH_ENV.strip().lower() in {"1", "true", "yes", "on"}
)
# Legacy non-authoritative comparison controls. Normal V2 authority is selected
# independently below and does not rely on an experimental shadow switch.
MEMORY_V2_SHADOW_ENABLED = True
# Compatibility name retained for legacy diagnostic callers. Normal V2 uses
# canonical observation and governed lanes; this flag is not its authority gate.
MEMORY_V2_SHADOW_WRITE_ENABLED = True
# Development flight-recorder attachment for real-turn V1/V2 comparison.
# The observer is inert unless a Development Unity client has enabled the
# central recorder; release/disabled recorder paths schedule no retrieval.
MEMORY_V2_REAL_TURN_SHADOW_ENABLED = True

# V2 is the ordinary authority. V1 is an explicit process-local compatibility
# selection, never an automatic response to an unhealthy V2 store.
MEMORY_AUTHORITY_ENV = "AIFREN_MEMORY_AUTHORITY"
DEVELOPMENT_QA_ENV = "AIFREN_ENABLE_DEVELOPMENT_QA"
V2_AUTHORITY_RECENT_MESSAGES = 12
V2_AUTHORITY_RECENT_CHARACTERS = 12000
# Legacy request-composition rollback and bounded retrospective-guard work only.
# Normal V2 prompt selection is adaptive; these are not its history boundary.
V2_AUTHORITY_RECENT_POLICY = "memory_query_contained"


def configured_memory_authority(environment=None):
    values = os.environ if environment is None else environment
    requested = str(values.get(MEMORY_AUTHORITY_ENV, "v2") or "v2").strip().casefold()
    if requested not in {"v1", "v2"}:
        raise RuntimeError("AIFREN_MEMORY_AUTHORITY must be either v1 or v2.")
    return requested

# Disabled-by-default, validation-only contextual Active State observation.
# This must name an explicitly approved local extractor before the default
# service installs one; Gemini is never selected implicitly for private turns.
ACTIVE_STATE_CONTEXTUAL_SHADOW_ENABLED = False
ACTIVE_STATE_CONTEXTUAL_SHADOW_PROVIDER = "none"

# Open Thread contextual observation follows the same explicit local-provider
# policy. It is validation/trace only and never selected implicitly.
OPEN_THREAD_CONTEXTUAL_SHADOW_ENABLED = False
OPEN_THREAD_CONTEXTUAL_SHADOW_PROVIDER = "none"

# Provider-neutral prompt assembly defaults.  These are character budgets, not
# tokenizer estimates: provider/model profiles may override them later without
# changing conversation or Memory V2 semantics.
RECENT_CONTEXT_MAX_MESSAGES = 100
RECENT_CONTEXT_MAX_CHARS = 60000

# Non-authoritative attention experiment: the 2026-09-10 eight-pair comparison
# did not meet the benefit/safety gate. No preference or authority change.
RECENT_PULSE_ENABLED = False
