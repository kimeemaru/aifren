from config import LLM_PROVIDER


def create_llm():

    provider = LLM_PROVIDER.lower().strip()

    if provider == "gemini":

        from llm.gemini import Gemini

        return Gemini()

    raise ValueError(
        f"Unknown LLM provider: {LLM_PROVIDER}"
    )