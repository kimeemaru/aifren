from types import SimpleNamespace
import unittest
from unittest.mock import patch

from memory_query_decision import MemoryQueryDecision, decide_memory_query
from memory_v2_authority import DevelopmentV2MemoryAuthority
import memory_v2_authority


class MemoryQueryDecisionTests(unittest.TestCase):
    def test_required_query_families_have_one_typed_classification(self):
        cases = (
            ("Do you know if I told you about my cat?", "user_historical_source", "user"),
            ("Have I ever told you about my cat?", "user_historical_source", "user"),
            ("Did I mention my cat before?", "user_historical_source", "user"),
            ("Do you remember me talking about my cat?", "user_historical_source", "user"),
            ("What do you remember me saying about my cat?", "user_historical_source", "user"),
            ("Did you ever tell me anything about Game Boy?", "assistant_historical_source", "assistant"),
            ("What did you tell me before about Game Boy?", "assistant_historical_source", "assistant"),
            ("Do you remember saying anything about that?", "assistant_historical_source", "assistant"),
            ("Have we talked about cats before?", "shared_historical_conversation", "shared"),
            ("What did we discuss about old games?", "shared_historical_conversation", "shared"),
        )
        for query, intent, speaker in cases:
            with self.subTest(query=query):
                decision = decide_memory_query(query)
                self.assertIsInstance(decision, MemoryQueryDecision)
                self.assertTrue(decision.applicable)
                self.assertEqual(intent, decision.intent)
                self.assertEqual(speaker, decision.requested_speaker)
                self.assertEqual("historical", decision.time_semantics)
                self.assertTrue(decision.authoritative_no_evidence_allowed)
                self.assertTrue(decision.contain_recent_context)
                self.assertTrue(decision.exact_source_must_communicate)

    def test_opinion_and_non_memory_controls_do_not_become_memory_queries(self):
        for query in (
            "What do you think about cats?",
            "What do you think I like to do?",
            "What kind of weather do you like?",
        ):
            with self.subTest(query=query):
                decision = decide_memory_query(query)
                self.assertFalse(decision.applicable)
                self.assertEqual("assistant_opinion", decision.intent)
                self.assertFalse(decision.authoritative_no_evidence_allowed)
                self.assertFalse(decision.contain_recent_context)

        generic = decide_memory_query("Tell me something interesting about astronomy.")
        self.assertFalse(generic.applicable)
        self.assertEqual("non_memory", generic.intent)
        self.assertEqual("generic_reasoning", generic.retrieval_intent)

    def test_relation_and_subject_projection_are_structural_not_topic_specific(self):
        cat = decide_memory_query("Do you know if I told you about my cat?")
        telescope = decide_memory_query("Do you know if I told you about my telescope?")
        ownership = decide_memory_query("Which motorcycle did I say I owned?")
        self.assertEqual(cat.intent, telescope.intent)
        self.assertEqual("your cat", cat.subject_reference)
        self.assertEqual("your telescope", telescope.subject_reference)
        self.assertEqual("ownership", ownership.requested_relation)

    def test_historical_question_speech_act_comes_from_shared_decision(self):
        for query in (
            "Did I ever ask about a telescope?",
            "Do you know if I asked about a telescope?",
            "What did I ask about telescopes?",
        ):
            with self.subTest(query=query):
                decision = decide_memory_query(query)
                self.assertTrue(decision.applicable)
                self.assertEqual("user_historical_source", decision.intent)
                self.assertEqual("question", decision.requested_speech_act)

    def test_concrete_followup_attribute_is_typed_and_source_required(self):
        for query, relation in (
            ("Which programming language was connected to it?", "programming_language"),
            (
                "Something to do with a certain programming language. "
                "Do you remember which one?",
                "programming_language",
            ),
            ("What language was that project in?", "programming_language"),
            ("Which place was it?", "place"),
            ("Who was that person?", "person"),
            ("What was its name?", "identity"),
        ):
            with self.subTest(query=query):
                decision = decide_memory_query(query)
                self.assertTrue(decision.applicable)
                self.assertEqual("grounded_followup_attribute", decision.intent)
                self.assertEqual(relation, decision.requested_relation)
                self.assertEqual("shared", decision.requested_speaker)
                self.assertTrue(decision.exact_source_must_communicate)

        vague = decide_memory_query("What else was connected to that?")
        self.assertNotEqual("grounded_followup_attribute", vague.intent)

    def test_named_new_project_does_not_inherit_deictic_source(self):
        for query in (
            "Which programming language was my astronomy project written in?",
            "Which programming language was your separate compiler project written in?",
        ):
            with self.subTest(query=query):
                self.assertNotEqual("grounded_followup_attribute", decide_memory_query(query).intent)

    def test_authority_passes_the_exact_decision_to_all_runtime_consumers(self):
        query = "Do you know if I told you about my cat?"
        decision = decide_memory_query(query)
        captured = {}

        class Recall:
            def retrieve(self, _query, *, memory_query_decision):
                captured["retrieval"] = memory_query_decision
                return SimpleNamespace(candidates=(), abstention_reason="no_candidates")

        original_select = memory_v2_authority.select_recent_messages
        original_bind = memory_v2_authority.bind_memory_answer_source_containment

        def select(messages, policy, **kwargs):
            captured["recent_context"] = kwargs.get("memory_query_decision")
            return original_select(messages, policy, **kwargs)

        def bind(requirement, query_text, recent, **kwargs):
            captured["governance"] = kwargs.get("memory_query_decision")
            return original_bind(requirement, query_text, recent, **kwargs)

        authority = DevelopmentV2MemoryAuthority(
            SimpleNamespace(active_truth_scope_id=lambda _character: "scope-current"),
            "11111111-1111-4111-8111-111111111111",
            ({"role": "user", "content": query},),
            recall=Recall(),
        )
        with patch.object(memory_v2_authority, "select_recent_messages", select), patch.object(
            memory_v2_authority, "bind_memory_answer_source_containment", bind,
        ):
            turn = authority.prepare(
                query,
                active_truth_scope_id="scope-current",
                memory_query_decision=decision,
            )

        self.assertIs(decision, turn.memory_query_decision)
        self.assertIs(decision, turn.requirement.memory_query_decision)
        self.assertIs(decision, captured["retrieval"])
        self.assertIs(decision, captured["recent_context"])
        self.assertIs(decision, captured["governance"])

    def test_decision_has_no_mutable_character_or_scope_state(self):
        first = decide_memory_query("Have I ever told you about cats?")
        second = decide_memory_query("What did you tell me about games?")
        self.assertNotEqual(first.requested_speaker, second.requested_speaker)
        self.assertFalse(hasattr(first, "character_id"))
        self.assertFalse(hasattr(first, "truth_scope_id"))


if __name__ == "__main__":
    unittest.main()
