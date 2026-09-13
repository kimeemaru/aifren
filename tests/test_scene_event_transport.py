"""Scene acknowledgements through ephemeral loopback, with synthetic owners only."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

import websockets

from aifren.backend_host import AIFrenWebSocketHost, LOOPBACK_HOST
from benchmarks.active_state.production_session import ProductionSession, response_envelope
from test_cancelled_response_repair import FocusedPtt
from test_scene_event_persistence import PreparedTts
import test_scene_event_persistence as scene_fixture


class SceneEventTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.addCleanup(os.chdir, Path.cwd())
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        self.session = ProductionSession(self.id(), tts=PreparedTts())
        self.addCleanup(self.session.close)
        self.session.turn("I blindfold you.", response_envelope("*Holds still.*"))
        self.service = self.session.service
        self.service._response_generator = None
        self.service._ptt_factory = FocusedPtt
        self.session.provider.generate = Mock(return_value=response_envelope("*Blinks.*"))
        snapshot = self.service.continuity_snapshot()
        self.command = {
            **self.service.character_binding(), "command": "continuity_control", "command_id": str(uuid.uuid4()),
            "action": "interact_scene_relation", "expected_revision": snapshot["revision"],
            "action_token": snapshot["scene_relations"][0]["clear_token"],
        }
        runtime = SimpleNamespace(
            stop=Mock(return_value={"state": "off", "ownership": "none"}),
            snapshot=Mock(return_value={"state": "off", "ownership": "none", "installed_models": []}),
        )
        # Runtime is controlled, and any accidental auto-start is a failure.
        self.host = AIFrenWebSocketHost(
            service=self.service, application_dir=self.session.root, port=0, local_model_runtime=runtime,
        )
        launch = patch.object(self.host, "_schedule_local_model_start",
                              side_effect=AssertionError("Service launch forbidden"))
        launch.start()
        self.addCleanup(launch.stop)
        await self.host.start()
        self.addAsyncCleanup(self.host.stop)
        self.client = await websockets.connect(f"ws://{LOOPBACK_HOST}:{self.host.port}")
        async def close_client():
            await self.client.close()
        self.addAsyncCleanup(close_client)

    async def receive_until(self, predicate):
        seen = []
        for _ in range(100):
            message = json.loads(await asyncio.wait_for(self.client.recv(), timeout=3))
            seen.append(message)
            if predicate(message):
                return message, seen
        self.fail("Transport barrier was not reached")

    async def control(self, **changes):
        await self.client.send(json.dumps({**self.command, **changes}))
        return await self.receive_until(lambda row: row.get("type") == "event"
                                        and row["event"]["type"] == "continuity_control_result")

    async def snapshot(self):
        await self.client.send(json.dumps({"command": "get_snapshot"}))
        message, _ = await self.receive_until(lambda row: row.get("type") == "snapshot")
        return message["data"]

    def scene_records(self):
        return [row for row in json.loads(self.session.conversation_file.read_text())
                if row.get("origin", {}).get("kind") == "scene_ui"]

    async def test_scoped_errors_retain_request_owner_for_pending_controls(self):
        await self.client.send(json.dumps({**self.command, "expected_revision": "retired-revision"}))
        error, _ = await self.receive_until(lambda row: row.get("type") == "command_error")
        for key, value in self.service.character_binding().items():
            self.assertEqual(value, error[key])
        self.assertEqual(0, error["character_generation"])
        self.assertEqual([], self.scene_records())

    async def test_stale_command_error_cannot_impersonate_current_session(self):
        await self.client.send(json.dumps({**self.command, "character_session": "retired-session"}))
        error, _ = await self.receive_until(lambda row: row.get("type") == "command_error")
        self.assertEqual("stale_character_control", error["error"]["code"])
        self.assertEqual("retired-session", error["character_session"])
        self.assertEqual([], self.scene_records())

    async def test_unavailable_reaction_ack_and_reconnect_retry_keep_one_canonical_identity(self):
        self.session.provider.is_available = False
        message, events = await self.control()
        result = message["event"]["data"]
        self.assertEqual("applied", result["outcome"])
        self.assertEqual("committed", result["canonical_event"]["state"])
        self.assertFalse(result["reaction"]["published"])
        self.assertFalse(any(row.get("event", {}).get("type") == "turn_started" for row in events))
        snapshot = await self.snapshot()
        identity = result["canonical_event"]["message_id"]
        self.assertEqual(1, sum(row["message_id"] == identity for row in snapshot["conversation"]))
        await self.client.close()
        # Closing handshake waits for the server handler's owner retirement.
        for handler in tuple(self.host._server.handlers):
            task = self.host._server.handlers[handler]
            await asyncio.wait_for(asyncio.shield(task), timeout=3)
        self.client = await websockets.connect(f"ws://{LOOPBACK_HOST}:{self.host.port}")
        message, _ = await self.control()
        result = message["event"]["data"]
        self.assertTrue(result["duplicate"])
        self.assertNotIn("reaction", result)
        self.assertEqual(identity, result["canonical_event"]["message_id"])
        self.assertEqual(1, len(self.scene_records()))
        self.session.provider.generate.assert_not_called()

    async def test_incomplete_ack_reports_safe_failure_then_retry_completes_record(self):
        before = self.session.conversation_file.read_bytes()
        with patch('aifren.conversation.persistence.json.dump', side_effect=scene_fixture.SceneEventPersistenceTests.partial_write):
            message, events = await self.control()
        result = message["event"]["data"]
        self.assertEqual("applied_record_incomplete", result["outcome"])
        self.assertEqual("pending", result["canonical_event"]["state"])
        snapshot = await self.snapshot()
        self.assertEqual("error", snapshot["status"]["state"])
        self.assertEqual(before, self.session.conversation_file.read_bytes())
        errors = [row["event"]["data"] for row in events if row.get("event", {}).get("type") == "error"]
        self.assertEqual(1, len(errors))
        self.assertEqual("conversation_persistence_failed", errors[0]["code"])
        self.assertFalse(errors[0]["replacement_committed"])
        self.assertNotIn("fake-secret", json.dumps(errors))
        self.assertNotIn("synthetic-private-path", json.dumps(errors))
        self.assertNotIn(str(self.session.root), json.dumps(errors))
        self.assertFalse(any(row.get("event", {}).get("type") == "turn_started" for row in events))
        message, _ = await self.control()
        self.assertEqual("committed", message["event"]["data"]["canonical_event"]["state"])
        self.assertTrue(message["event"]["data"]["canonical_event"]["recovered"])
        self.assertEqual(1, len(self.scene_records()))
        self.assertEqual("ready", (await self.snapshot())["status"]["state"])
        self.session.provider.generate.assert_not_called()
        await self.client.send(json.dumps({"command": "submit_text", "text": "Hello again.", **self.service.character_binding()}))
        response, _ = await self.receive_until(lambda row: row.get("event", {}).get("type") == "assistant_response")
        self.assertEqual("*Blinks.*", response["event"]["data"]["content"])
        await asyncio.wait_for(asyncio.gather(*list(self.host._turn_tasks)), timeout=3)
        self.assertEqual("ready", (await self.snapshot())["status"]["state"])

    async def test_socket_ptt_interrupts_blocked_synthesis_without_stale_playback(self):
        self.session.provider.generate.return_value = response_envelope("Good to see you.")
        self.session.tts.block = True
        self.addCleanup(self.session.tts.release.set)
        await self.client.send(json.dumps(self.command))
        self.assertTrue(await asyncio.to_thread(self.session.tts.entered.wait, 3))
        await self.client.send(json.dumps({"command": "ptt_press", **self.service.character_binding()}))
        _, events = await self.receive_until(
            lambda row: row.get("event", {}).get("type") == "voice_state"
            and row["event"]["data"]["state"] == "listening",
        )
        self.assertFalse(self.session.tts.release.is_set())
        self.assertEqual(1, len(self.scene_records()))
        self.session.tts.release.set()
        message, later = await self.receive_until(
            lambda row: row.get("event", {}).get("type") == "continuity_control_result",
        )
        self.assertTrue(message["event"]["data"]["reaction"]["published"])
        self.assertEqual([], self.session.tts.spoken)
        self.assertFalse(any(row.get("event", {}).get("type") == "turn_cancelled" for row in events + later))
        self.assertFalse(any(row.get("event", {}).get("type") == "tts_state"
                             and row["event"]["data"].get("state") == "playback_started" for row in events + later))


if __name__ == "__main__":
    unittest.main()
