import unittest

from tools.memory_v2.memory_v2_adversarial_recall_evaluation import (
    EVALUATION_VERSION,
    FROZEN_RETRIEVAL_CHECKPOINT,
    AdversarialCase,
    EvaluatedResult,
    _metrics,
    build_adversarial_fixture,
)


class MemoryV2AdversarialRecallEvaluationTests(unittest.TestCase):
    def test_fixture_is_large_deterministic_and_separate(self):
        fixture, cases, extra_v1 = build_adversarial_fixture(500)
        fixture_again, cases_again, extra_v1_again = build_adversarial_fixture(500)

        self.assertEqual(EVALUATION_VERSION, fixture.version)
        self.assertEqual(fixture, fixture_again)
        self.assertEqual(cases, cases_again)
        self.assertEqual(extra_v1, extra_v1_again)
        self.assertEqual(88, len(cases))
        self.assertGreaterEqual(len(fixture.claims), 600)
        self.assertEqual(4, len(extra_v1))
        self.assertEqual("public-synthetic-retrieval-v1", FROZEN_RETRIEVAL_CHECKPOINT)

    def test_fixture_covers_adversarial_contract(self):
        fixture, cases, _extra_v1 = build_adversarial_fixture(500)
        categories = {case.category for case in cases}
        self.assertEqual({
            "before_after",
            "corrected_superseded",
            "cross_scope_distractor",
            "entity_reference",
            "episode_only_detail",
            "exact_old_detail",
            "false_premise",
            "first_occurrence",
            "heavy_paraphrase",
            "insufficient_evidence",
            "latest_occurrence",
            "named_time_period",
            "never_stated",
            "old_incidental_event",
            "other_character",
            "rare_name_term",
            "repeated_occurrences",
            "retired_distractor",
            "similar_object_attributes",
            "source_associated",
            "tempting_invalid_association",
        }, categories)
        counts = {category: sum(case.category == category for case in cases) for category in categories}
        self.assertTrue(all(count >= 4 for count in counts.values()))
        self.assertGreaterEqual(sum(case.negative for case in cases), 16)
        self.assertGreaterEqual(sum(bool(case.forbidden) for case in cases), 40)

        retired = [case for case in cases if case.category == "retired_distractor"]
        self.assertEqual(4, len(retired))
        self.assertEqual(4, len({case.query for case in retired}))
        self.assertTrue(all(
            case.expected_ids[0].endswith("-current")
            and len(case.forbidden) == 1
            and case.forbidden[0][0] != case.expected_ids[0]
            for case in retired
        ))

        fixture_ids = {claim.claim_id for claim in fixture.claims}
        episode_ids = {claim_id for claim_id, _content, _importance in _extra_v1}
        for case in cases:
            if case.negative:
                self.assertFalse(case.expected_ids)
            else:
                self.assertTrue(case.expected_ids)
                self.assertTrue(set(case.expected_ids) <= fixture_ids | episode_ids)

    def test_history_size_changes_noise_without_changing_cases(self):
        small, small_cases, _small_extra = build_adversarial_fixture(500)
        large, large_cases, _large_extra = build_adversarial_fixture(1500)

        self.assertEqual(small_cases, large_cases)
        self.assertEqual(1000, len(large.claims) - len(small.claims))
        self.assertEqual(
            [claim.content for claim in small.claims if not claim.claim_id.startswith("noise-")],
            [claim.content for claim in large.claims if not claim.claim_id.startswith("noise-")],
        )

    def test_category_metrics_tolerate_positive_or_negative_only_subsets(self):
        positive = AdversarialCase("positive", "positive", "query", ("answer",), ("answer",))
        negative = AdversarialCase("negative", "negative", "query")

        hit = EvaluatedResult(
            ("answer",), (), (), 1.0, 1.0, 1.0, 1.0, 1, True,
            False, (), "", 1.0,
        )
        abstained = EvaluatedResult(
            (), (), (), 1.0, 1.0, 1.0, 0.0, None, True,
            False, (), "", 1.0,
        )

        positive_metrics = _metrics((hit,), (positive,))
        negative_metrics = _metrics((abstained,), (negative,))
        self.assertEqual(0.0, positive_metrics.abstention_accuracy)
        self.assertEqual(1.0, negative_metrics.abstention_accuracy)
        self.assertEqual(0.0, negative_metrics.false_recall_rate)


if __name__ == "__main__":
    unittest.main()
