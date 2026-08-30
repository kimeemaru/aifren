import unittest

from benchmarks.active_state.harness import (
    curated_cases,
    long_context_cases,
    run_curated_matrix,
    run_long_context_matrix,
    run_randomized_state_machine,
)


class ActiveStateSyntheticQATests(unittest.TestCase):
    def test_curated_matrix_has_480_sequences_and_thousands_of_assertions(self):
        self.assertEqual(len(curated_cases()), 480)
        report = run_curated_matrix()
        self.assertEqual(report.failed, 0)
        self.assertEqual(report.passed, 480)
        self.assertGreaterEqual(report.checks, 5_000)
        self.assertLessEqual(report.max_context_chars, 2_100)

    def test_randomized_state_machine_smoke_is_deterministic(self):
        first = run_randomized_state_machine(seed_count=5, operations_per_seed=20)
        second = run_randomized_state_machine(seed_count=5, operations_per_seed=20)
        self.assertEqual(first.failed, 0)
        self.assertEqual(second.failed, 0)
        self.assertEqual(
            [(item.case_id, item.passed, item.checks, item.turns) for item in first.results],
            [(item.case_id, item.passed, item.checks, item.turns) for item in second.results],
        )

    def test_randomized_seed_ranges_are_disjoint_and_reproducible(self):
        first = run_randomized_state_machine(
            seed_start=7, seed_count=2, operations_per_seed=5,
        )
        second = run_randomized_state_machine(
            seed_start=7, seed_count=2, operations_per_seed=5,
        )
        self.assertEqual(
            ["random-seed-007", "random-seed-008"],
            [item.case_id for item in first.results],
        )
        self.assertEqual(first.checks, second.checks)
        self.assertEqual(first.failed, second.failed)

    def test_long_context_matrix_covers_required_50_to_1000_turn_gaps(self):
        gaps = [item.gap_turns for item in long_context_cases()]
        self.assertTrue({50, 100, 250, 500, 1000}.issubset(gaps))
        report = run_long_context_matrix()
        self.assertEqual(report.failed, 0)
        self.assertEqual(report.passed, len(long_context_cases()))
        self.assertLessEqual(report.max_context_chars, 2_100)


if __name__ == "__main__":
    unittest.main()
