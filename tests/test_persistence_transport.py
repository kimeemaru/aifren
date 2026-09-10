"""Real service and loopback transport, with temporary records and fake devices."""

import asyncio
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import websockets

from assistant_service import AssistantService
from backend_host import AIFrenWebSocketHost, LOOPBACK_HOST
from conversation.conversation import Conversation
from conversation.persistence import ConversationPersistenceError
from test_assistant_service import FakeTTS
from test_conversation_persistence import Memory, Provider


class PersistenceTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="aifren-persistence-transport-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.addCleanup(os.chdir, Path.cwd())
        os.chdir(self.root)
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for name in tuple(os.environ):
            if name.startswith("AIFREN_"):
                del os.environ[name]

        self.path = self.root / "conversation.json"
        self.summary = self.root / "summary.json"
        self.provider = Provider()
        self.memory = Memory()
        self.tts = FakeTTS()
        self.conversation = Conversation(
            self.provider, conversation_file=self.path, summary_file=self.summary,
        )
        self.conversation.add_assistant_message("Synthetic retained record.")
        self.conversation.save()
        self.original = self.path.read_bytes()
        self.service = AssistantService(
            self.provider, self.memory, self.conversation, object(),
            {"name": "Synthetic"}, "Synthetic character.", self.tts,
            memory_authority="v1",
        )
        self.recorder = Mock(enabled=False)
        for target in ("assistant_service.development_flight_recorder",
                       "backend_host.development_flight_recorder"):
            recorder = patch(target, return_value=self.recorder)
            recorder.start()
            self.addCleanup(recorder.stop)
        self.runtime = SimpleNamespace(
            stop=Mock(return_value={"state": "off", "ownership": "none"}),
            snapshot=Mock(return_value={
                "state": "off", "ownership": "none", "installed_models": [],
            }),
        )
        self.host = AIFrenWebSocketHost(
            service=self.service, application_dir=self.root, port=0,
            local_model_runtime=self.runtime,
        )
        await self.host.start()
        self.addAsyncCleanup(self.host.stop)
        self.client = await websockets.connect(f"ws://{LOOPBACK_HOST}:{self.host.port}")
        self.addAsyncCleanup(self.client.close)

    async def receive(self):
        return json.loads(await asyncio.wait_for(self.client.recv(), timeout=2))

    async def submit(self, text):
        await self.client.send(json.dumps({"command": "submit_text", "text": text}))
        events = []
        for _ in range(50):
            message = await self.receive()
            self.assertEqual("event", message["type"])
            event = message["event"]
            events.append(event)
            if event["type"] == "status" and event["data"]["state"] in {"error", "ready"}:
                break
        else:
            self.fail("Announced turn did not leave its active status")

        # The status event can arrive just before the worker returns. Drain
        # worker/event tasks before using a snapshot as the transport barrier.
        await asyncio.wait_for(
            asyncio.gather(*list(self.host._turn_tasks)), timeout=2,
        )
        await asyncio.sleep(0)
        await asyncio.wait_for(
            asyncio.gather(*list(self.host._event_tasks)), timeout=2,
        )
        await self.client.send(json.dumps({"command": "get_snapshot"}))
        for _ in range(50):
            message = await self.receive()
            if message["type"] == "snapshot":
                return events, message["data"]
            self.assertEqual("event", message["type"])
            events.append(message["event"])
        self.fail("Snapshot barrier was not received")

    def assert_failure_outcome(self, events, snapshot, *, committed, assistant_persisted):
        starts = [event for event in events if event["type"] == "turn_started"]
        errors = [event["data"] for event in events if event["type"] == "error"]
        self.assertEqual(1, len(starts))
        self.assertEqual(1, len(errors))
        failure = errors[0]
        turn_id = starts[0]["data"]["turn_id"]
        self.assertEqual(turn_id, failure["turn_id"])
        self.assertEqual("conversation_persistence", failure["source"])
        self.assertEqual("conversation_persistence_failed", failure["code"])
        self.assertEqual(committed, failure["replacement_committed"])
        self.assertEqual(assistant_persisted, failure["assistant_persisted"])
        self.assertEqual("error", snapshot["status"]["state"])
        self.assertFalse(self.service._turn_lock.locked())
        self.assertIsNone(self.service._active_turn_cancel)
        self.assertFalse(any(event["type"] == "turn_cancelled" for event in events))
        terminals = [call.kwargs for call in self.recorder.mark.call_args_list
                     if call.args == ("turn_terminal_outcome",)
                     and call.kwargs.get("turn_id") == turn_id]
        self.assertEqual(1, len(terminals))
        self.assertEqual("persistence_error", terminals[0]["outcome"])
        self.assertFalse(terminals[0]["succeeded"])
        safe_payloads = json.dumps({"failure": failure, "status": snapshot["status"]})
        for private in (str(self.root), "Synthetic", "private-sentinel", "fake-secret"):
            self.assertNotIn(private, safe_payloads)
        return failure

    async def test_partial_write_error_is_terminal_and_next_turn_is_usable(self):
        def partial_write(_data, handle, **_kwargs):
            handle.write('[{"role":')
            raise OSError(f"{self.root}/private-sentinel fake-secret Synthetic input")

        with patch("conversation.persistence.json.dump", side_effect=partial_write):
            events, snapshot = await self.submit("Synthetic pending input.")
        self.assert_failure_outcome(events, snapshot, committed=False, assistant_persisted=False)
        self.assertEqual(self.original, self.path.read_bytes())
        self.assertEqual(json.loads(self.original), self.conversation.messages)
        self.assertEqual(1, len(snapshot["conversation"]))
        self.assertFalse(any(event["type"] in {"assistant_response", "conversation_message", "memory_updated"}
                             for event in events))
        self.memory.process.assert_not_called()
        self.assertEqual(1, len(self.provider.calls))

        events, snapshot = await self.submit("Synthetic next input.")
        self.assertEqual("ready", snapshot["status"]["state"])
        self.assertEqual(1, sum(event["type"] == "assistant_response" for event in events))
        self.assertFalse(any(event["type"] == "error" for event in events))
        self.assertEqual(3, len(snapshot["conversation"]))
        self.assertEqual(2, len(self.provider.calls))

    async def test_post_replace_error_retains_commit_without_retry_or_success_event(self):
        with patch("conversation.persistence._sync_directory", side_effect=[None, OSError("private-sentinel")]):
            events, snapshot = await self.submit("Synthetic committed input.")
        failure = self.assert_failure_outcome(events, snapshot, committed=True, assistant_persisted=True)
        self.assertIn("do not resend", failure["message"])
        self.assertEqual(3, len(snapshot["conversation"]))
        self.assertEqual(json.loads(self.path.read_bytes()), self.conversation.messages)
        self.assertEqual(1, len(self.provider.calls))
        self.assertFalse(any(event["type"] == "assistant_response" for event in events))
        self.memory.process.assert_not_called()
        self.service.save()
        self.assertEqual(3, len(json.loads(self.path.read_bytes())))
        self.assertEqual(1, len(self.provider.calls))

    async def test_later_summary_error_reports_saved_response_without_cancelling_speech(self):
        for _ in range(22):
            self.conversation.add_user_message("Synthetic older input.")
        self.conversation.save()
        original_replace = os.replace

        def replace(source, destination):
            if Path(destination) == self.summary:
                raise OSError("private-sentinel")
            return original_replace(source, destination)

        with patch.object(self.conversation, "_recent_context_start_index", return_value=22), \
                patch("conversation.persistence.os.replace", side_effect=replace):
            events, snapshot = await self.submit("Synthetic current input.")
        failure = self.assert_failure_outcome(events, snapshot, committed=False, assistant_persisted=True)
        self.assertEqual("summary", failure["record_kind"])
        self.assertIn("assistant response is saved; do not resend", failure["message"])
        self.assertEqual(1, sum(event["type"] == "assistant_response" for event in events))
        self.assertEqual(2, sum(event["type"] == "conversation_message" for event in events))
        self.assertEqual(25, len(snapshot["conversation"]))
        self.assertEqual(["Synthetic reply."], self.tts.spoken)
        self.assertEqual(0, self.tts.stopped)
        # One dialogue generation and one summary generation, never a retry.
        self.assertEqual(2, len(self.provider.calls))

    async def test_load_failure_is_terminal_and_explicit_recovery_allows_next_turn(self):
        self.path.write_bytes(b"[")
        with self.assertRaises(ConversationPersistenceError):
            self.conversation.reload()
        events, snapshot = await self.submit("Synthetic blocked input.")
        failure = self.assert_failure_outcome(events, snapshot, committed=False, assistant_persisted=False)
        self.assertEqual("load", failure["persistence_stage"])
        self.assertEqual([], self.provider.calls)
        self.assertEqual(b"[", self.path.read_bytes())
        self.path.write_bytes(self.original)
        self.conversation.reload()
        events, snapshot = await self.submit("Synthetic recovered input.")
        self.assertEqual("ready", snapshot["status"]["state"])
        self.assertEqual(1, sum(event["type"] == "assistant_response" for event in events))
        self.assertEqual(3, len(snapshot["conversation"]))

    async def test_shutdown_save_error_still_closes_transport_and_signals_runner(self):
        server = self.host._server
        with patch("conversation.persistence.os.replace", side_effect=OSError("private-sentinel")):
            with self.assertRaises(ConversationPersistenceError) as raised:
                await self.host.stop()
        self.assertFalse(raised.exception.committed)
        self.assertFalse(server.is_serving())
        self.assertIsNone(self.host._server)
        self.assertIsNone(self.host._client)
        self.assertFalse(self.host._running)
        self.assertTrue(self.host._shutdown_requested.is_set())
        self.assertIsNotNone(self.client.close_code)
        self.runtime.stop.assert_called_once()
        self.recorder.stop.assert_called_once()
        self.assertEqual(self.original, self.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
