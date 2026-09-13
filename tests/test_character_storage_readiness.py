"""Local startup rejects incomplete identity/schema without rebuilding storage."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from aifren.character.character_registry import CharacterRegistry, CharacterStorageError
from aifren.memory_v2_store.store import MemoryV2Store


class CharacterStorageReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.registry = CharacterRegistry(self.root)
        self.character = self.registry.create("Synthetic Rowan", personality="Synthetic human companion.")
        self.paths = self.registry.runtime_paths(self.character.character_id)

    def change_database(self, statement, arguments=()):
        with closing(sqlite3.connect(self.paths["memory_v2"])) as connection:
            with connection:
                connection.execute(statement, arguments)

    def write_profile(self, value):
        self.paths["character"].write_text(json.dumps(value), encoding="utf-8")

    def ready(self):
        return self.registry.assert_storage_ready(self.character.character_id)

    def test_metadata_does_not_substitute_for_the_selected_character_row(self):
        self.change_database("DELETE FROM characters WHERE character_id=?", (self.character.character_id,))
        with self.assertRaisesRegex(CharacterStorageError, "identity|owner|character row"):
            self.ready()
        with closing(sqlite3.connect(self.paths["memory_v2"])) as connection:
            self.assertEqual(0, connection.execute("SELECT count(*) FROM characters").fetchone()[0])

    def test_unknown_schema_is_refused_without_initialization(self):
        for statement, undo in (
            ("CREATE TABLE unknown_state(value TEXT)", "DROP TABLE unknown_state"),
            ("ALTER TABLE characters ADD COLUMN hidden_owner TEXT", "ALTER TABLE characters DROP COLUMN hidden_owner"),
            ("CREATE TRIGGER hidden_mutation AFTER INSERT ON characters BEGIN DELETE FROM claims; END", "DROP TRIGGER hidden_mutation"),
            ("UPDATE database_meta SET value='999' WHERE key='schema_version'", "UPDATE database_meta SET value='21' WHERE key='schema_version'"),
        ):
            with self.subTest(schema=statement):
                self.change_database(statement)
                with self.assertRaisesRegex(CharacterStorageError, "schema|layout"):
                    self.ready()
                self.change_database(undo)
        self.assertEqual(self.paths, self.ready())

    def test_profile_is_parsed_and_any_explicit_uuid_is_bound(self):
        original = self.paths["character"].read_bytes()
        cases = (["not a profile"], {"name": ""}, {"name": 7},
                 {"name": "Other", "character_id": str(uuid.uuid4())},
                 {"name": "Other", "_character_id": str(uuid.uuid4())})
        for profile in cases:
            with self.subTest(profile=profile):
                self.write_profile(profile)
                before = self.paths["character"].read_bytes()
                with self.assertRaisesRegex(CharacterStorageError, "profile|identity"):
                    self.ready()
                self.assertEqual(before, self.paths["character"].read_bytes())
        self.paths["character"].write_text("{bad json", encoding="utf-8")
        with self.assertRaisesRegex(CharacterStorageError, "profile"):
            self.ready()
        self.paths["character"].write_bytes(original)
        self.assertEqual(self.paths, self.ready())

    def test_legacy_profile_without_uuid_and_renamed_registry_label_remain_valid(self):
        self.assertEqual(self.paths, self.ready())
        self.registry.update(self.character.character_id, display_name="Renamed synthetic label")
        self.assertEqual(self.paths, self.ready(), "Mutable labels cannot replace UUID ownership")
        profile = json.loads(self.paths["character"].read_text())
        profile["character_id"] = self.character.character_id
        self.write_profile(profile)
        self.assertEqual(self.paths, self.ready())

    def test_readiness_connection_is_closed_on_success_and_error(self):
        opened = []
        connect = sqlite3.connect

        def tracked(database, *args, **kwargs):
            connection = connect(database, *args, **kwargs)
            if str(database).startswith(self.paths["memory_v2"].as_uri()):
                opened.append(connection)
            return connection

        for valid in (True, False):
            if not valid:
                self.change_database("UPDATE database_meta SET value='wrong' WHERE key='timeline_generation'")
            with patch('aifren.character.character_registry.sqlite3.connect', side_effect=tracked):
                if valid:
                    self.ready()
                else:
                    with self.assertRaises(CharacterStorageError):
                        self.ready()
        self.assertEqual(2, len(opened))
        for connection in opened:
            with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
                connection.execute("SELECT 1")

    def test_schema_check_does_not_inventory_or_read_dialogue_rows(self):
        queries = []
        connect = sqlite3.connect

        def tracked(database, *args, **kwargs):
            connection = connect(database, *args, **kwargs)
            if str(database).startswith(self.paths["memory_v2"].as_uri()):
                connection.set_trace_callback(queries.append)
            return connection

        with patch('aifren.character.character_registry.sqlite3.connect', side_effect=tracked), patch(
            'aifren.memory_v2_store.character_copy.selected_character_inventory',
            side_effect=AssertionError("readiness must not inventory the archive"),
        ):
            self.ready()
        reads = [query.lower() for query in queries if query.lstrip().lower().startswith("select")]
        self.assertTrue(reads)
        self.assertFalse(any("from claims" in query or "from events" in query for query in reads), reads)
        self.assertFalse(any(query.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for query in queries))

    def test_missing_local_file_or_other_owner_never_selects_legacy_storage(self):
        self.change_database("UPDATE database_meta SET value=? WHERE key='storage_character_id'", (str(uuid.uuid4()),))
        with self.assertRaises(CharacterStorageError):
            self.ready()
        self.change_database("UPDATE database_meta SET value=? WHERE key='storage_character_id'", (self.character.character_id,))
        self.paths["conversation"].unlink()
        with self.assertRaises(CharacterStorageError):
            self.ready()
        self.assertFalse(self.paths["conversation"].exists())
        self.assertEqual("local", self.registry.get(self.character.character_id).storage_layout)

    def test_local_database_rejects_an_additional_character_owner(self):
        store = MemoryV2Store(str(self.paths["memory_v2"]))
        try:
            store.create_character(str(uuid.uuid4()), "Other synthetic owner")
        finally:
            store.close()
        with self.assertRaisesRegex(CharacterStorageError, "ownership"):
            self.ready()

    def test_profile_read_bound_fails_without_rewriting_the_profile(self):
        content = b" " * (2 * 1024 * 1024 + 1)
        self.paths["character"].write_bytes(content)
        with self.assertRaisesRegex(CharacterStorageError, "read bound"):
            self.ready()
        self.assertEqual(len(content), self.paths["character"].stat().st_size)


if __name__ == "__main__":
    unittest.main()
