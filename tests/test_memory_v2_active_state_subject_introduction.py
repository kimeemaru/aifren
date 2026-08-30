import unittest

from benchmarks.memory_v2.active_state_contextual_extraction import ParsedCandidate, ProposalOperation, evaluate_candidates
from benchmarks.memory_v2.active_state_subject_introduction import (
    INTRODUCTION_GUIDANCE, _metrics, load_subject_introduction_manifest, revised_instructions,
)


class ActiveStateSubjectIntroductionBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = load_subject_introduction_manifest()
        cls.by_id = {case.case_id: case for case in cls.cases}

    def test_manifest_is_frozen_and_covers_context_subject_and_ambiguity_boundaries(self):
        self.assertEqual(25, len(self.cases))
        categories = {case.category for case in self.cases}
        self.assertTrue({"context_value", "required_subject", "existing_subject", "incidental", "ambiguous", "environment_gap"} <= categories)
        self.assertTrue(self.by_id["incidental-chair"].reject_mutation)
        self.assertTrue(any(item["target"] == "new:coffee" for item in self.by_id["introduce-coffee"].required))

    def test_metrics_keep_unnecessary_introduction_separate_from_existing_and_actor_updates(self):
        candidates = {case.case_id: ParsedCandidate(None, ()) for case in self.cases}
        candidates["no-subject-user-kitchen"] = ParsedCandidate(None, (
            ProposalOperation("actor:user", "location", "set", "kitchen", "explicit"),
            ProposalOperation("new:kitchen", "kind", "set", "kitchen", "explicit"),
        ))
        candidates["existing-food-burnt"] = ParsedCandidate(None, (
            ProposalOperation("scene:food_1", "condition", "set", "burnt", "explicit"),
        ))
        evaluated = evaluate_candidates(self.cases, candidates)
        metrics = _metrics(self.cases, candidates, evaluated.traces)
        self.assertEqual(1, metrics.unnecessary_introductions)
        self.assertEqual(1, metrics.existing_subject_updates_correct)
        self.assertGreater(metrics.required_introductions, metrics.correct_introductions)

    def test_guidance_is_general_semantic_boundary_not_fixture_values(self):
        combined = revised_instructions("base")
        self.assertIn(INTRODUCTION_GUIDANCE, combined)
        self.assertIn("independent evolving attributes", INTRODUCTION_GUIDANCE)
        self.assertNotIn("food_1", INTRODUCTION_GUIDANCE)
        self.assertNotIn("shirt_1", INTRODUCTION_GUIDANCE)


if __name__ == "__main__":
    unittest.main()
