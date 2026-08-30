from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

from assistant import build_character_prompt, load_character
from assistant_service import AssistantService
from character_registry import CharacterRegistry
from conversation.conversation import Conversation
from memory.memory import EMBEDDING_DIMENSIONS, Memory
from memory_v2_shadow_writer import MemoryV2ShadowWriter


class _Embedding:
    def encode(self, _content):
        return [0.0] * EMBEDDING_DIMENSIONS


class _Llm:
    is_available = True

    def generate(self, *_args, **_kwargs):
        return "[]"

    def __init__(self):
        self.cancelled = 0

    def cancel_active_generation(self):
        self.cancelled += 1


class _Tts:
    def __init__(self): self.stop_calls = 0
    def set_playback_started_callback(self, _callback): pass
    def set_playback_finished_callback(self, _callback): pass
    def stop(self):
        self.stop_calls += 1
        return 0


class CharacterSwitchOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        default = self.root / "characters/default"
        default.mkdir(parents=True)
        (default / "character.json").write_text(
            json.dumps({
                "name": "Character A", "description": "Test A",
                "default_scene": {"worn": ["black dress"]},
            }), encoding="utf-8",
        )
        (default / "personality.md").write_text("Character A test prompt.", encoding="utf-8")
        self.registry = CharacterRegistry(self.root)
        self.a = self.registry.active()
        self.b = self.registry.create("Character B", personality="Character B test prompt.")
        b_character = self.registry.runtime_paths(self.b.character_id)["character"]
        b_data = json.loads(b_character.read_text(encoding="utf-8"))
        b_data["default_scene"] = {"worn": ["white boots"]}
        b_character.write_text(json.dumps(b_data), encoding="utf-8")
        self.original_cwd = Path.cwd()
        os.chdir(self.root)
        self.service = self._new_service(self.a.character_id)

    def tearDown(self):
        try:
            self.service.close()
        finally:
            os.chdir(self.original_cwd)
            self.temp.cleanup()

    def _new_service(self, character_id):
        selected = self.registry.get(character_id)
        paths = self.registry.runtime_paths(character_id)
        llm = _Llm()
        memory = Memory(llm, memory_file=str(paths["memory"]), embedding_model=_Embedding())
        conversation = Conversation(
            llm, conversation_file=str(paths["conversation"]), summary_file=str(paths["summary"]),
        )
        character, personality = load_character(paths["character"], paths["personality"])
        character = dict(character, _character_id=character_id, _display_name=selected.display_name)
        writer = MemoryV2ShadowWriter(
            self.root, character_id=character_id, display_name=selected.display_name,
            memory_file=paths["memory"],
        )
        writer.reconcile()
        unsubscribe = memory.subscribe_mutations(writer.observe)
        return AssistantService(
            llm, memory, conversation, object(), character,
            build_character_prompt(character, personality), _Tts(),
            response_generator=lambda *_args: "Okay.",
            memory_v2_shadow_writer=writer, character_id=character_id,
            memory_v2_unsubscribe=unsubscribe,
        )

    def _turn(self, text):
        result = self.service.process_text_turn(text, speak=False)
        self.assertTrue(result.succeeded, result.error)

    def _switch(self, selected):
        self.service.prepare_character_switch()
        self.assertTrue(self.service.wait_for_character_switch_idle())
        self.registry.select(selected.character_id)
        self.service.switch_character_state(
            character_id=selected.character_id, display_name=selected.display_name,
            runtime_paths=self.registry.runtime_paths(selected.character_id),
            application_dir=self.root,
        )
        self.assertEqual(selected.character_id, self.service.character_id)
        self.assertEqual(selected.character_id, self.service.character["_character_id"])
        self.assertEqual(selected.character_id, self.service._memory_v2_shadow_writer.character_id)
        return self.service.continuity_snapshot()

    @staticmethod
    def _snapshot_text(snapshot):
        return json.dumps(snapshot, sort_keys=True).casefold()

    def test_a_b_a_b_state_history_scope_threads_and_capabilities_are_isolated(self):
        self._turn("Let's roleplay that we're in the Blue Room.")
        self._turn("You're wearing a blue hat and holding a cup.")
        self._turn("I put a blindfold on you. Sit down.")
        self._turn("I'm waiting for my blue parcel to arrive.")
        a_conversation = len(self.service.conversation.messages)
        a_state = self.service.continuity_snapshot()
        a_text = self._snapshot_text(a_state)
        self.assertIn("blue hat", a_text)
        self.assertIn("cup", a_text)
        self.assertIn("unavailable", a_text)
        self.assertIn('"state": "sitting"', a_text)
        self.assertEqual("scenario", a_state["scope"]["kind"])
        self.assertTrue(a_state["open_threads"])
        self.assertEqual(["black dress"], self.service.character["default_scene"]["worn"])

        b_initial = self._switch(self.b)
        self.assertEqual([], self.service.conversation.messages)
        self.assertEqual("real_world", b_initial["scope"]["kind"])
        self.assertEqual([], b_initial["scene_subjects"])
        self.assertEqual([], b_initial["scene_relations"])
        self.assertEqual([], b_initial["open_threads"])
        self.assertEqual(["white boots"], self.service.character["default_scene"]["worn"])
        self.assertIn("white boots", self._snapshot_text(b_initial))

        self._turn("You're wearing a red scarf.")
        self._turn("Stand up.")
        b_conversation = len(self.service.conversation.messages)
        b_state = self.service.continuity_snapshot()
        b_text = self._snapshot_text(b_state)
        self.assertIn("red scarf", b_text)
        self.assertNotIn("blue hat", b_text)
        self.assertNotIn("blindfold", b_text)
        self.assertIn('"state": "standing"', b_text)
        self.assertEqual(["white boots"], self.service.character["default_scene"]["worn"])

        restored_a = self._switch(self.a)
        self.assertEqual(a_conversation, len(self.service.conversation.messages))
        self.assertIn("blue hat", self._snapshot_text(restored_a))
        self.assertNotIn("red scarf", self._snapshot_text(restored_a))
        self.assertEqual("scenario", restored_a["scope"]["kind"])
        self.assertTrue(restored_a["open_threads"])
        self.assertEqual(["black dress"], self.service.character["default_scene"]["worn"])

        self._turn("Take off the blue hat.")
        mutated_a = self.service.continuity_snapshot()
        self.assertNotIn("blue hat", json.dumps(mutated_a["scene_relations"]).casefold())
        self.assertTrue(any(
            item["label"] == "blue hat" and item["lifecycle"] == "dormant"
            for item in mutated_a["scene_subjects"]
        ))

        restored_b = self._switch(self.b)
        self.assertEqual(b_conversation, len(self.service.conversation.messages))
        self.assertIn("red scarf", self._snapshot_text(restored_b))
        self.assertNotIn("blue hat", self._snapshot_text(restored_b))
        self.assertEqual("real_world", restored_b["scope"]["kind"])

        persisted_a = self._switch(self.a)
        self.assertNotIn("blue hat", json.dumps(persisted_a["scene_relations"]).casefold())
        self.assertTrue(any(
            item["label"] == "blue hat" and item["lifecycle"] == "dormant"
            for item in persisted_a["scene_subjects"]
        ))
        final_b = self._switch(self.b)
        self.assertEqual(self._snapshot_text(restored_b), self._snapshot_text(final_b))

    def test_restart_reconstructs_selected_character_without_cross_character_state(self):
        self._turn("You're wearing a blue hat.")
        a_before = self.service.continuity_snapshot()
        self._switch(self.b)
        self._turn("You're wearing a red scarf.")
        before = self.service.continuity_snapshot()
        self.service.close()
        self.service = self._new_service(self.b.character_id)
        after = self.service.continuity_snapshot()
        self.assertEqual(self._snapshot_text(before), self._snapshot_text(after))
        self.assertIn("red scarf", self._snapshot_text(after))
        self.assertNotIn("blue hat", self._snapshot_text(after))

        restored_a = self._switch(self.a)
        self.assertEqual(self._snapshot_text(a_before), self._snapshot_text(restored_a))
        self.assertIn("blue hat", self._snapshot_text(restored_a))
        self.assertNotIn("red scarf", self._snapshot_text(restored_a))
        restored_b = self._switch(self.b)
        self.assertEqual(self._snapshot_text(after), self._snapshot_text(restored_b))

    def test_switch_invalidation_suppresses_old_character_generation_before_rebind(self):
        started, release = threading.Event(), threading.Event()

        def delayed_response(*_args):
            started.set()
            release.wait(timeout=2)
            return "Old character response."

        self.service._response_generator = delayed_response
        outcome = []
        worker = threading.Thread(
            target=lambda: outcome.append(self.service.process_text_turn("Old turn", speak=False)),
        )
        worker.start()
        self.assertTrue(started.wait(timeout=2))
        self.service.prepare_character_switch()
        release.set()
        self.assertTrue(self.service.wait_for_character_switch_idle(timeout=2))
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(outcome)
        self.assertTrue(outcome[0].error)

        self.registry.select(self.b.character_id)
        self.service.switch_character_state(
            character_id=self.b.character_id, display_name=self.b.display_name,
            runtime_paths=self.registry.runtime_paths(self.b.character_id),
            application_dir=self.root,
        )
        self.assertEqual([], self.service.conversation.messages)
        self.assertGreaterEqual(self.service.llm.cancelled, 1)
        self.assertGreaterEqual(self.service.tts.stop_calls, 1)
