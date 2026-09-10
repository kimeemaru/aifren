"""Configured provider lifecycle for explicit offline maintenance commands."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from llm.llm import create_llm
from local_model_runtime import LocalModelRuntime
from model_settings import get_model_settings


@dataclass(frozen=True)
class MaintenanceProviderSession:
    provider: object
    mode: str
    adapter: str
    model: str
    runtime_ownership: str = "none"
    runtime_compute: str = ""


@contextmanager
def configured_maintenance_provider(
    resource_root: str | Path,
    *,
    settings_getter: Callable[[], dict[str, object]] = get_model_settings,
    provider_factory: Callable[[], object] = create_llm,
    runtime_factory: Callable[..., object] = LocalModelRuntime,
) -> Iterator[MaintenanceProviderSession]:
    """Yield the configured provider, starting only its existing local runtime.

    Product hosts normally own managed-local startup. Explicit maintenance
    commands have no Unity/backend host, so they must reuse that lifecycle
    directly instead of assuming that a configured endpoint is already live.
    A compatible external endpoint remains external and is never stopped.
    """
    settings = settings_getter()
    mode = str(settings.get("mode") or "")
    runtime = None
    status: dict[str, object] = {}
    try:
        if mode == "local":
            from config import LOCAL_LLM_CONTEXT_SIZE, LOCAL_LLM_MODEL_DIR

            runtime = runtime_factory(
                Path(resource_root).resolve(),
                model_directory=LOCAL_LLM_MODEL_DIR,
                context_size=LOCAL_LLM_CONTEXT_SIZE,
            )
            status = runtime.start(
                endpoint=str(settings.get("local_endpoint") or ""),
                selected_model=str(settings.get("local_model") or ""),
                api_key=str(settings.get("local_api_key") or ""),
            )
            if status.get("state") != "ready":
                reason = str(status.get("error") or status.get("state") or "unavailable")
                raise RuntimeError(f"configured local model runtime is unavailable: {reason}")

        provider = provider_factory()
        if getattr(provider, "is_available", True) is False:
            raise RuntimeError("configured model provider is unavailable")
        yield MaintenanceProviderSession(
            provider=provider,
            mode=mode,
            adapter=type(provider).__name__,
            model=str(getattr(provider, "model", "") or ""),
            runtime_ownership=str(status.get("ownership") or "none"),
            runtime_compute=str(status.get("compute") or ""),
        )
    finally:
        if runtime is not None:
            runtime.stop()
