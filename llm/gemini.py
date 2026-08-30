"""Gemini transport adapter; it shares the normal compatible-provider contract."""

from config import ONLINE_MODEL
from model_settings import get_model_settings
from llm.openai_compatible import OpenAICompatibleLLM
from llm.unavailable import MODEL_CONFIGURATION_MESSAGE, ModelConfigurationError


GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


class Gemini(OpenAICompatibleLLM):
    def __init__(self) -> None:
        settings = get_model_settings()
        api_key = settings["api_key"]
        if not api_key:
            raise ModelConfigurationError(MODEL_CONFIGURATION_MESSAGE)
        super().__init__(
            api_key=api_key,
            base_url=settings["online_base_url"] or GEMINI_OPENAI_BASE_URL,
            model=settings["online_model"] or ONLINE_MODEL,
        )
