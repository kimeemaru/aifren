import unittest

from benchmarks.active_state.performance import run


class ActiveStatePerformanceTests(unittest.TestCase):
    def test_large_scene_stays_bounded_and_validation_remains_available(self):
        report = run()
        self.assertGreaterEqual(report.scene_subject_count, 20)
        self.assertGreaterEqual(report.relation_count, 20)
        self.assertLessEqual(report.context_chars, 2_100)
        self.assertLessEqual(report.hard_capability_chars, 1_100)
        self.assertLessEqual(report.query_requirement_chars, 1_000)
        self.assertFalse(report.internal_ids_present)
        self.assertTrue(report.response_validation_accepted)
        self.assertTrue(report.action_validation_accepted)
        self.assertGreater(report.database_bytes, 0)


if __name__ == "__main__":
    unittest.main()
