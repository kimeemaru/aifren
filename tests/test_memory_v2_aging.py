import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.memory_v2.adapters import GoldReferenceAdapter, SemanticRetrievalV2Adapter
from benchmarks.memory_v2.aging import (
    AgingCaseResult,
    AgingManifestError,
    MEMORY_CLASSES,
    build_aging_fixture,
    evaluate_aging_results,
    load_aging_manifest,
    run_aging_evaluation,
)
from benchmarks.memory_v2.models import RetrievalQuery
from memory_v2_store import retrieval as retrieval_module


class MemoryV2AgingFixtureTests(unittest.TestCase):
    def setUp(self):
        self.manifest = load_aging_manifest()
        # Mechanics tests deliberately use a compact generated archive; the
        # frozen manifest itself retains its 10k release-scale default.
        self.fixture = build_aging_fixture(self.manifest, unrelated_history_count=32)

    def gold_results(self):
        return {
            case.case_id: AgingCaseResult.from_ids(case.expected_claim_ids)
            for case in self.manifest.cases
        }

    def test_manifest_is_small_explicit_and_covers_all_release_classes(self):
        self.assertEqual(MEMORY_CLASSES, {case.memory_class for case in self.manifest.cases})
        self.assertEqual(10_000, self.manifest.unrelated_history_count)
        self.assertEqual(12, len(self.manifest.cases))
        self.assertEqual(len(self.manifest.records) + 32, len(self.fixture.benchmark_fixture.claims))
        durable = next(case for case in self.manifest.cases if case.case_id == "durable-name-direct-7y")
        self.assertEqual(7, durable.synthetic_age_years)
        self.assertFalse(durable.allows_abstention)
        self.assertTrue(durable.must_ignore_v1_importance)
        name = next(record for record in self.manifest.records if record.claim_id == "aging-name-elena")
        self.assertEqual(1, name.v1_importance)
        current = next(case for case in self.manifest.cases if case.case_id == "durable-correction-current")
        historical = next(case for case in self.manifest.cases if case.case_id == "durable-correction-historical")
        self.assertEqual(("aging-home-harbor",), current.expected_claim_ids)
        self.assertEqual(("aging-home-cedar",), current.forbidden_claim_ids)
        self.assertEqual("historical", historical.query_mode)
        self.assertEqual(("aging-home-cedar",), historical.expected_claim_ids)
        self.assertEqual(("aging-home-harbor",), historical.forbidden_claim_ids)
        self.assertEqual(8, sum(case.evaluation_kind == "hard" for case in self.manifest.cases))
        self.assertEqual(4, sum(case.evaluation_kind == "fuzzy" for case in self.manifest.cases))

    def test_manifest_validation_rejects_missing_class_coverage(self):
        payload = json.loads(Path("benchmarks/memory_v2/aging_manifest.json").read_text(encoding="utf-8"))
        payload["cases"] = [case for case in payload["cases"] if case["memory_class"] != "ambiguous_unknown_query"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad-aging-manifest.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(AgingManifestError):
                load_aging_manifest(path)

    def test_gold_reference_validates_frozen_recall_and_abstention_contracts(self):
        report = run_aging_evaluation(self.fixture, GoldReferenceAdapter())
        self.assertEqual(11, report.successful_recall)
        self.assertEqual(11, report.top_k_candidate_recall)
        self.assertEqual(1, report.true_abstention)
        self.assertEqual(0, report.false_abstention)
        self.assertEqual(0, report.important_recorded_false_abstention)
        self.assertEqual(0, report.hard_invariant_failures)
        self.assertFalse(report.owner_thresholds_configured)

    def test_direct_durable_abstention_is_hard_and_important_false_abstention(self):
        results = self.gold_results()
        results["durable-name-direct-7y"] = AgingCaseResult()
        report = evaluate_aging_results(self.fixture, results)
        assessment = report.case_assessments["durable-name-direct-7y"]
        self.assertTrue(assessment.false_abstention)
        self.assertTrue(assessment.important_recorded_false_abstention)
        self.assertTrue(assessment.hard_invariant_failure)
        self.assertEqual(1, report.important_recorded_false_abstention)

    def test_mundane_abstention_is_accounted_separately_and_allowed(self):
        results = self.gold_results()
        results["mundane-detail-vague-7y"] = AgingCaseResult()
        report = evaluate_aging_results(self.fixture, results)
        assessment = report.case_assessments["mundane-detail-vague-7y"]
        self.assertTrue(assessment.true_abstention)
        self.assertFalse(assessment.false_abstention)
        self.assertFalse(assessment.hard_invariant_failure)

    def test_character_correction_lifecycle_and_assistant_controls_are_hard(self):
        results = self.gold_results()
        results.update({
            "durable-name-direct-7y": AgingCaseResult.from_ids(("aging-b-name-bryn",)),
            "durable-correction-current": AgingCaseResult.from_ids(("aging-home-cedar",)),
            "ambiguous-unknown-control": AgingCaseResult.from_ids(("aging-assistant-invented",)),
        })
        report = evaluate_aging_results(self.fixture, results)
        self.assertEqual(1, report.wrong_character_leakage)
        self.assertEqual(1, report.lifecycle_current_history_violations)
        self.assertEqual(1, report.assistant_authority_violations)
        self.assertGreaterEqual(report.hard_invariant_failures, 3)
        self.assertEqual(3, report.incorrect_selection)


class MemoryV2AgingSyntheticClockTests(unittest.TestCase):
    def setUp(self):
        self.fixture = build_aging_fixture(unrelated_history_count=32)

    def test_fixture_clock_makes_recent_case_eligible_only_for_opted_in_adapter(self):
        recent = next(
            case.retrieval_case for case in self.fixture.manifest.cases
            if case.case_id == "recent-session-continuity"
        )
        adapter = SemanticRetrievalV2Adapter(
            self.fixture.benchmark_fixture, use_fixture_query_clock=True,
        )
        try:
            outcome = adapter.retrieve_outcome(self.fixture.benchmark_fixture, recent)
        finally:
            adapter.close()
        self.assertEqual(("aging-recent-lab-plan",), outcome.claim_ids)

    def test_fixture_clock_patch_is_scoped_and_default_clock_remains_real_time(self):
        ordinary_query = RetrievalQuery("aging-a", "What is my name?", "2000-01-01T00:00:00Z")
        before = retrieval_module.utc_now_us()
        historical, default_at = retrieval_module._historical_time(ordinary_query)
        self.assertFalse(historical)
        self.assertGreaterEqual(default_at, before)

        recent = next(
            case.retrieval_case for case in self.fixture.manifest.cases
            if case.case_id == "recent-session-continuity"
        )
        adapter = SemanticRetrievalV2Adapter(
            self.fixture.benchmark_fixture, use_fixture_query_clock=True,
        )
        try:
            adapter.retrieve_outcome(self.fixture.benchmark_fixture, recent)
        finally:
            adapter.close()

        after = retrieval_module.utc_now_us()
        _, restored_at = retrieval_module._historical_time(ordinary_query)
        self.assertGreaterEqual(after, before)
        self.assertGreaterEqual(restored_at, before)


if __name__ == "__main__":
    unittest.main()
