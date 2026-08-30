#!/usr/bin/env python3
"""Run the real Kokoro presentation path with ephemeral synthetic state.

This helper is deliberately gated by ``AIFREN_ENABLE_DEVELOPMENT_QA`` and
creates its character registry in a temporary directory.  It is suitable for
profiling a Development player without opening any installed character,
conversation, memory, or model configuration.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
import tempfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from assistant_service import AssistantService  # noqa: E402
from backend_host import AIFrenWebSocketHost, DEFAULT_PORT  # noqa: E402
from llm.unavailable import UnavailableLLM  # noqa: E402
from tts.tts import KokoroTextToSpeech  # noqa: E402


class _EphemeralConversation:
    messages: list[dict] = []

    def save(self) -> None:
        pass


class _EphemeralMemory:
    memories: list[dict] = []

    def save(self) -> None:
        pass


def _require_development_gate() -> None:
    enabled = os.environ.get("AIFREN_ENABLE_DEVELOPMENT_QA", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        raise RuntimeError(
            "Set AIFREN_ENABLE_DEVELOPMENT_QA=1 to run synthetic player QA."
        )


async def _run() -> None:
    _require_development_gate()
    # Construct Kokoro while relative model paths still resolve inside the
    # repository.  The host then changes into the isolated application root.
    tts = KokoroTextToSpeech()
    service = AssistantService(
        llm=UnavailableLLM("Synthetic presentation QA does not accept model turns."),
        memory=_EphemeralMemory(),
        conversation=_EphemeralConversation(),
        voice=object(),
        character={
            "name": "Synthetic Presentation QA",
            "description": "Ephemeral Development-build presentation fixture.",
        },
        character_prompt="",
        tts=tts,
    )

    with tempfile.TemporaryDirectory(prefix="aifren-development-qa-") as directory:
        # Kokoro's pipeline retains a repository-relative voice path after
        # construction.  Expose the existing read-only model tree inside the
        # ephemeral application root; never copy it into a build or fixture.
        Path(directory, "models").symlink_to(REPOSITORY_ROOT / "models", target_is_directory=True)
        host = AIFrenWebSocketHost(
            service=service,
            application_dir=directory,
            port=DEFAULT_PORT,
        )
        await host.start()
        print(
            f"Synthetic AIFren QA backend listening at ws://{host.host}:{host.port}",
            flush=True,
        )
        try:
            await host._shutdown_requested.wait()
        finally:
            await host.stop()


if __name__ == "__main__":
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
