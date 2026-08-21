import os
import sqlite3
import tempfile
import unittest
import uuid

from benchmarks.memory_v2.adapters import MemoryV2StructuralAdapter
from benchmarks.memory_v2.fixtures import build_core_fixture, structural_baseline_cases
from benchmarks.memory_v2.harness import run_retrieval_benchmark
from memory_v2_store import MemoryV2Store, StoreError


class MemoryV2StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryV2Store()
        self.character_a = str(uuid.uuid4())
        self.character_b = str(uuid.uuid4())
        self.store.create_character(self.character_a, "A")
        self.store.create_character(self.character_b, "B")

    def tearDown(self):
        self.store.close()

    def event(self, character_id, event_id="event-1", sequence=1, content="The user likes tea.", **kwargs):
        self.store.add_event(character_id, event_id, sequence, content_text=content, recorded_at_us=1_000, **kwargs)

    def claim(self, character_id, claim_id="claim-1", **kwargs):
        self.store.add_claim(character_id, claim_id, claim_type="fact", assertion_scope="user_fact", content="The user likes tea.", created_at_us=1_000, **kwargs)

    def test_schema_pragmas_version_and_integrity(self):
        self.assertEqual(self.store.schema_version(), 4)
        self.assertEqual(self.store.pragma("foreign_keys"), 1)
        self.assertEqual(self.store.pragma("synchronous"), 2)
        self.assertEqual(self.store.integrity_check(), "ok")

    def test_append_events_and_duplicate_sequence_are_rejected(self):
        self.event(self.character_a)
        with self.assertRaises(sqlite3.IntegrityError):
            self.event(self.character_a, "event-2", 1)
        self.event(self.character_b, "event-2", 1)

    def test_complete_provenance_and_cross_character_evidence_rejection(self):
        self.event(self.character_a)
        self.event(self.character_b, "event-b")
        self.claim(self.character_a)
        self.store.attach_evidence(self.character_a, "claim-1", "event-1", excerpt_start_cp=0, excerpt_end_cp=8)
        self.assertEqual(len(self.store.structural_claims(self.character_a, 2_000)), 1)
        with self.assertRaises(StoreError):
            self.store.attach_evidence(self.character_a, "claim-1", "event-b")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.connection.execute(
                "INSERT INTO claim_evidence VALUES (?, ?, ?, 'direct_user_statement', NULL, NULL, NULL, 1.0, NULL, 1)",
                (self.character_a, "claim-1", "event-b"),
            )

    def test_cross_character_relation_is_rejected_by_composite_foreign_key(self):
        self.event(self.character_a)
        self.event(self.character_b, "event-b")
        self.claim(self.character_a, "claim-a")
        self.claim(self.character_b, "claim-b")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.add_relation(self.character_a, "claim-a", "claim-b")

    def test_status_history_and_current_vs_historical_filtering(self):
        self.event(self.character_a)
        self.claim(self.character_a, valid_from_us=1_000, valid_to_us=2_000)
        self.store.attach_evidence(self.character_a, "claim-1", "event-1")
        self.store.add_status(self.character_a, "claim-1", "superseded", created_at_us=2_000)
        self.assertEqual(self.store.effective_status(self.character_a, "claim-1"), "superseded")
        self.assertEqual(self.store.effective_status(self.character_a, "claim-1", at_us=1_500), "active")
        self.assertEqual(self.store.structural_claims(self.character_a, 1_500, historical=True)[0]["claim_id"], "claim-1")
        self.assertEqual(self.store.structural_claims(self.character_a, 3_000), [])

    def test_transaction_rolls_back_on_failure(self):
        with self.assertRaises(RuntimeError):
            with self.store.transaction():
                self.store.connection.execute("INSERT INTO characters VALUES (?, ?, ?, NULL, NULL, '{}')", (str(uuid.uuid4()), "rolled back", 1))
                raise RuntimeError("stop")
        count = self.store.connection.execute("SELECT COUNT(*) FROM characters WHERE display_name = 'rolled back'").fetchone()[0]
        self.assertEqual(count, 0)

    def test_file_database_reopens_and_uses_wal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "shadow.sqlite3")
            store = MemoryV2Store(path)
            character = str(uuid.uuid4())
            store.create_character(character, "Persisted")
            self.assertEqual(str(store.pragma("journal_mode")).lower(), "wal")
            store.close()
            reopened = MemoryV2Store(path)
            self.assertEqual(reopened.connection.execute("SELECT COUNT(*) FROM characters").fetchone()[0], 1)
            self.assertEqual(reopened.integrity_check(), "ok")
            reopened.close()

    def test_v2_structural_adapter_meets_fixture_integrity_gates(self):
        fixture = build_core_fixture()
        report, _ = run_retrieval_benchmark(
            fixture, MemoryV2StructuralAdapter(fixture),
            cases=structural_baseline_cases(fixture),
        )
        self.assertEqual(report.character_isolation_violation_rate, 0.0)
        self.assertEqual(report.provenance_completeness, 1.0)
        self.assertEqual(report.temporal_correctness, 1.0)
        self.assertEqual(report.forbidden_retrieval_rate, 0.0)
        self.assertEqual(report.negative_false_positive_rate, 0.0)


if __name__ == "__main__":
    unittest.main()
