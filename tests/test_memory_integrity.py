import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

import memory.memory as memory_module


class FakeEmbeddingModel:
    def encode(self, content):
        value = float(sum(ord(character) for character in content) % 10)
        return [value] * memory_module.EMBEDDING_DIMENSIONS


class FakeLLM:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    def generate(self, messages, prompt):
        self.prompts.append(prompt)
        return self.response


def make_memory(llm=None):
    instance = memory_module.Memory.__new__(memory_module.Memory)
    instance.llm = llm
    instance.memories = []
    instance.embedding_model = FakeEmbeddingModel()
    instance._lock = threading.RLock()
    return instance


class MemoryIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.memory_file = os.path.join(self.tempdir.name, "memories.json")
        self.path_patch = patch.object(
            memory_module,
            "MEMORY_FILE",
            self.memory_file,
        )
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.tempdir.cleanup()

    def record(self, memory_id=1, content="The user likes apples."):
        return {
            "id": memory_id,
            "category": "preference",
            "content": content,
            "importance": 5,
            "keywords": ["apples", "preference"],
            "embedding": [0.0] * memory_module.EMBEDDING_DIMENSIONS,
            "created": "2026-01-01T00:00:00",
            "updated": "2026-01-01T00:00:00",
        }

    def test_atomic_save_failure_preserves_existing_file(self):
        original = [self.record()]
        with open(self.memory_file, "w", encoding="utf-8") as file:
            json.dump(original, file)

        with patch.object(memory_module.os, "replace", side_effect=OSError("blocked")):
            with self.assertRaises(memory_module.MemoryDataError):
                memory_module.save_memories([self.record(content="The user likes pears.")])

        with open(self.memory_file, "r", encoding="utf-8") as file:
            self.assertEqual(json.load(file), original)
        self.assertTrue(os.path.exists(self.memory_file + ".bak"))

    def test_corrupt_json_raises_without_replacing_source_file(self):
        corrupt = "{ not valid json"
        with open(self.memory_file, "w", encoding="utf-8") as file:
            file.write(corrupt)

        with self.assertRaises(memory_module.MemoryDataError):
            memory_module.load_memories()

        with open(self.memory_file, "r", encoding="utf-8") as file:
            self.assertEqual(file.read(), corrupt)

    def test_invalid_records_are_rejected(self):
        invalid = self.record()
        invalid["importance"] = 4.5

        with self.assertRaises(memory_module.MemoryDataError):
            memory_module.save_memories([invalid])

        invalid = self.record()
        invalid["embedding"] = [0.0]
        with open(self.memory_file, "w", encoding="utf-8") as file:
            json.dump([invalid], file)

        with self.assertRaises(memory_module.MemoryDataError):
            memory_module.load_memories()

    def test_concurrent_mutations_remain_consistent(self):
        memory = make_memory()
        threads = [
            threading.Thread(
                target=memory.add_memory,
                args=("fact", f"The user owns item {index}.", 5),
            )
            for index in range(10)
        ]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(memory.memories), 10)
        self.assertEqual({item["id"] for item in memory.memories}, set(range(1, 11)))
        self.assertEqual(len(memory_module.load_memories()), 10)

    def test_manual_edit_regenerates_metadata_and_embedding(self):
        memory = make_memory()
        created = memory.add_memory("preference", "The user likes apples.", 5)
        original_embedding = list(created["embedding"])

        updated = memory.edit_memory(
            created["id"],
            "preference",
            "The user likes pears.",
            7,
        )

        self.assertEqual(updated["importance"], 7)
        self.assertIn("pears", updated["keywords"])
        self.assertNotEqual(updated["embedding"], original_embedding)
        self.assertEqual(updated["provenance"]["source"], "manual_edit")

    def test_assistant_only_claim_is_not_persisted(self):
        llm = FakeLLM(json.dumps([
            {
                "action": "ADD",
                "category": "fact",
                "content": "The user owns a dog.",
                "importance": 7,
            }
        ]))
        memory = make_memory(llm)
        assistant_reply = "You own a dog."

        memory.process("Tell me a joke.", assistant_reply)

        self.assertEqual(memory.memories, [])
        self.assertNotIn(assistant_reply, llm.prompts[0])


if __name__ == "__main__":
    unittest.main()
