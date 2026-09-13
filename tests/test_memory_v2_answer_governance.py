import unittest
from dataclasses import dataclass

from aifren.continuity.memory_v2_answer_governance import (
    MemoryAnswerEvidence,
    bind_memory_answer_source_containment,
    compose_memory_answer_requirement,
    memory_answer_repair_prompt,
    memory_answer_generation_context,
    remove_unsupported_retrospective_sentences,
    validate_memory_answer_response,
)
from aifren.continuity.memory_query_decision import decide_memory_query
from aifren.continuity.memory_v2_exact_source_callback_shadow import compose_exact_source_callback_contract
from aifren.continuity.memory_v2_prompt_admission_shadow import compose_supplemental_historical_prompt


@dataclass(frozen=True)
class _Candidate:
    memory_id: str = "history-1"
    lane: str = "historical_evidence"
    content: str = "I told a little story involving a Game Boy."
    score: float = 9.0
    truth_scope_id: str = "scope-real"
    status: str = "historical_unknown_scope"
    speaker_role: str = "assistant"
    speech_act: str = "assertion"
    source_class: str = "ordinary_conversation"
    scope_state: str = "unknown_scope"
    attribution_state: str = "canonical_source"
    canonical_record_id: str = "message-1"
    canonical_index: int = 12
    episode_id: str = ""


