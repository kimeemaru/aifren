"""Deterministic fresh-context checks for supported authoritative V2 slices."""
from pathlib import Path
import json
import tempfile
import unittest
import uuid

from aifren.conversation.conversation import Conversation
from aifren.continuity.memory_v2_shadow_writer import MemoryV2ShadowWriter
from aifren.memory_v2_store import MemoryV2Repository
from aifren.memory_v2_store.active_state_prompt import admit_active_headwear_context
from aifren.memory_v2_store.durable_prompt import admit_identity_name_context


class _Memory:
    def get_relevant_memories(self, _query, max_memories):
        self.max_memories = max_memories
        return []


class _LLM:
    def generate(self, _context, _prompt):
        return "unused"


class ContextAssemblyV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.character_id = str(uuid.uuid4())
        self.conversation = Conversation(
            _LLM(), conversation_file=self.root / "conversation.json", summary_file=self.root / "summary.json",
        )
        self.writer = MemoryV2ShadowWriter(
            self.root, character_id=self.character_id, display_name="Synthetic", memory_file=self.root / "memories.json",
        )
        self.repository = MemoryV2Repository(self.writer.store)
        self.repository.ensure_character(self.character_id, "Synthetic")
        scope = self.repository.active_truth_scope(self.character_id)
        self.truth_scope = {"kind": scope.kind, "scope_id": scope.truth_scope_id}
        self.memory = _Memory()

    def tearDown(self):
        self.writer.close()
        self.temp.cleanup()

    def _persist_and_observe(self, text, timestamp, observer):
        self.conversation.add_user_message(text, truth_scope=self.truth_scope)
        index = len(self.conversation.messages) - 1
        self.conversation.messages[index]["timestamp"] = timestamp
        self.conversation.add_assistant_message(
            "Synthetic completed response.", truth_scope=self.truth_scope,
        )
        self.conversation.save()
        return observer(self.conversation.messages[index], conversation_index=index,
                        conversation_file=self.conversation.conversation_file)

    def _fill_beyond_recent_window(self):
        for index in range(104):
            self.conversation.add_assistant_message(f"newer synthetic turn {index}")
        self.conversation.save()

    def _context(self, query):
        repository = MemoryV2Repository(self.writer.store)
        durable = admit_identity_name_context(repository, self.character_id, query)
        active = admit_active_headwear_context(repository, self.character_id, query)
        return self.conversation.build_context(
            self.memory, query,
            admitted_durable_context=durable.context_block,
            admitted_active_state_context=active.context_block,
        )

    def test_fresh_authoritative_durable_and_active_context_survive_raw_window_aging_and_updates(self):
        self.assertEqual("created", self._persist_and_observe(
            "My name is Elena.", "2020-01-01T00:00:00Z", self.writer.observe_canonical_user_message,
        )["state"])
        self.assertEqual("created", self._persist_and_observe(
            "She is wearing a red hat.", "2020-01-02T00:00:00Z", self.writer.observe_canonical_user_active_state,
        )["state"])
        self._fill_beyond_recent_window()
        raw = self.conversation.get_recent_messages()
        self.assertNotIn("My name is Elena.", [item["content"] for item in raw])
        self.assertNotIn("She is wearing a red hat.", [item["content"] for item in raw])

        name_context = self._context("What is my name?")
        self.assertIn('{"identity.name":"Elena"}', "\n".join(item["content"] for item in name_context))
        headwear_context = self._context("What hat is she wearing?")
        self.assertIn('{"active.avatar.headwear":"red hat"}', "\n".join(item["content"] for item in headwear_context))

        self.assertEqual("replaced", self._persist_and_observe(
            "She is wearing a blue hat.", "2021-01-01T00:00:00Z", self.writer.observe_canonical_user_active_state,
        )["state"])
        updated = "\n".join(item["content"] for item in self._context("What hat is she wearing?"))
        self.assertIn('"blue hat"', updated)
        self.assertNotIn('"red hat"', updated)

    def test_context_assembly_is_read_only_and_irrelevant_active_state_stays_withheld(self):
        self._persist_and_observe("She is wearing a blue hat.", "2020-01-02T00:00:00Z",
                                  self.writer.observe_canonical_user_active_state)
        message_count = len(self.conversation.messages)
        claim_count = self.writer.store.connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        first = self._context("Help me name a spaceship.")
        second = self._context("Help me name a spaceship.")
        joined = "\n".join(item["content"] for item in first + second)
        self.assertNotIn("[Verified current state", joined)
        self.assertEqual(message_count, len(self.conversation.messages))
        self.assertEqual(claim_count, self.writer.store.connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0])
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

    def test_fresh_context_uses_only_the_active_truth_scope_for_current_state(self):
        self._persist_and_observe("She is wearing a blue hat.", "2020-01-02T00:00:00Z",
                                  self.writer.observe_canonical_user_active_state)
        store = self.writer.store
        store.add_event(self.character_id, "scope-open", 2, recorded_at_us=3_000,
                        content_text="Start the campaign.")
        scenario = store.create_scenario_truth_scope(
            self.character_id, "Campaign", evidence_event_id="scope-open", evidence_excerpt_start_cp=0,
            evidence_excerpt_end_cp=5,
        )
        store.add_event(self.character_id, "scope-enter", 3, recorded_at_us=4_000,
                        content_text="Enter the campaign.")
        store.activate_truth_scope(self.character_id, scenario, evidence_event_id="scope-enter",
                                   evidence_excerpt_start_cp=0, evidence_excerpt_end_cp=5)
        store.add_event(self.character_id, "scenario-hat", 4, recorded_at_us=5_000,
                        content_text="She is wearing a red hat.")
        store.set_active_state(self.character_id, "scenario-hat", subject_key="active.avatar.headwear",
                               value="red hat", evidence_event_id="scenario-hat")
        in_scenario = "\n".join(item["content"] for item in self._context("What hat is she wearing?"))
        self.assertIn('"red hat"', in_scenario)
        self.assertNotIn('"blue hat"', in_scenario)
        store.add_event(self.character_id, "scope-leave", 5, recorded_at_us=6_000,
                        content_text="Leave the campaign.")
        store.deactivate_to_real_world(self.character_id, evidence_event_id="scope-leave",
                                       evidence_excerpt_start_cp=0, evidence_excerpt_end_cp=5)
        real_world = "\n".join(item["content"] for item in self._context("What hat is she wearing?"))
        self.assertIn('"blue hat"', real_world)
        self.assertNotIn('"red hat"', real_world)


if __name__ == "__main__":
    unittest.main()
