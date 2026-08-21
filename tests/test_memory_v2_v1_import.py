import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from memory_v2_store.v1_import import V1ImportError, import_v1_shadow, verify_v1_shadow


class V1ShadowImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "source"
        (self.root / "characters/default").mkdir(parents=True)
        self.write_valid()

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def write_valid(self, *, conversation=None, memories=None, summary=None):
        self.write("conversation.json", conversation if conversation is not None else [
            {"role": "user", "content": "Synthetic hello", "timestamp": "2025-01-01T01:02:03"},
            {"role": "assistant", "content": "Synthetic reply", "timestamp": "2025-01-01T01:02:04Z", "extra": "preserve"},
        ])
        self.write("memories.json", memories if memories is not None else [{
            "id": 7, "category": "fact", "content": "Synthetic fact", "importance": 5,
            "created": "2025-01-01T01:02:03Z", "updated": "2025-01-02T01:02:03Z", "keywords": ["synthetic"], "embedding": [0.0] * 384, "extra": "audit",
        }])
        self.write("conversation_summary.json", summary if summary is not None else {"summary": "Synthetic summary", "summarized_messages": 1, "extra": "audit"})
        self.write("characters/default/character.json", {"name": "Synthetic", "description": "test", "version": "1", "voice": {}, "avatar": {}})

    def destination(self, name="shadow.sqlite3"):
        return Path(self.temp.name) / name

    def test_import_preserves_sources_and_verifies(self):
        source_files = [self.root / name for name in ("conversation.json", "memories.json", "conversation_summary.json", "characters/default/character.json")]
        before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files}
        result = import_v1_shadow(self.root, self.destination())
        self.assertTrue(result.passed)
        self.assertEqual((result.event_count, result.claim_count, result.summary_count), (2, 1, 1))
        self.assertTrue(Path(result.manifest_path).exists())
        self.assertTrue(Path(result.report_path).exists())
        self.assertEqual(before, {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files})
        self.assertTrue(verify_v1_shadow(self.root, self.destination())["passed"])

    def test_repeated_imports_have_identical_canonical_identity(self):
        first = import_v1_shadow(self.root, self.destination("one.sqlite3"))
        second = import_v1_shadow(self.root, self.destination("two.sqlite3"))
        self.assertEqual(first.character_id, second.character_id)
        self.assertEqual(first.aggregate_digest, second.aggregate_digest)

    def test_missing_optional_summary_is_allowed(self):
        (self.root / "conversation_summary.json").unlink()
        result = import_v1_shadow(self.root, self.destination())
        self.assertEqual(result.summary_count, 0)

    def test_empty_valid_history_imports(self):
        self.write_valid(conversation=[], memories=[], summary={"summary": "", "summarized_messages": 0})
        result = import_v1_shadow(self.root, self.destination())
        self.assertEqual((result.event_count, result.claim_count, result.summary_count), (0, 0, 0))

    def test_malformed_json_duplicate_ids_invalid_timestamp_and_bad_range_fail(self):
        (self.root / "conversation.json").write_text("{", encoding="utf-8")
        with self.assertRaises(V1ImportError): import_v1_shadow(self.root, self.destination())
        self.write_valid(memories=[{"id": 1, "category": "x", "content": "a", "importance": 1}, {"id": 1, "category": "x", "content": "b", "importance": 1}])
        with self.assertRaises(V1ImportError): import_v1_shadow(self.root, self.destination("two.sqlite3"))
        self.write_valid(conversation=[{"role": "user", "content": "x", "timestamp": "not-a-date"}])
        with self.assertRaises(V1ImportError): import_v1_shadow(self.root, self.destination("three.sqlite3"))
        self.write_valid(conversation=[], summary={"summary": "x", "summarized_messages": 1})
        with self.assertRaises(V1ImportError): import_v1_shadow(self.root, self.destination("four.sqlite3"))
        self.write_valid(memories=[{"id": 1, "category": "x", "content": "a", "importance": 1, "embedding": [0.0]}])
        with self.assertRaises(V1ImportError): import_v1_shadow(self.root, self.destination("five.sqlite3"))

    def test_existing_destination_is_never_overwritten(self):
        destination = self.destination()
        destination.write_bytes(b"do not replace")
        with self.assertRaises(V1ImportError): import_v1_shadow(self.root, destination)
        self.assertEqual(destination.read_bytes(), b"do not replace")


if __name__ == "__main__":
    unittest.main()
