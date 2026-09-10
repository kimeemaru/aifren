import asyncio
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import websockets

import model_settings
from assistant_service import AssistantEvent, TurnResult
from backend_host import AIFrenWebSocketHost, LOOPBACK_HOST


class FakeConversation:
    def __init__(self):
        self.messages = [
            {
                "role": "assistant",
                "content": "Welcome back.",
                "timestamp": "2026-08-11T12:00:00",
            }
        ]


class FakeTTS:
    def __init__(self):
        self.volume = 0.4

    def get_volume(self):
        return self.volume


class FakeService:
    def __init__(self):
        self.conversation = FakeConversation()
        self.character = {
            "name": "Lyra",
            "description": "A companion",
            "avatar": "avatar.png",
        }
        self.tts = FakeTTS()
        self.listeners = []
        self.submitted = []
        self.stop_calls = 0
        self.volume_calls = []
        self.transcription_modes = []
        self.ptt_press_calls = 0
        self.ptt_release_calls = 0
        self.ptt_error = None
        self.closed = 0
        self.llm_replacements = []
        self.model_availability = "configured"
        self.model_reconfigure_busy = False
        self.turn_started = threading.Event()
        self.release_turn = threading.Event()
        self.block_turns = False
        self.switch_busy = False
        self.development_qa_calls = []
        self.truth_scope = {"kind": "real_world", "label": ""}
        self.continuity = {
            "scope": dict(self.truth_scope), "activity": None,
            "open_threads": [], "revision": "1",
        }
        self.continuity_controls = []
        self.block_continuity_controls = False
        self.continuity_control_started = threading.Event()
        self.release_continuity_control = threading.Event()
        self.provider_active = False
        self.memory_view_queries = []
        self.memory_view_details = []
        self.memory_view_mutations = []

    def provider_request_active(self):
        return self.provider_active

    def subscribe(self, listener):
        self.listeners.append(listener)

        def unsubscribe():
            if listener in self.listeners:
                self.listeners.remove(listener)

        return unsubscribe

    def emit(self, event_type, **data):
        event = AssistantEvent(event_type, data)
        for listener in list(self.listeners):
            listener(event)

    def process_text_turn(self, text):
        self.submitted.append(text)
        self.turn_started.set()
        self.emit("turn_started", user_message=text)
        self.emit("status", state="thinking", message="Thinking...")
        if self.block_turns:
            self.release_turn.wait(timeout=2)
        self.emit("assistant_response", content="Reply")
        self.emit("status", state="ready", message="Ready")
        return TurnResult(user_message=text, reply="Reply")

    def run_development_presentation_qa(self, response):
        self.development_qa_calls.append(response)
        return True

    def stop_speaking(self):
        self.stop_calls += 1
        self.emit("tts_state", state="stopped")

    def set_tts_volume(self, volume):
        self.volume_calls.append(volume)
        self.tts.volume = volume
        self.emit("tts_state", state="volume_changed", volume=volume)

    def replace_llm(self, llm):
        self.llm_replacements.append(llm)

    def model_runtime_availability(self):
        return self.model_availability

    def report_model_runtime_available(self):
        self.model_availability = "configured"

    def report_model_runtime_unavailable(self):
        self.model_availability = "unavailable"

    def can_reconfigure_model(self):
        return not self.model_reconfigure_busy

    def set_ptt_auto_submit_transcriptions(self, enabled):
        self.transcription_modes.append(bool(enabled))

    def set_push_to_talk_binding(self, binding):
        self.ptt_binding = binding

    def push_to_talk_press(self):
        self.ptt_press_calls += 1
        if self.ptt_error is not None:
            raise self.ptt_error

    def push_to_talk_release(self):
        self.ptt_release_calls += 1
        if self.ptt_error is not None:
            raise self.ptt_error

    def close(self):
        self.closed += 1

    def character_switch_busy(self):
        return self.switch_busy

    def truth_scope_status(self):
        return dict(self.truth_scope)

    def continuity_snapshot(self):
        return dict(self.continuity)

    def apply_continuity_control(self, **command):
        self.continuity_controls.append(dict(command))
        self.continuity_control_started.set()
        if self.block_continuity_controls:
            self.release_continuity_control.wait(timeout=2)
        if command["expected_revision"] != self.continuity["revision"]:
            raise RuntimeError("Current continuity changed")
        return {
            "accepted": True, "duplicate": False, "outcome": "applied",
            "command_id": command["command_id"], "continuity": dict(self.continuity),
        }

    def memory_view_page(self, **query):
        self.memory_view_queries.append(dict(query))
        return {
            "character_id": query["character_id"], "lane": query["lane"],
            "query": query["query"], "status_filter": query["status_filter"],
            "scope_filter": query["scope_filter"], "offset": query["offset"],
            "limit": query["limit"], "has_more": False, "availability": "ready",
            "authority_label": "Memory V1 · canonical prompt-facing memory authority",
            "warning": "", "items": [{
                "record_id": "v1:1", "lane": "v1", "authority": "Memory V1",
                "content": "Synthetic memory", "category": "test", "importance": 5,
                "status": "current", "scope": "Character-owned / general",
                "provenance": "synthetic", "editable": True, "retirable": True,
                "derived": False,
            }],
        }

    def apply_memory_view_mutation(self, **mutation):
        self.memory_view_mutations.append(dict(mutation))
        return {"accepted": True, "action": mutation["action"], "record_id": mutation["record_id"]}

    def memory_view_detail(self, **query):
        self.memory_view_details.append(dict(query))
        return {
            "character_id": query["character_id"], "lane": query["lane"],
            "record_id": query["record_id"], "limit": query["limit"],
            "offset": query["offset"], "has_more": False, "availability": "ready",
            "warning": "", "detail": {
                "kind": "claim_provenance", "claim_id": query["record_id"],
                "evidence": [{"event_id": "synthetic-event", "source_class": "canonical_conversation"}],
            },
        }


