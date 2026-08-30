"""Small local-only readiness check used by AIFren developer launchers."""

import asyncio
import json
import sys

import websockets


# Transport revisions are additive.  The development launcher needs the
# v2 snapshot contract, not one frozen revision number; a newer compatible
# backend must be considered ready.
MINIMUM_TRANSPORT_VERSION = 2


def is_compatible_transport_version(version: object) -> bool:
    return isinstance(version, int) and not isinstance(version, bool) and version >= MINIMUM_TRANSPORT_VERSION


async def check() -> int:
    try:
        async with websockets.connect("ws://127.0.0.1:8765", open_timeout=2) as socket:
            if "--shutdown" in sys.argv:
                await socket.send(json.dumps({"command": "shutdown"}))
                return 0
            await socket.send(json.dumps({"command": "get_snapshot"}))
            reply = json.loads(await asyncio.wait_for(socket.recv(), timeout=3))
    except Exception as error:
        print(f"Backend readiness check failed: {type(error).__name__}: {error}")
        return 1

    data = reply.get("data", {}) if isinstance(reply, dict) else {}
    is_aifren = reply.get("type") == "snapshot" and isinstance(data, dict) and {
        "conversation", "character", "status"
    }.issubset(data)
    if "--classify" in sys.argv:
        print("aifren" if is_aifren else "other")
        return 0 if is_aifren else 2

    version = data.get("transport_version") if isinstance(data, dict) else None
    if reply.get("type") != "snapshot" or not is_compatible_transport_version(version):
        print("A listener on port 8765 is not the current AIFren backend (transport v2-compatible snapshot required).")
        return 2

    print("AIFren backend transport v2 is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(check()))
