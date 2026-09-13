"""Disposable QA follows registered local paths without enabling shared fallback."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from character_registry import CharacterRegistry
from development_staged_runtime import (
    DevelopmentStagedRuntimeError, _runtime_history_attested, development_staged_environment,
)
from memory_v2_historical_evidence import (
    HistoricalEvidenceError, HistoricalEvidenceIndexer, STAGED_DATABASE_DIRECTORY,
    STAGED_DISPOSABLE_MARKER, open_staged_historical_evidence_writer,
    staged_disposable_marker_payload, validate_staged_disposable_target,
)
from memory_v2_shadow_writer import MemoryV2ShadowWriter, default_v2_path
from memory_v2_staged_clone import StagedCloneError, create_memory_v2_staged_clone
from memory_v2_store.store import MemoryV2Store


class StagedLocalCloneTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source, self.target = self.root / "source", self.root / "target"
        self.registry = CharacterRegistry(self.source)
        self.character = self.registry.create("Synthetic Avery")
        self.cid = self.character.character_id
        self.paths = self.registry.runtime_paths(self.cid)
        self.namespace = f"characters/{self.cid}/conversation.json"
        with self.registry.locked():
            self.registry._entry(self.cid)["source_namespace"] = self.namespace
            self.registry._save()
        store = MemoryV2Store(str(self.paths["memory_v2"]))
        try:
            scope = store.default_truth_scope_id(self.cid)
            store.add_event(self.cid, "local-event", 1, content_text="We discussed a synthetic model railway.",
                            source_origin="canonical_conversation", source_reference=self.namespace + "#0", recorded_at_us=1)
            store.add_claim(self.cid, "local-claim", claim_type="conversation_event", assertion_scope="shared_event",
                            content="Synthetic local context", created_at_us=1)
            store.attach_evidence(self.cid, "local-claim", "local-event", created_at_us=1)
            store.ensure_fts()
        finally:
            store.close()
        self.paths["conversation"].write_text(json.dumps([
            {"role": "user", "content": "We discussed a synthetic model railway.",
             "timestamp": "2026-01-02T12:00:00+00:00", "truth_scope": {"kind": "real_world", "scope_id": scope}},
            {"role": "assistant", "content": "A model railway sounds interesting.",
             "timestamp": "2026-01-02T12:00:01+00:00", "truth_scope": {"kind": "real_world", "scope_id": scope}},
        ]))

    def clone(self):
        return create_memory_v2_staged_clone(self.source, self.target, self.cid)

    def test_registered_readable_local_path_and_namespace_survive_clone(self):
        self.assertIn("--", self.paths["directory"].name)
        original = self.paths["conversation"].read_bytes()
        report = self.clone()
        registry = CharacterRegistry(self.target)
        paths = registry.runtime_paths(self.cid)
        self.assertEqual("local", registry.get(self.cid).storage_layout)
        self.assertEqual(self.namespace, registry.canonical_namespace(self.cid))
        self.assertEqual(paths["memory_v2"], report.target_database)
        self.assertEqual(original, paths["conversation"].read_bytes())
        self.assertEqual(self.paths["directory"].name, paths["directory"].name)
        self.assertEqual(paths, registry.assert_storage_ready(self.cid))
        self.assertFalse(default_v2_path(self.target).exists())
        with closing(sqlite3.connect(report.target_database)) as connection:
            self.assertEqual(self.namespace + "#0", connection.execute(
                "SELECT source_reference FROM events WHERE event_id='local-event'").fetchone()[0])
        with development_staged_environment(self.target, self.cid, require_rebuilt_history=False) as attested:
            self.assertEqual(report.target_database, attested.database_path)
            writer = MemoryV2ShadowWriter(self.target, character_id=self.cid, display_name="Synthetic Avery",
                                         memory_file=paths["memory"])
            try:
                self.assertEqual(report.target_database, writer.database_path)
                self.assertEqual("local", writer.storage_layout)
                self.assertEqual(self.namespace, writer.source_namespace)
                self.assertEqual("local_character_no_implicit_legacy_import", writer.reconcile()["reason"])
            finally:
                writer.close()
        writer = open_staged_historical_evidence_writer(self.target, self.cid, report.target_database)
        try:
            indexer = HistoricalEvidenceIndexer(writer, paths["conversation"], confirm_staged_disposable=True)
            result = indexer.run_page()
            self.assertEqual("complete", result.state)
        finally:
            writer.close()
        with patch("development_staged_runtime._runtime_history_attested", wraps=_runtime_history_attested) as check:
            with development_staged_environment(self.target, self.cid, require_rebuilt_history=False):
                pass
        self.assertEqual(Path(self.namespace), check.call_args.kwargs["relative_path"])

    def test_missing_local_source_does_not_read_an_existing_shared_decoy(self):
        self.paths["memory_v2"].unlink()
        shared = default_v2_path(self.source)
        shared.parent.mkdir(exist_ok=True)
        store = MemoryV2Store(str(shared))
        try:
            store.create_character(self.cid, "Decoy shared owner")
        finally:
            store.close()
        with self.assertRaises(StagedCloneError):
            self.clone()
        self.assertFalse((self.target / STAGED_DISPOSABLE_MARKER).exists())

    def test_wrong_local_generation_never_receives_a_marker(self):
        with closing(sqlite3.connect(self.paths["memory_v2"])) as connection:
            with connection:
                connection.execute("UPDATE database_meta SET value='wrong' WHERE key='timeline_generation'")
        with self.assertRaises(StagedCloneError):
            self.clone()
        self.assertFalse((self.target / STAGED_DISPOSABLE_MARKER).exists())

    def test_local_database_is_not_qa_writable_without_the_exact_marker(self):
        with self.assertRaises(HistoricalEvidenceError):
            validate_staged_disposable_target(self.source, self.cid, self.paths["memory_v2"], self.paths["conversation"])
        report = self.clone()
        paths = CharacterRegistry(self.target).runtime_paths(self.cid)
        marker = self.target / STAGED_DISPOSABLE_MARKER
        value = json.loads(marker.read_text())
        value["character_id"] = str(uuid.uuid4())
        marker.write_text(json.dumps(value))
        with self.assertRaises(HistoricalEvidenceError):
            validate_staged_disposable_target(self.target, self.cid, report.target_database, paths["conversation"])

    def test_local_attestation_rejects_wrong_timeline_and_alternate_database(self):
        report = self.clone()
        paths = CharacterRegistry(self.target).runtime_paths(self.cid)
        with closing(sqlite3.connect(report.target_database)) as connection:
            with connection:
                connection.execute("UPDATE database_meta SET value='different-generation' WHERE key='timeline_generation'")
        with self.assertRaises(DevelopmentStagedRuntimeError):
            with development_staged_environment(self.target, self.cid, require_rebuilt_history=False):
                pass
        alternate = self.target / STAGED_DATABASE_DIRECTORY / "alternate.sqlite3"
        alternate.parent.mkdir(parents=True)
        alternate.touch()
        (self.target / STAGED_DISPOSABLE_MARKER).write_text(json.dumps(
            staged_disposable_marker_payload(self.target, self.cid, alternate, paths["conversation"])))
        with self.assertRaisesRegex(HistoricalEvidenceError, "registered database"):
            validate_staged_disposable_target(self.target, self.cid, alternate, paths["conversation"])

    def test_governed_source_gate_is_retained_for_local_layout(self):
        store = MemoryV2Store(str(self.paths["memory_v2"]))
        try:
            store.add_durable_claim(self.cid, "governed", subject_key="preference.color", content="blue",
                                    evidence_event_id="local-event", evidence_role="direct_user_statement",
                                    created_at_us=1, updated_at_us=1)
        finally:
            store.close()
        with self.assertRaisesRegex(StagedCloneError, "governed durable facts"):
            self.clone()
        self.assertFalse((self.target / STAGED_DISPOSABLE_MARKER).exists())


if __name__ == "__main__":
    unittest.main()
