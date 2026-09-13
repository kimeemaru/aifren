import json
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest import mock
import uuid

from aifren.character.character_registry import CharacterRegistry
from aifren.runtime.development_staged_runtime import development_staged_environment
from aifren.continuity.memory_v2_historical_evidence import (
    STAGED_DISPOSABLE_MARKER,
    validate_staged_disposable_target,
)
from aifren.continuity.memory_v2_shadow_writer import default_v2_path
from aifren.continuity.memory_v2_staged_clone import (
    StagedCloneError,
    create_memory_v2_staged_clone,
)
from aifren.memory_v2_store.production_import import v1_import_scope
from aifren.memory_v2_store.store import MemoryV2Store


class MemoryV2StagedCloneTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.source = self.workspace / "source"
        self.target = self.workspace / "target"
        self.source.mkdir()
        self.selected_id = str(uuid.uuid4())
        self.foreign_id = str(uuid.uuid4())
        self._write_source_application()
        self._write_source_store()

    def tearDown(self):
        self.temporary.cleanup()

    def _character_entry(self, character_id, name):
        return {
            "character_id": character_id,
            "display_name": name,
            "config_directory": f"characters/{character_id}",
            "created_at": "2026-01-01T00:00:00+00:00",
            "legacy_default": False,
        }

    def _write_source_application(self):
        registry = {
            "version": 1,
            "active_character_id": self.foreign_id,
            "characters": [
                self._character_entry(self.selected_id, "Selected synthetic"),
                self._character_entry(self.foreign_id, "Foreign synthetic"),
            ],
        }
        registry_path = self.source / "characters" / "registry.json"
        registry_path.parent.mkdir(parents=True)
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        for character_id, sentinel in (
            (self.selected_id, "selected-sentinel"),
            (self.foreign_id, "foreign-sentinel"),
        ):
            directory = self.source / "characters" / character_id
            directory.mkdir()
            (directory / "character.json").write_text(
                json.dumps({"name": sentinel, "voice": {"provider": None}}),
                encoding="utf-8",
            )
            (directory / "personality.md").write_text(
                f"Synthetic personality {sentinel}.\n", encoding="utf-8",
            )
            (directory / "memories.json").write_text(
                json.dumps([{"id": 1, "content": sentinel}]), encoding="utf-8",
            )
            (directory / "conversation.json").write_text(
                json.dumps([
                    {
                        "role": "user",
                        "content": f"Canonical {sentinel}",
                        "timestamp": "2026-01-01T00:00:00+00:00",
                        "truth_scope": {"kind": "real_world", "scope_id": "synthetic"},
                    },
                    {
                        "role": "assistant",
                        "content": "Synthetic reply.",
                        "timestamp": "2026-01-01T00:00:01+00:00",
                        "truth_scope": {"kind": "real_world", "scope_id": "synthetic"},
                    },
                ]),
                encoding="utf-8",
            )
            (directory / "conversation_summary.json").write_text(
                json.dumps({"summary": sentinel}), encoding="utf-8",
            )

    def _write_source_store(self):
        path = default_v2_path(self.source)
        path.parent.mkdir()
        store = MemoryV2Store(str(path))
        try:
            for offset, (character_id, sentinel) in enumerate((
                (self.selected_id, "selected-sentinel"),
                (self.foreign_id, "foreign-sentinel"),
            ), start=1):
                store.create_character(character_id, sentinel, created_at_us=offset)
                event_id = f"event-{sentinel}"
                claim_id = f"claim-{sentinel}"
                store.add_event(
                    character_id, event_id, 1,
                    recorded_at_us=10 + offset,
                    content_text=f"Evidence {sentinel}",
                    source_origin="synthetic",
                    source_reference=f"conversation.json#{offset}",
                )
                store.add_claim(
                    character_id, claim_id,
                    claim_type="stable_user_fact",
                    assertion_scope="user_fact",
                    content=f"Claim {sentinel}",
                    created_at_us=20 + offset,
                )
                store.attach_evidence(
                    character_id, claim_id, event_id,
                    evidence_role="direct_user_statement",
                    created_at_us=30 + offset,
                )
                store.add_status(
                    character_id, claim_id, "active",
                    source_event_id=event_id,
                    created_at_us=40 + offset,
                )
                store.add_summary(
                    character_id, f"summary-{sentinel}", f"Episode {sentinel}",
                    source_count=1, provenance_state="complete",
                    generator_name="synthetic", generator_version="1",
                    created_at_us=50 + offset,
                )
                store.add_summary_source_range(
                    character_id, f"summary-{sentinel}", 1, 1,
                )
                store.connection.execute(
                    """INSERT INTO claim_embeddings(
                           character_id, claim_id, provider, model, model_version,
                           dimensions, dtype, normalized, preprocessing_fingerprint,
                           content_fingerprint, source_content_sha256, vector_blob,
                           generated_at_us, state, failure_reason)
                         VALUES (?, ?, 'synthetic', 'tiny', '1', 1, 'float32', 1,
                                 'prep', ?, ?, ?, ?, 'current', NULL)""",
                    (character_id, claim_id, f"content-{sentinel}",
                     f"source-{sentinel}", b"\x00\x00\x80?", 60 + offset),
                )
                store.connection.execute(
                    """INSERT INTO v1_import_records(
                           source_scope, legacy_memory_id, claim_id,
                           content_sha256, imported_at_us)
                         VALUES (?, 1, ?, ?, ?)""",
                    (v1_import_scope(character_id), claim_id,
                     f"legacy-{sentinel}", 70 + offset),
                )
            store.ensure_fts()
        finally:
            store.close()

    def _open_target(self):
        report = create_memory_v2_staged_clone(
            self.source, self.target, self.selected_id, batch_rows=1,
        )
        connection = sqlite3.connect(report.target_database)
        connection.row_factory = sqlite3.Row
        self.addCleanup(connection.close)
        return report, connection

    def test_clone_is_character_filtered_and_marker_bound(self):
        source_database = default_v2_path(self.source)
        source_sidecars_before = {
            suffix: Path(f"{source_database}{suffix}").exists()
            for suffix in ("-wal", "-shm", "-journal")
        }
        report, connection = self._open_target()
        self.assertTrue(report.marker_path.is_file())
        registry = CharacterRegistry(self.target)
        self.assertEqual(
            [character.character_id for character in registry.list_characters()],
            [self.selected_id],
        )
        self.assertEqual(registry.active().character_id, self.selected_id)
        target_paths = registry.runtime_paths(self.selected_id)
        self.assertIn("selected-sentinel", target_paths["conversation"].read_text(encoding="utf-8"))
        self.assertFalse((self.target / "characters" / self.foreign_id).exists())

        characters = connection.execute(
            "SELECT character_id FROM characters",
        ).fetchall()
        self.assertEqual([row[0] for row in characters], [self.selected_id])
        claims = connection.execute(
            "SELECT character_id, content FROM claims",
        ).fetchall()
        self.assertEqual(
            [(row["character_id"], row["content"]) for row in claims],
            [(self.selected_id, "Claim selected-sentinel")],
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM claim_embeddings").fetchone()[0], 1,
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM summaries").fetchone()[0], 1,
        )
        self.assertEqual(
            connection.execute("SELECT source_scope FROM v1_import_records").fetchone()[0],
            v1_import_scope(self.selected_id),
        )
        validate_staged_disposable_target(
            self.target, self.selected_id, report.target_database,
            target_paths["conversation"],
        )
        self.assertEqual(
            source_sidecars_before,
            {
                suffix: Path(f"{source_database}{suffix}").exists()
                for suffix in ("-wal", "-shm", "-journal")
            },
        )

    def test_source_state_regression_environment_keeps_all_clone_guards(self):
        report, _connection = self._open_target()
        with development_staged_environment(
            self.target,
            self.selected_id,
            resource_root=self.source,
            require_rebuilt_history=False,
        ) as runtime:
            self.assertEqual(report.target_database, runtime.database_path)
            self.assertEqual(self.selected_id, runtime.character_id)
            self.assertEqual(0, runtime.historical_evidence_count)
            self.assertEqual(0, runtime.validated_episode_count)

    def test_clone_rebuilds_fts_and_passes_integrity(self):
        report, connection = self._open_target()
        self.assertEqual(report.fts_row_count, 1)
        fts = connection.execute(
            "SELECT character_id, claim_id FROM claims_fts WHERE claims_fts MATCH 'selected'",
        ).fetchall()
        self.assertEqual(
            [(row["character_id"], row["claim_id"]) for row in fts],
            [(self.selected_id, "claim-selected-sentinel")],
        )
        self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
        self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_exact_schema_20_source_clones_without_source_migration(self):
        source = sqlite3.connect(default_v2_path(self.source))
        source.execute("DROP TABLE canonical_observation_dispositions")
        source.execute("DELETE FROM schema_migrations WHERE version=21")
        source.execute("UPDATE database_meta SET value='20' WHERE key='schema_version'")
        source.commit()
        source.close()
        report, connection = self._open_target()
        self.assertEqual(21, report.schema_version)
        self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM canonical_observation_dispositions").fetchone()[0])
        source = sqlite3.connect(default_v2_path(self.source))
        self.assertEqual('20', source.execute("SELECT value FROM database_meta WHERE key='schema_version'").fetchone()[0])
        source.close()

    def test_exact_schema_17_source_clones_without_source_migration(self):
        source = sqlite3.connect(default_v2_path(self.source))
        try:
            source.execute("DROP INDEX active_scene_relation_current_identity")
            source.execute("ALTER TABLE active_scene_relations DROP COLUMN locus")
            source.execute("""CREATE UNIQUE INDEX active_scene_relation_current_identity ON active_scene_relations(
                character_id,truth_scope_id,target_kind,target_actor,COALESCE(facet,''),COALESCE(side,''),
                predicate,cause_kind,cause,COALESCE(cause_subject_id,'')) WHERE valid_to_us IS NULL""")
            source.execute("DROP TABLE canonical_observation_progress")
            source.execute("DROP TABLE canonical_observation_dispositions")
            source.execute("DROP TABLE historical_evidence_fts")
            source.execute("DROP TABLE historical_evidence_checkpoints")
            source.execute("DROP TABLE historical_evidence")
            source.execute("DELETE FROM schema_migrations WHERE version>=18")
            source.execute(
                "UPDATE database_meta SET value='17' WHERE key='schema_version'",
            )
            source.commit()
        finally:
            source.close()

        report, connection = self._open_target()

        self.assertEqual(21, report.schema_version)
        source = sqlite3.connect(default_v2_path(self.source))
        try:
            source_version = source.execute(
                "SELECT value FROM database_meta WHERE key='schema_version'",
            ).fetchone()[0]
        finally:
            source.close()
        self.assertEqual("17", source_version)
        self.assertEqual(
            "21",
            connection.execute(
                "SELECT value FROM database_meta WHERE key='schema_version'",
            ).fetchone()[0],
        )

    def test_clone_outputs_are_private(self):
        report, _connection = self._open_target()
        registry = CharacterRegistry(self.target)
        paths = registry.runtime_paths(self.selected_id)
        private_directories = {
            self.target,
            self.target / "characters",
            self.target / "characters" / self.selected_id,
            report.target_database.parent,
        }
        for path in private_directories:
            self.assertEqual(0o700, stat.S_IMODE(path.stat().st_mode), path)
        private_files = {
            report.marker_path,
            report.target_database,
            self.target / "characters" / "registry.json",
            paths["character"], paths["personality"], paths["memory"],
            paths["conversation"], paths["summary"],
        }
        for path in private_files:
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode), path)

    def test_clone_resets_telemetry_and_evidence_checkpoint(self):
        source = sqlite3.connect(default_v2_path(self.source))
        try:
            source.execute(
                """INSERT INTO retrieval_telemetry(
                       recorded_at_us, character_id, query_sha256, v1_ids_json,
                       v2_ids_json, overlap_count, v1_abstained, v2_abstained,
                       retrieval_strategy)
                     VALUES (1, ?, 'digest', '[]', '[]', 0, 1, 1, 'synthetic')""",
                (self.selected_id,),
            )
            source.execute(
                """INSERT INTO historical_evidence_checkpoints(
                       character_id, policy_version, source_key, next_index,
                       source_prefix_digest, source_archive_digest,
                       source_record_count, state, updated_at_us)
                     VALUES (?, 'old', 'conversation.json', 2, 'prefix', 'archive',
                             2, 'complete', 1)""",
                (self.selected_id,),
            )
            source.commit()
        finally:
            source.close()
        _, connection = self._open_target()
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM retrieval_telemetry").fetchone()[0], 0)
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM historical_evidence_checkpoints").fetchone()[0], 0,
        )

    def test_prepared_seed_clone_preserves_only_completed_evidence_checkpoint(self):
        first = create_memory_v2_staged_clone(
            self.source, self.target, self.selected_id,
        )
        connection = sqlite3.connect(first.target_database)
        try:
            connection.execute(
                """INSERT INTO historical_evidence_checkpoints(
                       character_id, policy_version, source_key, next_index,
                       source_prefix_digest, source_archive_digest,
                       source_record_count, state, updated_at_us)
                     VALUES (?, 'current', 'conversation.json', 2, 'prefix',
                             'archive', 2, 'complete', 1)""",
                (self.selected_id,),
            )
            connection.commit()
        finally:
            connection.close()

        prepared_target = self.workspace / "prepared-target"
        second = create_memory_v2_staged_clone(
            self.target,
            prepared_target,
            self.selected_id,
            source_database_path=first.target_database,
            preserve_completed_historical_reconstruction=True,
        )
        connection = sqlite3.connect(second.target_database)
        try:
            row = connection.execute(
                """SELECT state, source_record_count, next_index
                     FROM historical_evidence_checkpoints""",
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(("complete", 2, 2), row)

    def test_checkpoint_preservation_requires_an_attested_staged_source(self):
        with self.assertRaisesRegex(StagedCloneError, "attested staged source"):
            create_memory_v2_staged_clone(
                self.source,
                self.target,
                self.selected_id,
                preserve_completed_historical_reconstruction=True,
            )

    def test_unknown_source_table_refuses_without_marker(self):
        source = sqlite3.connect(default_v2_path(self.source))
        try:
            source.execute(
                "CREATE TABLE unexpected_character_state(character_id TEXT, value TEXT)",
            )
            source.commit()
        finally:
            source.close()
        with self.assertRaisesRegex(StagedCloneError, "unknown or missing table"):
            create_memory_v2_staged_clone(self.source, self.target, self.selected_id)
        self.assertFalse((self.target / STAGED_DISPOSABLE_MARKER).exists())

    def test_noncanonical_character_directory_is_rejected_before_copy(self):
        registry_path = self.source / "characters" / "registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        selected = next(
            value for value in registry["characters"]
            if value["character_id"] == self.selected_id
        )
        original = selected["config_directory"]
        for unsafe in (
            str((self.source / original).resolve()),
            f"characters/../characters/{self.selected_id}",
        ):
            with self.subTest(config_directory=unsafe):
                selected["config_directory"] = unsafe
                registry_path.write_text(json.dumps(registry), encoding="utf-8")
                with self.assertRaisesRegex(StagedCloneError, "non-canonical config"):
                    create_memory_v2_staged_clone(
                        self.source, self.target, self.selected_id,
                    )
                self.assertFalse(self.target.exists())
        selected["config_directory"] = original
        registry_path.write_text(json.dumps(registry), encoding="utf-8")

    def test_invalid_database_name_and_bounded_copy_leave_no_marker(self):
        with self.assertRaisesRegex(StagedCloneError, "one SQLite filename"):
            create_memory_v2_staged_clone(
                self.source, self.target, self.selected_id, database_name=".sqlite3",
            )
        self.assertFalse(self.target.exists())

        with mock.patch('aifren.continuity.memory_v2_staged_clone.MAX_SELECTED_ROWS', 0):
            with self.assertRaisesRegex(StagedCloneError, "row bound"):
                create_memory_v2_staged_clone(self.source, self.target, self.selected_id)
        self.assertFalse((self.target / STAGED_DISPOSABLE_MARKER).exists())

    def test_final_validation_failure_removes_marker(self):
        with mock.patch(
            'aifren.continuity.memory_v2_staged_clone.validate_staged_disposable_target',
            side_effect=StagedCloneError("synthetic final validation failure"),
        ):
            with self.assertRaisesRegex(StagedCloneError, "synthetic final"):
                create_memory_v2_staged_clone(self.source, self.target, self.selected_id)
        self.assertFalse((self.target / STAGED_DISPOSABLE_MARKER).exists())

    def test_precondition_failure_never_writes_marker(self):
        source = sqlite3.connect(default_v2_path(self.source))
        try:
            source.execute(
                "UPDATE characters SET active_truth_scope_id='missing' WHERE character_id=?",
                (self.selected_id,),
            )
            source.commit()
        finally:
            source.close()
        with self.assertRaisesRegex(StagedCloneError, "active real-world"):
            create_memory_v2_staged_clone(self.source, self.target, self.selected_id)
        self.assertFalse((self.target / STAGED_DISPOSABLE_MARKER).exists())


if __name__ == "__main__":
    unittest.main()
