"""Provider-neutral recoverable LLM availability states.

An unavailable model is deliberately an adapter-shaped object rather than a
startup exception.  It lets the backend and Unity settings remain available on
a fresh install while preserving one provider boundary for online and local
models.
"""

from __future__ import annotations

from collections.abc import Iterator


MODEL_CONFIGURATION_MESSAGE = "Configure a model in Settings > Model."


class ModelConfigurationError(RuntimeError):
    """A turn was attempted before a usable model was configured."""


class ModelTransportError(RuntimeError):
    """A configured model could not serve a generation request."""


class UnavailableLLM:
    """Fail-soft adapter used until model configuration is usable.

    It has no provider-specific behavior and never fabricates a response.  The
    service detects it before it accepts a canonical user turn.
    """

    is_available = False

    def __init__(self, message: str = MODEL_CONFIGURATION_MESSAGE) -> None:
        self.message = message

    def generate(self, _messages, _character_prompt) -> str:
        raise ModelConfigurationError(self.message)

    def stream_generate(self, _messages, _character_prompt) -> Iterator[str]:
        raise ModelConfigurationError(self.message)
