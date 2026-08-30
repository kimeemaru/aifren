import json
from pathlib import Path
import tempfile
import unittest
import uuid

from memory_v2_store import (
    MemoryV2Repository,
    MemoryV2Store,
    SHARED_EPISODE,
    STABLE_USER_FACT,
    export_v2_json,
    import_v1_memories,
)


class MemoryV2ProductionFoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "memory-v2.sqlite3"
        self.store = MemoryV2Store(str(self.database))
        self.repository = MemoryV2Repository(self.store)
        self.character_a = str(uuid.uuid4())
        self.character_b = str(uuid.uuid4())
        self.repository.ensure_character(self.character_a, "A")
        self.repository.ensure_character(self.character_b, "B")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def add_memory(self, character_id, memory_id, content, memory_type=STABLE_USER_FACT):
        self.store.add_event(character_id, "event-" + memory_id, self.store.connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE character_id=?", (character_id,)
        ).fetchone()[0], content_text=content, source_origin="test")
        self.store.add_claim(character_id, memory_id, claim_type=memory_type, assertion_scope="user_fact",
                             content=content, created_at_us=1000, provenance_state="complete")
        self.store.attach_evidence(character_id, memory_id, "event-" + memory_id)

    def test_paging_and_search_are_character_scoped_and_bounded(self):
        self.add_memory(self.character_a, "a-tea", "The user prefers tea.")
        self.add_memory(self.character_b, "b-coffee", "The user prefers coffee.")
        self.store.ensure_fts()

        self.assertEqual(["a-tea"], [item.memory_id for item in self.repository.search(self.character_a, "tea", limit=1)])
        self.assertEqual([], self.repository.search(self.character_a, "coffee"))
        self.assertEqual(["a-tea"], [item.memory_id for item in self.repository.page(self.character_a, limit=1)])

    def test_supersession_and_archive_are_append_only_and_hidden_from_current_results(self):
        self.add_memory(self.character_a, "old", "The user lives in A.")
        self.add_memory(self.character_a, "new", "The user lives in B.")
        self.repository.supersede(self.character_a, "old", "new")
        self.repository.archive(self.character_a, "new")

        self.assertEqual([], self.repository.page(self.character_a))
        self.assertEqual("superseded", self.repository.get_memory(self.character_a, "old").status)
        self.assertEqual("archived", self.repository.get_memory(self.character_a, "new").status)
        relation = self.store.connection.execute("SELECT from_claim_id, to_claim_id FROM claim_relations").fetchone()
        self.assertEqual(("new", "old"), tuple(relation))

    def test_tolerant_v1_import_is_idempotent_and_supersedes_changed_legacy_row(self):
        source = self.root / "source"
        source.mkdir()
        memories = [
            {"id": 1, "category": "preference", "content": "The user likes tea.", "importance": 6,
             "created": "2025-01-01T00:00:00Z", "updated": "2025-01-02T00:00:00Z"},
            {"id": 2, "category": "event", "content": "We configured the project together.", "importance": 5},
            {"id": "bad", "content": "invalid"},
        ]
        (source / "memories.json").write_text(json.dumps(memories), encoding="utf-8")
        first = import_v1_memories(self.store, source)
        second = import_v1_memories(self.store, source)

        self.assertEqual((2, 0, 1), (first.imported, first.unchanged, first.skipped))
        self.assertEqual((0, 2, 1), (second.imported, second.unchanged, second.skipped))
        kinds = {item.memory_type for item in self.repository.page(first.character_id, limit=10)}
        self.assertEqual({STABLE_USER_FACT, SHARED_EPISODE}, kinds)
        self.assertTrue(all(item.source_reference and item.source_reference.startswith("memories.json#")
                            for item in self.repository.page(first.character_id, limit=10)))
        tea = next(item for item in self.repository.page(first.character_id, limit=10) if "tea" in item.content)
        self.assertGreater(tea.updated_at_us, tea.created_at_us)

        memories[0]["content"] = "The user now prefers coffee."
        (source / "memories.json").write_text(json.dumps(memories), encoding="utf-8")
        updated = import_v1_memories(self.store, source)
        self.assertEqual(1, updated.superseded)
        active = self.repository.page(updated.character_id, limit=10)
        self.assertTrue(any("coffee" in item.content for item in active))
        self.assertFalse(any("likes tea" in item.content for item in active))

    def test_export_is_deterministic_and_does_not_change_storage_identity(self):
        self.add_memory(self.character_a, "fact", "The user prefers tea.")
        target = self.root / "export.json"
        export_v2_json(self.store, target, character_id=self.character_a)
        first = target.read_bytes()
        export_v2_json(self.store, target, character_id=self.character_a)
        payload = json.loads(target.read_text(encoding="utf-8"))

        self.assertEqual(first, target.read_bytes())
        self.assertEqual("aifren-memory-v2-export", payload["format"])
        self.assertEqual(["fact"], [item["claim_id"] for item in payload["memories"]])
        self.assertEqual("ok", self.store.integrity_check())

    def test_unreadable_legacy_source_returns_error_without_creating_import_scope(self):
        source = self.root / "bad-source"
        source.mkdir()
        (source / "memories.json").write_text("{", encoding="utf-8")

        result = import_v1_memories(self.store, source)

        self.assertEqual(1, len(result.errors))
        self.assertEqual(2, self.store.connection.execute("SELECT COUNT(*) FROM characters").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
