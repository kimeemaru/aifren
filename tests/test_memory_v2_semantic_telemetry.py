import tempfile
import unittest

from tools.memory_v2.memory_v2_semantic_evaluation import run_semantic_evaluation
from aifren.memory_v2_store import MemoryV2Repository, MemoryV2Store
from aifren.continuity.memory_v2_telemetry import MAX_TELEMETRY_ROWS, record_dual_read, retrieval_report


class MemoryV2SemanticTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MemoryV2Store(f"{self.temp.name}/memory.sqlite3")
        MemoryV2Repository(self.store).ensure_character(
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "Telemetry"
        )

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_telemetry_is_bounded_and_never_persists_raw_query_text(self):
        character_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        for index in range(MAX_TELEMETRY_ROWS + 3):
            record_dual_read(
                self.store,
                character_id=character_id,
                query=f"private query {index}",
                v1_ids=["legacy-1"],
                v2_ids=["claim-1"],
                comparison_v1_ids=["claim-1"],
                v1_latency_ms=2.0,
                v2_latency_ms=3.0,
            )
        rows = self.store.connection.execute("SELECT query_sha256, v1_ids_json FROM retrieval_telemetry").fetchall()
        self.assertEqual(MAX_TELEMETRY_ROWS, len(rows))
        self.assertTrue(all("private query" not in row["query_sha256"] for row in rows))
        self.assertTrue(all(row["v1_ids_json"] == '["legacy-1"]' for row in rows))
        report = retrieval_report(self.store)
        self.assertEqual(MAX_TELEMETRY_ROWS, report["total_compared"])
        self.assertEqual(1.0, report["overlap_rate"])
        self.assertEqual(0.0, report["v2_error_rate"])

    def test_semantic_fixture_enforces_lifecycle_scope_and_abstention(self):
        report = run_semantic_evaluation(scale=8)
        semantic = report.semantic_v2
        self.assertEqual(0, report.character_scope_leaks)
        self.assertEqual(0, report.duplicate_active_claims)
        self.assertEqual(0, report.lifecycle["superseded"])
        self.assertEqual(0, report.lifecycle["archived"])
        self.assertEqual(0.0, semantic["abstention"].abstention_false_positive_rate)
        self.assertEqual(1.0, semantic["corrections"].top5_recall)
        self.assertEqual(1.0, semantic["character_isolation"].top5_recall)


if __name__ == "__main__":
    unittest.main()
