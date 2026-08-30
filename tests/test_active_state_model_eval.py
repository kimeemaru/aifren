import unittest

from benchmarks.active_state.model_eval import cases, run_preflight


class ActiveStateModelEvalTests(unittest.TestCase):
    def test_real_model_matrix_is_bounded_and_deterministic_pipeline_preflight_passes(self):
        self.assertGreaterEqual(len(cases()), 30)
        self.assertLessEqual(len(cases()), 50)
        results = run_preflight()
        self.assertEqual(len(results), len(cases()))
        self.assertEqual([item for item in results if not item.passed], [])
        self.assertLessEqual(max(item.context_chars for item in results), 2_100)


if __name__ == "__main__":
    unittest.main()
