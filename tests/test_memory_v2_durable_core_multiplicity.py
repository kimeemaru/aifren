import hashlib
from pathlib import Path
import unittest

from benchmarks.memory_v2.durable_core_multiplicity import (
    load_multiplicity_fixture,
    run_multiplicity_evaluation,
)


class MemoryV2DurableCoreMultiplicityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = load_multiplicity_fixture()
        self.report = run_multiplicity_evaluation(self.fixture)

    def test_frozen_corpus_is_deterministic_and_has_required_multiplicity(self):
        source = Path("benchmarks/memory_v2/durable_core_multiplicity_manifest.json")
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        self.assertEqual(28, self.report.total_records)
        self.assertEqual(30, self.report.total_queries)
        self.assertEqual(5, self.report.records_by_core_class["important_person_relationship_fact"])
        self.assertEqual(6, self.report.records_by_core_class["durable_preference"])
        self.assertEqual(self.fixture, load_multiplicity_fixture())
        self.assertEqual(before, hashlib.sha256(source.read_bytes()).hexdigest())

    def test_current_historical_and_collision_queries_admit_only_expected_durable_facts(self):
        expected_cases = [case for case in self.fixture.cases if case.expected_claim_ids]
        self.assertEqual(len(expected_cases), self.report.expected_recovered)
        self.assertEqual(0, self.report.incorrect_or_irrelevant_selections)
        self.assertEqual(0, self.report.incorrect_or_irrelevant_admissions)
        self.assertEqual(("multi-home-port-meridian",), self.report.results["multi-home-direct"].assessment.outcome.admitted_claim_ids)
        self.assertEqual(("multi-home-cedar-old",), self.report.results["multi-home-historical"].assessment.outcome.admitted_claim_ids)
        self.assertEqual(("multi-job-lab-old",), self.report.results["multi-job-historical"].assessment.outcome.admitted_claim_ids)

    def test_generic_hypothetical_and_unrelated_queries_never_admit_durable_context(self):
        for case in self.fixture.cases:
            if case.expected_claim_ids:
                continue
            outcome = self.report.results[case.case_id].assessment.outcome
            self.assertTrue(outcome.lookup_ran)
            self.assertEqual((), outcome.admitted_claim_ids)
        self.assertEqual(0, self.report.wrong_character_leakage)
        self.assertEqual(0, self.report.lifecycle_violations)
        self.assertEqual(0, self.report.provenance_violations)
        self.assertEqual(0, self.report.assistant_authority_violations)


if __name__ == "__main__":
    unittest.main()
