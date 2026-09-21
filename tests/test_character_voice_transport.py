import asyncio
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

import websockets

from aifren.backend_host import AIFrenWebSocketHost
from aifren.character.character_registry import CharacterRegistry
from aifren.tts.character_voice import CharacterVoiceTTS
from test_character_voice import recording
from test_websocket_transport import FakeService


class CharacterVoiceTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_cwd = Path.cwd()
        self.original_environment = dict(os.environ)
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.registry = CharacterRegistry(self.root)
        self.character = self.registry.create("Synthetic voice", personality="Synthetic fixture.")
        self.runtime = Mock()
        self.service = FakeService()
        self.service.character_id = self.character.character_id
        self.service.tts = CharacterVoiceTTS(Mock(), self.registry, self.character.character_id, runtime=self.runtime)
        self.session = "synthetic-session"

        def require_character_binding(character_id, character_session):
            if (character_id, character_session) != (self.character.character_id, self.session):
                raise RuntimeError("Character session was retired.")

        self.service.require_character_binding = require_character_binding
        self.service.stop_speaking = lambda **_: (self.service.tts.cancel_voice_job(), self.service.tts.stop())
        self.service.stop_voice_preview = self.service.tts.stop_voice_preview
        self.host = AIFrenWebSocketHost(service=self.service, port=0, application_dir=self.root)
        await self.host.start()
        self.client = await websockets.connect(f"ws://127.0.0.1:{self.host.port}")

    async def asyncTearDown(self):
        await self.client.close()
        await self.host.stop()
        self.service.tts.close()
        os.chdir(self.original_cwd)
        os.environ.clear()
        os.environ.update(self.original_environment)
        self.temporary.cleanup()

    async def command(self, action, **values):
        command = dict(command="character_voice", action=action, request_id="voice-test",
                       character_id=self.character.character_id, character_session=self.session,
                       voice_engine="kokoro", voice_revision="initial")
        command.update(values)
        await self.client.send(json.dumps(command))

    async def result(self):
        for _ in range(20):
            result = json.loads(await asyncio.wait_for(self.client.recv(), timeout=3))
            if result.get("request_id", result.get("error", {}).get("request_id")) == "voice-test":
                if result["type"] == "command_error":
                    return result
                if result["data"]["tts"]["character_voice"]["state"] != "preparing":
                    return result
        self.fail("No final voice response")

    async def test_normal_save_get_and_stale_command_without_canonical_mutation(self):
        before = json.dumps(self.service.conversation.messages)
        await self.command("save")
        response = await self.result()
        self.assertEqual(response["type"], "character_voice")
        self.assertEqual(response["character_id"], self.character.character_id)
        saved = self.service.tts.profiles.load(self.character.character_id)
        self.assertNotEqual(saved.revision, "initial")
        await self.command("save", character_session="retired")
        response = await self.result()
        self.assertEqual(response["type"], "command_error")
        self.assertEqual(self.service.tts.profiles.load(self.character.character_id), saved)
        self.assertEqual(json.dumps(self.service.conversation.messages), before)
        self.assertEqual(self.service.submitted, [])
        self.runtime.request.assert_not_called()

    async def test_disconnect_retires_preparation_before_preview_can_play(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def prepare(*args, **kwargs):
            entered.set()
            release.wait(5)

        self.runtime.request.side_effect = prepare
        reference = self.root / "synthetic.wav"
        recording(reference)
        await self.command("preview", voice_engine="gpt_sovits", voice_reference=str(reference),
                           voice_transcript="A synthetic reference.")
        self.assertTrue(await asyncio.to_thread(entered.wait, 3))
        await self.client.close()
        for _ in range(20):
            if self.host._client is None:
                break
            await asyncio.sleep(.01)
        release.set()
        await asyncio.wait_for(self.host._character_voice_task, timeout=3)
        self.assertIsNone(self.service.tts.playback_thread)
        self.assertFalse(self.registry.runtime_paths(self.character.character_id)["voice_profile"].exists())
        self.assertEqual(self.service.submitted, [])

    async def test_old_preview_stop_does_not_stop_a_newer_reply(self):
        await self.command("prepare")
        await self.result()
        stop = Mock()
        self.service.stop_speaking = stop
        # The normal service retires the preview job as it claims a new turn.
        self.service.tts.cancel_voice_job()
        await self.command("stop", voice_operation_id="voice-test")
        await self.result()
        stop.assert_not_called()

    async def test_final_acknowledgement_allows_the_next_operation(self):
        for _ in range(3):
            await self.command("prepare")
            result = await self.result()
            self.assertEqual(result["type"], "character_voice")
            self.assertEqual(result["data"]["tts"]["character_voice"]["state"], "ready")

    async def test_stop_matches_current_preview_operation(self):
        await self.command("prepare")
        await self.result()
        stop = Mock()
        self.service.stop_voice_preview = stop
        await self.command("stop", voice_operation_id="another-request")
        await self.result()
        stop.assert_not_called()
        await self.command("stop", voice_operation_id="voice-test")
        await self.result()
        stop.assert_called_once_with(self.host._character_voice_job)

    async def test_preview_stop_after_validation_cannot_stop_replacement_playback(self):
        provider = self.service.tts
        job = provider.begin_voice_job()
        old = provider._next_playback_generation()
        provider._mark_playback_active(old)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = provider.stop_if_generation

        def delayed_conditional_stop(generation):
            entered.set()
            release.wait(5)
            return original(generation)

        provider.stop_if_generation = delayed_conditional_stop
        task = asyncio.create_task(asyncio.to_thread(provider.stop_voice_preview, job))
        self.assertTrue(await asyncio.to_thread(entered.wait, 2))
        # Replacement starts after the preview job was validated and retired,
        # but before its stop reaches the shared playback owner.
        provider.cancel_voice_job()
        replacement = provider._next_playback_generation()
        provider._mark_playback_active(replacement)
        release.set()
        self.assertIsNone(await asyncio.wait_for(task, 3))
        self.assertEqual(provider.playback_generation, replacement)
        self.assertEqual(provider.playback_debug_state()["playing"], replacement)


if __name__ == "__main__":
    unittest.main()
