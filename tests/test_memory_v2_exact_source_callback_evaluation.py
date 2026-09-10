import unittest

from memory_v2_exact_source_callback_evaluation import (
    run_exact_source_callback_evaluation,
)
from memory_v2_historical_interpretation_evaluation import (
    SYNTHETIC_INTERPRETATION_CASES,
)


class ExactSourceCallbackEvaluationTests(unittest.TestCase):
    def test_a_b_c2_d_are_matched_and_raw_generation_precedes_repair(self):
        class Provider:
            def __init__(self):
                self.calls = []
                self.responses = iter((
                    "A unrelated response.",
                    "B unrelated response.",
                    "C2 unrelated response.",
                    "I remember you saying you couldn't finish the project that day.",
                    "I remember you saying you couldn't finish the project that day.",
                    "You said you couldn't finish the project that day.",
                    "You said you couldn't finish the project that day.",
                ))

            def generate(self, context, character_prompt, *, seed):
                self.calls.append(([dict(item) for item in context], character_prompt, seed))
                return next(self.responses)

        case = SYNTHETIC_INTERPRETATION_CASES[0]
        context = [
            {"role": "user", "content": "AUTHORITATIVE LIFELONG MEMORIES ABOUT THE USER:\nconflict"},
            {"role": "assistant", "content": "recent", "timestamp": "1"},
            {"role": "user", "content": case.query},
        ]
        provider = Provider()
        report = run_exact_source_callback_evaluation(
            provider,
            "character",
            {case.case_id: context},
            {case.case_id: ()},
            cases=(case,),
            seeds=(77,),
            govern_rejected=True,
        )
        self.assertEqual(7, len(provider.calls))
        self.assertTrue(all(call[2] == 77 for call in provider.calls))
        self.assertEqual(context, provider.calls[0][0])
        for call in provider.calls[3:5]:
            rendered = "\n".join(item["content"] for item in call[0])
            self.assertNotIn("LIFELONG MEMORIES", rendered)
            self.assertIn("required evidence", rendered)
        self.assertEqual(1, report["metrics"]["d_source_before_query"]["raw_acceptable"])
        self.assertEqual(1, report["metrics"]["d_source_after_query"]["raw_acceptable"])
        self.assertEqual(1, report["metrics"]["v1_plus_v2"]["repair_required"])
        self.assertEqual(1, report["metrics"]["c2_canonical_priority"]["repair_required"])

    def test_exact_source_metrics_separate_omission_and_substitution(self):
        class Provider:
            def __init__(self):
                self.responses = iter((
                    "A.", "B.", "C.",
                    "You told me you were doing a Python refactor.",
                    "I remember you saying you couldn't finish the project that day.",
                ))

            def generate(self, context, character_prompt, *, seed):
                return next(self.responses)

        case = SYNTHETIC_INTERPRETATION_CASES[0]
        v1 = ({"id": 1, "category": "project", "content": "The user was doing a Python refactor."},)
        report = run_exact_source_callback_evaluation(
            Provider(), "character",
            {case.case_id: [{"role": "user", "content": case.query}]},
            {case.case_id: v1}, cases=(case,), seeds=(1,), govern_rejected=False,
        )
        before = report["metrics"]["d_source_before_query"]
        after = report["metrics"]["d_source_after_query"]
        self.assertEqual(1, before["unrelated_substitutions"])
        self.assertEqual(0, after["unrelated_substitutions"])
        self.assertEqual(1.0, after["raw_source_communication_rate"])


if __name__ == "__main__":
    unittest.main()
