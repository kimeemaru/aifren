import asyncio
import json
import threading
import unittest

import websockets

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
            "name": "AIFren",
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
        self.turn_started = threading.Event()
        self.release_turn = threading.Event()
        self.block_turns = False

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

    def stop_speaking(self):
        self.stop_calls += 1
        self.emit("tts_state", state="stopped")

    def set_tts_volume(self, volume):
        self.volume_calls.append(volume)
        self.tts.volume = volume
        self.emit("tts_state", state="volume_changed", volume=volume)

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


class WebSocketTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = FakeService()
        self.host = AIFrenWebSocketHost(service=self.service, port=0)
        await self.host.start()
        self.client = await websockets.connect(
            f"ws://{LOOPBACK_HOST}:{self.host.port}"
        )

    async def asyncTearDown(self):
        await self.client.close()
        await self.host.stop()

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

    async def test_snapshot_contains_frontend_state(self):
        await self.client.send(json.dumps({"command": "get_snapshot"}))
        message = await self.receive_json()

        self.assertEqual(message["type"], "snapshot")
        self.assertEqual(message["data"]["character"]["name"], "AIFren")
        self.assertEqual(message["data"]["conversation"][0]["content"], "Welcome back.")
        self.assertEqual(message["data"]["status"]["state"], "ready")
        self.assertEqual(message["data"]["tts"]["volume"], 0.4)
        self.assertEqual(message["data"]["voice"]["state"], "ready")
        self.assertIn("models", message["data"])
        self.assertEqual(message["data"]["models"]["gemini"]["model"], "gemini-3.5-flash-lite")

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

    async def test_submit_text_routes_to_one_service_turn_and_forwards_events(self):
        await self.client.send(json.dumps({"command": "submit_text", "text": "Hello"}))
        message = await self.receive_until(
            lambda item: item.get("type") == "event"
            and item["event"]["type"] == "assistant_response"
        )

        self.assertEqual(self.service.submitted, ["Hello"])
        self.assertEqual(message["event"]["data"]["content"], "Reply")

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


if __name__ == "__main__":
    unittest.main()