class FakeOwnedLocalRuntime:
    def __init__(self): self.stop_calls = 0
    def stop(self): self.stop_calls += 1; return {"state": "off", "ownership": "none"}


class FakeAutoStartRuntime:
    def __init__(self):
        self.start_calls = []

    def snapshot(self, selected_model=""):
        return {
            "state": "off", "ownership": "none", "active_model": "",
            "selected_model": selected_model, "installed_models": [],
        }

    def start(self, *, endpoint, selected_model, api_key):
        self.start_calls.append((endpoint, selected_model, api_key))
        return {
            "state": "ready", "ownership": "managed", "active_model": selected_model,
            "selected_model": selected_model, "installed_models": [],
        }

    def stop(self):
        return {"state": "off", "ownership": "none"}


class FakeProviderSwitchRuntime:
    def __init__(self, *, ownership="managed"):
        self.state = "ready"
        self.ownership = ownership
        self.active_model = "local-test"
        self.stop_calls = 0
        self.owned_terminations = 0
        self.start_calls = []

    def snapshot(self, selected_model=""):
        return {
            "state": self.state, "ownership": self.ownership,
            "active_model": self.active_model, "selected_model": selected_model,
            "installed_models": [],
        }

    def stop(self):
        self.stop_calls += 1
        if self.ownership == "managed":
            self.owned_terminations += 1
            self.state, self.ownership, self.active_model = "off", "none", ""
        return self.snapshot()

    def start(self, *, endpoint, selected_model, api_key):
        self.start_calls.append((endpoint, selected_model, api_key))
        self.state, self.ownership, self.active_model = "ready", "managed", selected_model
        return self.snapshot(selected_model)


class WebSocketTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_cwd = Path.cwd()
        self.temp = tempfile.TemporaryDirectory()
        self.service = FakeService()
        self.host = AIFrenWebSocketHost(
            service=self.service,
            port=0,
            application_dir=self.temp.name,
        )
        await self.host.start()
        self.client = await websockets.connect(
            f"ws://{LOOPBACK_HOST}:{self.host.port}"
        )

    async def asyncTearDown(self):
        await self.client.close()
        await self.host.stop()
        os.chdir(self.original_cwd)
        self.temp.cleanup()

    async def receive_json(self):
        return json.loads(await asyncio.wait_for(self.client.recv(), timeout=1))

    async def receive_until(self, predicate):
        for _ in range(10):
            message = await self.receive_json()
            if predicate(message):
                return message
        self.fail("Expected WebSocket message was not received.")

    def test_rejects_non_loopback_configuration(self):
        with self.assertRaises(ValueError):
            AIFrenWebSocketHost(service=FakeService(), host="0.0.0.0")

    def test_proactive_startup_grace_begins_at_latest_ready_boundary(self):
        host = AIFrenWebSocketHost(
            service=self.service, port=0, application_dir=self.temp.name,
            proactive_startup_grace_seconds=60,
        )
        host._backend_ready_at = 100.0
        host._client = object()
        host._frontend_snapshot_ready_at = 110.0
        host._provider_ready_at = 120.0
        self.assertEqual(("startup_grace", 1), host._proactive_startup_gate(now=179.2))
        self.assertEqual(("ready", -1), host._proactive_startup_gate(now=180.0))

    def test_local_proactive_readiness_requires_live_runtime(self):
        host = AIFrenWebSocketHost(
            service=self.service, port=0, application_dir=self.temp.name,
        )
        snapshot = {
            "current": {"configured": True, "availability": "configured", "mode": "local"},
            "local_runtime": {"state": "starting"},
        }
        with patch.object(host, "_model_snapshot", return_value=snapshot):
            self.assertFalse(host._proactive_provider_ready())
        snapshot["local_runtime"]["state"] = "ready"
        with patch.object(host, "_model_snapshot", return_value=snapshot):
            self.assertTrue(host._proactive_provider_ready())

    def test_flight_recorder_provider_state_uses_live_provider_request(self):
        self.service.provider_active = False
        self.service._active_turn_cancel = threading.Event()
        idle = self.host._flight_recorder_state()
        self.assertFalse(idle["qwen_generating"])
        self.assertEqual(0, idle["provider_streams"])

        self.service.provider_active = True
        generating = self.host._flight_recorder_state()
        self.assertTrue(generating["qwen_generating"])
        self.assertEqual(1, generating["provider_streams"])

    async def test_snapshot_contains_frontend_state(self):
        await self.client.send(json.dumps({"command": "get_snapshot"}))
        message = await self.receive_json()

        self.assertEqual(message["type"], "snapshot")
        self.assertEqual(message["data"]["character"]["name"], "Lyra")
        self.assertTrue(message["data"]["character"]["character_id"])
        self.assertEqual(1, len(message["data"]["characters"]))
        self.assertTrue(message["data"]["characters"][0]["is_active"])
        self.assertEqual(message["data"]["conversation"][0]["content"], "Welcome back.")
        self.assertEqual(message["data"]["status"]["state"], "ready")
        self.assertEqual(message["data"]["tts"]["volume"], 0.4)
        self.assertEqual(message["data"]["voice"]["state"], "ready")
        self.assertIn("models", message["data"])
        self.assertEqual(message["data"]["models"]["current"]["mode"], "online")
        self.assertFalse(message["data"]["models"]["current"]["configured"])
        self.assertEqual(message["data"]["models"]["current"]["model"], "")
        self.assertEqual(message["data"]["models"]["current"]["availability"], "unconfigured")
        self.assertEqual(8, message["data"]["transport_version"])
        self.assertEqual({"kind": "real_world", "label": ""}, message["data"]["truth_scope"])
        self.assertEqual("1", message["data"]["continuity"]["revision"])
        self.assertTrue(message["data"]["companion"]["proactive_behavior"])
        self.assertEqual("provider_not_ready", message["data"]["companion"]["proactive_eligibility"])

    async def test_companion_snapshot_preserves_obstructed_mode_and_explicit_restoration(self):
        self.service.continuity.update({
            "companion_activity": None,
            "capability_effects": [{
                "target": "companion", "domain": "perception",
                "capability": "vision", "state": "obstructed",
            }, {
                "target": "companion", "domain": "posture",
                "capability": "posture", "state": "lying",
            }],
        })
        await self.client.send(json.dumps({"command": "get_snapshot"}))
        obstructed = await self.receive_json()
        presentation = obstructed["data"]["companion"]["state_presentation"]
        self.assertEqual(presentation["vision_mode"], "obstructed")
        self.assertEqual(presentation["gaze_mode"], "suppressed")
        self.assertEqual(presentation["posture_mode"], "lying")

        self.service.continuity["capability_effects"] = []
        await self.client.send(json.dumps({"command": "get_snapshot"}))
        restored = await self.receive_json()
        presentation = restored["data"]["companion"]["state_presentation"]
        self.assertEqual(presentation["vision_mode"], "available")
        self.assertEqual(presentation["gaze_mode"], "normal")
        self.assertEqual(presentation["posture_mode"], "")

    async def test_continuity_control_requires_exact_ack_and_rejects_stale_revision(self):
        command_id = "4bb3cd23-a264-4658-a27e-cc4268cfeb32"
        await self.client.send(json.dumps({
            "command": "continuity_control", "command_id": command_id,
            "action": "clear_activity", "expected_revision": "1", "action_token": "",
        }))
        accepted = await self.receive_until(
            lambda item: item.get("type") == "event"
            and item["event"]["type"] == "continuity_control_result"
        )
        self.assertEqual(command_id, accepted["event"]["data"]["command_id"])
        self.assertEqual(1, len(self.service.continuity_controls))

        await self.client.send(json.dumps({
            "command": "continuity_control", "command_id": "ce61ccf0-ce01-4725-b58f-a37aab1c721b",
            "action": "clear_activity", "expected_revision": "stale", "action_token": "",
        }))
        rejected = await self.receive_until(lambda item: item.get("type") == "command_error")
        self.assertEqual("stale_continuity_control", rejected["error"]["code"])

    async def test_immersive_scene_interaction_does_not_block_receive_loop(self):
        self.service.block_continuity_controls = True
        await self.client.send(json.dumps({
            "command": "continuity_control",
            "command_id": "fdaf8a65-8bf8-42af-a164-bc2c845fc25f",
            "action": "interact_scene_relation",
            "expected_revision": "1",
            "action_token": "relation:0",
        }))
        started = await asyncio.to_thread(
            self.service.continuity_control_started.wait, 1,
        )
        self.assertTrue(started)

        await self.client.send(json.dumps({"command": "get_snapshot"}))
        snapshot = await self.receive_until(lambda item: item.get("type") == "snapshot")
        self.assertEqual("1", snapshot["data"]["continuity"]["revision"])

        self.service.release_continuity_control.set()
        accepted = await self.receive_until(
            lambda item: item.get("type") == "event"
            and item["event"]["type"] == "continuity_control_result"
        )
        self.assertEqual("fdaf8a65-8bf8-42af-a164-bc2c845fc25f", accepted["event"]["data"]["command_id"])

    async def test_truth_scope_snapshot_and_event_are_frontend_neutral(self):
        self.service.truth_scope = {"kind": "scenario", "label": "Silvervale"}
        await self.client.send(json.dumps({"command": "get_snapshot"}))
        snapshot = await self.receive_until(lambda item: item.get("type") == "snapshot")
        self.assertEqual({"kind": "scenario", "label": "Silvervale"}, snapshot["data"]["truth_scope"])
        self.service.emit("truth_scope_changed", scope_kind="real_world", scope_label="")
        event = await self.receive_until(
            lambda item: item.get("type") == "event" and item["event"]["type"] == "truth_scope_changed"
        )
        self.assertEqual("real_world", event["event"]["data"]["scope_kind"])

    async def test_development_flight_recorder_commands_are_structural_and_bounded(self):
        capture_id = "20260825T123456-fedcba"
        bundle = Path("/tmp") / ("aifren-flight-recorder-" + capture_id)
        import shutil
        shutil.rmtree(bundle, ignore_errors=True)
        try:
            await self.client.send(json.dumps({
                "command": "development_flight_recorder_start", "unity_pid": os.getpid(),
            }))
            ready = await self.receive_until(
                lambda item: item.get("type") == "event" and item["event"]["type"] == "flight_recorder_ready"
            )
            self.assertEqual(ready["event"]["data"]["state"], "ready")
            await self.client.send(json.dumps({
                "command": "development_flight_recorder_trigger", "capture_id": capture_id,
                "reason": "manual_hotkey",
            }))
            self.service.emit("assistant_response", content="synthetic words", turn_id=2)
            await self.client.send(json.dumps({
                "command": "development_flight_recorder_dump", "capture_id": capture_id,
            }))
            dumped = await self.receive_until(
                lambda item: item.get("type") == "event" and item["event"]["type"] == "flight_recorder_backend_dumped"
            )
            self.assertEqual(dumped["event"]["data"]["capture_id"], capture_id)
            timeline = (bundle / "backend_timeline.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("synthetic words", timeline)
            self.assertIn('"characters":15', timeline)
        finally:
            shutil.rmtree(bundle, ignore_errors=True)

    async def test_console_log_is_bounded_and_has_no_turn_content(self):
        self.service.emit("status", state="ready", message="A secret-like user message must not enter console output")
        await self.client.send(json.dumps({"command": "get_console_log"}))
        message = await self.receive_until(
            lambda item: item.get("type") == "event" and item["event"]["type"] == "console_log"
            and any("Console diagnostics requested" in line for line in item["event"]["data"].get("lines", ()))
        )
        self.assertEqual(message["type"], "event")
        self.assertEqual(message["event"]["type"], "console_log")
        lines = message["event"]["data"]["lines"]
        self.assertTrue(any("Local frontend connected" in line for line in lines))
        self.assertTrue(any("Console diagnostics requested" in line for line in lines))
        self.assertTrue(any("service event: status" in line for line in lines))
        self.assertFalse(any("secret-like" in line for line in lines))

    async def test_service_events_do_not_push_hidden_console_history(self):
        self.service.emit("tts_state", state="playback_started", duration_seconds=1.0)
        message = await self.receive_until(
            lambda item: item.get("type") == "event" and item["event"]["type"] == "tts_state"
        )
        self.assertEqual("playback_started", message["event"]["data"]["state"])
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(self.client.recv(), timeout=.05)

    async def test_submit_text_routes_to_one_service_turn_and_forwards_events(self):
        await self.client.send(json.dumps({"command": "submit_text", "text": "Hello"}))
        message = await self.receive_until(
            lambda item: item.get("type") == "event"
            and item["event"]["type"] == "assistant_response"
        )

        self.assertEqual(self.service.submitted, ["Hello"])
        self.assertEqual(message["event"]["data"]["content"], "Reply")

    async def test_development_presentation_qa_is_gated_and_uses_fixed_synthetic_text(self):
        await self.client.send(json.dumps({"command": "development_presentation_qa", "scenario": "cold"}))
        disabled = await self.receive_until(lambda item: item.get("type") == "command_error")
        self.assertEqual("development_qa_disabled", disabled["error"]["code"])
        self.assertEqual([], self.service.development_qa_calls)

        with patch("backend_host._DEVELOPMENT_QA_ENABLED", True):
            await self.client.send(json.dumps({"command": "development_presentation_qa", "scenario": "cold"}))
            for _ in range(20):
                if self.service.development_qa_calls:
                    break
                await asyncio.sleep(.01)
        self.assertEqual(1, len(self.service.development_qa_calls))
        self.assertIn("first portrait test", self.service.development_qa_calls[0])

    async def test_memory_view_query_is_bounded_character_scoped_and_frontend_neutral(self):
        request_id = str(__import__("uuid").uuid4())
        await self.client.send(json.dumps({
            "command": "memory_view_query", "request_id": request_id,
            "character_id": "character-a", "memory_lane": "v1",
            "query": "synthetic", "status_filter": "current",
            "scope_filter": "applicable", "limit": 20, "offset": 0,
        }))
        message = await self.receive_until(
            lambda item: item.get("type") == "event"
            and item["event"]["type"] == "memory_view_page"
        )

        self.assertEqual(request_id, message["event"]["data"]["request_id"])
        page = message["event"]["data"]["memory_page"]
        self.assertEqual("character-a", page["character_id"])
        self.assertEqual(20, page["limit"])
        self.assertEqual(1, len(page["items"]))
        self.assertNotIn("embedding", page["items"][0])
        self.assertEqual("synthetic", self.service.memory_view_queries[0]["query"])

    async def test_memory_view_mutation_preserves_request_identity_and_transport(self):
        import uuid
        request_id, command_id = str(uuid.uuid4()), str(uuid.uuid4())
        await self.client.send(json.dumps({
            "command": "memory_view_mutate", "request_id": request_id,
            "command_id": command_id, "character_id": "character-a",
            "action": "edit_v1", "record_id": "v1:1",
            "content": "Updated synthetic memory", "category": "test", "importance": 5,
        }))
        message = await self.receive_until(
            lambda item: item.get("type") == "event"
            and item["event"]["type"] == "memory_view_mutation_result"
        )

        data = message["event"]["data"]
        self.assertTrue(data["accepted"])
        self.assertEqual(request_id, data["request_id"])
        self.assertEqual(command_id, data["command_id"])
        self.assertEqual("character-a", data["character_id"])
        self.assertEqual(1, len(self.service.memory_view_mutations))

        await self.client.send(json.dumps({"command": "get_snapshot"}))
        snapshot = await self.receive_until(lambda item: item.get("type") == "snapshot")
        self.assertEqual(8, snapshot["data"]["transport_version"])

    async def test_memory_view_detail_is_bounded_and_preserves_request_identity(self):
        import uuid
        request_id = str(uuid.uuid4())
        await self.client.send(json.dumps({
            "command": "memory_view_detail", "request_id": request_id,
            "character_id": "character-a", "memory_lane": "v2_claims",
            "record_id": "claim-a", "limit": 8, "offset": 0,
        }))
        message = await self.receive_until(
            lambda item: item.get("type") == "event"
            and item["event"]["type"] == "memory_view_detail"
        )

        data = message["event"]["data"]
        self.assertEqual(request_id, data["request_id"])
        self.assertEqual("claim-a", data["memory_detail"]["record_id"])
        self.assertEqual(8, self.service.memory_view_details[0]["limit"])
        self.assertEqual(
            "canonical_conversation",
            data["memory_detail"]["detail"]["evidence"][0]["source_class"],
        )

    async def test_optional_presentation_metadata_is_forwarded_without_breaking_old_events(self):
        self.service.emit("assistant_response", content="New reply")
        legacy = await self.receive_until(
            lambda item: item.get("type") == "event" and item["event"]["type"] == "assistant_response"
            and item["event"]["data"].get("content") == "New reply"
        )
        self.assertNotIn("presentation", legacy["event"]["data"])

        self.service.emit(
            "assistant_response",
            content="Happy reply",
            has_presentation=True,
            presentation={"emotion": "happy", "intensity": .75, "has_intensity": True, "gesture": "greeting"},
        )
        enriched = await self.receive_until(
            lambda item: item.get("type") == "event" and item["event"]["type"] == "assistant_response"
            and item["event"]["data"].get("content") == "Happy reply"
        )
        self.assertTrue(enriched["event"]["data"]["has_presentation"])
        self.assertEqual("happy", enriched["event"]["data"]["presentation"]["emotion"])

    async def test_receive_loop_remains_responsive_during_a_turn(self):
        self.service.block_turns = True
        await self.client.send(json.dumps({"command": "submit_text", "text": "Slow"}))
        await asyncio.to_thread(self.service.turn_started.wait, 1)

        await self.client.send(json.dumps({"command": "get_snapshot"}))
        message = await self.receive_until(lambda item: item.get("type") == "snapshot")
        self.assertEqual(message["data"]["status"]["state"], "thinking")

        self.service.release_turn.set()

    async def test_tts_commands_and_unknown_command(self):
        await self.client.send(json.dumps({"command": "stop_tts"}))
        stopped = await self.receive_until(
            lambda item: item.get("type") == "event"
            and item["event"]["type"] == "tts_state"
            and item["event"]["data"]["state"] == "stopped"
        )
        self.assertEqual(self.service.stop_calls, 1)
        self.assertEqual(stopped["event"]["data"]["state"], "stopped")

        await self.client.send(json.dumps({"command": "set_tts_volume", "volume": 0.7}))
        volume = await self.receive_until(
            lambda item: item.get("type") == "event"
            and item["event"]["data"].get("state") == "volume_changed"
        )
        self.assertEqual(self.service.volume_calls, [0.7])
        self.assertEqual(volume["event"]["data"]["volume"], 0.7)

        await self.client.send(json.dumps({"command": "not_a_command"}))
        error = await self.receive_until(lambda item: item.get("type") == "command_error")
        self.assertEqual(error["type"], "command_error")
        self.assertEqual(error["error"]["code"], "unknown_command")

    async def test_kokoro_early_speech_setting_defaults_on_persists_and_snapshots(self):
        with patch.object(model_settings, "KOKORO_EARLY_SPEECH_OVERRIDE", None):
            await self.client.send(json.dumps({"command": "get_snapshot"}))
            initial = await self.receive_until(lambda item: item.get("type") == "snapshot")
            self.assertTrue(initial["data"]["tts"]["early_speech"])
            self.assertTrue(initial["data"]["tts"]["early_speech_configured"])
            self.assertFalse(initial["data"]["tts"]["early_speech_overridden"])
            self.assertFalse(initial["data"]["tts"]["early_speech_supported"])

            await self.client.send(json.dumps({
                "command": "set_kokoro_early_speech", "early_speech": False,
            }))
            saved = await self.receive_until(lambda item: item.get("type") == "snapshot")
            self.assertFalse(saved["data"]["tts"]["early_speech"])
            self.assertFalse(saved["data"]["tts"]["early_speech_configured"])
            self.assertFalse(model_settings.kokoro_early_speech_status()["configured"])

            await self.client.send(json.dumps({"command": "get_snapshot"}))
            restarted = await self.receive_until(lambda item: item.get("type") == "snapshot")
            self.assertFalse(restarted["data"]["tts"]["early_speech"])

    async def test_kokoro_early_speech_rejects_non_boolean_values(self):
        await self.client.send(json.dumps({
            "command": "set_kokoro_early_speech", "early_speech": "yes",
        }))
        error = await self.receive_until(lambda item: item.get("type") == "command_error")
        self.assertEqual("invalid_kokoro_early_speech", error["error"]["code"])

    async def test_proactive_behavior_setting_persists_snapshots_and_rejects_non_boolean(self):
        await self.client.send(json.dumps({
            "command": "set_proactive_behavior", "proactive_behavior": False,
        }))
        saved = await self.receive_until(lambda item: item.get("type") == "snapshot")
        self.assertFalse(saved["data"]["companion"]["proactive_behavior"])
        self.assertFalse(model_settings.proactive_behavior_status()["enabled"])

        await self.client.send(json.dumps({
            "command": "set_proactive_behavior", "proactive_behavior": "false",
        }))
        error = await self.receive_until(lambda item: item.get("type") == "command_error")
        self.assertEqual("invalid_proactive_behavior", error["error"]["code"])

    async def test_proactive_interval_setting_persists_and_rejects_unknown_values(self):
        await self.client.send(json.dumps({
            "command": "set_proactive_interval", "proactive_interval_seconds": 30,
        }))
        saved = await self.receive_until(lambda item: item.get("type") == "snapshot")
        self.assertTrue(saved["data"]["companion"]["proactive_behavior"])
        self.assertEqual(30, saved["data"]["companion"]["proactive_interval_seconds"])
        self.assertEqual(30, model_settings.proactive_behavior_status()["interval_seconds"])

        await self.client.send(json.dumps({
            "command": "set_proactive_interval", "proactive_interval_seconds": 31,
        }))
        error = await self.receive_until(lambda item: item.get("type") == "command_error")
        self.assertEqual("invalid_proactive_interval", error["error"]["code"])

    async def test_local_settings_wait_for_runtime_model_verification_without_restarting_transport(self):
        with patch("llm.llm.create_llm", return_value=object()):
            await self.client.send(json.dumps({
                "command": "set_model_settings", "mode": "local", "provider": "auto",
                "local_endpoint": "http://127.0.0.1:8000/v1", "local_model": "local-test",
            }))
            snapshot = await self.receive_until(lambda item: item.get("type") == "snapshot")

        self.assertEqual(1, len(self.service.llm_replacements))
        self.assertFalse(getattr(self.service.llm_replacements[0], "is_available", True))
        self.assertEqual("local", snapshot["data"]["models"]["current"]["mode"])
        self.assertEqual("local-test", snapshot["data"]["models"]["current"]["model"])

    async def test_mode_only_updates_persist_and_preserve_local_configuration(self):
        with patch("llm.llm.create_llm", return_value=object()):
            await self.client.send(json.dumps({
                "command": "set_model_settings", "mode": "local",
                "local_endpoint": "http://127.0.0.1:8000/v1", "local_model": "local-test",
            }))
            await self.receive_until(lambda item: item.get("type") == "snapshot")

            await self.client.send(json.dumps({"command": "set_model_settings", "mode": "online"}))
            online = await self.receive_until(lambda item: item.get("type") == "snapshot")
            self.assertEqual("online", online["data"]["models"]["current"]["mode"])

            await self.client.send(json.dumps({"command": "set_model_settings", "mode": "local"}))
            local = await self.receive_until(lambda item: item.get("type") == "snapshot")

        current = local["data"]["models"]["current"]
        self.assertEqual("local", current["mode"])
        self.assertEqual("local-test", current["model"])

    async def test_local_online_switch_stops_owned_runtime_and_respects_auto_start(self):
        runtime = FakeProviderSwitchRuntime()
        self.host._local_model_runtime = runtime
        conversation = self.service.conversation

        await self.client.send(json.dumps({
            "command": "set_model_settings", "mode": "local",
            "local_endpoint": "http://127.0.0.1:8000/v1", "local_model": "local-test",
            "has_local_auto_start": True, "local_auto_start": True,
        }))
        await self.receive_until(lambda item: item.get("type") == "snapshot")

        await self.client.send(json.dumps({"command": "set_model_settings", "mode": "online"}))
        online = await self.receive_until(lambda item: item.get("type") == "snapshot")
        self.assertEqual("online", online["data"]["models"]["current"]["mode"])
        self.assertEqual("unconfigured", online["data"]["models"]["current"]["availability"])
        self.assertFalse(getattr(self.service.llm_replacements[-1], "is_available", True))
        self.assertEqual(1, runtime.stop_calls)
        self.assertEqual(1, runtime.owned_terminations)
        self.assertEqual("off", runtime.state)
        self.assertIs(conversation, self.service.conversation)

        await self.client.send(json.dumps({"command": "set_model_settings", "mode": "local"}))
        local = await self.receive_until(lambda item: item.get("type") == "snapshot")
        self.assertEqual("local", local["data"]["models"]["current"]["mode"])
        for _ in range(20):
            if runtime.start_calls:
                break
            await asyncio.sleep(.01)
        self.assertEqual(
            [("http://127.0.0.1:8000/v1", "local-test", "")],
            runtime.start_calls,
        )
        self.assertIs(conversation, self.service.conversation)

    async def test_online_switch_never_terminates_an_external_local_server(self):
        runtime = FakeProviderSwitchRuntime(ownership="external")
        self.host._local_model_runtime = runtime
        await self.client.send(json.dumps({
            "command": "set_model_settings", "mode": "local",
            "local_endpoint": "http://127.0.0.1:8000/v1", "local_model": "local-test",
        }))
        await self.receive_until(lambda item: item.get("type") == "snapshot")

        await self.client.send(json.dumps({"command": "set_model_settings", "mode": "online"}))
        await self.receive_until(lambda item: item.get("type") == "snapshot")

        self.assertEqual(1, runtime.stop_calls)
        self.assertEqual(0, runtime.owned_terminations)
        self.assertEqual("external", runtime.ownership)

    async def test_busy_model_reconfiguration_is_rejected_before_persistence(self):
        self.service.model_reconfigure_busy = True
        await self.client.send(json.dumps({"command": "set_model_settings", "mode": "local"}))
        error = await self.receive_until(lambda item: item.get("type") == "command_error")

        self.assertEqual("model_settings_busy", error["error"]["code"])
        await self.client.send(json.dumps({"command": "get_snapshot"}))
        snapshot = await self.receive_until(lambda item: item.get("type") == "snapshot")
        self.assertEqual("online", snapshot["data"]["models"]["current"]["mode"])

    async def test_unavailable_local_discovery_is_a_command_error_not_a_transport_exit(self):
        with patch("llm.llm.create_llm", return_value=object()):
            await self.client.send(json.dumps({
                "command": "set_model_settings", "mode": "local", "provider": "auto",
                "local_endpoint": "http://127.0.0.1:1/v1", "local_model": "local-test",
            }))
            await self.receive_until(lambda item: item.get("type") == "snapshot")
        with patch("llm.llm.discover_local_models", side_effect=RuntimeError("connection refused")):
            await self.client.send(json.dumps({"command": "discover_local_models", "local_endpoint": "http://127.0.0.1:1/v1"}))
            error = await self.receive_until(lambda item: item.get("type") == "command_error")

        self.assertEqual("local_model_discovery_failed", error["error"]["code"])
        self.assertEqual("Could not reach the local model endpoint. Check Settings > Model.", error["error"]["message"])
        snapshot = await self.receive_until(lambda item: item.get("type") == "snapshot")
        current = snapshot["data"]["models"]["current"]
        self.assertEqual("local", current["mode"])
        self.assertEqual("local-test", current["model"])
        self.assertEqual("unavailable", current["availability"])

    async def test_unity_auto_start_command_persists_while_provider_reconfiguration_is_busy(self):
        with patch("llm.llm.create_llm", return_value=object()):
            await self.client.send(json.dumps({
                "command": "set_model_settings", "mode": "local",
                "local_endpoint": "http://127.0.0.1:8000/v1", "local_model": "four-b.gguf",
            }))
            await self.receive_until(lambda item: item.get("type") == "snapshot")

        self.service.model_reconfigure_busy = True
        await self.client.send(json.dumps({
            "command": "set_local_auto_start", "local_auto_start": True,
        }))
        snapshot = await self.receive_until(lambda item: item.get("type") == "snapshot")
        self.assertTrue(snapshot["data"]["models"]["current"]["local_auto_start"])
        from model_settings import get_model_settings
        self.assertTrue(get_model_settings()["local_auto_start"])

        await self.client.close()
        await self.host.stop()
        runtime = FakeAutoStartRuntime()
        self.service = FakeService()
        self.host = AIFrenWebSocketHost(
            service=self.service, port=0, application_dir=self.temp.name,
            local_model_runtime=runtime,
        )
        await self.host.start()
        for _ in range(20):
            if runtime.start_calls:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(
            [("http://127.0.0.1:8000/v1", "four-b.gguf", "")],
            runtime.start_calls,
        )
        self.client = await websockets.connect(f"ws://{LOOPBACK_HOST}:{self.host.port}")
        await self.client.send(json.dumps({"command": "get_snapshot"}))
        restarted = await self.receive_until(lambda item: item.get("type") == "snapshot")
        self.assertTrue(restarted["data"]["models"]["current"]["local_auto_start"])

    async def test_host_shutdown_stops_only_its_injected_owned_runtime(self):
        await self.client.close()
        await self.host.stop()
        runtime = FakeOwnedLocalRuntime()
        self.host = AIFrenWebSocketHost(service=self.service, port=0, application_dir=self.temp.name,
            local_model_runtime=runtime)
        await self.host.start()
        self.client = await websockets.connect(f"ws://{LOOPBACK_HOST}:{self.host.port}")
        await self.host.stop()
        self.assertEqual(1, runtime.stop_calls)

    async def test_ptt_transcription_mode_command_preserves_review_before_send(self):
        await self.client.send(json.dumps({"command": "set_ptt_transcription_mode", "mode": "review"}))
        await asyncio.sleep(0.05)
        self.assertEqual(self.service.transcription_modes, [False])
        await self.client.send(json.dumps({"command": "set_ptt_transcription_mode", "mode": "invalid"}))
        error = await self.receive_until(lambda item: item.get("type") == "command_error")
        self.assertEqual(error["error"]["code"], "invalid_transcription_mode")

    async def test_ptt_binding_command_routes_to_the_service(self):
        await self.client.send(json.dumps({"command": "set_ptt_binding", "binding": "F8"}))
        await asyncio.sleep(0.05)
        self.assertEqual(self.service.ptt_binding, "F8")

    async def test_ptt_provider_errors_are_returned_without_closing_transport(self):
        self.service.ptt_error = RuntimeError("PTT unavailable")
        await self.client.send(json.dumps({"command": "ptt_press"}))
        press_error = await self.receive_until(lambda item: item.get("type") == "command_error")
        self.assertEqual(press_error["error"]["code"], "ptt_press_failed")

        await self.client.send(json.dumps({"command": "ptt_release"}))
        release_error = await self.receive_until(lambda item: item.get("type") == "command_error")
        self.assertEqual(release_error["error"]["code"], "ptt_release_failed")

        await self.client.send(json.dumps({"command": "get_snapshot"}))
        snapshot = await self.receive_until(lambda item: item.get("type") == "snapshot")
        self.assertEqual(snapshot["type"], "snapshot")

    async def test_invalid_command_and_clean_shutdown(self):
        await self.client.send("not json")
        error = await self.receive_json()
        self.assertEqual(error["error"]["code"], "invalid_json")

        await self.host.stop()
        self.assertEqual(self.service.closed, 1)
        self.assertIsNotNone(self.client.close_code)

    async def test_packaged_backend_shutdown_requires_matching_launcher_nonce(self):
        await self.client.close()
        await self.host.stop()
        self.service = FakeService()
        self.host = AIFrenWebSocketHost(
            service=self.service,
            port=0,
            application_dir=self.temp.name,
            backend_owner_token="package-owner-token",
        )
        await self.host.start()
        self.client = await websockets.connect(f"ws://{LOOPBACK_HOST}:{self.host.port}")

        await self.client.send(json.dumps({
            "command": "probe_runtime_owner", "owner_token": "wrong-token",
        }))
        wrong = await self.receive_json()
        self.assertEqual({"matched": False}, wrong["data"])
        await self.client.send(json.dumps({
            "command": "shutdown", "owner_token": "wrong-token",
        }))
        rejected = await self.receive_json()
        self.assertEqual("backend_owner_mismatch", rejected["error"]["code"])
        self.assertTrue(self.host._running)

        await self.client.send(json.dumps({
            "command": "probe_runtime_owner", "owner_token": "package-owner-token",
        }))
        matched = await self.receive_json()
        self.assertEqual({"matched": True}, matched["data"])

    async def test_character_create_select_and_snapshot_refresh_are_frontend_neutral(self):
        replacement_b = FakeService()
        replacement_b.character["name"] = "Second"
        replacement_b.conversation.messages[0]["content"] = "Second history."
        replacement_legacy = FakeService()
        replacement_legacy.character["name"] = "Lyra"
        replacement_legacy.conversation.messages[0]["content"] = "Welcome back."
        replacements = [replacement_b, replacement_legacy]
        self.host._service_factory = lambda: replacements.pop(0)

        await self.client.send(json.dumps({
            "command": "create_character",
            "display_name": "Second",
            "personality": "A separate companion.",
        }))
        created = await self.receive_until(lambda item: item.get("type") == "snapshot")
        listed = created["data"]["characters"]
        self.assertEqual({"AIFren", "Second"}, {item["display_name"] for item in listed})
        legacy_id = next(item["character_id"] for item in listed if item["is_active"])
        second_id = next(item["character_id"] for item in listed if item["display_name"] == "Second")

        await self.client.send(json.dumps({"command": "select_character", "character_id": second_id}))
        switched = await self.receive_until(lambda item: item.get("type") == "snapshot")
        self.assertEqual(second_id, switched["data"]["character"]["character_id"])
        self.assertEqual("Second history.", switched["data"]["conversation"][0]["content"])
        self.assertEqual(1, self.service.closed)

        await self.client.send(json.dumps({"command": "select_character", "character_id": legacy_id}))
        restored = await self.receive_until(lambda item: item.get("type") == "snapshot")
        self.assertEqual(legacy_id, restored["data"]["character"]["character_id"])
        self.assertEqual("Welcome back.", restored["data"]["conversation"][0]["content"])
        self.assertEqual(1, replacement_b.closed)

    async def test_production_style_switch_rebinds_one_service_before_one_snapshot(self):
        class SwitchableService(FakeService):
            def __init__(self):
                super().__init__()
                self.prepared = 0
                self.switches = []

            def prepare_character_switch(self):
                self.prepared += 1

            def wait_for_character_switch_idle(self, _timeout):
                return True

            def switch_character_state(self, **request):
                self.switches.append(request["character_id"])
                self.character = {
                    "name": request["display_name"],
                    "_character_id": request["character_id"],
                }
                self.conversation.messages = [{
                    "role": "assistant", "content": request["display_name"] + " history",
                    "timestamp": "2026-08-29T12:00:00",
                }]
                self.continuity = {
                    "scope": {"kind": "real_world", "label": ""},
                    "activity": None, "open_threads": [], "revision": request["character_id"],
                }

        service = SwitchableService()
        self.host._unsubscribe()
        self.host._service = service
        self.host._unsubscribe = service.subscribe(self.host._on_service_event)
        self.service = service
        self.host._service_factory = lambda: self.fail("full service factory must not run")

        await self.client.send(json.dumps({
            "command": "create_character", "display_name": "Second",
            "personality": "A separate companion.",
        }))
        created = await self.receive_until(lambda item: item.get("type") == "snapshot")
        second_id = next(
            item["character_id"] for item in created["data"]["characters"]
            if item["display_name"] == "Second"
        )
        await self.client.send(json.dumps({"command": "select_character", "character_id": second_id}))
        switched = await self.receive_until(lambda item: item.get("type") == "snapshot")

        self.assertEqual(1, service.prepared)
        self.assertEqual([second_id], service.switches)
        self.assertEqual(0, service.closed)
        self.assertEqual([], service.llm_replacements)
        self.assertEqual(second_id, switched["data"]["character"]["character_id"])
        self.assertEqual("Second history", switched["data"]["conversation"][0]["content"])
        self.assertEqual(second_id, switched["data"]["continuity"]["revision"])

    async def test_character_switch_keeps_the_same_socket_when_retired_cleanup_fails(self):
        class CleanupFaultService(FakeService):
            def close(self):
                super().close()
                raise RuntimeError("retired SQLite cleanup fault")

        replacement = FakeService()
        replacement.character["name"] = "Second"
        self.host._service_factory = lambda: replacement
        self.service = CleanupFaultService()
        self.host._unsubscribe()
        self.host._service = self.service
        self.host._unsubscribe = self.service.subscribe(self.host._on_service_event)

        await self.client.send(json.dumps({
            "command": "create_character",
            "display_name": "Second",
            "personality": "A separate companion.",
        }))
        created = await self.receive_until(lambda item: item.get("type") == "snapshot")
        second_id = next(item["character_id"] for item in created["data"]["characters"] if item["display_name"] == "Second")

        await self.client.send(json.dumps({"command": "select_character", "character_id": second_id}))
        switched = await self.receive_until(lambda item: item.get("type") == "snapshot")
        self.assertEqual(second_id, switched["data"]["character"]["character_id"])
        self.assertEqual(1, self.service.closed)

        # A normal command on the original WebSocket proves no reconnect was
        # needed after retired-service cleanup reported its fault.
        await self.client.send(json.dumps({"command": "submit_text", "text": "Still connected"}))
        reply = await self.receive_until(
            lambda item: item.get("type") == "event"
            and item["event"]["type"] == "assistant_response"
        )
        self.assertEqual("Reply", reply["event"]["data"]["content"])

    async def test_character_selection_rejects_busy_and_unknown_requests(self):
        await self.client.send(json.dumps({"command": "create_character", "display_name": "Second", "personality": "Second prompt."}))
        created = await self.receive_until(lambda item: item.get("type") == "snapshot")
        second_id = next(item["character_id"] for item in created["data"]["characters"] if item["display_name"] == "Second")

        self.service.switch_busy = True
        await self.client.send(json.dumps({"command": "select_character", "character_id": second_id}))
        busy = await self.receive_until(lambda item: item.get("type") == "command_error")
        self.assertEqual("character_switch_busy", busy["error"]["code"])

        self.service.switch_busy = False
        await self.client.send(json.dumps({"command": "select_character", "character_id": "not-a-uuid"}))
        invalid = await self.receive_until(lambda item: item.get("type") == "command_error")
        self.assertEqual("character_select_failed", invalid["error"]["code"])

    async def test_character_creation_requires_a_personality_prompt(self):
        await self.client.send(json.dumps({"command": "create_character", "display_name": "Second", "personality": "  "}))
        invalid = await self.receive_until(lambda item: item.get("type") == "command_error")
        self.assertEqual("invalid_character_personality", invalid["error"]["code"])


if __name__ == "__main__":
    unittest.main()
