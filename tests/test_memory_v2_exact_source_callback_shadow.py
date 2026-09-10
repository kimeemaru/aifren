import copy
import unittest

from memory_v2_exact_source_callback_shadow import (
    MAX_CALLBACK_RECENT_CHARACTERS,
    MAX_CALLBACK_RECENT_MESSAGES,
    compose_exact_source_callback_contract,
    source_grounded_callback_context,
    validate_exact_source_callback_response,
)
from memory_v2_historical_interpretation_evaluation import (
    SYNTHETIC_INTERPRETATION_CASES,
    interpretation_candidate,
)
from memory_v2_hybrid_recall import HybridRecallCandidate
from memory_v2_prompt_admission_shadow import (
    compose_supplemental_historical_prompt,
)


class ExactSourceCallbackShadowTests(unittest.TestCase):
    @staticmethod
    def _contract(case_index=0):
        case = SYNTHETIC_INTERPRETATION_CASES[case_index]
        design = compose_supplemental_historical_prompt(
            case.query, (interpretation_candidate(case),),
        )
        return case, compose_exact_source_callback_contract(design)

    def test_contract_reuses_must_communicate_and_must_respect_governance(self):
        case, contract = self._contract()
        self.assertTrue(contract.triggered)
        self.assertEqual("user", contract.callback_source_kind)
        self.assertEqual("must_communicate", contract.must_communicate.mode)
        self.assertEqual(
            ("historical_source", "historical_speaker"),
            tuple(fact.name for fact in contract.must_communicate.facts),
        )
        self.assertIs(contract.must_respect_design.items[0], contract.primary_item)
        self.assertIn(case.source_quote, contract.context_block)
        self.assertIn("required evidence", contract.context_block)

    def test_project_source_is_required_and_unrelated_substitution_is_rejected(self):
        _, contract = self._contract()
        competing = ({"content": "The user was doing a Python refactor."},)
        accepted = validate_exact_source_callback_response(
            contract,
            "I remember you saying you weren't able to finish the project that day.",
            competing_v1_items=competing,
        )
        self.assertTrue(accepted.accepted)
        substituted = validate_exact_source_callback_response(
            contract,
            "You told me that you were doing a Python refactor.",
            competing_v1_items=competing,
        )
        self.assertFalse(substituted.accepted)
        self.assertTrue(substituted.unrelated_substitution)
        self.assertIn("unrelated_memory_substitution", substituted.violations)
        inverted = validate_exact_source_callback_response(
            contract, "You said you successfully finished the project.",
        )
        self.assertFalse(inverted.accepted)
        self.assertIn("negation_or_completion_inversion", inverted.violations)
        safe_warning = validate_exact_source_callback_response(
            contract,
            "You said you couldn't finish it; don't assume the project is finished now.",
        )
        self.assertTrue(safe_warning.accepted)

    def test_assistant_source_requires_assistant_ownership(self):
        _, contract = self._contract(4)
        accepted = validate_exact_source_callback_response(
            contract,
            "I remember telling you that I thought you probably had a huge collection.",
        )
        self.assertTrue(accepted.accepted)
        misattributed = validate_exact_source_callback_response(
            contract,
            "You told me that you probably had a huge collection.",
        )
        self.assertFalse(misattributed.accepted)
        self.assertIn("speaker_attribution_error", misattributed.violations)
        explicit_me = validate_exact_source_callback_response(
            contract,
            "It was me who said you probably had a huge collection.",
        )
        self.assertTrue(explicit_me.accepted)

    def test_shared_callback_keeps_actual_source_speaker(self):
        candidate = HybridRecallCandidate(
            "source", "historical_evidence",
            "I drew a harbor map beside a brass compass.", 10.0,
            ("exact",), "", "historical_unknown_scope_assistant_source",
            speaker_role="assistant", speech_act="assertion",
            source_class="ordinary_conversation", scope_state="unknown_scope",
            canonical_record_id="record", canonical_index=1,
        )
        design = compose_supplemental_historical_prompt(
            "What did we talk about before regarding the harbor map?",
            (candidate,),
        )
        contract = compose_exact_source_callback_contract(design)
        self.assertEqual("shared", contract.callback_source_kind)
        self.assertEqual("assistant", contract.primary_item.speaker_role)
        good = validate_exact_source_callback_response(
            contract,
            "I remember telling you that I drew a harbor map beside a brass compass.",
        )
        self.assertTrue(good.accepted)

    def test_no_admitted_source_leaves_context_unchanged(self):
        design = compose_supplemental_historical_prompt(
            "Which motorcycle do I own?", (),
        )
        contract = compose_exact_source_callback_contract(design)
        context = [{"role": "user", "content": "Which motorcycle do I own?"}]
        result = source_grounded_callback_context(context, contract)
        self.assertFalse(contract.triggered)
        self.assertEqual(context, result)
        self.assertIsNot(context, result)

    def test_minimal_context_removes_broad_memory_and_bounds_recent_history(self):
        case, contract = self._contract()
        context = [
            {"role": "user", "content": "Temporal context: Tuesday"},
            {"role": "user", "content": "[Authoritative current truth scope: unknown]"},
            {"role": "user", "content": "AUTHORITATIVE LIFELONG MEMORIES ABOUT THE USER:\nconflict"},
            {"role": "user", "content": "LONG-TERM CONVERSATION BACKGROUND:\nnoise"},
            {"role": "assistant", "content": "old", "timestamp": "1"},
        ]
        for index in range(MAX_CALLBACK_RECENT_MESSAGES + 2):
            context.append({
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"recent-{index}",
                "timestamp": str(index + 2),
            })
        context.append({"role": "user", "content": case.query})
        original = copy.deepcopy(context)
        before = source_grounded_callback_context(
            context, contract, requirement_order="before_query",
        )
        after = source_grounded_callback_context(
            context, contract, requirement_order="after_query",
        )
        before_text = "\n".join(str(item["content"]) for item in before)
        self.assertNotIn("LIFELONG MEMORIES", before_text)
        self.assertNotIn("LONG-TERM CONVERSATION", before_text)
        self.assertIn("Temporal context", before_text)
        self.assertIn("current truth scope", before_text)
        self.assertEqual(case.query, before[-1]["content"])
        self.assertIn("historical callback response requirement", before[-2]["content"])
        self.assertIn("historical callback response requirement", after[-1]["content"])
        recent = [item for item in before if "timestamp" in item]
        self.assertLessEqual(len(recent), MAX_CALLBACK_RECENT_MESSAGES)
        self.assertLessEqual(
            sum(len(str(item["content"])) for item in recent),
            MAX_CALLBACK_RECENT_CHARACTERS,
        )
        self.assertEqual(original, context)

    def test_oversized_recent_message_cannot_break_character_bound(self):
        case, contract = self._contract()
        context = [
            {"role": "user", "content": "Temporal context: Tuesday"},
            {
                "role": "assistant",
                "content": "x" * (MAX_CALLBACK_RECENT_CHARACTERS + 1),
                "timestamp": "1",
            },
            {"role": "user", "content": case.query},
        ]
        rendered = source_grounded_callback_context(context, contract)
        recent = [item for item in rendered if "timestamp" in item]
        self.assertEqual([], recent)


if __name__ == "__main__":
    unittest.main()
