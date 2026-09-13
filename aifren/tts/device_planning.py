"""Stable pre-synthesis device planning for local TTS providers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


MANAGED_LLM_RUNTIME_RESERVE_BYTES = 1792 * 1024 * 1024
KOKORO_ACCELERATOR_RESERVE_BYTES = 1280 * 1024 * 1024


@dataclass(frozen=True)
class KokoroDevicePlan:
    device: str
    reason: str
    gpu_total_bytes: int = 0
    managed_model_bytes: int = 0
    required_total_bytes: int = 0


def _managed_model_size(
    settings: Mapping[str, object], model_directory: str | Path,
) -> int:
    selected = str(settings.get("local_model") or "").strip()
    if not selected:
        return 0
    root = Path(model_directory).resolve()
    candidate = Path(selected)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        candidate = candidate.resolve()
        candidate.relative_to(root)
    except (OSError, ValueError):
        return 0
    try:
        return candidate.stat(follow_symlinks=False).st_size if candidate.is_file() else 0
    except OSError:
        return 0


def plan_kokoro_device(
    configured_device: object,
    *,
    torch_module: object,
    settings: Mapping[str, object] | None = None,
    model_directory: str | Path | None = None,
) -> KokoroDevicePlan:
    """Select once using capacity metadata; never probe with user speech."""
    requested = str(configured_device or "auto").strip().casefold() or "auto"
    if requested != "auto":
        return KokoroDevicePlan(requested, "configured_device_override")

    cuda = getattr(torch_module, "cuda", None)
    try:
        available = bool(cuda is not None and cuda.is_available())
    except Exception:
        available = False
    if not available:
        return KokoroDevicePlan("cpu", "accelerator_unavailable")

    try:
        total = int(cuda.get_device_properties(0).total_memory)
    except Exception:
        total = 0

    if settings is None:
        from aifren.runtime.model_settings import get_model_settings
        settings = get_model_settings()
    mode = str(settings.get("mode") or "").casefold()
    managed = mode == "local" and bool(settings.get("local_auto_start"))
    if not managed:
        return KokoroDevicePlan(
            "cuda", "accelerator_available_without_managed_local_llm",
            gpu_total_bytes=total,
        )

    if model_directory is None:
        from aifren.runtime.config import LOCAL_LLM_MODEL_DIR
        model_directory = LOCAL_LLM_MODEL_DIR
    model_bytes = _managed_model_size(settings, model_directory)
    if total <= 0 or model_bytes <= 0:
        return KokoroDevicePlan(
            "cpu", "managed_local_gpu_capacity_unknown",
            gpu_total_bytes=total, managed_model_bytes=model_bytes,
        )
    required = (
        model_bytes
        + MANAGED_LLM_RUNTIME_RESERVE_BYTES
        + KOKORO_ACCELERATOR_RESERVE_BYTES
    )
    if total < required:
        return KokoroDevicePlan(
            "cpu", "managed_local_gpu_headroom_insufficient",
            total, model_bytes, required,
        )
    return KokoroDevicePlan(
        "cuda", "managed_local_gpu_headroom_sufficient",
        total, model_bytes, required,
    )


__all__ = [
    "KOKORO_ACCELERATOR_RESERVE_BYTES",
    "KokoroDevicePlan",
    "MANAGED_LLM_RUNTIME_RESERVE_BYTES",
    "plan_kokoro_device",
]
