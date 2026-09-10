import hashlib
from pathlib import Path
import unittest

from benchmarks.memory_v2.aging import build_aging_fixture
from benchmarks.memory_v2.durable_core_lane import (
    aging_core_records,
    assess_durable_lane,
    lookup_durable_core,
    router_core_records,
)
from benchmarks.memory_v2.router_intent import build_router_intent_fixture
from benchmarks.memory_v2.router_intent_counterfactual import load_generic_holdout
from benchmarks.memory_v2.models import RetrievalCase


class MemoryV2DurableCoreLaneTests(unittest.TestCase):
    def setUp(self):
        self.router_fixture = build_router_intent_fixture()
        self.router_records = router_core_records(self.router_fixture)
        self.aging_fixture = build_aging_fixture(unrelated_history_count=32)
        self.aging_records = aging_core_records(self.aging_fixture)

    def test_lane_uses_a_small_fixture_classified_pool_without_mutating_manifests(self):
        source = Path("benchmarks/memory_v2/router_intent_manifest.json")
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        self.assertEqual(6, len(self.router_records))
        self.assertEqual(4, len(self.aging_records))
        lookup_durable_core(self.router_records, self.router_fixture.suite.cases[0].retrieval_case)
        self.assertEqual(before, hashlib.sha256(source.read_bytes()).hexdigest())

    def test_all_router_collision_queries_recover_without_using_router_as_a_gate(self):
        for case in self.router_fixture.suite.cases:
            if case.query_style != "generic_pattern_collision":
                continue
            assessment = assess_durable_lane(
                self.router_fixture.benchmark_fixture, self.router_records, case.retrieval_case,
                expected_claim_ids=case.expected_claim_ids, forbidden_claim_ids=case.forbidden_claim_ids,
            )
            self.assertFalse(assessment.outcome.current_router_intent == "user_memory")
            self.assertTrue(assessment.outcome.lookup_ran)
            self.assertTrue(assessment.expected_claim_recovered)
            self.assertFalse(assessment.irrelevant_durable_selected)

    def test_generic_holdout_executes_lookup_without_injecting_durable_memory(self):
        for holdout_case in load_generic_holdout():
            case = RetrievalCase(holdout_case.case_id, "router-a", holdout_case.query, "2033-02-02T09:00:00Z")
            assessment = assess_durable_lane(self.router_fixture.benchmark_fixture, self.router_records, case)
            self.assertTrue(assessment.outcome.lookup_ran)
            self.assertEqual((), assessment.outcome.selected_claim_ids)
            self.assertFalse(assessment.irrelevant_durable_selected)

    def test_aging_name_and_current_vs_historical_correction_are_preserved(self):
        cases = {case.case_id: case for case in self.aging_fixture.manifest.cases}
        for case_id in ("durable-name-direct-7y", "durable-name-paraphrase-7y", "durable-correction-current", "durable-correction-historical"):
            case = cases[case_id]
            assessment = assess_durable_lane(
                self.aging_fixture.benchmark_fixture, self.aging_records, case.retrieval_case,
                expected_claim_ids=case.expected_claim_ids, forbidden_claim_ids=case.forbidden_claim_ids,
            )
            self.assertTrue(assessment.expected_claim_recovered)
            self.assertFalse(assessment.irrelevant_durable_selected)
            self.assertFalse(assessment.wrong_character_leakage)
            self.assertFalse(assessment.lifecycle_violation)
            self.assertFalse(assessment.provenance_violation)
            self.assertFalse(assessment.assistant_authority_violation)


if __name__ == "__main__":
    unittest.main()
