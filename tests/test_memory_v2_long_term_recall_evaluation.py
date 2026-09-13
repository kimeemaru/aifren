import unittest

from tools.memory_v2.memory_v2_long_term_recall_evaluation import build_long_history_fixture


class LongTermRecallEvaluationTests(unittest.TestCase):
    def test_fixture_is_long_noisy_and_covers_required_diagnostics(self):
        fixture, cases, extra_v1 = build_long_history_fixture(noise_claims=100)
        self.assertGreaterEqual(len(fixture.claims), 119)
        self.assertEqual(15, len(cases))
        categories = {value.category for value in cases}
        self.assertTrue({
            "exact_old_detail", "paraphrased_old_detail", "rare_exact_term",
            "named_entity", "correction", "old_incidental_anecdote",
            "historical_episode", "temporal_first", "temporal_latest",
            "temporal_relation", "associative", "distractor", "negative",
            "compacted_episode",
        } <= categories)
        self.assertEqual("raw-planetarium", extra_v1[0][0])
        self.assertTrue(all(value.character_id in {"primary", "other"} for value in fixture.events))

    def test_expected_evidence_and_failure_hints_are_explicit(self):
        _fixture, cases, _extra_v1 = build_long_history_fixture(noise_claims=100)
        positives = [value for value in cases if value.expected_claim_ids]
        self.assertTrue(all(value.expected_source_ids for value in positives))
        hints = {value.diagnostic_hint for value in cases}
        self.assertIn("missing_episode_participation", hints)
        self.assertIn("missing_temporal_reasoning", hints)
        self.assertIn("missing_associative_expansion", hints)


if __name__ == "__main__":
    unittest.main()
