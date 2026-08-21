"""Loopback-only WebSocket host for a separate AIFren frontend process.

This is a transport adapter around one :class:`AssistantService` instance.
It deliberately does not contain presentation, Unity, or persistence logic.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

import websockets

from assistant_service import AssistantEvent, AssistantService


LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


class AIFrenWebSocketHost:
    """Expose one AssistantService instance over one local WebSocket client."""

    def __init__(
        self,
        service: Optional[AssistantService] = None,
        service_factory: Callable[[], AssistantService] = AssistantService.create_default,
        host: str = LOOPBACK_HOST,
        port: int = DEFAULT_PORT,
        application_dir: Optional[Path | str] = None,
    ) -> None:
        if host != LOOPBACK_HOST:
            raise ValueError("AIFren's WebSocket host must bind to 127.0.0.1.")

        self.host = host
        self.port = port
        self.application_dir = Path(
            application_dir or Path(__file__).resolve().parent
        ).resolve()
        self._service = service
        self._service_factory = service_factory
        self._server = None
        self._client = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._unsubscribe: Optional[Callable[[], None]] = None
        self._turn_tasks: set[asyncio.Task] = set()
        self._event_tasks: set[asyncio.Task] = set()
        self._running = False
        self._shutdown_requested: Optional[asyncio.Event] = None
        self._status = {"state": "ready", "message": "Ready"}
        self._voice_state = "ready"
        self._console_lines: deque[str] = deque(maxlen=250)
        self._diagnostic_log_path = self.application_dir / "logs" / "runtime_diagnostics.log"

    def _log(self, message: str) -> None:
        """Keep a bounded, local diagnostics stream without user content/secrets."""
        text = str(message).replace("\n", " ").strip()
        if text:
            entry = f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {text[:500]}"
            self._console_lines.append(entry)
            try:
                self._diagnostic_log_path.parent.mkdir(parents=True, exist_ok=True)
                if self._diagnostic_log_path.exists() and self._diagnostic_log_path.stat().st_size > 512 * 1024:
                    backup = self._diagnostic_log_path.with_suffix(".previous.log")
                    os.replace(self._diagnostic_log_path, backup)
                with self._diagnostic_log_path.open("a", encoding="utf-8") as handle:
                    handle.write(entry + "\n")
            except OSError:
                pass

    @property
    def service(self) -> AssistantService:
        if self._service is None:
            raise RuntimeError("The backend host has not been started.")
        return self._service

    async def start(self) -> "AIFrenWebSocketHost":
        """Initialize the service once, then listen on loopback only."""
        if self._running:
            return self

        if not self.application_dir.is_dir():
            raise ValueError(f"Application directory does not exist: {self.application_dir}")

        # Existing persistent paths are intentionally relative.  Establish the
        # application location before default initialization reads them.
        os.chdir(self.application_dir)

        if self._service is None:
            self._service = self._service_factory()

        self._loop = asyncio.get_running_loop()
        self._shutdown_requested = asyncio.Event()
        self._unsubscribe = self.service.subscribe(self._on_service_event)
        self._server = await websockets.serve(self._handle_client, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        self._running = True
        self._log(f"Backend listening on ws://{self.host}:{self.port}")
        return self

    async def stop(self) -> None:
        """Finish active turns, close the service, and close local clients."""
        if not self._running:
            return

        if self._turn_tasks:
            await asyncio.gather(*list(self._turn_tasks), return_exceptions=True)

        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

        self.service.close()

        if self._client is not None:
            try:
                await self._client.close(code=1001, reason="Backend shutting down")
            except Exception:
                pass
            self._client = None

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        self._running = False
        self._loop = None
        if self._shutdown_requested is not None:
            self._shutdown_requested.set()

    async def _handle_client(self, websocket) -> None:
        if self._client is not None:
            await self._send_command_error(
                websocket,
                "client_already_connected",
                "Only one local frontend connection is supported.",
            )
            await websocket.close(code=1013, reason="Frontend already connected")
            return

        self._client = websocket
        self._log("Local frontend connected.")

        try:
            async for raw_message in websocket:
                await self._handle_command(websocket, raw_message)
        finally:
            if self._client is websocket:
                self._client = None
                self._log("Local frontend disconnected.")
                # A focused Unity PTT hold cannot outlive its frontend.  The
                # existing PTT implementation ignores unmatched releases.
                release_ptt = getattr(self.service, "push_to_talk_release", None)
                if callable(release_ptt):
                    try:
                        release_ptt()
                    except Exception as error:
                        self._log(f"PTT disconnect release failed: {type(error).__name__}")

    async def _handle_command(self, websocket, raw_message: Any) -> None:
        if not isinstance(raw_message, str):
            await self._send_command_error(
                websocket,
                "invalid_message",
                "Commands must be JSON text messages.",
            )
            return

        try:
            command_data = json.loads(raw_message)
        except json.JSONDecodeError:
            await self._send_command_error(
                websocket,
                "invalid_json",
                "Command is not valid JSON.",
            )
            return

        if not isinstance(command_data, dict):
            await self._send_command_error(
                websocket,
                "invalid_command",
                "Command must be a JSON object.",
            )
            return

        command = command_data.get("command")

        if command == "get_snapshot":
            await self._send_snapshot(websocket)
            return

        if command == "shutdown":
            # The developer launcher uses this only for the exact backend PID
            # it started.  It lets normal service cleanup run before Windows
            # process termination is used as a last resort.
            asyncio.create_task(self.stop())
            return

        if command == "get_console_log":
            self._log("Console diagnostics requested.")
            await self._send_json(websocket, {"type": "event", "event": {"type": "console_log", "data": {"lines": list(self._console_lines)}}})
            return

        if command == "submit_text":
            text = command_data.get("text")
            if not isinstance(text, str) or not text.strip():
                await self._send_command_error(
                    websocket,
                    "invalid_text",
                    "submit_text requires a non-empty text string.",
                )
                return

            task = asyncio.create_task(self._run_text_turn(text))
            self._turn_tasks.add(task)
            task.add_done_callback(self._turn_tasks.discard)
            return

        if command == "stop_tts":
            self.service.stop_speaking()
            return

        if command == "ptt_press":
            try:
                self.service.push_to_talk_press()
            except Exception as error:
                self._log(f"PTT press failed: {type(error).__name__}")
                await self._send_command_error(websocket, "ptt_press_failed", str(error))
            return

        if command == "ptt_release":
            try:
                self.service.push_to_talk_release()
            except Exception as error:
                self._log(f"PTT release failed: {type(error).__name__}")
                await self._send_command_error(websocket, "ptt_release_failed", str(error))
            return

        if command == "set_ptt_binding":
            binding = command_data.get("binding")
            if not isinstance(binding, str) or not binding.strip():
                await self._send_command_error(
                    websocket,
                    "invalid_ptt_binding",
                    "set_ptt_binding requires a non-empty binding string.",
                )
                return
            try:
                self.service.set_push_to_talk_binding(binding)
            except Exception as error:
                await self._send_command_error(websocket, "ptt_binding_failed", str(error))
            return

        if command == "set_ptt_transcription_mode":
            mode = command_data.get("mode")
            if mode not in ("review", "auto_send"):
                await self._send_command_error(
                    websocket,
                    "invalid_transcription_mode",
                    "set_ptt_transcription_mode requires review or auto_send.",
                )
                return
            self.service.set_ptt_auto_submit_transcriptions(mode == "auto_send")
            return

        if command == "set_tts_volume":
            volume = command_data.get("volume")
            if isinstance(volume, bool) or not isinstance(volume, (int, float)):
                await self._send_command_error(
                    websocket,
                    "invalid_volume",
                    "set_tts_volume requires a numeric volume.",
                )
                return

            self.service.set_tts_volume(volume)
            return

        if command == "set_gemini_api_key":
            key = command_data.get("api_key")
            if not isinstance(key, str):
                await self._send_command_error(websocket, "invalid_api_key", "API key must be text.")
                return
            try:
                from local_settings import set_gemini_api_key
                from llm.llm import create_llm
                set_gemini_api_key(key)
                self.service.replace_llm(create_llm())
            except Exception as error:
                await self._send_command_error(websocket, "api_key_update_failed", str(error))
                return
            await self._send_snapshot(websocket)
            return

        await self._send_command_error(
            websocket,
            "unknown_command",
            f"Unknown command: {command!r}.",
        )

    async def _run_text_turn(self, text: str) -> None:
        # The service owns turn serialization.  Running it outside the receive
        # loop keeps snapshot, stop, and volume commands responsive.
        await asyncio.to_thread(self.service.process_text_turn, text)

    def _on_service_event(self, event: AssistantEvent) -> None:
        console_updated = False
        if event.type in {"status", "error", "tts_state", "voice_state"}:
            data = event.data if isinstance(event.data, dict) else {}
            # The in-client console is diagnostic only. Never relay arbitrary
            # backend messages, because provider exceptions can contain URLs,
            # credentials, or user-originated content.
            state = data.get("state") if isinstance(data.get("state"), str) else event.type
            self._log(f"service event: {event.type} ({state})")
            console_updated = True
        if event.type == "status":
            self._status = {
                "state": str(event.data.get("state", "ready")),
                "message": str(event.data.get("message", "")),
            }
        elif event.type == "voice_state":
            state = event.data.get("state") if isinstance(event.data, dict) else None
            self._voice_state = state if isinstance(state, str) else "ready"

        if not self._running or self._loop is None:
            return

        message = {
            "type": "event",
            "event": {
                "type": event.type,
                "data": self._json_safe(event.data),
            },
        }

        def schedule() -> None:
            task = asyncio.create_task(self._broadcast(message))
            self._event_tasks.add(task)
            task.add_done_callback(self._event_tasks.discard)
            if console_updated:
                console_message = {
                    "type": "event",
                    "event": {"type": "console_log", "data": {"lines": list(self._console_lines)}},
                }
                console_task = asyncio.create_task(self._broadcast(console_message))
                self._event_tasks.add(console_task)
                console_task.add_done_callback(self._event_tasks.discard)

        self._loop.call_soon_threadsafe(schedule)

    async def _send_snapshot(self, websocket) -> None:
        conversation = []
        for message in getattr(self.service.conversation, "messages", []):
            if not isinstance(message, dict):
                continue
            conversation.append(
                {
                    "role": str(message.get("role", "")),
                    "content": str(message.get("content", "")),
                    "timestamp": message.get("timestamp"),
                }
            )

        character = getattr(self.service, "character", {})
        identity = {
            key: str(character[key])
            for key in ("name", "description", "avatar")
            if isinstance(character, dict) and character.get(key) is not None
        }

        volume = None
        get_volume = getattr(self.service.tts, "get_volume", None)
        if callable(get_volume):
            volume = get_volume()

        self._log("Snapshot sent with active model, voice, and TTS status.")
        await self._send_json(
            websocket,
            {
                "type": "snapshot",
                "data": {
                    "transport_version": 2,
                    "conversation": conversation,
                    "character": identity,
                    "status": dict(self._status),
                    "voice": self._voice_snapshot(),
                    "tts": {"volume": volume, **self._tts_snapshot()},
                    "models": self._model_snapshot(),
                },
            },
        )

    @staticmethod
    def _model_snapshot() -> dict[str, Any]:
        from config import GEMINI_MODEL
        from local_settings import gemini_status
        return {"gemini": gemini_status(GEMINI_MODEL)}

    def _tts_snapshot(self) -> dict[str, Any]:
        from config import KOKORO_DEVICE, KOKORO_VOICE, TTS_PROVIDER
        return {
            "provider": type(self.service.tts).__name__.replace("TextToSpeech", "").lower(),
            "configured_provider": str(TTS_PROVIDER),
            "voice": str(getattr(self.service.tts, "voice", KOKORO_VOICE)),
            "device": str(getattr(self.service.tts, "device", KOKORO_DEVICE)),
        }

    def _voice_snapshot(self) -> dict[str, Any]:
        ptt = getattr(self.service, "_ptt", None)
        listener_active = getattr(ptt, "global_listener_active", None)
        return {
            "state": self._voice_state,
            "global_listener": bool(listener_active()) if callable(listener_active) else False,
        }

    async def _broadcast(self, message: dict[str, Any]) -> None:
        if self._client is not None:
            await self._send_json(self._client, message)

    async def _send_command_error(self, websocket, code: str, message: str) -> None:
        await self._send_json(
            websocket,
            {
                "type": "command_error",
                "error": {"code": code, "message": message},
            },
        )

    async def _send_json(self, websocket, message: dict[str, Any]) -> None:
        try:
            await websocket.send(json.dumps(self._json_safe(message), ensure_ascii=False))
        except Exception:
            if websocket is self._client:
                self._client = None

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(key): AIFrenWebSocketHost._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [AIFrenWebSocketHost._json_safe(item) for item in value]
        return str(value)


async def run_backend_host(port: int = DEFAULT_PORT) -> None:
    """Run the default local backend until interrupted."""
    host = AIFrenWebSocketHost(port=port)
    await host.start()
    print(f"AIFren backend listening at ws://{host.host}:{host.port}")

    try:
        # A local launcher can request clean shutdown through the same
        # loopback-only transport.  This must also release this outer runner
        # so backend_host.py exits instead of leaving an orphan process.
        await host._shutdown_requested.wait()
    finally:
        await host.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run AIFren's local WebSocket backend.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    try:
        asyncio.run(run_backend_host(args.port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
