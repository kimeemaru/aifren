"""Loopback-only WebSocket host for a separate AIFren frontend process.

This is a transport adapter around one :class:`AssistantService` instance.
It deliberately does not contain presentation, Unity, or persistence logic.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import time
import uuid
from contextvars import ContextVar
from datetime import datetime
from dataclasses import dataclass, field
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

import websockets
from websockets.exceptions import ConnectionClosed

from aifren.assistant_service import AssistantEvent, AssistantService, canonical_message_identity
from aifren.character.character_registry import CharacterRegistry, CharacterRegistryError, CharacterStorageError
from aifren.runtime.development_flight_recorder import development_flight_recorder, valid_capture_id
from aifren.runtime.local_model_runtime import LocalModelRuntime
from aifren.runtime.runtime_layout import initialize_data_root, resolve_runtime_roots, source_root


LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
PROACTIVE_STARTUP_GRACE_SECONDS = 60.0
_DEVELOPMENT_QA_ENABLED = os.environ.get("AIFREN_ENABLE_DEVELOPMENT_QA", "").strip().lower() in {
    "1", "true", "yes", "on",
}

_DEVELOPMENT_QA_RESPONSES = {
    "cold": (
        "*smiles and settles into the conversation* The first portrait test begins with a calm, readable line. "
        "This **important phrase** should remain emphasized while the voice continues without an artificial break. "
        "A final sentence gives the subtitle presenter enough material to cross its first page boundary naturally."
    ),
    "warm": (
        "*nods once* The second equivalent response checks the warmed presentation and speech path. "
        "Every received word should remain exact, *very clear*, and paced according to the active Instant Text setting. "
        "The same avatar, subtitle material, lip sync, and audio lifecycle are exercised again."
    ),
    "long": (
        "*takes a quiet breath and looks toward the window* This deliberately long synthetic response exercises portrait subtitles over many pages. "
        "The opening thought establishes a steady conversational rhythm while **key words remain emphasized** for readability. "
        "A second idea follows without asking the speech engine to stop at an arbitrary clause, allowing Kokoro to preserve natural prosody. "
        "The background and avatar remain visible while the main interface stays hidden, which is the primary daily-driver presentation. "
        "Another sentence supplies enough spoken words for repeated page transitions, timestamp advancement, lip movement, and normal reveal pacing. "
        "Nothing in this scenario belongs to a real character, conversation, memory, or voice profile; it is intentionally synthetic QA material. "
        "The final section checks that later pages retain the same pink face, clean dark outline, italic emphasis, and exact canonical wording. "
        "When playback completes, the subtitle session should retire once, without a flash, blank page, stale callback, or resumed cancelled speech."
    ),
}


@dataclass(frozen=True)
class _ModelOperation:
    """Host-loop identity; captured settings are never diagnostics."""
    runtime: Any = field(repr=False)
    runtime_token: Any = field(repr=False)
    settings: dict[str, Any] = field(repr=False)


class AIFrenWebSocketHost:
    """Expose one AssistantService instance over one local WebSocket client."""

    def __init__(
        self,
        service: Optional[AssistantService] = None,
        service_factory: Callable[[], AssistantService] = AssistantService.create_default,
        host: str = LOOPBACK_HOST,
        port: int = DEFAULT_PORT,
        application_dir: Optional[Path | str] = None,
        resource_root: Optional[Path | str] = None,
        data_root: Optional[Path | str] = None,
        seed_data_root: Optional[Path | str] = None,
        backend_owner_token: Optional[str] = None,
        local_model_runtime: Optional[LocalModelRuntime] = None,
        proactive_startup_grace_seconds: float = PROACTIVE_STARTUP_GRACE_SECONDS,
    ) -> None:
        if host != LOOPBACK_HOST:
            raise ValueError("AIFren's WebSocket host must bind to 127.0.0.1.")

        self.host = host
        self.port = port
        legacy_root = application_dir
        self.resource_dir, self.data_dir, self.seed_data_dir = resolve_runtime_roots(
            source_root(),
            resource_root=resource_root or legacy_root,
            data_root=data_root or legacy_root,
            seed_data_root=seed_data_root,
        )
        # ``application_dir`` historically named the writable/CWD root. Keep
        # the attribute as a compatibility alias while package resources live
        # behind the separate immutable resource root.
        self.application_dir = self.data_dir
        self._backend_owner_token = str(
            backend_owner_token
            if backend_owner_token is not None
            else os.environ.get("AIFREN_BACKEND_OWNER_TOKEN", "")
        ).strip()
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
        self._character_operations = None
        self._command_request_id = ContextVar("command_request_id", default="")
        self._character_generation = 0
        self._character_switching = False
        self._command_owner = ContextVar("character_command_owner", default=None)
        self._local_model_runtime: Optional[LocalModelRuntime] = local_model_runtime
        self._local_model_tasks: set[asyncio.Task] = set()
        self._model_operation: _ModelOperation | None = None
        self._local_model_health: str | None = None
        self._local_model_error: dict[str, Any] | None = None
        self._proactive_task: asyncio.Task | None = None
        self._stopping = False
        self._proactive_startup_grace_seconds = max(
            0.0, float(proactive_startup_grace_seconds),
        )
        self._backend_ready_at: float | None = None
        self._provider_ready_at: float | None = None
        self._frontend_snapshot_ready_at: float | None = None
        self._development_staged_runtime = None

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

        if not self.resource_dir.is_dir():
            raise ValueError(f"Resource directory does not exist: {self.resource_dir}")

        from aifren.runtime.development_staged_runtime import attest_development_staged_runtime
        self._development_staged_runtime = attest_development_staged_runtime(
            self.data_dir,
            character_id=os.environ.get("AIFREN_DEVELOPMENT_STAGED_CHARACTER_ID"),
        )

        initialize_data_root(self.data_dir, self.seed_data_dir)

        # Existing canonical path owners remain unchanged. Establish the
        # writable location before they initialize, while config resolves
        # packaged models and other immutable assets from the resource root.
        os.environ["AIFREN_RESOURCE_ROOT"] = str(self.resource_dir)
        os.chdir(self.data_dir)
        self._character_registry = CharacterRegistry(self.application_dir)

        if self._service is None:
            self._service = self._create_service_or_management()

        self._loop = asyncio.get_running_loop()
        self._shutdown_requested = asyncio.Event()
        self._unsubscribe = self.service.subscribe(self._on_service_event)
        self._server = await websockets.serve(self._handle_client, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        self._running = True
        self._backend_ready_at = time.monotonic()
        self._proactive_task = asyncio.create_task(self._proactive_loop())
        self._log(f"Backend listening on ws://{self.host}:{self.port}")
        if self._development_staged_runtime is not None:
            self._log(
                "Development staged character data is active "
                f"(character={self._development_staged_runtime.character_id}; "
                f"historical_evidence={self._development_staged_runtime.historical_evidence_count}; "
                f"validated_episodes={self._development_staged_runtime.validated_episode_count})."
            )
        authority = getattr(self.service, "memory_authority_status", lambda: {"mode": "v1"})()
        if authority.get("mode") == "v2":
            self._log("Memory authority: V2 (Memory V1 prompt and learned-memory writes disabled).")
        from aifren.runtime.model_settings import get_model_settings
        settings = get_model_settings()
        if settings["mode"] == "local" and settings["local_auto_start"]:
            self._schedule_local_model_start()
        return self

    async def stop(self) -> None:
        """Finish active turns, close the service, and close local clients."""
        if not self._running:
            return

        self._stopping = True
        retirement = self._new_model_operation()
        if self._turn_tasks:
            await asyncio.gather(*list(self._turn_tasks), return_exceptions=True)
        if self._proactive_task is not None:
            self._proactive_task.cancel()
            await asyncio.gather(self._proactive_task, return_exceptions=True)
            self._proactive_task = None
        # A readiness probe runs in a worker thread. Stop its owned process
        # first so closing AIFren never waits the full readiness timeout.
        for task in list(self._local_model_tasks):
            task.cancel()
        if self._local_model_runtime is not None:
            await asyncio.to_thread(retirement.runtime.stop, **self._runtime_operation_args(retirement))
        if self._local_model_tasks:
            await asyncio.gather(*list(self._local_model_tasks), return_exceptions=True)

        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

        try:
            self.service.close()
        finally:
            # A canonical save failure remains explicit to the shutdown
            # caller, but cannot retain the socket or strand the outer runner.
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
                voice_provider = getattr(self.service, "tts", None)
                cancel_voice = getattr(type(voice_provider), "cancel_voice_job", None)
                if callable(cancel_voice):
                    cancel_voice(voice_provider)
                    self.service.stop_speaking(interrupted=True)
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
        # Async command tasks inherit their immutable request owner. A delayed
        # failure must not be relabelled as belonging to a later selection.
        token = self._command_owner.set(None)
        request_token = self._command_request_id.set("")
        try:
            await self._dispatch_command(websocket, raw_message)
        finally:
            self._command_owner.reset(token)
            self._command_request_id.reset(request_token)

    async def _dispatch_command(self, websocket, raw_message: Any) -> None:
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

        request_id = command_data.get("request_id")
        if isinstance(request_id, str) and len(request_id) <= 128:
            self._command_request_id.set(request_id)
        command = command_data.get("command")
        if self._stopping:
            await self._send_command_error(websocket, "backend_stopping", "Backend is shutting down.")
            return

        binding = {}
        if command in {"submit_text", "continuity_control", "memory_view_query",
                       "memory_view_detail", "memory_view_mutate", "ptt_press",
                       "ptt_release", "stop_tts", "character_voice"}:
            self._command_owner.set({
                "character_id": str(command_data.get("character_id") or ""),
                "character_session": str(command_data.get("character_session") or ""),
                "character_generation": self._character_generation,
            })
            checker = getattr(self.service, "require_character_binding", None)
            if callable(checker):
                try:
                    if self._character_switching:
                        raise RuntimeError("Character selection is changing. Wait for its snapshot.")
                    checker(command_data.get("character_id"), command_data.get("character_session"))
                    binding = {"character_id": command_data["character_id"],
                               "character_session": command_data["character_session"]}
                except RuntimeError as error:
                    await self._send_command_error(websocket, "stale_character_control", str(error))
                    return

        reply_owner = {**binding, "character_generation": self._character_generation}

        if command == "character_voice":
            from aifren.tts.character_voice import CharacterVoiceTTS
            provider = self.service.tts
            if not isinstance(provider, CharacterVoiceTTS):
                await self._send_command_error(websocket, "character_voice_unavailable", "Character voices need the normal local speech runtime.")
                return
            action = command_data.get("action")
            if action in {"get", "cancel", "stop"}:
                job = getattr(self, "_character_voice_job", None)
                if (action != "get" and job is not None
                        and command_data.get("voice_operation_id") == getattr(self, "_character_voice_request_id", None)):
                    self.service.stop_voice_preview(job)
                await self._send_json(websocket, {"type": "character_voice", **reply_owner,
                    "request_id": self._command_request_id.get(), "data": {"tts": self._tts_snapshot()}})
                return
            if action not in {"prepare", "preview", "save"}:
                await self._send_command_error(websocket, "character_voice_invalid", "Unsupported voice operation.")
                return
            if self._turn_tasks or self._character_switching:
                await self._send_command_error(websocket, "character_voice_busy", "Stop the current reply before preparing or changing its voice.")
                return
            task = getattr(self, "_character_voice_task", None)
            if task is not None and not task.done():
                await self._send_command_error(websocket, "character_voice_busy", "The previous voice operation is finishing. Stop it or wait briefly.")
                return
            payload = {"engine": command_data.get("voice_engine", "kokoro"),
                       "language": command_data.get("voice_language", "en"),
                       "transcript": command_data.get("voice_transcript", ""),
                       "reference_path": command_data.get("voice_reference", ""),
                       "revision": command_data.get("voice_revision", "")}
            if not all(isinstance(value, str) for value in payload.values()) or any(len(v) > 4096 for v in payload.values()):
                await self._send_command_error(websocket, "character_voice_invalid", "Voice fields are invalid or too long.")
                return
            self.service.stop_speaking(interrupted=True)
            job = provider.begin_voice_job()
            request_id = self._command_request_id.get()
            self._character_voice_job = job
            self._character_voice_request_id = request_id
            await self._send_json(websocket, {"type": "character_voice", **reply_owner,
                "request_id": request_id, "data": {"tts": self._tts_snapshot()}})
            async def run_voice_operation():
                def guard():
                    if (provider is not self.service.tts or self._character_switching
                            or self._client is not websocket or self._stopping):
                        raise RuntimeError("Character voice owner was retired.")
                    self.service.require_character_binding(**binding)
                result = await asyncio.to_thread(provider.edit_voice, action, payload, job=job, guard=guard)
                if getattr(self, "_character_voice_task", None) is asyncio.current_task():
                    # The native/file operation is complete before the final
                    # acknowledgement. A next click may arrive while sending it.
                    self._character_voice_task = None
                if result is not None and provider._job_current(job):
                    try:
                        guard()
                        await self._send_json(websocket, {"type": "character_voice", **reply_owner,
                            "request_id": request_id, "data": {"tts": self._tts_snapshot()}})
                    except RuntimeError:
                        pass
            self._character_voice_task = asyncio.create_task(run_voice_operation())
            return

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
                    memory_authority=getattr(
                        self.service, "memory_authority_status", lambda: {"mode": "v1"}
                    )().get("mode", "v1"),
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
            request_id = self._command_request_id.get()
            if request_id:
                try:
                    request_id = str(uuid.UUID(request_id))
                except (ValueError, TypeError):
                    await self._send_command_error(websocket, "invalid_snapshot_request", "Invalid refresh request.")
                    return
            try:
                if self._character_switching:
                    raise RuntimeError("binding_in_progress")
                await self._send_snapshot(websocket, request_id=request_id)
            except Exception:
                await self._send_command_error(websocket, "snapshot_unavailable", "Couldn't refresh history. Retry when the character is ready.")
            return

        if command == "memory_view_query":
            request_id = command_data.get("request_id")
            character_id = command_data.get("character_id")
            lane = command_data.get("memory_lane", "v1")
            query = command_data.get("query", "")
            status_filter = command_data.get("status_filter", "current")
            scope_filter = command_data.get("scope_filter", "applicable")
            limit = command_data.get("limit", 20)
            offset = command_data.get("offset", 0)
            try:
                request_id = str(uuid.UUID(str(request_id)))
            except (TypeError, ValueError, AttributeError):
                await self._send_command_error(
                    websocket, "invalid_memory_view_request",
                    "Memory Viewer requires a valid request ID.",
                )
                return
            if not all(isinstance(value, str) for value in (
                character_id, lane, query, status_filter, scope_filter,
            )) or any(isinstance(value, bool) or not isinstance(value, int) for value in (limit, offset)):
                await self._send_command_error(
                    websocket, "invalid_memory_view_request",
                    "Memory Viewer query fields are malformed.",
                )
                return
            reader = getattr(self.service, "memory_view_page", None)
            if not callable(reader):
                page = {
                    "character_id": character_id, "lane": lane, "items": [],
                    "has_more": False, "availability": "unavailable",
                    "authority_label": "Memory Viewer unavailable",
                    "warning": "This backend does not provide Memory Viewer data.",
                }
            else:
                try:
                    page = await asyncio.to_thread(
                        reader, character_id=character_id, lane=lane, query=query,
                        status_filter=status_filter, scope_filter=scope_filter,
                        limit=limit, offset=offset,
                    )
                except ValueError as error:
                    await self._send_command_error(
                        websocket, "invalid_memory_view_request", str(error),
                    )
                    return
                except RuntimeError as error:
                    await self._send_command_error(
                        websocket, "stale_memory_view_request", str(error),
                    )
                    return
                except Exception as error:
                    page = {
                        "character_id": character_id, "lane": lane, "items": [],
                        "has_more": False, "availability": "degraded",
                        "authority_label": "Memory Viewer unavailable; memory authority is unchanged",
                        "warning": f"Memory Viewer failed safely ({type(error).__name__}).",
                    }
            development_flight_recorder().mark_view("memory_built", request_id, len(page.get("items") or []))
            await self._send_json(websocket, {
                "type": "event", **reply_owner, "event": {"type": "memory_view_page", "data": {
                    "request_id": request_id, "memory_page": page,
                }},
            })
            return

        if command == "memory_view_detail":
            request_id = command_data.get("request_id")
            character_id = command_data.get("character_id")
            lane = command_data.get("memory_lane")
            record_id = command_data.get("record_id")
            limit = command_data.get("limit", 8)
            offset = command_data.get("offset", 0)
            try:
                request_id = str(uuid.UUID(str(request_id)))
            except (TypeError, ValueError, AttributeError):
                await self._send_command_error(
                    websocket, "invalid_memory_view_detail",
                    "Memory detail requires a valid request ID.",
                )
                return
            if (
                not all(isinstance(value, str) for value in (character_id, lane, record_id))
                or any(isinstance(value, bool) or not isinstance(value, int) for value in (limit, offset))
            ):
                await self._send_command_error(
                    websocket, "invalid_memory_view_detail",
                    "Memory detail fields are malformed.",
                )
                return
            reader = getattr(self.service, "memory_view_detail", None)
            if not callable(reader):
                detail = {
                    "character_id": character_id, "lane": lane,
                    "record_id": record_id, "detail": {}, "has_more": False,
                    "availability": "unavailable",
                    "warning": "This backend does not provide Memory Viewer detail.",
                }
            else:
                try:
                    detail = await asyncio.to_thread(
                        reader, character_id=character_id, lane=lane,
                        record_id=record_id, limit=limit, offset=offset,
                    )
                except ValueError as error:
                    await self._send_command_error(
                        websocket, "invalid_memory_view_detail", str(error),
                    )
                    return
                except RuntimeError as error:
                    await self._send_command_error(
                        websocket, "stale_memory_view_detail", str(error),
                    )
                    return
                except Exception as error:
                    detail = {
                        "character_id": character_id, "lane": lane,
                        "record_id": record_id, "detail": {}, "has_more": False,
                        "availability": "degraded",
                        "warning": f"Memory detail failed safely ({type(error).__name__}).",
                    }
            await self._send_json(websocket, {
                "type": "event", **reply_owner, "event": {"type": "memory_view_detail", "data": {
                    "request_id": request_id, "memory_detail": detail,
                }},
            })
            return

        if command == "memory_view_mutate":
            request_id = command_data.get("request_id")
            command_id = command_data.get("command_id")
            character_id = command_data.get("character_id")
            action = command_data.get("action")
            record_id = command_data.get("record_id")
            content = command_data.get("content", "")
            category = command_data.get("category", "")
            importance = command_data.get("importance", 5)
            try:
                request_id = str(uuid.UUID(str(request_id)))
                command_id = str(uuid.UUID(str(command_id)))
            except (TypeError, ValueError, AttributeError):
                await self._send_command_error(
                    websocket, "invalid_memory_view_mutation",
                    "Memory Editor requires valid request and command IDs.",
                )
                return
            if not all(isinstance(value, str) for value in (
                character_id, action, record_id, content, category,
            )) or isinstance(importance, bool) or not isinstance(importance, int):
                await self._send_command_error(
                    websocket, "invalid_memory_view_mutation",
                    "Memory Editor fields are malformed.",
                )
                return
            mutation = getattr(self.service, "apply_memory_view_mutation", None)
            result: dict[str, Any]
            if not callable(mutation):
                result = {"accepted": False, "message": "This backend does not provide Memory Editor mutations."}
            else:
                try:
                    result = await asyncio.to_thread(
                        mutation, character_id=character_id, action=action,
                        record_id=record_id, content=content, category=category,
                        importance=importance, command_id=command_id,
                        **({"character_session": binding["character_session"]} if binding else {}),
                    )
                except Exception as error:
                    result = {
                        "accepted": False,
                        "message": str(error)[:240] or type(error).__name__,
                    }
            result = dict(result)
            result.update(request_id=request_id, command_id=command_id, character_id=character_id)
            await self._send_json(websocket, {
                "type": "event", **reply_owner, "event": {
                    "type": "memory_view_mutation_result", "data": result,
                },
            })
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
                    action_token=action_token, **binding,
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
                    action_token=action_token, **binding,
                )
            except ValueError as error:
                await self._send_command_error(websocket, "invalid_continuity_control", str(error))
                return
            except RuntimeError as error:
                await self._send_command_error(websocket, "stale_continuity_control", str(error))
                return
            await self._send_json(websocket, {
                "type": "event", **reply_owner,
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
                created = self._registry().create(display_name, personality=personality, request_id=self._command_request_id.get())
            except (CharacterRegistryError,OSError) as error:
                await self._send_command_error(websocket, "character_create_failed", str(error) if isinstance(error,CharacterRegistryError)
                    else "Character creation could not finish its owned files. Your form is kept; review character status before retrying.")
                return
            await self._send_json(websocket, {"type":"event", "event":{"type":"character_created", "data":{
                "character_id":created.character_id,"display_name":created.display_name,"request_id":self._command_request_id.get()}}})
            if getattr(self.service, "storage_unavailable", False) and self._registry().active_or_none() == created:
                # The empty-library shell is a real retired binding. Publish
                # the same generation transition used by ordinary selection;
                # a new service at its old generation is correctly rejected
                # by the client's stale-character fence.
                await self._select_character(websocket, created.character_id)
                return
            await self._send_snapshot(websocket)
            return

        if command in {"character_operation_preview", "character_operation_confirm", "open_character_folder"}:
            if self._character_switching:
                await self._send_command_error(websocket,"character_operation_failed","Character selection is changing. Wait for its snapshot.")
                return
            try:
                identity = str(command_data.get("character_id") or "")
                registry = self._registry()
                if command == "open_character_folder":
                    directory = registry.owned_directory(identity)
                    if not directory.is_dir(): raise CharacterStorageError("Character folder is missing; use storage recovery.")
                    await self._send_json(websocket,{"type":"event","event":{"type":"character_folder","data":{
                        "character_id":identity,"folder_path":str(directory)}}})
                elif command == "character_operation_preview":
                    await self._prepare_character_operation_preview(identity)
                    preview = await asyncio.to_thread(self._operations().preview,identity,str(command_data.get("action") or ""))
                    await self._send_json(websocket,{"type":"event","event":{"type":"character_operation_preview","data":preview}})
                else:
                    await self._confirm_character_operation(websocket,command_data)
            except (CharacterRegistryError, ValueError, RuntimeError,OSError) as error:
                await self._send_command_error(websocket,
                    "character_folder_failed" if command=="open_character_folder" else "character_operation_failed", str(error) if not isinstance(error,OSError)
                    else "Character storage is unavailable. No completed operation was reported; review its current status.")
            return

        if command == "select_character":
            character_id = command_data.get("character_id")
            if not isinstance(character_id, str) or not character_id.strip():
                await self._send_command_error(websocket, "invalid_character_id", "select_character requires a character ID.")
                return
            await self._select_character(websocket, character_id)
            return

        if command == "shutdown":
            supplied = str(command_data.get("owner_token") or "")
            if self._backend_owner_token and not hmac.compare_digest(
                supplied, self._backend_owner_token
            ):
                await self._send_command_error(
                    websocket, "backend_owner_mismatch",
                    "This launcher does not own the active AIFren backend.",
                )
                return
            asyncio.create_task(self.stop())
            return

        if command == "probe_runtime_owner":
            supplied = str(command_data.get("owner_token") or "")
            matched = bool(self._backend_owner_token) and hmac.compare_digest(
                supplied, self._backend_owner_token
            )
            await self._send_json(websocket, {
                "type": "runtime_owner",
                "data": {"matched": matched},
            })
            return

        if command == "get_console_log":
            self._log("Console diagnostics requested.")
            await self._send_json(websocket, {"type": "event", "event": {"type": "console_log", "data": {"lines": list(self._console_lines)}}})
            return

        if command == "development_presentation_qa":
            if not _DEVELOPMENT_QA_ENABLED:
                await self._send_command_error(
                    websocket, "development_qa_disabled",
                    "Development presentation QA is not enabled for this backend.",
                )
                return
            scenario = command_data.get("scenario")
            response = _DEVELOPMENT_QA_RESPONSES.get(scenario)
            if response is None:
                await self._send_command_error(
                    websocket, "invalid_development_qa_scenario",
                    "Unknown development presentation QA scenario.",
                )
                return
            task = asyncio.create_task(asyncio.to_thread(
                self.service.run_development_presentation_qa, response
            ))
            self._turn_tasks.add(task)
            task.add_done_callback(self._turn_tasks.discard)
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

            task = asyncio.create_task(self._run_text_turn(text, **binding))
            self._turn_tasks.add(task)
            task.add_done_callback(self._turn_tasks.discard)
            return

        if command == "stop_tts":
            self.service.stop_speaking(interrupted=True)
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

        if command == "set_companion_preferences":
            if not self.service.can_reconfigure_model():
                await self._send_command_error(websocket, "companion_preferences_busy",
                                               "Wait for the current reply, then save these preferences.")
                return
            try:
                from aifren.runtime.model_settings import set_companion_preferences
                preferences = set_companion_preferences(**{key: command_data[key] for key in
                    ("conversation_style", "responsive_speech", "automatic_expressions") if key in command_data})
                self.service.apply_companion_preferences(preferences)
            except (TypeError, ValueError):
                await self._send_command_error(websocket, "invalid_companion_preferences", "Invalid companion preferences.")
                return
            except Exception:
                await self._send_command_error(websocket, "companion_preferences_failed", "Could not save companion preferences.")
                return
            await self._send_json(websocket, {"type": "companion_preferences",
                                            "data": self.service.companion_preferences_snapshot()})
            return

        if command == "set_explicit_avatar_cues":
            enabled = command_data.get("explicit_avatar_cues")
            if not isinstance(enabled, bool):
                await self._send_command_error(websocket, "invalid_avatar_cues", "Avatar cues require true or false.")
                return
            if not self.service.can_reconfigure_model():
                await self._send_command_error(websocket, "avatar_cues_busy", "Wait for the current reply, then save avatar cues.")
                return
            try:
                from aifren.runtime.model_settings import set_explicit_avatar_cues
                set_explicit_avatar_cues(enabled)
                self.service.explicit_avatar_cues = enabled
            except Exception:
                await self._send_command_error(websocket, "avatar_cues_failed", "Could not save avatar cues.")
                return
            # A presentation preference acknowledgement must not reapply a
            # character snapshot (which owns its own state/face restoration).
            await self._send_json(websocket, {"type": "avatar_cues_settings",
                                            "data": {"explicit_avatar_cues": enabled}})
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
                from aifren.runtime.model_settings import set_kokoro_early_speech
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
                from aifren.runtime.model_settings import set_proactive_behavior
                set_proactive_behavior(enabled)
            except Exception as error:
                await self._send_command_error(websocket, "companion_settings_failed", str(error))
                return
            await self._send_snapshot(websocket)
            return
        if command == "set_proactive_interval":
            interval = command_data.get("proactive_interval_seconds")
            try:
                from aifren.runtime.model_settings import set_proactive_interval
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
                from aifren.runtime.model_settings import set_local_auto_start
                set_local_auto_start(enabled)
            except Exception as error:
                await self._send_command_error(websocket, "model_settings_failed", str(error))
                return
            await self._send_snapshot(websocket)
            return

        if command == "set_model_settings":
            operation = None
            can_reconfigure = getattr(self.service, "can_reconfigure_model", None)
            if callable(can_reconfigure) and not can_reconfigure():
                await self._send_command_error(
                    websocket,
                    "model_settings_busy",
                    "Assistant is still processing. Try again when it is ready.",
                )
                return
            try:
                from aifren.runtime.model_settings import get_model_settings, set_model_settings
                from aifren.llm.llm import create_llm
                from aifren.llm.unavailable import UnavailableLLM
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
                # Save acceptance, operation retirement and adapter selection
                # have no await between them. Old worker results cannot enter
                # this host-loop boundary after the replacement is accepted.
                operation = self._new_model_operation(updated)
                if updated["mode"] == "local":
                    # A saved model name is not proof that an external endpoint
                    # serves it. Do not submit generation until /models has
                    # verified the selected model.
                    self.service.replace_llm(UnavailableLLM(
                        "Start or refresh the selected Local model in Settings > Model."
                    ))
                else:
                    self.service.replace_llm(create_llm())
            except Exception:
                if operation is None or self._owns_model_operation(operation):
                    if operation is not None and self.service.can_reconfigure_model():
                        self.service.replace_llm(UnavailableLLM("Could not initialize the selected model. Check Settings > Model."))
                    await self._send_command_error(websocket, "model_settings_failed", "Could not apply model settings. Try again in Settings > Model.")
                return
            if updated["mode"] == "online":
                try:
                    await asyncio.to_thread(operation.runtime.stop, **self._runtime_operation_args(operation))
                except Exception:
                    # Local cleanup failure does not invalidate an already
                    # accepted Online adapter, even for the current operation.
                    if self._owns_model_operation(operation):
                        await self._send_command_error(websocket, "local_model_stop_failed", "Could not stop the owned local model. Check Settings > Model.")
                if not self._owns_model_operation(operation):
                    return
            await self._send_snapshot(websocket)
            if not self._owns_model_operation(operation):
                return
            runtime = operation.runtime
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
            from aifren.runtime.model_settings import get_model_settings
            settings = get_model_settings()
            endpoint = command_data.get("local_endpoint") if isinstance(command_data.get("local_endpoint"), str) else settings["local_endpoint"]
            # Discovery of an edited/Online endpoint is inventory only. It
            # cannot assert health for the selected provider configuration.
            applicable = settings["mode"] == "local" and endpoint == settings["local_endpoint"]
            operation = self._new_model_operation(settings) if applicable else self._model_operation
            if not applicable:
                from aifren.llm.llm import discover_local_models
                try:
                    models = await asyncio.to_thread(discover_local_models, endpoint, settings["local_api_key"])
                except Exception:
                    models = ()
                if operation is not self._model_operation or self._stopping:
                    return
                installed = [row.snapshot() for row in self._runtime().discover_installed()]
                state = {"state": "ready" if models else "error", "installed_models": installed, "endpoint_models": models}
            else:
                state = await self._probe_local_operation(operation, "refresh_external")
                if state is None:
                    return
            if applicable:
                self._apply_local_result(operation, state)
            if state.get("state") != "ready":
                # Installed GGUF discovery remains useful while the server is
                # off, but retain a clear recoverable endpoint diagnostic.
                await self._send_command_error(
                    websocket, "local_model_discovery_failed",
                    "Could not reach the local model endpoint. Check Settings > Model.",
                )
            if operation is not self._model_operation or self._stopping:
                return
            installed = state.get("installed_models", [])
            endpoint_models = [
                {"identifier": str(item), "display_name": str(item), "source": "external"}
                for item in state.get("endpoint_models", [])
            ]
            await self._send_json(websocket, {"type": "event", "event": {"type": "local_models", "data": {"models": [*installed, *endpoint_models]}}})
            if operation is self._model_operation and not self._stopping:
                await self._send_snapshot(websocket)
            return

        if command == "start_local_model":
            can_reconfigure = getattr(self.service, "can_reconfigure_model", None)
            if callable(can_reconfigure) and not can_reconfigure():
                await self._send_command_error(websocket, "model_settings_busy", "Wait for the current response before starting a local model.")
                return
            from aifren.runtime.model_settings import get_model_settings
            settings = get_model_settings()
            if settings["mode"] != "local":
                await self._send_command_error(websocket, "local_mode_required", "Select Local mode before starting a managed local model.")
                return
            self._schedule_local_model_start()
            await self._send_snapshot(websocket)
            return

        if command == "stop_local_model":
            can_reconfigure = getattr(self.service, "can_reconfigure_model", None)
            if callable(can_reconfigure) and not can_reconfigure():
                await self._send_command_error(websocket, "model_settings_busy", "Wait for the current response before stopping a local model.")
                return
            operation = self._new_model_operation()
            try:
                status = await asyncio.to_thread(operation.runtime.stop, **self._runtime_operation_args(operation))
            except Exception:
                if self._owns_model_operation(operation):
                    await self._send_command_error(websocket, "local_model_stop_failed", "Could not stop the owned local model. Check Settings > Model.")
                return
            if not self._owns_model_operation(operation):
                return
            if status.get("ownership") == "external":
                await self._send_command_error(websocket, "external_local_model", "AIFren cannot stop an external local model server.")
            if self._owns_model_operation(operation):
                await self._send_snapshot(websocket)
            return

        await self._send_command_error(
            websocket,
            "unknown_command",
            f"Unknown command: {command!r}.",
        )

    async def _run_text_turn(self, text: str, **binding) -> None:
        # The service owns turn serialization.  Running it outside the receive
        # loop keeps snapshot, stop, and volume commands responsive.
        await asyncio.to_thread(self.service.process_text_turn, text, **binding)

    async def _run_continuity_control(
        self,
        websocket: Any,
        *,
        command_id: str,
        action: str,
        expected_revision: str,
        action_token: str,
        **binding,
    ) -> None:
        """Run an immersive scene gesture without blocking socket input."""
        reply_owner = {**binding, "character_generation": self._character_generation}
        try:
            result = await asyncio.to_thread(
                self.service.apply_continuity_control,
                command_id=command_id,
                action=action,
                expected_revision=expected_revision,
                action_token=action_token, **binding,
            )
        except ValueError as error:
            await self._send_command_error(websocket, "invalid_continuity_control", str(error))
            return
        except RuntimeError as error:
            await self._send_command_error(websocket, "stale_continuity_control", str(error))
            return
        await self._send_json(websocket, {
            "type": "event", **reply_owner,
            "event": {"type": "continuity_control_result", "data": self._json_safe(result)},
        })

    def _runtime(self) -> LocalModelRuntime:
        if self._local_model_runtime is None:
            from aifren.runtime.config import LOCAL_LLM_CONTEXT_SIZE, LOCAL_LLM_MODEL_DIR
            self._local_model_runtime = LocalModelRuntime(
                self.application_dir, model_directory=LOCAL_LLM_MODEL_DIR,
                context_size=LOCAL_LLM_CONTEXT_SIZE, log=self._log,
            )
        return self._local_model_runtime

    def _new_model_operation(self, settings=None) -> _ModelOperation:
        """Reserve on the host loop before any worker/await; identities never repeat."""
        from aifren.runtime.model_settings import get_model_settings
        previous = self._model_operation
        if previous is not None:
            cancel = getattr(previous.runtime, "cancel_operation", None)
            if callable(cancel):
                cancel(previous.runtime_token)
        runtime = self._runtime()
        reserve = getattr(runtime, "reserve_operation", None)
        operation = _ModelOperation(
            runtime, reserve() if callable(reserve) else None,
            dict(get_model_settings() if settings is None else settings),
        )
        self._model_operation = operation
        self._local_model_health = "unverified" if operation.settings["mode"] == "local" else None
        self._local_model_error = None
        self._provider_ready_at = None
        return operation

    def _owns_model_operation(self, operation: _ModelOperation) -> bool:
        return (self._running and not self._stopping
                and operation is self._model_operation
                and operation.runtime is self._local_model_runtime)

    @staticmethod
    def _runtime_operation_args(operation: _ModelOperation) -> dict[str, Any]:
        # Controlled injected adapters may have only the original interface.
        # The production LocalModelRuntime always carries worker ownership too.
        return {"operation": operation.runtime_token} if operation.runtime_token is not None else {}

    def _schedule_local_model_start(self) -> None:
        operation = self._new_model_operation()
        begin = getattr(operation.runtime, "begin_start", None)
        if callable(begin):
            begin(selected_model=operation.settings["local_model"], **self._runtime_operation_args(operation))
        task = asyncio.create_task(self._start_local_model_task(operation))
        self._local_model_tasks.add(task)
        task.add_done_callback(lambda completed: self._local_model_task_done(completed, operation))

    def _local_model_task_done(self, task: asyncio.Task, operation: _ModelOperation) -> None:
        self._local_model_tasks.discard(task)
        failed = not task.cancelled() and task.exception() is not None
        if (self._owns_model_operation(operation)
                and self._local_model_health == "unverified"
                and (failed or task.cancelled())):
            # Also covers cancellation before the coroutine's first instruction.
            cancel = getattr(operation.runtime, "cancel_operation", None)
            if callable(cancel):
                cancel(operation.runtime_token)
            result = self._readiness_failure(
                "local_model_readiness_failed" if failed else "local_model_readiness_cancelled",
            )
            self._apply_local_result(operation, result)
            publication = asyncio.create_task(self._publish_local_result(operation, result))
            self._event_tasks.add(publication)
            publication.add_done_callback(self._event_tasks.discard)

    async def _probe_local_operation(self, operation: _ModelOperation, method: str):
        if not self._owns_model_operation(operation):
            return None
        settings = operation.settings
        try:
            result = await asyncio.to_thread(
                getattr(operation.runtime, method), endpoint=settings["local_endpoint"],
                selected_model=settings["local_model"], api_key=settings["local_api_key"],
                **self._runtime_operation_args(operation),
            )
            if (self._owns_model_operation(operation) and result.get("state") == "ready"
                    and result.get("ownership") == "managed"
                    and result.get("active_model") == settings["local_model"]):
                from aifren.llm.local_template import installed_policy_role
                # GGUF vocabulary metadata can take seconds to traverse. The
                # transport must keep accepting Stop/settings during this read.
                result["_application_policy_role"] = await asyncio.to_thread(
                    installed_policy_role, settings["local_model"])
        except asyncio.CancelledError:
            if self._owns_model_operation(operation):
                cancel = getattr(operation.runtime, "cancel_operation", None)
                if callable(cancel):
                    cancel(operation.runtime_token)
                self._apply_local_result(operation, self._readiness_failure("local_model_readiness_cancelled"))
                await self._publish_local_result(operation, self._readiness_failure("local_model_readiness_cancelled"))
            raise
        except Exception:
            result = self._readiness_failure("local_model_readiness_failed")
        return result if self._owns_model_operation(operation) else None

    @staticmethod
    def _readiness_failure(code: str) -> dict[str, Any]:
        return {"state": "error", "error_code": code,
                "error": "Local model readiness did not complete. Retry in Settings > Model."}

    def _apply_local_result(self, operation: _ModelOperation, result: dict[str, Any]) -> None:
        # No await: settings acceptance, identity check, adapter/health update
        # and the ensuing outbound enqueue are serialized by the host loop.
        if not self._owns_model_operation(operation):
            return
        if result.get("state") == "ready":
            try:
                from aifren.llm.llm import create_llm
                self.service.replace_llm(create_llm())
                if (result.get("ownership") == "managed"
                        and result.get("active_model") == getattr(self.service.llm, "model", None)):
                    self.service.llm.application_policy_role = result.pop("_application_policy_role", "user")
                if getattr(getattr(self.service, "llm", None), "is_available", True) is False:
                    raise RuntimeError("Local adapter is unavailable")
                report = getattr(self.service, "report_model_runtime_available", None)
                if callable(report):
                    report()
                self._local_model_health = "configured"
                self._local_model_error = None
                return
            except Exception:
                result.update(self._readiness_failure("local_provider_install_failed"))
        self._local_model_health = "unavailable"
        if result.get("error_code"):
            self._local_model_error = self._readiness_failure(result["error_code"])
        report = getattr(self.service, "report_model_runtime_unavailable", None)
        if callable(report):
            report()
        self._log("Local readiness unavailable; retry remains available in Settings.")

    async def _publish_local_result(self, operation: _ModelOperation, result: dict[str, Any]) -> None:
        if not self._owns_model_operation(operation):
            return
        await self._broadcast({"type": "event", "event": {"type": "local_model_runtime", "data": {"local_runtime": result}}})
        # A transport send may yield to settings replacement/shutdown. Do not
        # follow it with another publication from the retired operation.
        if self._owns_model_operation(operation) and self._client is not None:
            await self._send_snapshot(self._client)

    async def _start_local_model_task(self, operation: _ModelOperation) -> None:
        result = await self._probe_local_operation(operation, "start")
        if result is not None:
            self._apply_local_result(operation, result)
            await self._publish_local_result(operation, result)

    def _registry(self) -> CharacterRegistry:
        if self._character_registry is None:
            self._character_registry = CharacterRegistry(self.application_dir)
        return self._character_registry

    def _create_service_or_management(self):
        from aifren.character.character_unavailable import CharacterUnavailableService
        from aifren.conversation.conversation import ConversationPersistenceError
        from aifren.runtime.config import InferenceDeviceUnavailable
        selected=self._registry().active_or_none()
        if selected is None:
            self._status={"state":"character_required","message":"Create a character in Settings > Character."}
            return CharacterUnavailableService()
        try:
            self._registry().assert_storage_ready(selected.character_id)
            return self._service_factory()
        except InferenceDeviceUnavailable as error:
            self._status={"state":"runtime_unavailable","message":str(error)}
            return CharacterUnavailableService(selected,str(error))
        except (CharacterStorageError, ConversationPersistenceError) as error:
            self._status={"state":"storage_unavailable","message":str(error)}
            return CharacterUnavailableService(selected,str(error))
        except Exception as error:
            # A failed provider/runtime setup after maintenance must not revive
            # the retired service or strand the manager in a switching state.
            message="The selected character could not start. Its stored data was kept; select it again after resolving the runtime problem."
            self._log(f"Character runtime setup failed: {type(error).__name__}")
            self._status={"state":"storage_unavailable","message":message}
            return CharacterUnavailableService(selected,message)

    def _operations(self):
        if self._character_operations is None:
            from aifren.character.character_operations import CharacterOperationService
            self._character_operations=CharacterOperationService(self._registry())
        return self._character_operations

    async def _reload_managed_character(self, websocket):
        if self._unsubscribe is not None: self._unsubscribe()
        self._service=await asyncio.to_thread(self._create_service_or_management)
        self._unsubscribe=self.service.subscribe(self._on_service_event)
        self._voice_state="ready"
        if not getattr(self.service,"storage_unavailable",False): self._status={"state":"ready","message":"Ready"}

    async def _confirm_character_operation(self, websocket, command):
        identity=str(command.get("character_id") or "")
        if self._turn_tasks:
            raise CharacterStorageError("Wait for the current turn to finish before character maintenance.")
        await asyncio.to_thread(self._operations().validate_confirmation, token=command.get("token"),
            character_id=identity,revision=command.get("revision"))
        active=self._registry().active_or_none()
        affects_current=active is not None and active.character_id==identity
        self._character_switching=True
        self._character_generation+=1
        target=identity if affects_current else str(getattr(self.service,"character_id","") or "no-character")
        await self._send_json(websocket,{"type":"event","character_id":target,"character_session":"",
            "character_generation":self._character_generation,"event":{"type":"character_switching","data":{}}})
        proactive=self._proactive_task
        if proactive is not None:
            proactive.cancel();await asyncio.gather(proactive,return_exceptions=True);self._proactive_task=None
        closed=False
        try:
            if affects_current:
                await asyncio.to_thread(self.service.prepare_character_switch)
                if not await asyncio.to_thread(self.service.wait_for_character_switch_idle,30):
                    raise CharacterStorageError("Character is still busy. Try again after it stops.")
                # Even failed retirement must not leave a half-closed service
                # reachable under the current character label.
                closed=True
                await asyncio.to_thread(self.service.close)
            result=await asyncio.to_thread(self._operations().execute,token=command.get("token"),
                character_id=identity,revision=command.get("revision"))
            await self._send_json(websocket,{"type":"event","event":{"type":"character_operation_result","data":result}})
        finally:
            try:
                if closed:
                    await self._reload_managed_character(websocket)
            finally:
                self._character_switching=False
                await self._send_snapshot(websocket)
                if proactive is not None and self._running and not self._stopping and not getattr(self.service,"storage_unavailable",False):
                    self._proactive_task=asyncio.create_task(self._proactive_loop())

    async def _prepare_character_operation_preview(self, identity):
        # Verify the selected runtime's persisted snapshot before inventory.
        # A redundant save must not change the file identity and provoke an
        # idle observer update that invalidates this very review.
        if str(getattr(self.service,"character_id","")) != identity or getattr(self.service,"storage_unavailable",False):
            return
        if self._turn_tasks or getattr(self.service,"character_switch_busy",lambda:False)():
            raise CharacterStorageError("Wait for the current turn before reviewing character maintenance.")
        prepare = getattr(self.service,"prepare_character_maintenance",self.service.save)
        await asyncio.to_thread(prepare)

    async def _select_character(self, websocket, character_id: str) -> None:
        """Atomically rebind character-owned state on the existing runtime."""
        if self._character_switching:
            await self._send_command_error(websocket, "character_switch_busy", "Character selection is already changing.")
            return
        registry = self._registry()
        try:
            previous = registry.active_or_none()
            selected = registry.get(character_id)
        except CharacterRegistryError as error:
            await self._send_command_error(websocket, "character_select_failed", str(error))
            await self._send_snapshot(websocket)
            return
        if selected is None:
            await self._send_command_error(websocket, "unknown_character", "The selected character no longer exists.")
            await self._send_snapshot(websocket)
            return
        if (previous is not None and selected.character_id == previous.character_id
                and not getattr(self.service, "storage_unavailable", False)):
            await self._send_snapshot(websocket)
            return

        # Retire outgoing controls before waiting for synthesis/observers.
        # A failed switch settles back to a fresh snapshot of the old owner.
        self._character_switching = True
        self._character_generation += 1
        await self._send_json(websocket, {
            "type": "event", "character_id": selected.character_id,
            "character_session": "", "character_generation": self._character_generation,
            "event": {"type": "character_switching", "data": {
                "character_id": selected.character_id, "character_session": "",
                "character_generation": self._character_generation}}})

        proactive_was_running = self._proactive_task is not None
        if self._proactive_task is not None:
            self._proactive_task.cancel()
            await asyncio.gather(self._proactive_task, return_exceptions=True)
            self._proactive_task = None

        prepare = getattr(self.service, "prepare_character_switch", None)
        wait_idle = getattr(self.service, "wait_for_character_switch_idle", None)
        if callable(prepare):
            try:
                await asyncio.to_thread(prepare)
                self._voice_state = "ready"
                idle = not callable(wait_idle) or await asyncio.to_thread(wait_idle, 30.0)
            except Exception as error:
                idle = False
                self._log(f"Character quiescence failed: {type(error).__name__}")
            if not idle:
                self._character_switching = False
                await self._send_snapshot(websocket)
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
                self._character_switching = False
                await self._send_snapshot(websocket)
                if proactive_was_running and self._running and not self._stopping:
                    self._proactive_task = asyncio.create_task(self._proactive_loop())
                await self._send_command_error(
                    websocket, "character_switch_busy",
                    "Wait for the current response and speech to finish before switching characters.",
                )
                return

        try:
            switch_state = getattr(self.service, "switch_character_state", None)
            if callable(switch_state):
                await asyncio.to_thread(
                    switch_state,
                    character_id=selected.character_id,
                    display_name=selected.display_name,
                    runtime_paths=registry.runtime_paths(selected.character_id),
                    application_dir=self.application_dir,
                    publish_selection=lambda: registry.select(selected.character_id),
                )
                replacement = None
            else:
                registry.select(selected.character_id)
                # Compatibility seam for injected alternate/test services.
                replacement = await asyncio.to_thread(self._create_service_or_management)
        except Exception as error:
            # Preserve the already-running service and revert the durable
            # selection if a replacement cannot initialize.
            if previous is not None and str(getattr(self.service, "character_id", previous.character_id)) == previous.character_id:
                registry.select(previous.character_id)
            self._character_switching = False
            await self._send_snapshot(websocket)
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

        self._character_switching = False
        self._voice_state = "ready"
        if not getattr(self.service, "storage_unavailable", False):
            self._status = {"state": "ready", "message": "Ready"}
        self._provider_ready_at = None
        self._frontend_snapshot_ready_at = None
        self._log("Character switched.")
        await self._send_snapshot(websocket)
        if proactive_was_running and self._running and not self._stopping:
            self._proactive_task = asyncio.create_task(self._proactive_loop())

    def _on_service_event(self, event: AssistantEvent) -> None:
        if self._character_switching:
            return
        if isinstance(event.data, dict) and event.data.get("character_session"):
            current = getattr(self.service, "character_binding", lambda: {})()
            if any(event.data.get(key) != current.get(key) for key in ("character_id", "character_session")):
                return
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
            **self._binding_envelope(event.data),
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
            "qwen_generating": provider_active,
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

    async def _send_snapshot(self, websocket, *, request_id: str = "") -> None:
        if self._character_switching:
            return
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
        active_character = self._registry().active_or_none()
        identity["character_id"] = str(getattr(self.service, "character_id", "") or character.get("_character_id") or (active_character.character_id if active_character else "no-character"))
        characters = [
            {
                "character_id": item.character_id,
                "display_name": item.display_name,
                "is_active": active_character is not None and item.character_id == active_character.character_id,
                "storage_layout": item.storage_layout, "storage_status": item.storage_status,
                "timeline_generation": item.timeline_generation, "has_retained_copy":bool(item.migration_source),
                "operation_kind":str((item.operation or {}).get("kind") or ""),
            }
            for item in self._registry().list_characters()
        ]

        volume = None
        get_volume = getattr(self.service.tts, "get_volume", None)
        if callable(get_volume):
            volume = get_volume()

        development_flight_recorder().mark_view("history_built", request_id, len(conversation))
        self._log("Snapshot prepared with active model, voice, and TTS status.")
        await self._send_json(
            websocket,
            {
                "type": "snapshot", "request_id": request_id,
                **self._binding_envelope(),
                "data": {
                    **self._binding_envelope(),
                    "transport_version": 9,
                    "conversation": conversation,
                    "character": identity,
                    "characters": characters,
                    "registry_revision": self._registry().revision,
                    "storage_unavailable":bool(getattr(self.service,"storage_unavailable",False)),
                    "status": dict(self._status),
                    "voice": self._voice_snapshot(),
                    "tts": {"volume": volume, **self._tts_snapshot()},
                    "companion": self._companion_snapshot(),
                    "models": self._model_snapshot(),
                    "truth_scope": self._truth_scope_snapshot(),
                    "continuity": self._continuity_snapshot(),
                    "memory_authority": getattr(
                        self.service, "memory_authority_status", lambda: {"mode": "v1"}
                    )(),
                },
            },
        )

    def _model_snapshot(self) -> dict[str, Any]:
        from aifren.runtime.model_settings import model_status
        current = model_status()
        runtime_status = getattr(self.service, "model_runtime_availability", None)
        if current.get("configured") and callable(runtime_status):
            availability = runtime_status()
            if isinstance(availability, str) and availability and availability != "unknown":
                current["availability"] = availability
        runtime = self._runtime().snapshot(selected_model=str(current.get("selected_model", "") or ""))
        if current.get("mode") == "local":
            current["selected_model"] = str(runtime.get("selected_model", ""))
            if self._local_model_error is not None:
                runtime.update(self._local_model_error)
            if runtime.get("state") in {"error", "mismatch"}:
                current["availability"] = "unavailable"
            elif self._local_model_health in {"unverified", "unavailable"}:
                current["availability"] = self._local_model_health
            elif runtime.get("state") == "ready" and current.get("availability") not in {"unavailable", "unconfigured"}:
                current["availability"] = "configured"
        return {"current": current, "local_runtime": runtime}

    def _companion_snapshot(self) -> dict[str, Any]:
        from aifren.runtime.model_settings import proactive_behavior_status
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
            "explicit_avatar_cues": bool(getattr(self.service, "explicit_avatar_cues", False)),
            **(self.service.companion_preferences_snapshot()
               if callable(getattr(self.service, "companion_preferences_snapshot", None)) else {}),
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
                return availability == "configured" and isinstance(runtime, dict) and runtime.get("state") == "ready"
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
                maintain = getattr(self.service, "maintain_canonical_observers", None)
                if callable(maintain) and not self._turn_tasks and not self._stopping:
                    # Same slow maintenance poll, independent of provider/client
                    # readiness. Shield and join this finite owned page before
                    # character rebinding or shutdown can close its SQLite owner.
                    worker = asyncio.create_task(asyncio.to_thread(maintain))
                    self._turn_tasks.add(worker)
                    try:
                        try:
                            await asyncio.shield(worker)
                        except asyncio.CancelledError:
                            await worker
                            raise
                    finally:
                        self._turn_tasks.discard(worker)
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
        from aifren.runtime.config import KOKORO_DEVICE, KOKORO_VOICE, TTS_PROVIDER
        from aifren.runtime.model_settings import kokoro_early_speech_status
        early_speech = kokoro_early_speech_status()
        return {
            "provider": (self.service.tts.effective_provider if hasattr(type(self.service.tts), "effective_provider")
                         else type(self.service.tts).__name__.replace("TextToSpeech", "").lower()),
            "configured_provider": str(TTS_PROVIDER),
            "voice": str(getattr(self.service.tts, "voice", KOKORO_VOICE)),
            "device": str(getattr(self.service.tts, "device", KOKORO_DEVICE)),
            "device_selection_reason": str(
                getattr(self.service.tts, "device_selection_reason", "")
            ),
            "fallback_reason": str(getattr(self.service.tts, "fallback_reason", "")),
            "early_speech": early_speech["effective"],
            "early_speech_configured": early_speech["configured"],
            "early_speech_overridden": early_speech["overridden"],
            "early_speech_supported": bool(getattr(self.service.tts, "supports_early_speech", False)),
            "character_voice": (self.service.tts.snapshot() if callable(getattr(type(self.service.tts), "bind_character", None)) else None),
        }

    def _voice_snapshot(self) -> dict[str, Any]:
        ptt = getattr(self.service, "_ptt", None)
        listener_active = getattr(ptt, "global_listener_active", None)
        return {
            "state": self._voice_state,
            "global_listener": bool(listener_active()) if callable(listener_active) else False,
        }

    def _binding_envelope(self, captured=None) -> dict[str, Any]:
        owner = getattr(self.service, "character_binding", lambda: {})()
        if isinstance(captured, dict):
            owner = {**owner, **{key: captured[key] for key in ("character_id", "character_session") if key in captured}}
        return {**owner, "character_generation": self._character_generation}

    async def _broadcast(self, message: dict[str, Any]) -> None:
        if self._client is not None:
            await self._send_json(self._client, message)

    async def _send_command_error(self, websocket, code: str, message: str) -> None:
        await self._send_json(
            websocket,
            {
                "type": "command_error",
                **(self._command_owner.get() or {}),
                "error": {"code": code, "message": message, "request_id":self._command_request_id.get()},
            },
        )

    async def _send_json(self, websocket, message: dict[str, Any]) -> None:
        try:
            await websocket.send(json.dumps(self._json_safe(message), ensure_ascii=False))
            self._record_view_send(message, "sent")
        except Exception:
            self._record_view_send(message, "send_failed")
            if websocket is self._client:
                self._client = None

    @staticmethod
    def _record_view_send(message, stage):
        if message.get("type") == "snapshot":
            development_flight_recorder().mark_view("history_" + stage, message.get("request_id"),
                len((message.get("data") or {}).get("conversation") or []))
        elif (message.get("event") or {}).get("type") == "memory_view_page":
            data = message["event"].get("data") or {}
            development_flight_recorder().mark_view("memory_" + stage, data.get("request_id"),
                len((data.get("memory_page") or {}).get("items") or []))

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(key): AIFrenWebSocketHost._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [AIFrenWebSocketHost._json_safe(item) for item in value]
        return str(value)


async def run_backend_host(
    port: int = DEFAULT_PORT,
    *,
    resource_root: Path | str | None = None,
    data_root: Path | str | None = None,
    seed_data_root: Path | str | None = None,
) -> None:
    """Run the default local backend until interrupted."""
    host = AIFrenWebSocketHost(
        port=port,
        resource_root=resource_root,
        data_root=data_root,
        seed_data_root=seed_data_root,
    )
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
    parser.add_argument("--resource-root", type=Path)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--seed-data-root", type=Path)
    args = parser.parse_args()

    try:
        asyncio.run(run_backend_host(
            args.port,
            resource_root=args.resource_root,
            data_root=args.data_root,
            seed_data_root=args.seed_data_root,
        ))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
