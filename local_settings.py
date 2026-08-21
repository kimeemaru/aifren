"""User-owned runtime settings kept separate from source-controlled config."""

from __future__ import annotations

import json
import os
from pathlib import Path


LOCAL_SETTINGS_FILE = Path(".aifren_local_settings.json")


def _read() -> dict:
    try:
        data = json.loads(LOCAL_SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(data: dict) -> None:
    temporary = LOCAL_SETTINGS_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, LOCAL_SETTINGS_FILE)


def get_gemini_api_key() -> tuple[str, str]:
    """Return a key and non-secret source label without logging the key."""
    local_key = str(_read().get("gemini_api_key", "")).strip()
    if local_key:
        return local_key, "local_settings"
    environment_key = os.environ.get("AIFREN_GEMINI_API_KEY", "").strip()
    if environment_key:
        return environment_key, "environment"
    try:
        from config_secret import GEMINI_API_KEY

        secret_key = str(GEMINI_API_KEY or "").strip()
        if secret_key:
            return secret_key, "development_config"
    except ImportError:
        pass
    return "", "missing"


def set_gemini_api_key(key: str) -> None:
    data = _read()
    key = str(key or "").strip()
    if key:
        data["gemini_api_key"] = key
    else:
        data.pop("gemini_api_key", None)
    _write(data)


def gemini_status(model: str) -> dict[str, str | bool]:
    key, source = get_gemini_api_key()
    return {
        "configured": bool(key),
        "source": source,
        "model": str(model),
    }
