import hashlib
from pathlib import Path
import unittest

from benchmarks.memory_v2.router_intent import load_router_intent_suite
from benchmarks.memory_v2.router_intent_counterfactual import (
    PERSONAL_DURABLE_CUE_POLICY,
    POLICIES,
    RECALL_MARKER_POLICY,
    current_intent,
    evaluate_counterfactual,
    load_generic_holdout,
)


class MemoryV2RouterIntentCounterfactualTests(unittest.TestCase):
    def setUp(self):
        self.suite = load_router_intent_suite()
        self.holdout = load_generic_holdout()

    def test_original_frozen_manifest_is_not_mutated_and_holdout_is_deterministic(self):
        source = Path("benchmarks/memory_v2/router_intent_manifest.json")
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        self.assertEqual(22, len(self.suite.cases))
        self.assertEqual(12, len(self.holdout))
        self.assertEqual(self.holdout, load_generic_holdout())
        evaluate_counterfactual(self.suite, self.holdout, PERSONAL_DURABLE_CUE_POLICY)
        self.assertEqual(before, hashlib.sha256(source.read_bytes()).hexdigest())

    def test_counterfactual_accounting_keeps_false_skips_and_unnecessary_searches_separate(self):
        marker = evaluate_counterfactual(self.suite, self.holdout, RECALL_MARKER_POLICY)
        self.assertEqual(3, marker.durable_false_skips)
        self.assertEqual(3, marker.independent_holdout_unnecessary_memory_search_activations)
        self.assertEqual(2, marker.original_unnecessary_memory_search_activations)

    def test_counterfactual_evaluation_does_not_change_production_classifier(self):
        queries = tuple(case.query for case in self.suite.cases) + tuple(case.query for case in self.holdout)
        before = tuple(current_intent(query) for query in queries)
        for policy in POLICIES:
            evaluate_counterfactual(self.suite, self.holdout, policy)
        self.assertEqual(before, tuple(current_intent(query) for query in queries))


if __name__ == "__main__":
    unittest.main()
