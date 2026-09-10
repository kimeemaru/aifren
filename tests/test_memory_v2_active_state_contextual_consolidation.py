import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.memory_v2.active_state_contextual_consolidation import (
    CONSOLIDATED_EXTRACTION_GUIDANCE,
    load_heldout_manifest,
    suite_metrics,
)
from benchmarks.memory_v2.active_state_contextual_extraction import (
    ParsedCandidate,
    load_contextual_manifest,
    run_provider_evaluation_details,
)


class ContextualActiveStateConsolidationTests(unittest.TestCase):
    def test_heldout_suite_is_frozen_and_uses_distinct_generalization_situations(self):
        cases = load_heldout_manifest()
        self.assertEqual(22, len(cases))
        encoded = json.dumps([case.turn for case in cases])
        self.assertIn("bike", encoded)
        self.assertIn("laptop", encoded)
        self.assertIn("wrench", encoded)
        self.assertTrue(any(case.reject_mutation for case in cases))
        self.assertNotIn("bike", CONSOLIDATED_EXTRACTION_GUIDANCE)
        self.assertNotIn("laptop", CONSOLIDATED_EXTRACTION_GUIDANCE)

    def test_suite_metrics_keep_reference_introduction_and_safety_dimensions_separate(self):
        cases = load_heldout_manifest()
        metrics = suite_metrics(cases, {case.case_id: ParsedCandidate(None, ()) for case in cases}, {})
        self.assertEqual(22, metrics.total_cases)
        self.assertGreater(metrics.required_introductions, 0)
        self.assertEqual(0, metrics.correct_introductions)
        self.assertEqual(0, metrics.unnecessary_introductions)
        self.assertGreater(metrics.existing_subject_updates_required, 0)
        self.assertGreater(metrics.actor_updates_required, 0)
        self.assertEqual(7, metrics.safe_rejections)
        self.assertEqual(0, metrics.hard_safety_failures)

    def test_completed_provider_rows_are_saved_before_a_reporting_serializer_can_fail(self):
        cases = (load_contextual_manifest()[0],)

        def transport(_provider, batch, _instructions):
            self.assertEqual(cases, batch)
            return json.dumps({"cases": [{
                "case_id": "kitchen-current",
                "proposal": {
                    "introductions": [], "retirements": [],
                    "updates": [
                        {"target_kind": "actor", "target_ref": "user", "attribute": "location", "operation": "set", "value": "kitchen", "basis": "explicit", "evidence": "kitchen"},
                        {"target_kind": "actor", "target_ref": "user", "attribute": "activity", "operation": "set", "value": "cooking", "basis": "explicit", "evidence": "cooking"},
                    ],
                },
            }]}), 1.0

        with tempfile.TemporaryDirectory() as directory:
            result_path = Path(directory) / "result.json"
            run = run_provider_evaluation_details(
                cases=cases, provider=object(), transport=transport, result_path=result_path,
            )
            # This reproduces the prior display-only field mistake after the
            # provider run has completed; the durable record must survive it.
            with self.assertRaises(AttributeError):
                _ = run.report.traces[0].actual
            saved = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual("completed", saved["status"])
        self.assertEqual(1, saved["provider_calls"])
        self.assertEqual(["kitchen-current"], saved["batches"][0]["case_ids"])
        self.assertIn("raw_structured_response", saved["batches"][0])
        self.assertEqual("correct_proposal", saved["report"]["traces"][0]["classification"])
        self.assertEqual(1, saved["aggregate_metrics"]["correct_proposals"])


if __name__ == "__main__":
    unittest.main()
