"""Loopback-only WebSocket host for a separate AIFren frontend process.

This is a transport adapter around one :class:`AssistantService` instance.
It deliberately does not contain presentation, Unity, or persistence logic.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import datetime
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

import websockets
from websockets.exceptions import ConnectionClosed

from assistant_service import AssistantEvent, AssistantService, canonical_message_identity
from character_registry import CharacterRegistry, CharacterRegistryError
from development_flight_recorder import development_flight_recorder, valid_capture_id
from local_model_runtime import LocalModelRuntime


LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
PROACTIVE_STARTUP_GRACE_SECONDS = 60.0


class AIFrenWebSocketHost:
    """Expose one AssistantService instance over one local WebSocket client."""

    def __init__(
        self,
        service: Optional[AssistantService] = None,
        service_factory: Callable[[], AssistantService] = AssistantService.create_default,
        host: str = LOOPBACK_HOST,
        port: int = DEFAULT_PORT,
        application_dir: Optional[Path | str] = None,
        local_model_runtime: Optional[LocalModelRuntime] = None,
        proactive_startup_grace_seconds: float = PROACTIVE_STARTUP_GRACE_SECONDS,
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
        self._character_registry: Optional[CharacterRegistry] = None
        self._local_model_runtime: Optional[LocalModelRuntime] = local_model_runtime
        self._local_model_tasks: set[asyncio.Task] = set()
        self._proactive_task: asyncio.Task | None = None
        self._stopping = False
        self._proactive_startup_grace_seconds = max(
            0.0, float(proactive_startup_grace_seconds),
        )
        self._backend_ready_at: float | None = None
        self._provider_ready_at: float | None = None
        self._frontend_snapshot_ready_at: float | None = None

    def _log(self, message: str) -> None:
        """Keep a bounded, local diagnostics stream without user content/secrets."""
        text = str(message).replace("\n", " ").strip()
        development_flight_recorder().observe_model_log(text)
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

        self._stopping = False

        if not self.application_dir.is_dir():
            raise ValueError(f"Application directory does not exist: {self.application_dir}")

        # Existing persistent paths are intentionally relative.  Establish the
        # application location before default initialization reads them.
        os.chdir(self.application_dir)
        self._character_registry = CharacterRegistry(self.application_dir)

        if self._service is None:
            self._service = self._service_factory()

        self._loop = asyncio.get_running_loop()
        self._shutdown_requested = asyncio.Event()
        self._unsubscribe = self.service.subscribe(self._on_service_event)
        self._server = await websockets.serve(self._handle_client, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        self._running = True
        self._backend_ready_at = time.monotonic()
        self._proactive_task = asyncio.create_task(self._proactive_loop())
        self._log(f"Backend listening on ws://{self.host}:{self.port}")
        from model_settings import get_model_settings
        settings = get_model_settings()
        if settings["mode"] == "local" and settings["local_auto_start"]:
            self._schedule_local_model_start()
        return self

    async def stop(self) -> None:
        """Finish active turns, close the service, and close local clients."""
        if not self._running:
            return

        if self._turn_tasks:
            await asyncio.gather(*list(self._turn_tasks), return_exceptions=True)

        self._stopping = True
        if self._proactive_task is not None:
            self._proactive_task.cancel()
            await asyncio.gather(self._proactive_task, return_exceptions=True)
            self._proactive_task = None
        # A readiness probe runs in a worker thread. Stop its owned process
        # first so closing AIFren never waits the full readiness timeout.
        for task in list(self._local_model_tasks):
            task.cancel()
        if self._local_model_runtime is not None:
            await asyncio.to_thread(self._local_model_runtime.stop)
        if self._local_model_tasks:
            await asyncio.gather(*list(self._local_model_tasks), return_exceptions=True)

        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

        self.service.close()
        development_flight_recorder().stop()

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
        except ConnectionClosed:
            # Normal Unity/player closure can lack an explicit close frame.
            self._log("Local frontend connection closed.")
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

        if command == "development_flight_recorder_start":
            unity_pid = command_data.get("unity_pid")
            if isinstance(unity_pid, bool) or not isinstance(unity_pid, int) or unity_pid <= 0:
                await self._send_command_error(websocket, "invalid_flight_recorder_pid", "Development flight recorder requires a Unity process ID.")
                return
            recorder = development_flight_recorder()
            recorder.start(
                unity_pid=unity_pid, state_provider=self._flight_recorder_state,
                auto_trigger=self._on_flight_recorder_auto_trigger,
            )
            try:
                model = self._model_snapshot().get("current", {})
                tts = self._tts_snapshot()
                continuity = self._continuity_snapshot()
                recorder.mark(
                    "runtime_configuration", provider=model.get("provider", model.get("mode", "")),
                    model=model.get("model", model.get("selected_model", "")), device=tts.get("device", ""),
                )
                recorder.mark(
                    "continuity_snapshot_counts",
                    scene_subject_count=len(continuity.get("scene_subjects", ())),
                    scene_relation_count=len(continuity.get("scene_relations", ())),
                    capability_effect_count=len(continuity.get("capability_effects", ())),
                    baseline_fact_count=len(continuity.get("profile_baseline", ())),
                )
            except Exception:
                pass
            await self._send_json(websocket, {"type": "event", "event": {"type": "flight_recorder_ready", "data": {"state": "ready"}}})
            return

        if command == "development_flight_recorder_trigger":
            capture_id = command_data.get("capture_id")
            reason = command_data.get("reason")
            if not valid_capture_id(capture_id) or not isinstance(reason, str):
                await self._send_command_error(websocket, "invalid_flight_recorder_capture", "Invalid Development flight-recorder capture request.")
                return
            development_flight_recorder().trigger(capture_id, reason)
            return

        if command == "development_flight_recorder_dump":
            capture_id = command_data.get("capture_id")
            if not valid_capture_id(capture_id):
                await self._send_command_error(websocket, "invalid_flight_recorder_capture", "Invalid Development flight-recorder capture ID.")
                return
            summary = await asyncio.to_thread(development_flight_recorder().dump, capture_id)
            if summary is None:
                await self._send_command_error(websocket, "flight_recorder_dump_failed", "Development flight-recorder dump failed.")
                return
            await self._send_json(websocket, {
                "type": "event", "event": {"type": "flight_recorder_backend_dumped", "data": {
                    "capture_id": capture_id,
                    "minimum_available_ram_mb": summary.get("minimum_available_ram_mb"),
                    "swap_in_pages_total": summary.get("swap_in_pages_total", 0),
                    "swap_out_pages_total": summary.get("swap_out_pages_total", 0),
                    "peak_gpu_utilization_percent": summary.get("peak_gpu_utilization_percent"),
                    "peak_vram_mb": summary.get("peak_vram_mb"),
                    "peak_backend_rss_mb": summary.get("peak_backend_rss_mb"),
                    "peak_unity_rss_mb": summary.get("peak_unity_rss_mb"),
                    "peak_llama_rss_mb": summary.get("peak_llama_rss_mb"),
                }}
            })
            return

        if command == "get_snapshot":
            await self._send_snapshot(websocket)
            return

        if command == "continuity_control":
            command_id = command_data.get("command_id")
            action = command_data.get("action")
            expected_revision = command_data.get("expected_revision")
            action_token = command_data.get("action_token", "")
            if not all(isinstance(value, str) for value in (
                command_id, action, expected_revision, action_token,
            )):
                await self._send_command_error(
                    websocket, "invalid_continuity_control",
                    "Continuity controls require an action, command ID, and current revision.",
                )
                return
            if action == "interact_scene_relation":
                # An immersive scene interaction can include provider work
                # after its authoritative mutation. Keep the socket receive
                # loop available so a later click can cancel stale reaction
                # prose and submit the next snapshot-relative command.
                task = asyncio.create_task(self._run_continuity_control(
                    websocket,
                    command_id=command_id,
                    action=action,
                    expected_revision=expected_revision,
                    action_token=action_token,
                ))
                self._turn_tasks.add(task)
                task.add_done_callback(self._turn_tasks.discard)
                return
            try:
                result = await asyncio.to_thread(
                    self.service.apply_continuity_control,
                    command_id=command_id,
                    action=action,
                    expected_revision=expected_revision,
                    action_token=action_token,
                )
            except ValueError as error:
                await self._send_command_error(websocket, "invalid_continuity_control", str(error))
                return
            except RuntimeError as error:
                await self._send_command_error(websocket, "stale_continuity_control", str(error))
                return
            await self._send_json(websocket, {
                "type": "event",
                "event": {"type": "continuity_control_result", "data": self._json_safe(result)},
            })
            return

        if command in {"list_characters", "get_active_character"}:
            await self._send_snapshot(websocket)
            return

        if command == "create_character":
            display_name = command_data.get("display_name")
            personality = command_data.get("personality")
            if not isinstance(display_name, str) or not display_name.strip():
                await self._send_command_error(websocket, "invalid_character_name", "create_character requires a display name.")
                return
            if not isinstance(personality, str) or not personality.strip():
                await self._send_command_error(
                    websocket,
                    "invalid_character_personality",
                    "create_character requires a personality prompt.",
                )
                return
            try:
                self._registry().create(display_name, personality=personality)
            except CharacterRegistryError as error:
                await self._send_command_error(websocket, "character_create_failed", str(error))
                return
            await self._send_snapshot(websocket)
            return

        if command == "select_character":
            character_id = command_data.get("character_id")
            if not isinstance(character_id, str) or not character_id.strip():
                await self._send_command_error(websocket, "invalid_character_id", "select_character requires a character ID.")
                return
            await self._select_character(websocket, character_id)
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

        if command == "set_kokoro_early_speech":
            enabled = command_data.get("early_speech")
            if not isinstance(enabled, bool):
                await self._send_command_error(
                    websocket,
                    "invalid_kokoro_early_speech",
                    "set_kokoro_early_speech requires true or false.",
                )
                return
            try:
                from model_settings import set_kokoro_early_speech
                set_kokoro_early_speech(enabled)
            except Exception as error:
                await self._send_command_error(websocket, "tts_settings_failed", str(error))
                return
            await self._send_snapshot(websocket)
            return

        if command == "set_proactive_behavior":
            enabled = command_data.get("proactive_behavior")
            if not isinstance(enabled, bool):
                await self._send_command_error(
                    websocket, "invalid_proactive_behavior",
                    "set_proactive_behavior requires true or false.",
                )
                return
            try:
                from model_settings import set_proactive_behavior
                set_proactive_behavior(enabled)
            except Exception as error:
                await self._send_command_error(websocket, "companion_settings_failed", str(error))
                return
            await self._send_snapshot(websocket)
            return
        if command == "set_proactive_interval":
            interval = command_data.get("proactive_interval_seconds")
            try:
                from model_settings import set_proactive_interval
                set_proactive_interval(interval)
            except (TypeError, ValueError):
                await self._send_command_error(websocket, "invalid_proactive_interval",
                                               "Unsupported proactive minimum interval.")
                return
            await self._send_snapshot(websocket)
            return

        if command == "set_local_auto_start":
            enabled = command_data.get("local_auto_start")
            if not isinstance(enabled, bool):
                await self._send_command_error(
                    websocket,
                    "invalid_local_auto_start",
                    "set_local_auto_start requires true or false.",
                )
                return
            try:
                from model_settings import set_local_auto_start
                set_local_auto_start(enabled)
            except Exception as error:
                await self._send_command_error(websocket, "model_settings_failed", str(error))
                return
            await self._send_snapshot(websocket)
            return

        if command == "set_model_settings":
            can_reconfigure = getattr(self.service, "can_reconfigure_model", None)
            if callable(can_reconfigure) and not can_reconfigure():
                await self._send_command_error(
                    websocket,
                    "model_settings_busy",
                    "Assistant is still processing. Try again when it is ready.",
                )
                return
            try:
                from model_settings import get_model_settings, set_model_settings
                from llm.llm import create_llm
                from llm.unavailable import UnavailableLLM
                current = get_model_settings()

                def supplied(name: str, fallback: str) -> str:
                    value = command_data.get(name)
                    return value if isinstance(value, str) else fallback

                set_model_settings(
                    mode=supplied("mode", current["mode"]),
                    online_provider=supplied("provider", current["online_provider"]),
                    online_model=supplied("online_model", current["online_model"]),
                    online_base_url=supplied("online_base_url", current["online_base_url"]),
                    local_endpoint=supplied("local_endpoint", current["local_endpoint"]),
                    local_model=supplied("local_model", current["local_model"]),
                    local_auto_start=command_data.get("local_auto_start") if command_data.get("has_local_auto_start") is True else current["local_auto_start"],
                    api_key=command_data.get("api_key") if "api_key" in command_data else None,
                    local_api_key=command_data.get("local_api_key") if "local_api_key" in command_data else None,
                )
                updated = get_model_settings()
                if updated["mode"] == "online" and self._local_model_runtime is not None:
                    # LocalModelRuntime.stop() terminates only a process this
                    # host launched. An external compatible endpoint remains
                    # untouched and continues to report external ownership.
                    await asyncio.to_thread(self._local_model_runtime.stop)
                if updated["mode"] == "local":
                    # A saved model name is not proof that an external endpoint
                    # serves it. Do not submit generation until /models has
                    # verified the selected model.
                    self.service.replace_llm(UnavailableLLM(
                        "Start or refresh the selected Local model in Settings > Model."
                    ))
                else:
                    self.service.replace_llm(create_llm())
            except Exception as error:
                await self._send_command_error(websocket, "model_settings_failed", str(error))
                return
            await self._send_snapshot(websocket)
            updated = get_model_settings()
            runtime = self._runtime()
            state = runtime.snapshot(selected_model=updated["local_model"])
            if updated["mode"] == "local":
                selected_ready = (
                    state.get("state") == "ready"
                    and state.get("active_model") == updated["local_model"]
                )
                switch_managed_model = (
                    state.get("ownership") == "managed"
                    and state.get("active_model") != updated["local_model"]
                )
                auto_start_stopped_model = (
                    updated["local_auto_start"]
                    and state.get("ownership") != "external"
                    and not selected_ready
                )
                if switch_managed_model or auto_start_stopped_model:
                    self._schedule_local_model_start()
            return

        if command == "discover_local_models":
            from model_settings import get_model_settings
            settings = get_model_settings()
            endpoint = command_data.get("local_endpoint") if isinstance(command_data.get("local_endpoint"), str) else settings["local_endpoint"]
            state = await asyncio.to_thread(
                self._runtime().refresh_external, endpoint, settings["local_api_key"], selected_model=settings["local_model"]
            )
            if state.get("state") == "ready":
                report = getattr(self.service, "report_model_runtime_available", None)
                if callable(report): report()
            else:
                report = getattr(self.service, "report_model_runtime_unavailable", None)
                if callable(report): report()
                # Installed GGUF discovery remains useful while the server is
                # off, but retain a clear recoverable endpoint diagnostic.
                await self._send_command_error(
                    websocket, "local_model_discovery_failed",
                    "Could not reach the local model endpoint. Check Settings > Model.",
                )
            installed = state.get("installed_models", [])
            endpoint_models = [
                {"identifier": str(item), "display_name": str(item), "source": "external"}
                for item in state.get("endpoint_models", [])
            ]
            await self._send_json(websocket, {"type": "event", "event": {"type": "local_models", "data": {"models": [*installed, *endpoint_models]}}})
            await self._send_snapshot(websocket)
            return

        if command == "start_local_model":
            can_reconfigure = getattr(self.service, "can_reconfigure_model", None)
            if callable(can_reconfigure) and not can_reconfigure():
                await self._send_command_error(websocket, "model_settings_busy", "Wait for the current response before starting a local model.")
                return
            from model_settings import get_model_settings
            settings = get_model_settings()
            if settings["mode"] != "local":
                await self._send_command_error(websocket, "local_mode_required", "Select Local mode before starting a managed local model.")
                return
            self._runtime().begin_start(selected_model=settings["local_model"])
            self._schedule_local_model_start()
            await self._send_snapshot(websocket)
            return

        if command == "stop_local_model":
            can_reconfigure = getattr(self.service, "can_reconfigure_model", None)
            if callable(can_reconfigure) and not can_reconfigure():
                await self._send_command_error(websocket, "model_settings_busy", "Wait for the current response before stopping a local model.")
                return
            status = await asyncio.to_thread(self._runtime().stop)
            if status.get("ownership") == "external":
                await self._send_command_error(websocket, "external_local_model", "AIFren cannot stop an external local model server.")
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

    async def _run_continuity_control(
        self,
        websocket: Any,
        *,
        command_id: str,
        action: str,
        expected_revision: str,
        action_token: str,
    ) -> None:
        """Run an immersive scene gesture without blocking socket input."""
        try:
            result = await asyncio.to_thread(
                self.service.apply_continuity_control,
                command_id=command_id,
                action=action,
                expected_revision=expected_revision,
                action_token=action_token,
            )
        except ValueError as error:
            await self._send_command_error(websocket, "invalid_continuity_control", str(error))
            return
        except RuntimeError as error:
            await self._send_command_error(websocket, "stale_continuity_control", str(error))
            return
        await self._send_json(websocket, {
            "type": "event",
            "event": {"type": "continuity_control_result", "data": self._json_safe(result)},
        })

    def _runtime(self) -> LocalModelRuntime:
        if self._local_model_runtime is None:
            from config import LOCAL_LLM_CONTEXT_SIZE, LOCAL_LLM_MODEL_DIR
            self._local_model_runtime = LocalModelRuntime(
                self.application_dir, model_directory=LOCAL_LLM_MODEL_DIR,
                context_size=LOCAL_LLM_CONTEXT_SIZE, log=self._log,
            )
        return self._local_model_runtime

    def _schedule_local_model_start(self) -> None:
        task = asyncio.create_task(self._start_local_model_task())
        self._local_model_tasks.add(task)
        task.add_done_callback(self._local_model_tasks.discard)

    def _install_verified_local_provider(self) -> None:
        """Replace only the LLM adapter after selected-model validation."""
        try:
            from llm.llm import create_llm
            self.service.replace_llm(create_llm())
            report = getattr(self.service, "report_model_runtime_available", None)
            if callable(report):
                report()
        except Exception as error:
            self._log(f"Local provider refresh failed: {type(error).__name__}")

    async def _start_local_model_task(self) -> None:
        from model_settings import get_model_settings
        settings = get_model_settings()
        result = await asyncio.to_thread(
            self._runtime().start,
            endpoint=settings["local_endpoint"], selected_model=settings["local_model"], api_key=settings["local_api_key"],
        )
        if result.get("state") == "ready":
            # A managed alias is now known to be live; rebuild only the
            # replaceable provider adapter, never any continuity layer.
            self._install_verified_local_provider()
        else:
            report = getattr(self.service, "report_model_runtime_unavailable", None)
            if callable(report): report()
        if self._running and not self._stopping:
            await self._broadcast({"type": "event", "event": {"type": "local_model_runtime", "data": {"local_runtime": result}}})
            if self._client is not None:
                await self._send_snapshot(self._client)

    def _registry(self) -> CharacterRegistry:
        if self._character_registry is None:
            self._character_registry = CharacterRegistry(self.application_dir)
        return self._character_registry

    async def _select_character(self, websocket, character_id: str) -> None:
        """Atomically rebind character-owned state on the existing runtime."""
        registry = self._registry()
        try:
            previous = registry.active()
            selected = registry.get(character_id)
        except CharacterRegistryError as error:
            await self._send_command_error(websocket, "character_select_failed", str(error))
            return
        if selected is None:
            await self._send_command_error(websocket, "unknown_character", "The selected character no longer exists.")
            return
        if selected.character_id == previous.character_id:
            await self._send_snapshot(websocket)
            return

        proactive_was_running = self._proactive_task is not None
        if self._proactive_task is not None:
            self._proactive_task.cancel()
            await asyncio.gather(self._proactive_task, return_exceptions=True)
            self._proactive_task = None

        prepare = getattr(self.service, "prepare_character_switch", None)
        wait_idle = getattr(self.service, "wait_for_character_switch_idle", None)
        if callable(prepare):
            await asyncio.to_thread(prepare)
            if callable(wait_idle) and not await asyncio.to_thread(wait_idle, 30.0):
                if proactive_was_running and self._running and not self._stopping:
                    self._proactive_task = asyncio.create_task(self._proactive_loop())
                await self._send_command_error(
                    websocket, "character_switch_busy",
                    "The current response did not stop safely. Try switching again.",
                )
                return
            if self._turn_tasks:
                await asyncio.gather(*list(self._turn_tasks), return_exceptions=True)
            if self._event_tasks:
                await asyncio.gather(*list(self._event_tasks), return_exceptions=True)
        else:
            busy = getattr(self.service, "character_switch_busy", None)
            if self._turn_tasks or (callable(busy) and busy()):
                if proactive_was_running and self._running and not self._stopping:
                    self._proactive_task = asyncio.create_task(self._proactive_loop())
                await self._send_command_error(
                    websocket, "character_switch_busy",
                    "Wait for the current response and speech to finish before switching characters.",
                )
                return

        try:
            registry.select(selected.character_id)
            switch_state = getattr(self.service, "switch_character_state", None)
            if callable(switch_state):
                await asyncio.to_thread(
                    switch_state,
                    character_id=selected.character_id,
                    display_name=selected.display_name,
                    runtime_paths=registry.runtime_paths(selected.character_id),
                    application_dir=self.application_dir,
                )
                replacement = None
            else:
                # Compatibility seam for injected alternate/test services.
                replacement = await asyncio.to_thread(self._service_factory)
        except Exception as error:
            # Preserve the already-running service and revert the durable
            # selection if a replacement cannot initialize.
            registry.select(previous.character_id)
            self._log(f"Character switch failed: {type(error).__name__}")
            await self._send_command_error(websocket, "character_switch_failed", "Could not load the selected character.")
            if proactive_was_running and self._running and not self._stopping:
                self._proactive_task = asyncio.create_task(self._proactive_loop())
            return
        if replacement is not None:
            old_service = self.service
            if self._unsubscribe is not None:
                self._unsubscribe()
            self._service = replacement
            self._unsubscribe = replacement.subscribe(self._on_service_event)
            try:
                old_service.close()
            except Exception as error:
                self._log(f"Retired character service cleanup failed: {type(error).__name__}")

        self._status = {"state": "ready", "message": "Ready"}
        self._provider_ready_at = None
        self._frontend_snapshot_ready_at = None
        self._log("Character switched.")
        await self._send_snapshot(websocket)
        if proactive_was_running and self._running and not self._stopping:
            self._proactive_task = asyncio.create_task(self._proactive_loop())

    def _on_service_event(self, event: AssistantEvent) -> None:
        development_flight_recorder().observe_service_event(
            event.type, event.data if isinstance(event.data, dict) else None
        )
        if event.type in {"status", "error", "tts_state", "voice_state"}:
            data = event.data if isinstance(event.data, dict) else {}
            # The in-client console is diagnostic only. Never relay arbitrary
            # backend messages, because provider exceptions can contain URLs,
            # credentials, or user-originated content.
            state = data.get("state") if isinstance(data.get("state"), str) else event.type
            self._log(f"service event: {event.type} ({state})")
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

        self._loop.call_soon_threadsafe(schedule)

    def _flight_recorder_state(self) -> dict[str, Any]:
        """Return structural counters only; called by the low-rate sampler."""
        runtime = self._local_model_runtime
        process = getattr(runtime, "_process", None) if runtime is not None else None
        llama_pid = int(getattr(process, "pid", 0) or 0)
        tts_state_getter = getattr(self.service.tts, "playback_debug_state", None)
        try:
            tts_state = tts_state_getter() if callable(tts_state_getter) else {}
        except Exception:
            tts_state = {}
        voice = getattr(self.service, "voice", None)
        stt = getattr(voice, "stt", None)
        ptt = getattr(self.service, "_ptt", None)
        voice_state = str(self._voice_state or "")
        provider_active_getter = getattr(self.service, "provider_request_active", None)
        try:
            provider_active = bool(provider_active_getter()) if callable(provider_active_getter) else False
        except Exception:
            provider_active = False
        ptt_state_getter = getattr(ptt, "flight_recorder_state", None)
        try:
            ptt_state = ptt_state_getter() if callable(ptt_state_getter) else {}
        except Exception:
            ptt_state = {}
        return {
            "llama_pid": llama_pid,
            "turn_tasks": len(self._turn_tasks),
            "event_tasks": len(self._event_tasks),
            "provider_streams": 1 if provider_active else 0,
            "tts_active_jobs": 1 if tts_state.get("synthesizing") is not None else 0,
            "tts_pending_jobs": 0,
            "audio_queue_depth": 1 if tts_state.get("playing") is not None else 0,
            "model_generating": provider_active,
            "kokoro_synthesizing": tts_state.get("synthesizing") is not None,
            "portaudio_playing": tts_state.get("playing") is not None,
            "whisper_loaded": getattr(stt, "model", None) is not None,
            "whisper_active": voice_state in {"listening", "transcribing"} or bool(getattr(ptt, "record_thread", None)),
            **ptt_state,
        }

    def _on_flight_recorder_auto_trigger(self, reason: str) -> None:
        if not self._running or self._loop is None:
            return
        message = {
            "type": "event", "event": {
                "type": "flight_recorder_auto_trigger", "data": {"reason": str(reason)},
            },
        }
        self._loop.call_soon_threadsafe(lambda: asyncio.create_task(self._broadcast(message)))

    async def _send_snapshot(self, websocket) -> None:
        if self._frontend_snapshot_ready_at is None and websocket is self._client:
            # The snapshot can now be constructed and sent on the live
            # transport. Start the product grace from this readiness boundary,
            # not from process import or an old canonical timestamp.
            self._frontend_snapshot_ready_at = time.monotonic()
        conversation = []
        for index, message in enumerate(getattr(self.service.conversation, "messages", [])):
            if not isinstance(message, dict):
                continue
            conversation.append(
                {
                    "role": str(message.get("role", "")),
                    "content": str(message.get("content", "")),
                    "timestamp": message.get("timestamp"),
                    "message_id": canonical_message_identity(index, message),
                }
            )

        character = getattr(self.service, "character", {})
        identity = {
            key: str(character[key])
            for key in ("name", "description", "avatar")
            if isinstance(character, dict) and character.get(key) is not None
        }
        active_character = self._registry().active()
        identity["character_id"] = active_character.character_id
        characters = [
            {
                "character_id": item.character_id,
                "display_name": item.display_name,
                "is_active": item.character_id == active_character.character_id,
            }
            for item in self._registry().list_characters()
        ]

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
                    "transport_version": 6,
                    "conversation": conversation,
                    "character": identity,
                    "characters": characters,
                    "status": dict(self._status),
                    "voice": self._voice_snapshot(),
                    "tts": {"volume": volume, **self._tts_snapshot()},
                    "companion": self._companion_snapshot(),
                    "models": self._model_snapshot(),
                    "truth_scope": self._truth_scope_snapshot(),
                    "continuity": self._continuity_snapshot(),
                },
            },
        )

    def _model_snapshot(self) -> dict[str, Any]:
        from model_settings import model_status
        current = model_status()
        runtime_status = getattr(self.service, "model_runtime_availability", None)
        if current.get("configured") and callable(runtime_status):
            availability = runtime_status()
            if isinstance(availability, str) and availability and availability != "unknown":
                current["availability"] = availability
        runtime = self._runtime().snapshot(selected_model=str(current.get("selected_model", "") or ""))
        if current.get("mode") == "local":
            current["selected_model"] = str(runtime.get("selected_model", ""))
            if runtime.get("state") == "ready":
                current["availability"] = "configured"
            elif runtime.get("state") in {"error", "mismatch"}:
                current["availability"] = "unavailable"
        return {"current": current, "local_runtime": runtime}

    def _companion_snapshot(self) -> dict[str, Any]:
        from model_settings import proactive_behavior_status
        proactive = proactive_behavior_status()
        enabled = bool(proactive["enabled"])
        interval_seconds = int(proactive["interval_seconds"])
        eligibility_status = getattr(self.service, "proactive_eligibility", None)
        gate_outcome, gate_seconds = self._proactive_startup_gate()
        eligibility = (
            eligibility_status(enabled=enabled, interval_seconds=interval_seconds)
            if (not enabled or gate_outcome == "ready") and callable(eligibility_status) else None
        )
        now_us = int(time.time() * 1_000_000)
        continuity = self._continuity_snapshot()
        sleeping = bool(continuity.get("companion_activity")
                        and str(continuity["companion_activity"].get("value", "")).casefold() == "sleeping")
        capability_rows = {
            (str(item.get("domain", "")), str(item.get("capability", ""))): str(item.get("state", ""))
            for item in continuity.get("capability_effects", []) if isinstance(item, dict)
        }
        vision_mode = capability_rows.get(("perception", "vision"), "available")
        vision_obstructed = vision_mode in {"obstructed", "unavailable"}
        speech_mode = capability_rows.get(("communication", "speech"), "normal")
        hands_mode = capability_rows.get(("manipulation", "hands"), "free")
        locomotion_mode = capability_rows.get(("locomotion", "mode"), "walking")
        awareness_mode = capability_rows.get(("awareness", "awareness"), "asleep" if sleeping else "normal")
        posture_mode = capability_rows.get(("posture", "posture"), "")
        return {
            "proactive_behavior": enabled,
            "proactive_interval_seconds": interval_seconds,
            "proactive_eligibility": (
                str(eligibility.outcome)[:80] if eligibility is not None else gate_outcome
            ),
            "proactive_ignored_streak": int(getattr(eligibility, "ignored_streak", 0) or 0),
            "proactive_next_opportunity_seconds": max(
                0, int((eligibility.next_opportunity_us - now_us + 999_999) // 1_000_000)
            ) if eligibility is not None and eligibility.next_opportunity_us is not None else gate_seconds,
            "state_presentation": {
                "emotion": "relaxed" if sleeping else "neutral", "intensity": 0.35, "has_intensity": True,
                "pose": "sleeping" if sleeping else "awake",
                "gaze_mode": "suppressed" if sleeping or vision_obstructed else "normal",
                "speech_mode": "nonverbal" if sleeping else speech_mode,
                "vision_mode": vision_mode,
                "hands_mode": hands_mode,
                "locomotion_mode": locomotion_mode,
                "posture_mode": posture_mode,
                "awareness_mode": awareness_mode,
            },
        }

    def _proactive_provider_ready(self) -> bool:
        """Conservatively recognize a configured, live provider boundary."""
        try:
            model = self._model_snapshot()
            current = model.get("current", {}) if isinstance(model, dict) else {}
            if not isinstance(current, dict) or not bool(current.get("configured")):
                return False
            availability = str(current.get("availability", ""))
            if str(current.get("mode", "")) == "local":
                runtime = model.get("local_runtime", {})
                # Both owned and externally managed local endpoints are
                # represented as ready by LocalModelRuntime. A configured
                # selection alone does not mean the provider can answer yet.
                return isinstance(runtime, dict) and runtime.get("state") == "ready"
            return availability == "configured"
        except Exception:
            return False

    def _proactive_startup_gate(self, *, now: float | None = None) -> tuple[str, int]:
        """Return a structural readiness/grace reason and countdown."""
        current = time.monotonic() if now is None else float(now)
        if self._backend_ready_at is None:
            return "backend_not_ready", -1
        if self._client is None or self._frontend_snapshot_ready_at is None:
            return "frontend_not_ready", -1
        if self._provider_ready_at is None:
            if not self._proactive_provider_ready():
                return "provider_not_ready", -1
            self._provider_ready_at = current
        ready_at = max(
            self._backend_ready_at,
            self._provider_ready_at,
            self._frontend_snapshot_ready_at,
        )
        grace_end = ready_at + self._proactive_startup_grace_seconds
        if current < grace_end:
            remaining = max(0, int(grace_end - current + .999))
            return "startup_grace", remaining
        return "ready", -1

    async def _proactive_loop(self) -> None:
        """Poll slowly; deterministic eligibility, not the timer, authorizes speech."""
        try:
            while not self._stopping:
                await asyncio.sleep(10)
                if self._client is None or self._turn_tasks:
                    continue
                gate_outcome, gate_seconds = self._proactive_startup_gate()
                if gate_outcome != "ready":
                    development_flight_recorder().mark(
                        "proactive_attempt_deferred", outcome=gate_outcome,
                        duration_seconds=max(0, gate_seconds),
                        generation_origin="proactive",
                    )
                    continue
                try:
                    worker = asyncio.create_task(asyncio.to_thread(
                        self.service.process_proactive_checkin,
                    ))
                    self._turn_tasks.add(worker)
                    try:
                        result = await worker
                    finally:
                        self._turn_tasks.discard(worker)
                    if result.succeeded:
                        self._log("Displayed one eligible proactive companion check-in.")
                except Exception as error:
                    self._log(f"Proactive companion poll failed safely: {type(error).__name__}")
        except asyncio.CancelledError:
            return

    def _truth_scope_snapshot(self) -> dict[str, str]:
        status = getattr(self.service, "truth_scope_status", None)
        if not callable(status):
            return {"kind": "real_world", "label": ""}
        try:
            value = status()
        except Exception:
            return {"kind": "real_world", "label": ""}
        if not isinstance(value, dict) or value.get("kind") not in {"real_world", "scenario"}:
            return {"kind": "real_world", "label": ""}
        return {
            "kind": str(value["kind"]),
            "label": str(value.get("label") or "") if value["kind"] == "scenario" else "",
        }

    def _continuity_snapshot(self) -> dict[str, Any]:
        status = getattr(self.service, "continuity_snapshot", None)
        if not callable(status):
            return {
                "scope": self._truth_scope_snapshot(), "activity": None,
                "companion_activity": None, "scene_subjects": [],
                "scene_relations": [], "capability_effects": [],
                "profile_baseline": [],
                "open_threads": [], "revision": "unavailable",
            }
        try:
            value = status()
        except Exception:
            return {
                "scope": self._truth_scope_snapshot(), "activity": None,
                "companion_activity": None, "scene_subjects": [],
                "scene_relations": [], "capability_effects": [],
                "profile_baseline": [],
                "open_threads": [], "revision": "unavailable",
            }
        return value if isinstance(value, dict) else {
            "scope": self._truth_scope_snapshot(), "activity": None,
            "companion_activity": None, "scene_subjects": [],
            "scene_relations": [], "capability_effects": [],
            "profile_baseline": [],
            "open_threads": [], "revision": "unavailable",
        }

    def _tts_snapshot(self) -> dict[str, Any]:
        from config import KOKORO_DEVICE, KOKORO_VOICE, TTS_PROVIDER
        from model_settings import kokoro_early_speech_status
        early_speech = kokoro_early_speech_status()
        return {
            "provider": type(self.service.tts).__name__.replace("TextToSpeech", "").lower(),
            "configured_provider": str(TTS_PROVIDER),
            "voice": str(getattr(self.service.tts, "voice", KOKORO_VOICE)),
            "device": str(getattr(self.service.tts, "device", KOKORO_DEVICE)),
            "fallback_reason": str(getattr(self.service.tts, "fallback_reason", "")),
            "early_speech": early_speech["effective"],
            "early_speech_configured": early_speech["configured"],
            "early_speech_overridden": early_speech["overridden"],
            "early_speech_supported": bool(getattr(self.service.tts, "supports_early_speech", False)),
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
