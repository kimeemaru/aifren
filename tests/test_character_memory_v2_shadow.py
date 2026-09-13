import json
from pathlib import Path
import tempfile
import threading
import unittest

from character_registry import CharacterRegistry
from conversation.conversation import Conversation
from memory.memory import EMBEDDING_DIMENSIONS, Memory
from memory_v2_evaluation import run_synthetic_evaluation, shadow_health
from memory_v2_shadow_writer import MemoryV2ShadowWriter
from memory_v2_store import MemoryV2Repository
from memory_v2_telemetry import retrieval_report


class _Embedding:
    def encode(self, content):
        return [float(len(str(content)) % 7)] * EMBEDDING_DIMENSIONS


class _NoopLlm:
    def generate(self, *_args):
        return "[]"


class CharacterRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        default = self.root / "characters/default"
        default.mkdir(parents=True)
        (default / "character.json").write_text(json.dumps({"name": "Legacy"}), encoding="utf-8")
        (default / "personality.md").write_text("Legacy personality", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_legacy_default_is_idempotent_and_new_character_paths_are_isolated(self):
        first = CharacterRegistry(self.root)
        legacy = first.active()
        second = CharacterRegistry(self.root)
        self.assertEqual(legacy.character_id, second.active().character_id)
        self.assertTrue(legacy.legacy_default)
        self.assertEqual(self.root / "memories.json", first.runtime_paths()["memory"])

        created = first.create("Second", personality="Second personality")
        first.select(created.character_id)
        active = CharacterRegistry(self.root).active()
        paths = first.runtime_paths(created.character_id)
        self.assertEqual(created.character_id, active.character_id)
        self.assertNotEqual(first.runtime_paths(legacy.character_id)["memory"], paths["memory"])
        self.assertTrue(paths["character"].exists())
        self.assertTrue(paths["personality"].exists())
        first.update(created.character_id, display_name="Renamed second")
        self.assertEqual("Renamed second", CharacterRegistry(self.root).get(created.character_id).display_name)

    def test_selection_routes_new_history_and_memory_without_moving_legacy_files(self):
        registry = CharacterRegistry(self.root)
        legacy = registry.active()
        legacy_paths = registry.runtime_paths(legacy.character_id)
        legacy_conversation = Conversation(None, conversation_file=str(legacy_paths["conversation"]), summary_file=str(legacy_paths["summary"]))
        legacy_conversation.add_user_message("legacy history")
        legacy_conversation.save()

        created = registry.create("Isolated", personality="Different personality")
        registry.select(created.character_id)
        isolated_paths = registry.runtime_paths()
        isolated_conversation = Conversation(None, conversation_file=str(isolated_paths["conversation"]), summary_file=str(isolated_paths["summary"]))
        self.assertEqual([], isolated_conversation.messages)
        isolated_conversation.add_user_message("isolated history")
        isolated_conversation.save()
        isolated_memory = Memory(_NoopLlm(), memory_file=str(isolated_paths["memory"]))
        isolated_memory.embedding_model = _Embedding()
        isolated_memory.add_memory("fact", "The isolated character remembers basil.", 5)

        registry.select(legacy.character_id)
        self.assertEqual("legacy history", Conversation(None, conversation_file=str(legacy_paths["conversation"]), summary_file=str(legacy_paths["summary"])).messages[0]["content"])
        self.assertEqual([], Memory(_NoopLlm(), memory_file=str(legacy_paths["memory"])).memories)
        self.assertEqual("isolated history", Conversation(None, conversation_file=str(isolated_paths["conversation"]), summary_file=str(isolated_paths["summary"])).messages[0]["content"])

    def test_real_context_build_never_crosses_selected_character_history_or_summary(self):
        registry = CharacterRegistry(self.root)
        legacy = registry.active()
        legacy_paths = registry.runtime_paths(legacy.character_id)
        legacy_conversation = Conversation(
            None,
            conversation_file=str(legacy_paths["conversation"]),
            summary_file=str(legacy_paths["summary"]),
        )
        legacy_conversation.add_user_message("A_ONLY_SENTINEL_12345")
        legacy_conversation.save()
        legacy_summary = {"summary": "A_SUMMARY_SENTINEL_12345", "summarized_messages": 0}
        legacy_paths["summary"].write_text(json.dumps(legacy_summary), encoding="utf-8")
        legacy_conversation.summary_data = legacy_summary
        legacy_memory = Memory(_NoopLlm(), memory_file=str(legacy_paths["memory"]))

        second = registry.create("Second", personality="Second personality")
        registry.select(second.character_id)
        second_paths = registry.runtime_paths()
        second_conversation = Conversation(
            None,
            conversation_file=str(second_paths["conversation"]),
            summary_file=str(second_paths["summary"]),
        )
        second_conversation.add_user_message("B_ONLY_SENTINEL_98765")
        second_conversation.save()
        second_summary = {"summary": "B_SUMMARY_SENTINEL_98765", "summarized_messages": 0}
        second_paths["summary"].write_text(json.dumps(second_summary), encoding="utf-8")
        second_conversation.summary_data = second_summary
        second_memory = Memory(_NoopLlm(), memory_file=str(second_paths["memory"]))

        b_context = "\n".join(item["content"] for item in second_conversation.build_context(second_memory, "next B turn"))
        self.assertIn("B_ONLY_SENTINEL_98765", b_context)
        self.assertIn("B_SUMMARY_SENTINEL_98765", b_context)
        self.assertNotIn("A_ONLY_SENTINEL_12345", b_context)
        self.assertNotIn("A_SUMMARY_SENTINEL_12345", b_context)

        # This is the actual context-construction API used by the next
        # AssistantService prompt after switching back to the legacy character.
        registry.select(legacy.character_id)
        restored_conversation = Conversation(
            None,
            conversation_file=str(legacy_paths["conversation"]),
            summary_file=str(legacy_paths["summary"]),
        )
        a_context = "\n".join(item["content"] for item in restored_conversation.build_context(legacy_memory, "next A turn"))
        self.assertIn("A_ONLY_SENTINEL_12345", a_context)
        self.assertIn("A_SUMMARY_SENTINEL_12345", a_context)
        self.assertNotIn("B_ONLY_SENTINEL_98765", a_context)
        self.assertNotIn("B_SUMMARY_SENTINEL_98765", a_context)


class MemoryV2ShadowWriterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        default = self.root / "characters/default"
        default.mkdir(parents=True)
        (default / "character.json").write_text(json.dumps({"name": "Legacy"}), encoding="utf-8")
        self.registry = CharacterRegistry(self.root)
        self.character = self.registry.active()
        self.memory_file = self.root / "memories.json"
        self.memory = Memory(_NoopLlm(), memory_file=str(self.memory_file))
        self.memory.embedding_model = _Embedding()
        self.writer = MemoryV2ShadowWriter(
            self.root,
            character_id=self.character.character_id,
            display_name=self.character.display_name,
            memory_file=self.memory_file,
        )
        self.memory.subscribe_mutations(self.writer.observe)

    def tearDown(self):
        self.writer.close()
        self.temp.cleanup()

    def test_create_update_delete_are_append_only_character_scoped_shadows(self):
        created = self.memory.add_memory("preference", "The user likes tea.", 5)
        repository = MemoryV2Repository(self.writer.store)
        active = repository.page(self.character.character_id, limit=10)
        self.assertEqual(["The user likes tea."], [item.content for item in active])

        self.memory.update_memory(created["id"], content="The user likes coffee.")
        active = repository.page(self.character.character_id, limit=10)
        self.assertEqual(["The user likes coffee."], [item.content for item in active])
        self.assertEqual(2, self.writer.store.connection.execute(
            "SELECT COUNT(*) FROM claims WHERE character_id=?", (self.character.character_id,)
        ).fetchone()[0])

        self.memory.delete_memory(created["id"])
        self.assertEqual([], repository.page(self.character.character_id, limit=10))

    def test_observer_failure_does_not_undo_v1_and_reconcile_catches_up(self):
        self.memory.subscribe_mutations(lambda _mutation: (_ for _ in ()).throw(RuntimeError("shadow unavailable")))
        created = self.memory.add_memory("fact", "The user owns a telescope.", 5)
        self.assertEqual(created["content"], self.memory.memories[0]["content"])

        second_memory = Memory(_NoopLlm(), memory_file=str(self.root / "second.json"))
        second_memory.embedding_model = _Embedding()
        second_memory.add_memory("fact", "The user owns a compass.", 5)
        catchup = MemoryV2ShadowWriter(
            self.root,
            character_id=self.character.character_id,
            display_name=self.character.display_name,
            memory_file=self.root / "second.json",
            database_path=self.root / "catchup.sqlite3",
        )
        try:
            result = catchup.reconcile()
            self.assertEqual("ok", result["state"])
            self.assertEqual(1, result["imported"])
        finally:
            catchup.close()

    def test_shadow_health_and_synthetic_ground_truth_invariants(self):
        self.memory.add_memory("preference", "The user likes tea.", 5)
        report = shadow_health(self.writer.store, self.root, character_id=self.character.character_id)
        self.assertEqual(1, report["v1_valid_records"])
        self.assertEqual(0, report["unshadowed_v1_records"])
        self.assertEqual("ok", report["sqlite_quick_check"])
        self.assertTrue(report["foreign_keys_ok"])
        parity = self.writer.compare("tea", [{"id": "1", "category": "preference", "rank": 1}])
        self.assertEqual(1, parity["v1_mapped_count"])
        self.assertEqual(1, len(parity["overlap_claim_ids"]))
        self.assertTrue(parity["v2_retrieval_strategy"].startswith("adaptive_"))
        self.assertEqual(1, retrieval_report(self.writer.store)["total_compared"])
        evaluation = run_synthetic_evaluation(scale=100)
        self.assertEqual(1.0, evaluation.topk_recall)
        self.assertEqual(0, evaluation.superseded_leak_count)
        self.assertEqual(0, evaluation.archived_leak_count)
        self.assertEqual(0, evaluation.character_scope_leak_count)

    def test_two_characters_with_the_same_avatar_identifier_cannot_share_memories(self):
        second = self.registry.create("Second")
        # Avatar choice is deliberately presentation-only metadata. Both
        # character config files may point to the same asset without merging
        # identity or V2 scope.
        for character in (self.character, second):
            path = self.registry.runtime_paths(character.character_id)["character"]
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["avatar"] = {"enabled": True, "model": "shared-avatar", "path": "shared.vrm"}
            path.write_text(json.dumps(payload), encoding="utf-8")
        second_memory_path = self.registry.runtime_paths(second.character_id)["memory"]
        second_memory = Memory(_NoopLlm(), memory_file=str(second_memory_path))
        second_memory.embedding_model = _Embedding()
        second_writer = MemoryV2ShadowWriter(
            self.root,
            character_id=second.character_id,
            display_name=second.display_name,
            memory_file=second_memory_path,
        )
        try:
            self.assertEqual(self.registry.runtime_paths(second.character_id)["memory_v2"],second_writer.database_path)
            self.assertNotEqual(self.writer.database_path,second_writer.database_path)
            second_memory.subscribe_mutations(second_writer.observe)
            self.memory.add_memory("fact", "The first character remembers aurora.", 5)
            second_memory.add_memory("fact", "The second character remembers basil.", 5)
            repository = MemoryV2Repository(self.writer.store)
            self.assertEqual([], repository.search(self.character.character_id, "basil", limit=5))
            from memory_v2_store.store import StoreError
            with self.assertRaises(StoreError): repository.search(second.character_id, "aurora", limit=5)
            self.assertEqual(1, len(repository.search(self.character.character_id, "aurora", limit=5)))
            second_repository=MemoryV2Repository(second_writer.store)
            with self.assertRaises(StoreError):repository.search(second.character_id,"basil",limit=5)
            with self.assertRaises(StoreError):second_repository.search(self.character.character_id,"aurora",limit=5)
            self.assertEqual(1, len(second_repository.search(second.character_id, "basil", limit=5)))
        finally:
            second_writer.close()


if __name__ == "__main__":
    unittest.main()
