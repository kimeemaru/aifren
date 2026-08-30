import unittest

from benchmarks.current_continuity.harness import load_manifest, run


class CurrentContinuityHarnessTests(unittest.TestCase):
    def test_manifest_is_fixed_and_all_cases_pass(self):
        self.assertEqual(30, len(load_manifest()["cases"]))
        report = run()
        self.assertEqual(30, report.case_count)
        self.assertEqual(30, report.passed, [item for item in report.cases if not item.passed])
        self.assertEqual(0, report.failed)
        self.assertGreaterEqual(report.checks, 200)


if __name__ == "__main__":
    unittest.main()
