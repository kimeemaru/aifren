"""Tracked provider configuration API; credentials stay in ignored local JSON."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from config import (
    KOKORO_EARLY_SPEECH_OVERRIDE,
    LOCAL_LLM_ENDPOINT,
    LOCAL_LLM_MODEL,
    ONLINE_BASE_URL,
    ONLINE_MODEL,
    ONLINE_PROVIDER,
)

LOCAL_SETTINGS_FILE = Path(".aifren_local_settings.json")
_MODES = frozenset({"online", "local"})
_ONLINE_PROVIDERS = frozenset({"auto", "gemini", "openai_compatible"})


def _read() -> dict[str, Any]:
    try:
        data = json.loads(LOCAL_SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(data: dict[str, Any]) -> None:
    temporary = LOCAL_SETTINGS_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, LOCAL_SETTINGS_FILE)


def _compact(value: object, maximum: int = 512) -> str:
    value = " ".join(str(value or "").split())
    if len(value) > maximum:
        raise ValueError("model setting exceeds its safe bound")
    return value


def get_model_settings() -> dict[str, str]:
    data = _read()
    saved_key = str(data.get("model_api_key", "")).strip()
    saved_local_key = str(data.get("local_api_key", "")).strip()
    return {
        "mode": str(data.get("model_mode", "online")).strip().lower() or "online",
        "online_provider": str(data.get("online_provider", ONLINE_PROVIDER)).strip().lower() or "auto",
        "online_model": _compact(data.get("online_model", ONLINE_MODEL)),
        "online_base_url": _compact(data.get("online_base_url", ONLINE_BASE_URL)),
        "local_endpoint": _compact(data.get("local_endpoint", LOCAL_LLM_ENDPOINT)),
        "local_model": _compact(data.get("local_model", LOCAL_LLM_MODEL)),
        "local_auto_start": bool(data.get("local_auto_start", False)),
        "api_key": saved_key or os.environ.get("AIFREN_MODEL_API_KEY", "").strip() or os.environ.get("AIFREN_GEMINI_API_KEY", "").strip(),
        "local_api_key": saved_local_key or os.environ.get("AIFREN_LOCAL_MODEL_API_KEY", "").strip(),
    }


def set_model_settings(*, mode: object, online_provider: object = "auto", online_model: object = "",
                       online_base_url: object = "", local_endpoint: object = "", local_model: object = "",
                       local_auto_start: object | None = None, api_key: object | None = None,
                       local_api_key: object | None = None) -> None:
    mode, provider = str(mode).strip().lower(), str(online_provider).strip().lower() or "auto"
    if mode not in _MODES or provider not in _ONLINE_PROVIDERS:
        raise ValueError("unsupported model mode or online provider")
    data = _read()
    data.update({"model_mode": mode, "online_provider": provider, "online_model": _compact(online_model),
                 "online_base_url": _compact(online_base_url), "local_endpoint": _compact(local_endpoint),
                 "local_model": _compact(local_model)})
    if local_auto_start is not None:
        if not isinstance(local_auto_start, bool):
            raise ValueError("local auto-start must be true or false")
        data["local_auto_start"] = local_auto_start
    for key, value in (("model_api_key", api_key), ("local_api_key", local_api_key)):
        if value is None:
            continue
        value = str(value).strip()
        if value:
            data[key] = value
        else:
            data.pop(key, None)
    _write(data)


def set_local_auto_start(enabled: object) -> None:
    """Persist the managed-runtime launch preference without reconfiguring a provider."""
    if not isinstance(enabled, bool):
        raise ValueError("local auto-start must be true or false")
    data = _read()
    data["local_auto_start"] = enabled
    _write(data)


def kokoro_early_speech_status() -> dict[str, bool]:
    """Return persisted and effective Kokoro speech scheduling state."""
    configured = bool(_read().get("kokoro_early_speech", True))
    overridden = KOKORO_EARLY_SPEECH_OVERRIDE is not None
    return {
        "configured": configured,
        "effective": bool(KOKORO_EARLY_SPEECH_OVERRIDE) if overridden else configured,
        "overridden": overridden,
    }


def set_kokoro_early_speech(enabled: object) -> None:
    """Persist the normal user-facing Kokoro scheduling preference."""
    if not isinstance(enabled, bool):
        raise ValueError("Kokoro early speech must be true or false")
    data = _read()
    data["kokoro_early_speech"] = enabled
    _write(data)


PROACTIVE_INTERVAL_SECONDS = (0, 30, 60, 300, 600, 900, 1800, 2700, 3600,
                              7200, 10800, 14400, 18000, 21600)


def proactive_behavior_status() -> dict[str, bool | int]:
    """Return the persisted minimum opportunity interval with legacy migration."""
    data = _read()
    configured = data.get("proactive_interval_seconds")
    if isinstance(configured, int) and not isinstance(configured, bool) and configured in PROACTIVE_INTERVAL_SECONDS:
        interval = configured
    else:
        interval = 3600 if bool(data.get("proactive_behavior", True)) else 0
    return {"enabled": interval > 0, "interval_seconds": interval}


def set_proactive_interval(interval_seconds: object) -> None:
    if (not isinstance(interval_seconds, int) or isinstance(interval_seconds, bool)
            or interval_seconds not in PROACTIVE_INTERVAL_SECONDS):
        raise ValueError("unsupported proactive interval")
    data = _read()
    data["proactive_interval_seconds"] = interval_seconds
    _write(data)


def set_proactive_behavior(enabled: object) -> None:
    if not isinstance(enabled, bool):
        raise ValueError("proactive behavior must be true or false")
    set_proactive_interval(3600 if enabled else 0)


def model_status() -> dict[str, str | bool]:
    settings = get_model_settings()
    local = settings["mode"] == "local"
    if local:
        configured = bool(settings["local_endpoint"] and settings["local_model"])
    elif settings["online_provider"] == "openai_compatible":
        configured = bool(settings["api_key"] and settings["online_base_url"] and settings["online_model"])
    else:
        configured = bool(settings["api_key"])
    selected_model = settings["local_model"] if local else settings["online_model"]
    return {"mode": "local" if local else "online", "provider": "openai_compatible" if local else settings["online_provider"],
            # A default model label is not an active provider.  Hide it until
            # the required credentials/configuration are actually present.
            "model": selected_model if configured else "",
            "selected_model": selected_model if local else "",
            "endpoint": settings["local_endpoint"] if local else settings["online_base_url"],
            "configured": configured,
            # A saved local endpoint/model is not proof that its server is
            # running.  Runtime discovery or generation upgrades this to
            # configured; failures mark it unavailable without changing mode.
            "availability": "unverified" if local and configured else ("configured" if configured else "unconfigured"),
            "credential_source": "local_settings" if (settings["local_api_key"] if local else settings["api_key"]) else "none",
            "local_auto_start": settings["local_auto_start"]}