class MemoryV2AnswerGovernanceTests(unittest.TestCase):
    def _no_evidence(self, query):
        return compose_memory_answer_requirement(query, ())

    def test_no_evidence_requires_natural_abstention(self):
        requirement = self._no_evidence("Which motorcycle did I say I owned?")
        accepted = validate_memory_answer_response(
            requirement,
            "I don't remember you saying you owned one.",
        )
        self.assertTrue(accepted.accepted, accepted)
        omitted = validate_memory_answer_response(requirement, "I'm not going to guess.")
        self.assertFalse(omitted.accepted)
        self.assertIn("memory_abstention_omitted", omitted.violations)

    def test_complete_response_rejects_unsafe_claim_after_safe_abstention(self):
        requirement = self._no_evidence("Which motorcycle did I say I owned?")
        result = validate_memory_answer_response(
            requirement,
            "I can't reliably recall that. You owned a blue motorcycle.",
        )
        self.assertFalse(result.accepted)
        self.assertIn("unsupported_ownership_memory", result.violations)

    def test_no_evidence_covers_identity_preference_plan_and_place(self):
        cases = (
            ("What is my name?", "I don't have a reliable source-grounded memory. Your name is Riley.", "unsupported_identity_memory"),
            ("What is my favorite color?", "I don't have a reliable source-grounded memory. Your favorite is teal.", "unsupported_preference_memory"),
            ("Do you remember something I planned to do later?", "I don't remember reliably. You planned to travel.", "unsupported_future_plan_memory"),
            ("Do you remember a place I talked about going?", "I don't remember reliably. You visited Oslo.", "unsupported_place_memory"),
        )
        for query, response, violation in cases:
            with self.subTest(query=query):
                result = validate_memory_answer_response(self._no_evidence(query), response)
                self.assertIn(violation, result.violations)

    def test_recent_query_echo_cannot_become_memory_evidence(self):
        requirement = self._no_evidence(
            "Do you remember me asking what you remember about my project?"
        )
        result = validate_memory_answer_response(
            requirement,
            "I don't have reliable evidence. You previously asked me about your project.",
        )
        self.assertFalse(result.accepted)
        self.assertIn("unsupported_recalled_fact", result.violations)

    def test_no_evidence_generation_removes_only_proposition_bearing_recent_context(self):
        requirement = self._no_evidence("Which motorcycle did I say I owned?")
        context = (
            {"role": "user", "content": "We are sitting by a window.", "timestamp": "t1"},
            {"role": "assistant", "content": "You owned a blue motorcycle.", "timestamp": "t2"},
            {"role": "user", "content": "The tea is warm.", "timestamp": "t3"},
            {"role": "user", "content": "Which motorcycle did I say I owned?", "timestamp": "t4"},
        )
        projected = memory_answer_generation_context(context, requirement)
        rendered = "\n".join(str(item["content"]) for item in projected)
        self.assertNotIn("blue motorcycle", rendered)
        self.assertIn("window", rendered)
        self.assertIn("tea is warm", rendered)
        self.assertIn("Which motorcycle", rendered)

    def test_grounded_and_non_memory_contexts_are_not_filtered(self):
        context = ({"role": "assistant", "content": "recent", "timestamp": "t1"},)
        non_memory = compose_memory_answer_requirement("Say something silly.", ())
        grounded = compose_memory_answer_requirement(
            "What is my name?",
            (MemoryAnswerEvidence(
                "identity.name", "governed_current_fact", "", "real_world",
                "assertion", "", "identity.name", "Mara",
            ),),
        )
        self.assertEqual(context, memory_answer_generation_context(context, non_memory))
        self.assertEqual(context, memory_answer_generation_context(context, grounded))

    def test_concrete_grounded_followup_requires_the_source_attribute(self):
        query = "Which programming language was connected to it?"
        decision = decide_memory_query(query)
        evidence = (MemoryAnswerEvidence(
            "record-python", "historical_conversation_only", "assistant",
            "unknown_scope", "other",
            "I told you the Python parser module was the tricky part.",
        ),)
        requirement = compose_memory_answer_requirement(
            query, evidence, memory_query_decision=decision,
        )
        self.assertEqual("grounded_evidence", requirement.evidence_state)
        self.assertIsNotNone(requirement.response_requirement)
        self.assertEqual(
            "v2_grounded_followup_attribute",
            requirement.response_requirement.intent,
        )
        self.assertTrue(validate_memory_answer_response(
            requirement, "The programming language that came up was Python.",
        ).accepted)
        omitted = validate_memory_answer_response(
            requirement, "I remember that project.",
        )
        self.assertFalse(omitted.accepted)

    def test_assistant_callback_preserves_speaker_through_existing_contract(self):
        query = "What did you tell me before about Game Boy?"
        design = compose_supplemental_historical_prompt(
            query, (_Candidate(),), active_truth_scope_id="scope-real",
        )
        callback = compose_exact_source_callback_contract(design)
        evidence = (MemoryAnswerEvidence(
            "message-1", "historical_conversation_only", "assistant",
            "unknown_scope", "assertion", _Candidate.content,
        ),)
        requirement = compose_memory_answer_requirement(
            query, evidence, callback_contract=callback,
        )
        good = validate_memory_answer_response(
            requirement, "I remember telling you a little story involving a Game Boy.",
        )
        bad = validate_memory_answer_response(
            requirement, "You told me a little story involving a Game Boy.",
        )
        self.assertTrue(good.accepted, good)
        self.assertFalse(bad.accepted)
        self.assertIn("speaker_attribution_error", bad.violations)

    def test_exact_source_fallback_with_quoted_terminal_punctuation_revalidates(self):
        query = "What do you remember me saying about one of my projects?"
        candidate = _Candidate(
            content="I couldn't finish the project today.",
            speaker_role="user",
        )
        design = compose_supplemental_historical_prompt(
            query, (candidate,), active_truth_scope_id="scope-real",
        )
        callback = compose_exact_source_callback_contract(design)
        requirement = compose_memory_answer_requirement(
            query,
            (MemoryAnswerEvidence(
                "message-1", "historical_conversation_only", "user",
                "unknown_scope", "assertion", candidate.content,
            ),),
            callback_contract=callback,
        )

        result = validate_memory_answer_response(
            requirement, requirement.fallback_dialogue,
        )

        self.assertTrue(result.accepted, result)
        self.assertTrue(result.grounded_source_communicated)
        self.assertEqual(1, result.retrospective_claim_count)
        self.assertEqual(0, result.unsupported_retrospective_claim_count)

    def test_unknown_scope_historical_source_cannot_be_promoted_to_right_now(self):
        query = "What do you remember me saying about one of my projects?"
        candidate = _Candidate(
            content="I couldn't finish the project today.",
            speaker_role="user",
        )
        design = compose_supplemental_historical_prompt(
            query, (candidate,), active_truth_scope_id="scope-real",
        )
        requirement = compose_memory_answer_requirement(
            query,
            (MemoryAnswerEvidence(
                "message-1", "historical_conversation_only", "user",
                "unknown_scope", "assertion", candidate.content,
            ),),
            callback_contract=compose_exact_source_callback_contract(design),
        )

        result = validate_memory_answer_response(
            requirement,
            "You said you couldn't finish the project that day. Things are tricky for you right now.",
        )

        self.assertFalse(result.accepted)
        self.assertIn("unknown_scope_promoted_to_current_truth", result.violations)

    def test_governed_current_fact_is_typed_and_must_communicate(self):
        evidence = (MemoryAnswerEvidence(
            "identity.name", "governed_current_fact", "", "real_world",
            "assertion", "", "identity.name", "Mara",
        ),)
        requirement = compose_memory_answer_requirement("What is my name?", evidence)
        self.assertTrue(validate_memory_answer_response(requirement, "Your name is Mara.").accepted)
        omitted = validate_memory_answer_response(requirement, "I remember your name.")
        self.assertFalse(omitted.accepted)

    def test_non_memory_turn_has_no_added_behavior_and_repair_is_bounded(self):
        requirement = compose_memory_answer_requirement("Say something silly.", ())
        self.assertFalse(requirement.triggered)
        self.assertTrue(validate_memory_answer_response(requirement, "Banana hat!").accepted)
        memory = self._no_evidence("Which telescope did I say I owned?")
        repair = memory_answer_repair_prompt(memory, "You owned a refractor.")
        self.assertIn("COMPLETE draft", repair)
        self.assertIn("no_grounded_evidence", repair)
        self.assertLess(len(repair), 2000)

    def test_non_memory_opinion_rejects_unsupported_retrospective_decoration(self):
        query = "What do you think I like to do?"
        decision = decide_memory_query(query)
        requirement = compose_memory_answer_requirement(
            query, (), memory_query_decision=decision,
        )
        recent = (
            {"role": "user", "content": "Do you know if I told you about my cat?"},
            {"role": "assistant", "content": "You told me about your cat."},
            {"role": "user", "content": query},
        )
        bound = bind_memory_answer_source_containment(
            requirement, query, recent, memory_query_decision=decision,
        )
        result = validate_memory_answer_response(
            bound,
            "I think you enjoy quiet projects. You previously described your super pampered cat.",
        )
        self.assertFalse(result.accepted)
        self.assertEqual(1, result.retrospective_claim_count)
        self.assertIn("unsupported_retrospective_claim", result.violations)
        self.assertEqual(
            "I think you enjoy quiet projects.",
            remove_unsupported_retrospective_sentences(
                bound,
                "I think you enjoy quiet projects. You previously described your super pampered cat.",
            ),
        )

    def test_complete_response_rejects_unsafe_retrospective_tail(self):
        query = "What do you think about cats?"
        decision = decide_memory_query(query)
        requirement = bind_memory_answer_source_containment(
            compose_memory_answer_requirement(
                query, (), memory_query_decision=decision,
            ),
            query,
            ({"role": "user", "content": query},),
            memory_query_decision=decision,
        )
        result = validate_memory_answer_response(
            requirement,
            "Cats can be delightful companions. From what you've told me, yours is pampered.",
        )
        self.assertFalse(result.accepted)
        self.assertIn("unsupported_retrospective_claim", result.violations)

    def test_retrospective_variants_preserve_specific_source_ownership(self):
        query = "What do you think about cats?"
        decision = decide_memory_query(query)
        requirement = bind_memory_answer_source_containment(
            compose_memory_answer_requirement(
                query, (), memory_query_decision=decision,
            ),
            query,
            ({"role": "user", "content": query},),
            memory_query_decision=decision,
        )
        for response in (
            "I recall you mentioning a pampered cat.",
            "I recall telling you about a Game Boy.",
            "We were talking about a pampered cat before.",
            "I recall us discussing a telescope.",
        ):
            with self.subTest(response=response):
                result = validate_memory_answer_response(requirement, response)
                self.assertFalse(result.accepted)
                self.assertEqual(1, result.retrospective_claim_count)
                self.assertEqual(1, result.unsupported_retrospective_claim_count)

    def test_retrospective_diagnostics_count_each_rejected_claim(self):
        query = "What do you think about cats?"
        decision = decide_memory_query(query)
        requirement = bind_memory_answer_source_containment(
            compose_memory_answer_requirement(
                query, (), memory_query_decision=decision,
            ),
            query,
            ({"role": "user", "content": query},),
            memory_query_decision=decision,
        )
        result = validate_memory_answer_response(
            requirement,
            "You told me your cat is pampered. You mentioned owning a telescope.",
        )
        self.assertEqual(2, result.retrospective_claim_count)
        self.assertEqual(2, result.unsupported_retrospective_claim_count)

    def test_current_user_assertion_supports_immediate_continuity_only(self):
        query = "What do you think about that?"
        decision = decide_memory_query(query)
        requirement = compose_memory_answer_requirement(
            query, (), memory_query_decision=decision,
        )
        asserted = bind_memory_answer_source_containment(
            requirement,
            query,
            (
                {"role": "user", "content": "My dog is energetic."},
                {"role": "user", "content": query},
            ),
            memory_query_decision=decision,
        )
        supported = validate_memory_answer_response(
            asserted, "You just told me your dog is energetic; that sounds lively.",
        )
        self.assertTrue(supported.accepted, supported)
        self.assertEqual(1, supported.retrospective_claim_count)

        questioned = bind_memory_answer_source_containment(
            requirement,
            query,
            (
                {"role": "user", "content": "Do you remember anything about my dog?"},
                {"role": "user", "content": query},
            ),
            memory_query_decision=decision,
        )
        unsupported = validate_memory_answer_response(
            questioned, "You told me your dog is spoiled.",
        )
        self.assertFalse(unsupported.accepted)

    def test_known_v2_assistant_history_supports_assistant_attribution(self):
        query = "What do you think about old games?"
        decision = decide_memory_query(query)
        requirement = bind_memory_answer_source_containment(
            compose_memory_answer_requirement(
                query, (), memory_query_decision=decision,
            ),
            query,
            ({"role": "user", "content": query},),
            memory_query_decision=decision,
            additional_support=(MemoryAnswerEvidence(
                "source-game", "historical_conversation_only", "assistant",
                "unknown_scope", "assertion", "I mentioned a Game Boy story.",
            ),),
        )
        result = validate_memory_answer_response(
            requirement, "I remember mentioning a Game Boy story before.",
        )
        self.assertTrue(result.accepted, result)


if __name__ == "__main__":
    unittest.main()
