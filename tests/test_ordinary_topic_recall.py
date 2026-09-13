"""Ordinary named-topic callbacks use canonical V2 recall after restart."""
import unittest
from unittest.mock import patch

import test_v2_runtime_recovery as recovery


class OrdinaryTopicRecallTests(unittest.TestCase):
    open_service = recovery.V2RuntimeRecoveryTests.open_service
    reopen = recovery.V2RuntimeRecoveryTests.reopen

    def setUp(self):
        recovery.V2RuntimeRecoveryTests.setUp(self)
        self.llm.companion_memory_realization = True

    def seed_topic(self, statement):
        self.source_index = len(self.conversation.messages)
        for text in (statement, "Let's discuss how a bicycle gear works."):
            result = self.service.process_text_turn(text, speak=False)
            self.assertTrue(result.succeeded, result.error)
        self.reopen()
        self.assertIsNone(self.authority._recall_anchor)

    def callback(self, question):
        prepared = []
        original_messages = [dict(item) for item in self.conversation.messages]
        original = self.authority.prepare

        def capture(*args, **kwargs):
            turn = original(*args, **kwargs)
            prepared.append(turn)
            return turn

        # Only remove toy-vector noise; canonical observation, FTS, source
        # projection, admission, realization and publication are production.
        with patch.object(self.authority, "prepare", side_effect=capture), patch.object(
            self.authority.recall.semantic, "_semantic_rows", return_value=[]
        ):
            result = self.service.process_text_turn(question, speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(1, len(prepared))
        self.assertEqual(original_messages, self.conversation.messages[:len(original_messages)])
        return result, prepared[0]

    def test_retained_topic_return_after_restart_is_source_governed(self):
        self.seed_topic(
            "I have started a balcony garden with parsley and rosemary. "
            "I want to keep it manageable."
        )
        self.llm.response = "You picked rosemary and mint!"
        result, turn = self.callback(
            "Back to the balcony garden: which two herbs did I choose?"
        )
        self.assertTrue(turn.memory_query_decision.applicable)
        self.assertEqual("user", turn.memory_query_decision.requested_speaker)
        self.assertEqual("historical", turn.memory_query_decision.time_semantics)
        self.assertFalse(turn.recall_anchor_used)
        self.assertEqual({self.source_index}, {item.canonical_index for item in turn.design.items})
        self.assertIn("parsley", turn.requirement.fallback_dialogue)
        self.assertIn("rosemary", result.reply)
        self.assertIn("parsley", result.reply)
        self.assertNotIn("mint", result.reply)
        self.assertEqual("deterministic_grounded_core",
                         self.service._last_memory_authority_diagnostics["memory_realization"])
        self.assertEqual(0, self.service.memory.retrieval_calls)
        self.assertEqual([], self.service.memory.processed)

    def assert_source_answer(self, result, turn, *values):
        self.assertTrue(turn.memory_query_decision.applicable)
        self.assertTrue(turn.requirement.triggered)
        self.assertFalse(turn.recall_anchor_used)
        self.assertEqual({self.source_index}, {item.canonical_index for item in turn.design.items})
        for value in values:
            self.assertIn(value, result.reply)
        self.assertEqual(0, self.service.memory.retrieval_calls)
        self.assertEqual([], self.service.memory.processed)

    def test_other_topic_and_ordinary_wording_preserve_exact_source(self):
        self.seed_topic("I chose amber and ivory tiles for the hallway mosaic.")
        self.llm.response = "You chose silver tiles."
        result, turn = self.callback("Returning to the hallway mosaic, which tiles did I pick?")
        self.assert_source_answer(result, turn, "amber", "ivory")
        self.assertNotIn("silver", result.reply)

    def test_competing_older_topic_is_not_the_named_topic(self):
        self.assertTrue(self.service.process_text_turn(
            "I started a kitchen garden with mint and thyme.", speak=False).succeeded)
        self.seed_topic("I have started a balcony garden with parsley and rosemary.")
        result, turn = self.callback("Back to the balcony garden: which two herbs did I choose?")
        self.assert_source_answer(result, turn, "parsley", "rosemary")
        self.assertNotIn("mint", result.reply)

    def test_ambiguous_deictic_topic_after_restart_does_not_restore_anchor(self):
        self.seed_topic("I chose amber and ivory tiles for the hallway mosaic.")
        result, turn = self.callback("Back to that: which tiles did I choose?")
        self.assertTrue(turn.memory_query_decision.applicable)
        self.assertFalse(turn.design.items)
        self.assertIn("clarify", result.reply)
        self.assertFalse(turn.recall_anchor_used)

    def test_multiple_distinct_sources_for_named_topic_need_clarification(self):
        self.assertTrue(self.service.process_text_turn(
            "I chose amber tiles for the hallway mosaic.", speak=False).succeeded)
        self.seed_topic("I chose ivory tiles for the hallway mosaic.")
        result, turn = self.callback("Back to the hallway mosaic: which tiles did I choose?")
        self.assertFalse(turn.design.items)
        self.assertIn("clarify", result.reply)

    def test_missing_named_topic_cannot_use_a_partial_topic_match(self):
        self.seed_topic("I started a kitchen garden with mint and thyme.")
        result, turn = self.callback("Back to the balcony garden: which two herbs did I choose?")
        self.assertFalse(turn.design.items)
        self.assertNotIn("mint", result.reply)

    def test_source_speaker_is_part_of_ordinary_callback_admission(self):
        scope = self.service.truth_scope_provenance()
        self.conversation.add_user_message("Tell me about your craft.", truth_scope=scope)
        self.conversation.add_assistant_message(
            "I chose amber and ivory tiles for the hallway mosaic.", truth_scope=scope)
        self.conversation.save()
        self.service.maintain_canonical_observers()
        self.reopen()
        self.source_index = 1
        result, turn = self.callback("Back to the hallway mosaic: which tiles did I choose?")
        self.assertFalse(turn.design.items)
        self.assertNotIn("amber", result.reply)
        result, turn = self.callback("Back to the hallway mosaic: which tiles did you choose?")
        self.assert_source_answer(result, turn, "amber", "ivory")
        self.assertEqual("assistant", turn.memory_query_decision.requested_speaker)
        self.assertTrue(all(item.speaker_role == "assistant" for item in turn.design.items))

    def test_current_correction_and_before_value_keep_existing_owners(self):
        for text in ("My favorite color is green.", "Actually, my favorite color is blue."):
            self.assertTrue(self.service.process_text_turn(text, speak=False).succeeded)
        self.reopen()
        for question, value, time_semantics in (
            ("Back to my favorite color: what is my favorite color?", "blue", "current"),
            ("Back to my favorite color: what was my favorite color before I changed it to blue?",
             "green", "historical"),
            ("Back to my favorite color: what was my favorite color after I changed it to blue?",
             None, "historical"),
        ):
            with self.subTest(question=question):
                result, turn = self.callback(question)
                self.assertEqual(time_semantics, turn.memory_query_decision.time_semantics)
                if value is None:
                    # AFTER is strict source ordering: the correction itself
                    # is not a later source. No later assertion exists here.
                    self.assertIn("clarify", result.reply)
                else:
                    self.assertIn(value, result.reply)

    def test_lookup_failure_is_unavailable_not_a_guessed_or_missing_value(self):
        self.seed_topic("I chose amber and ivory tiles for the hallway mosaic.")
        original = [dict(item) for item in self.conversation.messages]
        calls = len(self.llm.calls)
        with patch.object(self.authority.recall, "retrieve", side_effect=RuntimeError("synthetic failure")):
            result = self.service.process_text_turn(
                "Back to the hallway mosaic: which tiles did I choose?", speak=False)
        self.assertFalse(result.succeeded)
        self.assertEqual("lookup_unavailable", self.service._last_memory_authority_diagnostics["absence_kind"])
        self.assertEqual(calls, len(self.llm.calls))
        self.assertEqual(original, self.conversation.messages)

    def test_wrong_scope_cannot_supply_named_topic(self):
        self.assertTrue(self.service.process_text_turn(
            "Let's roleplay that we are in a workshop.", speak=False).succeeded)
        self.seed_topic("I chose amber and ivory tiles for the hallway mosaic.")
        self.assertTrue(self.service.process_text_turn("Back to real life.", speak=False).succeeded)
        self.reopen()
        result, turn = self.callback("Back to the hallway mosaic: which tiles did I choose?")
        self.assertFalse(turn.design.items)
        self.assertNotIn("amber", result.reply)

    def test_unrelated_ordinary_question_remains_ordinary(self):
        self.seed_topic("I chose amber and ivory tiles for the hallway mosaic.")
        for question in ("How do bicycle gears work?", "Back to the hallway mosaic: what should I try next?",
                         "Back to the balcony garden: which herbs would you recommend?",
                         'Is this sentence grammatical: "Back to the hallway mosaic: which tiles did I choose?"'):
            with self.subTest(question=question):
                result, turn = self.callback(question)
                self.assertFalse(turn.memory_query_decision.applicable)
                self.assertFalse(turn.requirement.triggered)


class OrdinaryTopicCharacterIsolationTests(unittest.TestCase):
    def test_switch_and_restart_resolve_only_the_selected_characters_sources(self):
        from test_character_switch_ownership import CharacterSwitchOwnershipTests
        from test_memory_v2_embeddings import ToyEmbeddingProvider

        fixture = CharacterSwitchOwnershipTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.service.llm.companion_memory_realization = True
        fixture._turn("I chose amber and ivory tiles for the hallway mosaic.")
        fixture._turn("Let's discuss how a bicycle gear works.")
        original = [dict(item) for item in fixture.service.conversation.messages]
        question = "Back to the hallway mosaic: which tiles did I choose?"
        with patch('aifren.continuity.memory_v2_authority.MiniLMEmbeddingProvider', ToyEmbeddingProvider):
            fixture._switch(fixture.b)
        result = fixture.service.process_text_turn(question, speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertFalse(fixture.service._last_memory_authority_diagnostics["admitted_items"])
        self.assertNotIn("amber", result.reply)
        fixture.service.close()
        fixture.service = fixture._new_service(fixture.a.character_id)
        fixture.service.llm.companion_memory_realization = True
        with patch.object(fixture.service._memory_v2_authority.recall.semantic, "_semantic_rows", return_value=[]):
            result = fixture.service.process_text_turn(question, speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertIn("amber", result.reply)
        self.assertIn("ivory", result.reply)
        self.assertEqual(original, fixture.service.conversation.messages[:len(original)])


if __name__ == "__main__":
    unittest.main()
