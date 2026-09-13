from pathlib import Path
import tempfile
import unittest

from tools.memory_v2.memory_v2_lifetime_benchmark import run_lifetime_benchmark
from aifren.memory_v2_store import MemoryV2Store


class LifetimeBenchmarkHarnessTests(unittest.TestCase):
    def test_disk_backed_streaming_harness_preserves_compact_ground_truth(self):
        with tempfile.TemporaryDirectory() as root:
            report = run_lifetime_benchmark(scale=96, seed=7, batch_size=16, root=root)
            # The report deliberately exposes metrics/measurements, not a
            # retained generated corpus or claim-content collection.
            self.assertFalse(hasattr(report, "claims"))
            self.assertGreater(report.database_bytes, 0)
            self.assertGreater(report.index_bytes, 0)
            self.assertEqual(16, report.max_live_batch_items)
            self.assertEqual(0, report.character_scope_leaks)
            self.assertEqual(0, report.metrics["corrections"].forbidden_leaks)
            self.assertFalse((Path(root) / "lifetime.sqlite3").exists() is False)
            # The bulk path uses the same authoritative tables as ordinary
            # ingestion: every generated claim has direct evidence and a
            # current persisted derived embedding.
            store = MemoryV2Store(str(Path(root) / "lifetime.sqlite3"))
            try:
                claims = store.connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
                evidence = store.connection.execute("SELECT COUNT(*) FROM claim_evidence").fetchone()[0]
                vectors = store.connection.execute("SELECT COUNT(*) FROM claim_embeddings WHERE state='current'").fetchone()[0]
                self.assertEqual(claims, evidence)
                self.assertEqual(claims, vectors)
                self.assertEqual("ok", store.integrity_check())
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
