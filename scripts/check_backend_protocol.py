"""Small local-only backend readiness and launcher-ownership check."""

import asyncio
import json
import os
import sys

import websockets
from websockets.exceptions import ConnectionClosed


# Transport revisions are additive.  The development launcher needs the
# v2 snapshot contract, not one frozen revision number; a newer compatible
# backend must be considered ready.
MINIMUM_TRANSPORT_VERSION = 2


def is_compatible_transport_version(version: object) -> bool:
    return isinstance(version, int) and not isinstance(version, bool) and version >= MINIMUM_TRANSPORT_VERSION


def is_v2_acceptance_ready(data: object) -> bool:
    """Require the guarded authority and an actually usable configured model."""
    if not isinstance(data, dict):
        return False
    authority = data.get("memory_authority")
    models = data.get("models")
    if not isinstance(authority, dict) or authority.get("mode") != "v2":
        return False
    if not isinstance(models, dict) or not isinstance(models.get("current"), dict):
        return False
    current = models["current"]
    if current.get("configured") is not True or current.get("availability") != "configured":
        return False
    if current.get("mode") == "local":
        runtime = models.get("local_runtime")
        return bool(
            isinstance(runtime, dict)
            and runtime.get("state") == "ready"
            and runtime.get("active_model")
            and runtime.get("active_model") == runtime.get("selected_model")
        )
    return True


def is_expected_memory_authority(data, expected=None):
    expected = expected or os.environ.get("AIFREN_MEMORY_AUTHORITY") or "v2"
    return bool(expected in ("v1", "v2") and isinstance(data, dict)
                and isinstance(data.get("memory_authority"), dict)
                and data["memory_authority"].get("mode") == expected)


async def check() -> int:
    try:
        async with websockets.connect("ws://127.0.0.1:8765", open_timeout=2) as socket:
            if "--shutdown" in sys.argv:
                await socket.send(json.dumps({
                    "command": "shutdown",
                    "owner_token": os.environ.get("AIFREN_BACKEND_OWNER_TOKEN", ""),
                }))
                try:
                    reply = json.loads(await asyncio.wait_for(socket.recv(), timeout=3))
                except ConnectionClosed:
                    return 0
                if isinstance(reply, dict) and reply.get("type") == "command_error":
                    print("Backend shutdown ownership check failed.")
                    return 2
                return 0
            if "--owner-check" in sys.argv:
                await socket.send(json.dumps({
                    "command": "probe_runtime_owner",
                    "owner_token": os.environ.get("AIFREN_BACKEND_OWNER_TOKEN", ""),
                }))
                reply = json.loads(await asyncio.wait_for(socket.recv(), timeout=3))
                data = reply.get("data", {}) if isinstance(reply, dict) else {}
                matched = reply.get("type") == "runtime_owner" and data.get("matched") is True
                print("owned" if matched else "not-owned")
                return 0 if matched else 2
            await socket.send(json.dumps({"command": "get_snapshot"}))
            reply = json.loads(await asyncio.wait_for(socket.recv(), timeout=3))
    except Exception as error:
        print(f"Backend readiness check failed: {type(error).__name__}: {error}")
        return 1

    data = reply.get("data", {}) if isinstance(reply, dict) else {}
    is_aifren = reply.get("type") == "snapshot" and isinstance(data, dict) and {
        "conversation", "character", "status"
    }.issubset(data)
    if "--v2-acceptance-ready" in sys.argv:
        ready = is_aifren and is_v2_acceptance_ready(data)
        print("v2-acceptance-ready" if ready else "v2-acceptance-not-ready")
        return 0 if ready else 2
    if "--classify" in sys.argv:
        print("aifren" if is_aifren else "other")
        return 0 if is_aifren else 2

    version = data.get("transport_version") if isinstance(data, dict) else None
    if reply.get("type") != "snapshot" or not is_compatible_transport_version(version):
        print("A listener on port 8765 is not the current AIFren backend (transport v2-compatible snapshot required).")
        return 2

    if not is_expected_memory_authority(data):
        print("AIFren backend memory authority differs from this launch; it will not be reused.")
        return 3
    print("AIFren backend transport v2 and requested memory authority are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(check()))
