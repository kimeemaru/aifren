import unittest

from memory_v2_historical_interpretation_evaluation import (
    SYNTHETIC_INTERPRETATION_CASES,
    run_matched_interpretation_evaluation,
    validate_historical_interpretation,
)


class _Provider:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def generate(self, context, character_prompt, *, seed):
        self.calls.append(([dict(item) for item in context], character_prompt, seed))
        return next(self.responses)


class HistoricalInterpretationEvaluationTests(unittest.TestCase):
    def test_obvious_semantic_inversions_are_rejected_and_safe_recall_is_accepted(self):
        cases = {item.case_id: item for item in SYNTHETIC_INTERPRETATION_CASES}
        examples = (
            ("negated_completion", "You successfully finished the project.", False, "negation_inversion"),
            ("negated_completion", "You said you couldn't finish the project that day.", True, None),
            ("uncertain_purchase", "You bought and now own a telescope.", False, "modality_strengthening"),
            ("uncertain_purchase", "You said you thought you might buy a telescope.", True, None),
            ("future_intent", "You worked on and finished the game.", False, "temporal_promotion"),
            ("future_intent", "You were planning to work on the game tomorrow.", True, None),
            ("past_collection", "You still collect cartridges.", False, "current_truth_promotion"),
            ("past_collection", "You said you used to collect cartridges.", True, None),
            ("assistant_speculation", "You told me you have a huge collection.", False, "speaker_attribution_error"),
            (
                "assistant_speculation",
                "I told you that I remembered you mentioning how it was huge.",
                False,
                "speaker_attribution_error",
            ),
            (
                "assistant_speculation",
                "I remember you saying that you probably have a huge collection.",
                False,
                "speaker_attribution_error",
            ),
            (
                "assistant_speculation",
                "I told you that you probably have a huge collection, and you enjoy collecting it.",
                False,
                "unsupported_inference",
            ),
            ("assistant_speculation", "I previously guessed that you probably had a huge collection.", True, None),
            ("positive_completion", "You couldn't finish the Python refactor.", False, "positive_assertion_inverted"),
            ("positive_completion", "You said you finished the Python refactor yesterday.", True, None),
        )
        for case_id, response, accepted, violation in examples:
            with self.subTest(case_id=case_id, response=response):
                result = validate_historical_interpretation(cases[case_id], response)
                self.assertEqual(accepted, result.accepted)
                if violation is not None:
                    self.assertIn(violation, result.violations)

        fallback = (
            'I previously said "You probably have a huge collection."; '
            "that is my prior statement, not something you told me or necessarily current truth."
        )
        result = validate_historical_interpretation(
            cases["assistant_speculation"], fallback,
        )
        self.assertTrue(result.accepted, result.violations)

    def test_matched_runner_uses_identical_seed_and_never_mutates_context(self):
        case = SYNTHETIC_INTERPRETATION_CASES[0]
        context = [{"role": "user", "content": case.query}]
        provider = _Provider((
            "I don't know whether you finished it.",
            "You said you couldn't finish the project that day.",
        ))
        report = run_matched_interpretation_evaluation(
            provider, "synthetic character", {case.case_id: context},
            cases=(case,), seeds=(4242,),
        )
        self.assertEqual(2, len(provider.calls))
        self.assertEqual(4242, provider.calls[0][2])
        self.assertEqual(4242, provider.calls[1][2])
        self.assertEqual(1, len(context))
        self.assertEqual(0, report["totals"]["b_violations"])
        self.assertFalse(report["rows"][0]["persisted"])
        self.assertFalse(report["rows"][0]["tts_submitted"])
        self.assertFalse(report["rows"][0]["published"])

    def test_one_bounded_repair_is_measured_and_has_no_side_effect_path(self):
        case = SYNTHETIC_INTERPRETATION_CASES[0]
        provider = _Provider((
            "I don't know.",
            "You successfully finished the project.",
            "You previously said you couldn't finish the project that day.",
        ))
        report = run_matched_interpretation_evaluation(
            provider,
            "synthetic character",
            {case.case_id: [{"role": "user", "content": case.query}]},
            cases=(case,), seeds=(9,), repair_invalid=True,
        )
        self.assertEqual(3, len(provider.calls))
        self.assertEqual(1, report["totals"]["repair_attempts"])
        self.assertEqual(1, report["totals"]["repair_successes"])
        repair = report["rows"][0]["repair"]
        self.assertTrue(repair["accepted"])
        self.assertFalse(repair["persisted"])
        self.assertFalse(repair["tts_submitted"])
        self.assertFalse(repair["published"])

    def test_failed_repair_uses_source_derived_deterministic_fallback(self):
        case = SYNTHETIC_INTERPRETATION_CASES[0]
        provider = _Provider((
            "I don't know.",
            "You successfully finished the project.",
            "You successfully finished the project.",
        ))
        report = run_matched_interpretation_evaluation(
            provider,
            "synthetic character",
            {case.case_id: [{"role": "user", "content": case.query}]},
            cases=(case,), seeds=(11,), repair_invalid=True,
        )
        self.assertEqual(1, report["totals"]["fallback_uses"])
        self.assertEqual(0, report["totals"]["fallback_failures"])
        fallback = report["rows"][0]["repair"]["fallback"]
        self.assertTrue(fallback["accepted"])
        self.assertIn("couldn't finish", fallback["response"])
        self.assertFalse(fallback["persisted"])


if __name__ == "__main__":
    unittest.main()
