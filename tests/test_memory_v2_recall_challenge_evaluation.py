import unittest

from memory_v2_recall_challenge_evaluation import (
    CHALLENGE_VERSION,
    build_challenge_fixture,
)


class MemoryV2RecallChallengeEvaluationTests(unittest.TestCase):
    def test_challenge_is_deterministic_separate_and_authored(self):
        fixture, cases = build_challenge_fixture(500)
        repeated_fixture, repeated_cases = build_challenge_fixture(500)
        self.assertEqual(CHALLENGE_VERSION, fixture.version)
        self.assertEqual(fixture, repeated_fixture)
        self.assertEqual(cases, repeated_cases)
        self.assertEqual(26, len(cases))
        self.assertGreaterEqual(len(fixture.claims), 520)
        self.assertEqual({
            "challenge_paraphrase", "challenge_entity",
            "challenge_supported_action", "challenge_negative",
            "challenge_temporal_rank",
        }, {case.category for case in cases})
        self.assertEqual(6, sum(case.negative for case in cases))
        positive_queries = {case.query for case in cases if not case.negative}
        negative_queries = {case.query for case in cases if case.negative}
        self.assertFalse(positive_queries & negative_queries)

    def test_history_growth_changes_only_noise(self):
        small, small_cases = build_challenge_fixture(500)
        large, large_cases = build_challenge_fixture(4000)
        self.assertEqual(small_cases, large_cases)
        self.assertEqual(3500, len(large.claims) - len(small.claims))
        self.assertEqual(
            [claim.content for claim in small.claims if not claim.claim_id.startswith("challenge-noise-")],
            [claim.content for claim in large.claims if not claim.claim_id.startswith("challenge-noise-")],
        )


if __name__ == "__main__":
    unittest.main()
