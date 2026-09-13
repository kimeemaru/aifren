import unittest

from tools.memory_v2.memory_v2_answer_governance_evaluation import (
    run_memory_answer_governance_evaluation,
    run_recent_context_budget_evaluation,
)
from tools.memory_v2.memory_v2_replacement_evaluation import ReplacementEvaluationCase
from aifren.continuity.memory_v2_replacement_shadow import compose_v2_replacement_context


class _Provider:
    def generate(self, context, prompt, *, seed=None):
        rendered = prompt + "\n" + "\n".join(str(item.get("content", "")) for item in context)
        if "MEMORY_ANSWER_REQUIREMENT" in rendered or "[Authoritative memory answer brief]" in rendered:
            return "I don't have a reliable source-grounded memory that establishes that."
        return "You owned a blue motorcycle."

    def generate_bounded(self, context, prompt, *, max_output_tokens, seed=None):
        return "I don't have a reliable source-grounded memory that establishes that."


class MemoryV2AnswerGovernanceEvaluationTests(unittest.TestCase):
    def _input(self):
        case = ReplacementEvaluationCase(
            "motorcycle", "false_premise", "Which motorcycle did I say I owned?",
        )
        production = (
            {"role": "assistant", "content": "Maybe it was blue.", "timestamp": "t1"},
            {"role": "user", "content": case.query, "timestamp": "t2"},
        )
        design = compose_v2_replacement_context(production, case.query, ())
        return {"case": case, "v2_design": design}

    def test_v0_vg_use_identical_evidence_but_only_vg_has_typed_requirement(self):
        report = run_memory_answer_governance_evaluation(
            _Provider(), "character", (self._input(),), seeds=(7,),
        )
        row = report["rows"][0]
        self.assertFalse(row["v0"]["raw_safety_pass"])
        self.assertTrue(row["vg"]["raw_safety_pass"])
        self.assertEqual(0, report["metrics"]["vg_repairs_required"])
        self.assertFalse(row["production_influenced"])
        self.assertEqual(1, report["metrics"]["vg_final_safety_passes"])

    def test_failed_raw_and_repair_are_validated_as_complete_responses(self):
        class Unsafe(_Provider):
            def generate(self, context, prompt, *, seed=None):
                return "I can't reliably recall that. You owned a blue motorcycle."

            def generate_bounded(self, context, prompt, *, max_output_tokens, seed=None):
                return "I can't reliably recall that. You owned a blue motorcycle."

        report = run_memory_answer_governance_evaluation(
            Unsafe(), "character", (self._input(),), seeds=(7,),
        )
        repair = report["rows"][0]["vg_repair"]
        self.assertIsNotNone(repair)
        self.assertFalse(repair["validation"]["accepted"])
        self.assertIsNotNone(repair["fallback"])
        self.assertTrue(repair["fallback"]["validation"]["accepted"])
        self.assertEqual(1, report["metrics"]["vg_final_safety_passes"])

    def test_recent_budget_reports_prompt_size_without_mutating_design(self):
        source = self._input()
        before = source["v2_design"].context
        report = run_recent_context_budget_evaluation(
            _Provider(), "character", (source,), seeds=(7,),
        )
        self.assertLessEqual(
            report["metrics"]["last_6"]["context_characters_mean"],
            report["metrics"]["production"]["context_characters_mean"],
        )
        self.assertEqual(before, source["v2_design"].context)


if __name__ == "__main__":
    unittest.main()
