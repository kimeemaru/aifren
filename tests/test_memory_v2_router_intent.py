import unittest

from benchmarks.memory_v2.adapters import SemanticRetrievalV2Adapter
from benchmarks.memory_v2.router_intent import (
    RouterCaseResult,
    build_router_intent_fixture,
    evaluate_router_intent_results,
    load_router_intent_suite,
    run_router_intent_audit,
)


class MemoryV2RouterIntentTests(unittest.TestCase):
    def setUp(self):
        self.suite = load_router_intent_suite()
        self.fixture = build_router_intent_fixture(self.suite)

    def test_frozen_suite_has_held_out_durable_and_generic_coverage(self):
        self.assertEqual(22, len(self.suite.cases))
        self.assertEqual(18, sum(case.expected_search_behavior == "memory_search" for case in self.suite.cases))
        self.assertEqual(4, sum(case.expected_search_behavior == "skip" for case in self.suite.cases))
        self.assertEqual(
            {"user_name", "preferred_address", "stable_home_location", "important_person_relationship_fact", "durable_preference", "durable_biographical_fact", "generic_control"},
            {case.fact_type for case in self.suite.cases},
        )
        self.assertEqual(self.fixture.benchmark_fixture, build_router_intent_fixture(load_router_intent_suite()).benchmark_fixture)

    def test_false_skips_and_unnecessary_searches_are_accounted_separately(self):
        results = {}
        for case in self.suite.cases:
            attempted = case.expected_search_behavior == "memory_search"
            results[case.case_id] = RouterCaseResult("user_memory" if attempted else "generic_reasoning", attempted, (), case.expected_claim_ids if attempted else ())
        results["name-generic-collision"] = RouterCaseResult("generic_reasoning", False)
        results["generic-desk-control"] = RouterCaseResult("user_memory", True)
        report = evaluate_router_intent_results(self.fixture, results)
        self.assertEqual(1, report.durable_query_false_skips)
        self.assertEqual(1, report.generic_pattern_collision_false_skips)
        self.assertEqual(1, report.unnecessary_memory_search_activations)
        self.assertEqual(3, report.generic_controls_correctly_skipped)

    def test_real_adapter_preserves_character_and_lifecycle_controls_when_searching(self):
        adapter = SemanticRetrievalV2Adapter(self.fixture.benchmark_fixture, use_fixture_query_clock=True)
        report = run_router_intent_audit(self.fixture, adapter)
        self.assertEqual(0, report.wrong_character_leakage)
        self.assertEqual(0, report.lifecycle_violations)
        self.assertEqual(0, report.provenance_violations)
        self.assertEqual(0, report.assistant_authority_violations)


if __name__ == "__main__":
    unittest.main()
