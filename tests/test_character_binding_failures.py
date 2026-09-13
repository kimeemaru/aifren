"""Synthetic character-session failure paths; no live data or capture device."""
import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import uuid

from assistant_service import AssistantEvent
from backend_host import AIFrenWebSocketHost
from character_registry import CharacterRegistry
import test_character_switch_ownership as ownership_fixture
from test_websocket_transport import FakeService


class _BoundService(FakeService):
    def __init__(self, character):
        super().__init__()
        self.character_id = character.character_id
        self.character = {"name": "Synthetic A", "_character_id": self.character_id}
        self.session = str(uuid.uuid4())
        self.idle = True
        self.host = None

    def character_binding(self):
        return {"character_id": self.character_id, "character_session": self.session}

    def require_character_binding(self, character_id, character_session):
        if {"character_id": character_id, "character_session": character_session} != self.character_binding():
            raise RuntimeError("Retired synthetic selection")

    def prepare_character_switch(self):
        self.session = str(uuid.uuid4())
        if self.host is not None:
            self.host._on_service_event(AssistantEvent("voice_state", {
                **self.character_binding(), "state": "stopped"}))

    def wait_for_character_switch_idle(self, timeout):
        return self.idle

    def switch_character_state(self, *, character_id, publish_selection=None, **_kwargs):
        # Model the production ordering: candidate first, durable acceptance,
        # then the indivisible live rebind. The old host omitted this callback.
        candidate = {"name": "Synthetic B", "_character_id": character_id}
        if publish_selection is not None:
            publish_selection()
        self.character = candidate
        self.character_id = character_id
        self.session = str(uuid.uuid4())
        self.conversation.messages = []


class CharacterBindingFailureTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = CharacterRegistry(self.root)
        self.a = self.registry.active(); self.b = self.registry.create("Synthetic B")
        self.service = _BoundService(self.a)
        self.host = AIFrenWebSocketHost(service=self.service, application_dir=self.root)
        self.host._character_registry = self.registry
        self.service.host = self.host
        self.sent = []
        async def send(_websocket, message):
            self.sent.append(message)
        self.host._send_json = send
        self.patches = ExitStack(); self.addCleanup(self.patches.close)
        for name in ("_companion_snapshot", "_model_snapshot", "_tts_snapshot"):
            self.patches.enter_context(patch.object(self.host, name, return_value={}))

    def command(self):
        return {"command": "continuity_control", **self.service.character_binding(),
                "action": "clear_scene_relation", "command_id": str(uuid.uuid4()),
                "expected_revision": "synthetic-retired-revision", "action_token": "0"}

    def error(self):
        return [m for m in self.sent if m.get("type") == "command_error"][-1]

    async def test_current_scoped_error_has_deliverable_character_identity(self):
        self.host._character_generation = 7
        wanted = {**self.service.character_binding(), "character_generation": 7}
        with patch.object(self.service, "apply_continuity_control", side_effect=RuntimeError("Stale revision")):
            await self.host._handle_command(object(), json.dumps(self.command()))
        error = self.error()
        self.assertEqual("stale_continuity_control", error["error"]["code"])
        self.assertEqual(wanted, {key: error.get(key) for key in wanted})

    async def test_delayed_scoped_error_keeps_captured_owner_after_rebind(self):
        entered, release = threading.Event(), threading.Event()
        self.host._character_generation = 7
        wanted = {**self.service.character_binding(), "character_generation": 7}
        def delayed(**_kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError("Synthetic release missing")
            raise RuntimeError("Old revision")
        with patch.object(self.service, "apply_continuity_control", side_effect=delayed):
            task = asyncio.create_task(self.host._handle_command(object(), json.dumps(self.command())))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                self.service.character_id = self.b.character_id
                self.service.session = str(uuid.uuid4())
                self.host._character_generation = 8
            finally:
                release.set(); await task
        self.assertEqual(wanted, {key: self.error().get(key) for key in wanted})

    async def test_registry_commit_failure_keeps_old_live_owner_and_can_retry(self):
        original_save = self.registry._save
        failed = False
        def fail_once():
            nonlocal failed
            if not failed and self.registry.active().character_id == self.b.character_id:
                failed = True
                raise OSError("Synthetic registry commit failure")
            original_save()
        with patch.object(self.registry, "_save", side_effect=fail_once):
            await self.host._select_character(object(), self.b.character_id)
        self.assertTrue(failed)
        self.assertEqual(self.a.character_id, self.service.character_id)
        self.assertEqual(self.a.character_id, self.registry.active().character_id)
        self.assertFalse(self.host._character_switching)
        snapshot = [m for m in self.sent if m.get("type") == "snapshot"][-1]
        self.assertEqual(self.a.character_id, snapshot["data"]["character"]["character_id"])
        await self.host._select_character(object(), self.b.character_id)
        self.assertEqual(self.b.character_id, self.service.character_id)
        self.assertEqual(self.b.character_id, self.registry.active().character_id)

    async def test_settled_switch_and_busy_failure_clear_retired_voice_readiness(self):
        for idle in (False, True):
            with self.subTest(idle=idle):
                self.host._voice_state = "transcribing"
                self.service.idle = idle
                await self.host._select_character(object(), self.b.character_id)
                snapshot = [m for m in self.sent if m.get("type") == "snapshot"][-1]
                self.assertEqual("ready", snapshot["data"]["voice"]["state"])
                self.assertFalse(self.host._character_switching)


class CharacterBindingFailureServiceTests(unittest.TestCase):
    setUp = ownership_fixture.CharacterSwitchOwnershipTests.setUp
    tearDown = ownership_fixture.CharacterSwitchOwnershipTests.tearDown
    _new_service = ownership_fixture.CharacterSwitchOwnershipTests._new_service
    _switch = ownership_fixture.CharacterSwitchOwnershipTests._switch

    def test_failed_publish_selection_preserves_live_state_before_any_rebind(self):
        before = {name: getattr(self.service, name) for name in (
            "memory", "conversation", "character", "_memory_v2_shadow_writer", "_memory_v2_authority")}
        self.service.prepare_character_switch()
        self.assertTrue(self.service.wait_for_character_switch_idle(2))
        owner = self.service.character_binding()
        with self.assertRaisesRegex(OSError, "Synthetic registry failure"):
            self.service.switch_character_state(character_id=self.b.character_id,
                display_name=self.b.display_name, runtime_paths=self.registry.runtime_paths(self.b.character_id),
                application_dir=self.root,
                publish_selection=Mock(side_effect=OSError("Synthetic registry failure")))
        for name, value in before.items():
            self.assertIs(value, getattr(self.service, name), name)
        self.assertEqual(owner, self.service.character_binding())
        self.service._assert_character_state_ownership()
        result = self.service.process_text_turn("Hello.", speak=False)
        self.assertTrue(result.succeeded, result.error)

    def create_fake_ptt(self):
        callbacks = {}
        class Ptt:
            def __init__(self, _voice, _tts, transcription, **kwargs):
                callbacks.update(transcription=transcription, **kwargs)
            def set_binding(self, _binding): pass
            def stop(self): pass
        self.service._ptt_factory = Ptt
        self.service.start_push_to_talk(listen_globally=False)
        return callbacks

    def check_retired_callbacks(self, callbacks):
        events = []; self.service.subscribe(events.append)
        before = list(self.service.conversation.messages)
        with patch.object(self.service, "process_text_turn") as submit, \
                patch.object(self.service, "_cancel_active_turn") as cancel, \
                patch.object(self.service, "stop_speaking") as stop:
            callbacks["on_state"]("released")
            callbacks["on_error"]("Synthetic retired capture error")
            callbacks["on_tts_interrupt"]()
            callbacks["transcription"]("Synthetic words from the retired capture.")
        self.assertEqual([], events)
        submit.assert_not_called(); cancel.assert_not_called(); stop.assert_not_called()
        self.assertEqual(before, self.service.conversation.messages)

    def test_retired_ptt_callbacks_cannot_publish_or_mutate_new_character(self):
        old = self.create_fake_ptt()
        self._switch(self.b)
        self.check_retired_callbacks(old)

    def test_a_b_a_does_not_reauthorize_an_old_ptt_capture(self):
        old = self.create_fake_ptt()
        self._switch(self.b); self._switch(self.a)
        self.check_retired_callbacks(old)

    def test_current_ptt_transcription_carries_binding_without_holding_callback_lock(self):
        callbacks = self.create_fake_ptt()
        wanted = self.service.character_binding()
        def at_submission(*_args, **_kwargs):
            acquired = []
            def other_thread():
                locked = self.service._character_binding_lock.acquire(timeout=.2)
                acquired.append(locked)
                if locked:
                    self.service._character_binding_lock.release()
            worker = threading.Thread(target=other_thread)
            worker.start(); worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertEqual([True], acquired, "Inference must not hold the PTT binding lock")
        with patch.object(self.service, "process_text_turn", side_effect=at_submission) as submit:
            callbacks["transcription"]("Current synthetic transcript.")
        submit.assert_called_once()
        for key, value in wanted.items():
            self.assertEqual(value, submit.call_args.kwargs.get(key))

    def test_old_ptt_paused_before_turn_claim_cannot_cancel_new_character_turn(self):
        callbacks = self.create_fake_ptt()
        entered, release = threading.Event(), threading.Event()
        outcomes = []
        original_policy = self.service.interaction_policy
        def delayed_policy(text):
            result = original_policy(text)
            entered.set()
            if not release.wait(3):
                raise AssertionError("Synthetic release missing")
            return result
        def old_capture():
            try:
                outcomes.append(callbacks["transcription"]("Hello from the retired capture."))
            except Exception as error:
                outcomes.append(error)
        with patch.object(self.service, "interaction_policy", side_effect=delayed_policy):
            worker = threading.Thread(target=old_capture)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                # The old request has passed its first binding check but has
                # not claimed _turn_lock; this is an actual PTT handoff gap.
                self._switch(self.b)
                current_turn, current_cancel, _ = self.service._claim_replacement_turn()
                stop_calls = self.service.tts.stop_calls
                release.set(); worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertFalse(current_cancel.is_set(), "Retired PTT cancelled the new character's turn")
                self.assertEqual(current_turn, self.service._active_turn_id)
                self.assertEqual(stop_calls, self.service.tts.stop_calls)
                self.assertEqual([], self.service.conversation.messages)
            finally:
                release.set(); worker.join(3)
        self.assertEqual(1, len(outcomes))


if __name__ == "__main__":
    unittest.main()
