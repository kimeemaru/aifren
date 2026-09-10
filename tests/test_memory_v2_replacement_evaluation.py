import unittest
from dataclasses import dataclass

from memory_v2_replacement_evaluation import (
    ReplacementEvaluationCase,
    run_v1_v2_replacement_evaluation,
)
from memory_v2_replacement_shadow import compose_v2_replacement_context


@dataclass(frozen=True)
class _Candidate:
    memory_id: str = "history-1"
    lane: str = "historical_evidence"
    content: str = "I couldn't finish the project today."
    score: float = 9.0
    truth_scope_id: str = "scope-real"
    status: str = "historical_unknown_scope"
    speaker_role: str = "user"
    speech_act: str = "assertion"
    source_class: str = "ordinary_conversation"
    scope_state: str = "unknown_scope"
    attribution_state: str = "canonical_source"
    canonical_record_id: str = "record-1"
    canonical_index: int = 4
    episode_id: str = ""


class _Provider:
    def __init__(self):
        self.calls = []

    def generate(self, context, prompt, *, seed=None):
        self.calls.append((tuple(context), prompt, seed))
        rendered = "\n".join(str(item.get("content", "")) for item in context)
        if "must_communicate" in rendered:
            return "You told me that you couldn't finish the project that day."
        return "I remember your project."


class MemoryV2ReplacementEvaluationTests(unittest.TestCase):
    def test_matched_v1_v2_drafts_are_disconnected_and_measured(self):
        case = ReplacementEvaluationCase(
            "project", "explicit_user_history",
            "What do you remember me saying about one of my projects?",
        )
        production = (
            {"role": "user", "content": "AUTHORITATIVE LIFELONG MEMORIES ABOUT THE USER:\nproject"},
            {"role": "user", "content": case.query, "timestamp": "now"},
        )
        design = compose_v2_replacement_context(
            production, case.query, (_Candidate(),),
            active_truth_scope_id="scope-real",
        )
        provider = _Provider()
        report = run_v1_v2_replacement_evaluation(
            provider, "character", ({
                "case": case,
                "v1_context": production,
                "v1_selected": ({"id": "v1-1", "category": "fact"},),
                "v2_design": design,
                "v2_candidates": ({"memory_id": "history-1", "lane": "historical_evidence"},),
                "v2_retrieval_ms": 8.0,
            },), seeds=(7,),
        )
        row = report["rows"][0]
        self.assertFalse(row["production_influenced"])
        self.assertTrue(row["v2"]["source_communicated"])
        self.assertTrue(row["v2"]["raw_safety_pass"])
        self.assertIsNone(row["v2_repair"])
        self.assertLess(row["v2_context_characters"], row["v1_context_characters"] + 2000)
        self.assertEqual(len(provider.calls), 2)

    def test_false_premise_validator_flags_affirmative_ownership(self):
        case = ReplacementEvaluationCase(
            "motorcycle", "false_premise", "Which motorcycle did I say I owned?",
            "motorcycle_ownership",
        )
        production = ({"role": "user", "content": case.query, "timestamp": "now"},)
        design = compose_v2_replacement_context(
            production, case.query, (), active_truth_scope_id="scope-real",
        )

        class UnsafeProvider(_Provider):
            def generate(self, context, prompt, *, seed=None):
                return "You owned a blue motorcycle."

        report = run_v1_v2_replacement_evaluation(
            UnsafeProvider(), "character", ({
                "case": case, "v1_context": production, "v2_design": design,
            },), seeds=(1,),
        )
        row = report["rows"][0]
        self.assertIn(
            "unsupported_motorcycle_ownership",
            row["v2"]["safety_violations"],
        )
        self.assertFalse(row["v2"]["raw_safety_pass"])
        self.assertIsNotNone(row["v2_repair"])
        self.assertFalse(row["v2_repair"]["safety_pass"])
        self.assertIn(
            "unsupported_motorcycle_ownership",
            row["v2_repair"]["safety_violations"],
        )
        self.assertEqual(
            1, report["metrics"]["v2_repairs_with_content_safety_failure"],
        )
        self.assertEqual(1, report["metrics"]["v2_final_safety_passes"])

    def test_raw_no_evidence_abstention_satisfies_typed_requirement(self):
        case = ReplacementEvaluationCase(
            "unknown", "never_stated", "Which telescope did I say I owned?",
            "telescope_ownership",
        )
        production = ({"role": "user", "content": case.query, "timestamp": "now"},)
        design = compose_v2_replacement_context(
            production, case.query, (), active_truth_scope_id="scope-real",
        )

        class AbstainingProvider(_Provider):
            def generate(self, context, prompt, *, seed=None):
                return "I can't reliably recall source-grounded evidence for that."

        report = run_v1_v2_replacement_evaluation(
            AbstainingProvider(), "character", ({
                "case": case, "v1_context": production, "v2_design": design,
            },), seeds=(1,),
        )
        row = report["rows"][0]
        self.assertTrue(row["v2"]["raw_safety_pass"])
        self.assertTrue(row["v2"]["abstention_validation"]["accepted"])
        self.assertIsNone(row["v2_repair"])


if __name__ == "__main__":
    unittest.main()
