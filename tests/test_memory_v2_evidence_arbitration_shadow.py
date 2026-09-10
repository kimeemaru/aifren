import unittest

from conversation.conversation import ContextManager
from memory_v2_evidence_arbitration_shadow import (
    arbitrate_historical_callback_evidence,
    arbitrated_counterfactual_context,
)
from memory_v2_evidence_arbitration_evaluation import (
    run_arbitrated_interpretation_evaluation,
)
from memory_v2_historical_interpretation_evaluation import (
    SYNTHETIC_INTERPRETATION_CASES,
)
from memory_v2_hybrid_recall import HybridRecallCandidate
from memory_v2_prompt_admission_shadow import compose_supplemental_historical_prompt


class EvidenceArbitrationShadowTests(unittest.TestCase):
    @staticmethod
    def _candidate(
        content,
        *,
        speaker="user",
        speech_act="assertion",
        status="historical_unknown_scope_user_source",
    ):
        return HybridRecallCandidate(
            "v2-source", "historical_evidence", content, 9.0, ("exact",), "",
            status, speaker_role=speaker, speech_act=speech_act,
            source_class="ordinary_conversation", scope_state="unknown_scope",
            canonical_record_id="canonical-source", canonical_index=1,
        )

    @staticmethod
    def _v1(memory_id, category, content, source="user_message"):
        return {
            "id": memory_id,
            "category": category,
            "content": content,
            "provenance": {"source": source},
        }

    def test_project_arbitration_classifies_conflict_echo_support_and_unrelated(self):
        query = "What do you remember me saying about one of my projects?"
        design = compose_supplemental_historical_prompt(
            query, (self._candidate("I couldn't finish the project today."),),
        )
        v1 = (
            self._v1(1, "project", "The user successfully finished the project."),
            self._v1(2, "interest", "The user is interested in what was said about a project."),
            self._v1(3, "project", "The user was working on a Python project."),
            self._v1(4, "weather", "The user likes rain against a window."),
        )
        result = arbitrate_historical_callback_evidence(query, v1, design)
        self.assertTrue(result.applied)
        classes = {item.memory_id: item.classification for item in result.decisions}
        self.assertEqual("polarity_conflict", classes["1"])
        self.assertEqual("query_inquiry_echo", classes["2"])
        self.assertEqual("compatible_supportive", classes["3"])
        self.assertEqual("unrelated", classes["4"])
        self.assertEqual({3, 4}, {item["id"] for item in result.c1_items})
        self.assertEqual({3}, {item["id"] for item in result.c2_items})

    def test_semantic_conflict_classes_are_deterministic_and_generic(self):
        cases = (
            (
                "What do you remember me saying about a telescope?",
                "I think I might buy a telescope.",
                self._v1(1, "object", "The user bought and owns a telescope."),
                "modality_conflict",
            ),
            (
                "What do you remember me saying about the game?",
                "I'm planning to work on the game tomorrow.",
                self._v1(2, "project", "The user completed the game."),
                "temporal_current_state_conflict",
            ),
            (
                "What do you remember me saying about cartridges?",
                "I used to collect cartridges.",
                self._v1(3, "interest", "The user still collects cartridges."),
                "temporal_current_state_conflict",
            ),
            (
                "What do you remember me saying about the Python refactor?",
                "I finished the Python refactor yesterday.",
                self._v1(4, "project", "The user couldn't finish the Python refactor."),
                "polarity_conflict",
            ),
        )
        for query, source, v1, expected in cases:
            with self.subTest(expected=expected):
                design = compose_supplemental_historical_prompt(
                    query, (self._candidate(source),),
                )
                result = arbitrate_historical_callback_evidence(query, (v1,), design)
                self.assertEqual(expected, result.decisions[0].classification)
                self.assertFalse(result.decisions[0].retained_c1)
                self.assertFalse(result.decisions[0].retained_c2)

    def test_v1_category_participates_and_empty_record_is_indeterminate(self):
        query = "What do you remember me saying about one of my projects?"
        design = compose_supplemental_historical_prompt(
            query, (self._candidate("I couldn't finish the project today."),),
        )
        categorized = self._v1(
            1, "project", "The user successfully finished refactoring the script.",
        )
        empty = self._v1(2, "", "")
        result = arbitrate_historical_callback_evidence(
            query, (categorized, empty), design,
        )
        self.assertEqual("polarity_conflict", result.decisions[0].classification)
        self.assertEqual("cannot_determine_safely", result.decisions[1].classification)
        self.assertTrue(result.decisions[1].retained_c1)
        self.assertFalse(result.decisions[1].retained_c2)

    def test_assistant_history_cannot_be_supported_by_derivative_user_fact(self):
        query = "What did you tell me before about Game Boy?"
        design = compose_supplemental_historical_prompt(
            query,
            (self._candidate(
                "Fast story: a green pixel jumped over a blue Game Boy.",
                speaker="assistant",
                status="historical_unknown_scope_assistant_source",
            ),),
        )
        v1 = self._v1(
            1, "collection", "The user owns a huge Game Boy collection.",
        )
        result = arbitrate_historical_callback_evidence(query, (v1,), design)
        self.assertEqual("attribution_conflict", result.decisions[0].classification)
        self.assertFalse(result.c1_items)
        self.assertFalse(result.c2_items)

    def test_copied_context_changes_only_v1_block_and_adds_bounded_v2_precedence(self):
        query = "What do you remember me saying about one of my projects?"
        candidate = self._candidate("I couldn't finish the project today.")
        design = compose_supplemental_historical_prompt(query, (candidate,))
        conflict = self._v1(1, "project", "The user successfully finished the project.")
        support = self._v1(2, "project", "The user was working on a Python project.")
        memory_context = ContextManager().build_memory_context((conflict, support))
        production = [
            {"role": "user", "content": "fixed authority"},
            memory_context,
            {"role": "assistant", "content": "recent assistant"},
            {"role": "user", "content": query},
        ]
        arbitration = arbitrate_historical_callback_evidence(
            query, (conflict, support), design,
        )
        c1 = arbitrated_counterfactual_context(
            production, arbitration, design, variant="c1_conflicts_removed",
        )
        c2 = arbitrated_counterfactual_context(
            production, arbitration, design, variant="c2_canonical_priority",
        )
        self.assertEqual("fixed authority", c1[0]["content"])
        self.assertEqual("recent assistant", c1[-3]["content"])
        self.assertEqual(query, c1[-1]["content"])
        self.assertNotIn("successfully finished", "\n".join(item["content"] for item in c1))
        self.assertIn("working on a Python project", "\n".join(item["content"] for item in c2))
        self.assertIn("exact canonical SOURCE QUOTE", c2[-2]["content"])
        self.assertNotIn("source precedence", "\n".join(item["content"] for item in production))

    def test_non_callback_never_arbitrates(self):
        design = compose_supplemental_historical_prompt(
            "Which motorcycle do I own?",
            (self._candidate("I might buy a motorcycle."),),
        )
        result = arbitrate_historical_callback_evidence(
            "Which motorcycle do I own?", (), design,
        )
        self.assertFalse(result.applied)
        self.assertEqual("ineligible_callback", result.reason)

    def test_matched_evaluator_preserves_a_b_and_filters_only_c_contexts(self):
        class Provider:
            def __init__(self):
                self.calls = []
                self.responses = iter((
                    "V1-only draft.",
                    "You said you couldn't finish the project that day.",
                    "You said you couldn't finish the project that day.",
                    "You said you couldn't finish the project that day.",
                ))

            def generate(self, context, character_prompt, *, seed):
                self.calls.append(([dict(item) for item in context], character_prompt, seed))
                return next(self.responses)

        case = SYNTHETIC_INTERPRETATION_CASES[0]
        conflict = self._v1(
            1, "project", "The user successfully finished the project.",
        )
        support = self._v1(
            2, "project", "The user was working on a Python project.",
        )
        production = [
            ContextManager().build_memory_context((conflict, support)),
            {"role": "user", "content": case.query},
        ]
        provider = Provider()
        report = run_arbitrated_interpretation_evaluation(
            provider,
            "synthetic character",
            {case.case_id: production},
            {case.case_id: (conflict, support)},
            cases=(case,), seeds=(4242,), govern_rejected=False,
        )
        self.assertEqual(4, len(provider.calls))
        self.assertTrue(all(call[2] == 4242 for call in provider.calls))
        self.assertEqual(production, provider.calls[0][0])
        self.assertIn("successfully finished", provider.calls[1][0][0]["content"])
        self.assertNotIn(
            "successfully finished",
            "\n".join(item["content"] for item in provider.calls[2][0]),
        )
        self.assertIn(
            "Historical callback source precedence",
            "\n".join(item["content"] for item in provider.calls[3][0]),
        )
        self.assertEqual(
            1, report["metrics"]["c2_canonical_priority"]["raw_acceptable"],
        )
        self.assertEqual(production[0]["content"], provider.calls[0][0][0]["content"])

    def test_all_raw_variants_run_before_any_repair(self):
        class Provider:
            def __init__(self):
                self.calls = []
                self.responses = iter((
                    "V1-only draft.",
                    "You successfully finished the project.",
                    "You said you couldn't finish the project that day.",
                    "You said you couldn't finish the project that day.",
                    "You said you couldn't finish the project that day.",
                ))

            def generate(self, context, character_prompt, *, seed):
                self.calls.append(([dict(item) for item in context], character_prompt, seed))
                return next(self.responses)

        case = SYNTHETIC_INTERPRETATION_CASES[0]
        production = [{"role": "user", "content": case.query}]
        provider = Provider()
        report = run_arbitrated_interpretation_evaluation(
            provider,
            "synthetic character",
            {case.case_id: production},
            {case.case_id: ()},
            cases=(case,), seeds=(7,), govern_rejected=True,
        )
        self.assertEqual(5, len(provider.calls))
        self.assertIn("Historical callback source precedence", provider.calls[3][0][0]["content"])
        self.assertEqual([], provider.calls[4][0])
        self.assertEqual(
            1, report["metrics"]["v1_plus_v2"]["repair_required"],
        )


if __name__ == "__main__":
    unittest.main()
