"""Configured LLM factory. Provider choice never changes AIFren context semantics."""

from model_settings import get_model_settings
from llm.openai_compatible import OpenAICompatibleLLM
from llm.unavailable import MODEL_CONFIGURATION_MESSAGE, UnavailableLLM


QWEN35_NON_THINKING_GENERAL = {
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "min_p": 0.0,
    "presence_penalty": 1.5,
    # llama-cpp-python's OpenAI-compatible request spelling is
    # ``repeat_penalty``, not the vendor-facing ``repetition_penalty`` label.
    "repeat_penalty": 1.0,
}


def _local_sampling_configuration(model: str) -> tuple[str, dict[str, int | float]]:
    normalized = str(model or "").casefold().replace("_", "").replace("-", "")
    if "qwen3.5" in normalized or "qwen35" in normalized:
        return "qwen3.5_non_thinking_general", dict(QWEN35_NON_THINKING_GENERAL)
    return "", {}


def create_llm():
    """Return the configured adapter without making startup provider-fatal.

    A missing credential, model name, or unsupported configured provider is a
    recoverable settings state.  The returned adapter is checked by
    ``AssistantService`` before a turn is persisted; it never gives a model
    store or conversation authority.
    """
    settings = get_model_settings()
    if settings["mode"] == "local":
        if not settings["local_endpoint"] or not settings["local_model"]:
            return UnavailableLLM(MODEL_CONFIGURATION_MESSAGE)
        try:
            from config import LOCAL_LLM_CONTEXT_CHAR_BUDGET
            sampling_preset, sampling_options = _local_sampling_configuration(settings["local_model"])
            return OpenAICompatibleLLM(
                api_key=settings["local_api_key"], base_url=settings["local_endpoint"], model=settings["local_model"],
                context_budget_chars=LOCAL_LLM_CONTEXT_CHAR_BUDGET,
                fresh_request_seeds=True,
                sampling_preset=sampling_preset, sampling_options=sampling_options,
            )
        except (RuntimeError, ValueError):
            return UnavailableLLM(MODEL_CONFIGURATION_MESSAGE)

    if not settings["api_key"]:
        return UnavailableLLM(MODEL_CONFIGURATION_MESSAGE)
    provider = settings["online_provider"]
    if provider in {"auto", "gemini"}:
        try:
            from llm.gemini import Gemini
            return Gemini()
        except (RuntimeError, ValueError):
            return UnavailableLLM(MODEL_CONFIGURATION_MESSAGE)
    if provider == "openai_compatible":
        try:
            return OpenAICompatibleLLM(
                api_key=settings["api_key"], base_url=settings["online_base_url"], model=settings["online_model"],
            )
        except (RuntimeError, ValueError):
            return UnavailableLLM(MODEL_CONFIGURATION_MESSAGE)
    return UnavailableLLM(MODEL_CONFIGURATION_MESSAGE)


def discover_local_models(endpoint: str, api_key: str = "") -> tuple[str, ...]:
    """Best-effort discovery; failure leaves explicit model configuration usable."""
    adapter = OpenAICompatibleLLM(api_key=api_key, base_url=endpoint, model="discovery-placeholder")
    return adapter.discover_models()
