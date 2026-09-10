"""Canonical source order is required, not a matching value somewhere in history."""
import unittest
import uuid
from unittest.mock import patch

import test_v2_runtime_recovery as recovery_tests
from memory_query_decision import decide_memory_query


class HistoricalOrderingServiceTests(unittest.TestCase):
    setUp = recovery_tests.V2RuntimeRecoveryTests.setUp
    open_service = recovery_tests.V2RuntimeRecoveryTests.open_service
    reopen = recovery_tests.V2RuntimeRecoveryTests.reopen

    def learn(self, *texts):
        for text in texts:
            self.assertTrue(self.service.process_text_turn(text, speak=False).succeeded)
        self.service.maintain_canonical_observers()

    def ask(self, query):
        self.service.maintain_canonical_observers()
        # Healthy deterministic lexical lookup; no semantic quality claim.
        with patch.object(self.authority.recall.semantic, '_semantic_rows', return_value=[]):
            return self.service.process_text_turn(query, speak=False)

    def test_before_correction_retains_predecessor_after_reopen(self):
        self.learn('My favorite color is green.', 'Actually, my favorite color is blue.')
        self.reopen()
        result = self.ask('What favorite color did I say before I changed it to blue?')
        self.assertTrue(result.succeeded, result.error)
        self.assertIn('green', result.reply)
        self.assertNotIn('was blue', result.reply)

    def test_explicit_anchor_before_first_correction_not_later_correction(self):
        self.learn('My favorite color is green.', 'Actually, my favorite color is blue.',
                   'Actually, my favorite color is orange.')
        answer = self.ask('What did I say my favorite color was before I corrected it to blue?')
        self.assertTrue(answer.succeeded, answer.error)
        self.assertIn('green', answer.reply)
        self.assertNotIn('orange', answer.reply)

    def test_current_value_does_not_acquire_historical_order(self):
        self.learn('My favorite color is green.', 'Actually, my favorite color is blue.')
        self.llm.response='Your favorite color is blue.'
        result=self.ask('What is my current favorite color?')
        self.assertTrue(result.succeeded,result.error)
        self.assertIn('blue',result.reply)
        self.assertEqual('current',decide_memory_query('What is my current favorite color?').time_semantics)
        self.assertIsNone(decide_memory_query('What is my favorite color?').source_order)

    def test_bounded_after_statement_uses_later_source(self):
        self.learn('My favorite color is green.', 'Actually, my favorite color is blue.')
        result=self.ask('What did I say about my favorite color after I said it was green?')
        self.assertTrue(result.succeeded,result.error)
        self.assertIn('blue',result.reply)
        self.assertNotIn('was green',result.reply)

    def test_unrelated_same_topic_does_not_supply_value(self):
        self.learn('The room has green walls and blue cushions.', 'My favorite color is green.',
                   'Actually, my favorite color is blue.', 'The blue room is bright.')
        result=self.ask('What favorite color did I say before I changed it to blue?')
        self.assertIn('green',result.reply)
        self.assertNotIn('room',result.reply)

    def test_multiple_predecessors_are_ambiguous_not_ranked(self):
        self.learn('My favorite color is green.', 'Actually, my favorite color is red.',
                   'Actually, my favorite color is blue.')
        before=len(self.llm.calls)
        result=self.ask('What favorite color did I say before I changed it to blue?')
        self.assertTrue(result.succeeded,result.error)
        self.assertIn('clarify',result.reply)
        self.assertEqual(before,len(self.llm.calls))

    def test_missing_anchor_does_not_become_unanchored_recall(self):
        self.learn('My favorite color is green.')
        for q in ('What favorite color did I say before I changed it to blue?',
                  'What favorite color did I say after the picnic?'):
            with self.subTest(q=q):
                result=self.ask(q)
                self.assertTrue(result.succeeded,result.error)
                self.assertIn('clarify',result.reply)
                self.assertNotIn('was green',result.reply)
        self.assertIsNone(decide_memory_query('What did I say about my favorite color?').source_order)
        self.assertIsNone(decide_memory_query('What did you tell me before about the telescope?').source_order)

    def test_nonassertive_predecessors_are_not_positive_values(self):
        self.learn('Maybe my favorite color is green.', 'My favorite color is not red.',
                   'Actually, my favorite color is blue.')
        result=self.ask('What favorite color did I say before I changed it to blue?')
        self.assertIn('clarify',result.reply)
        self.assertNotIn('was green',result.reply)
        self.assertNotIn('was red',result.reply)

    def test_wrong_speaker_cannot_supply_anchor_or_predecessor(self):
        scope=self.service.truth_scope_provenance()
        self.conversation.add_user_message('Tell me something.',truth_scope=scope)
        self.conversation.add_assistant_message('My favorite color is orange.',truth_scope=scope)
        self.conversation.save()
        self.learn('Actually, my favorite color is blue.')
        result=self.ask('What favorite color did I say before I changed it to blue?')
        self.assertIn('clarify',result.reply)
        self.assertNotIn('orange',result.reply)

    def test_different_scope_cannot_supply_predecessor(self):
        scope_id='scope-'+str(uuid.uuid4())
        self.h.writer.store.connection.execute(
            "INSERT INTO truth_scopes VALUES (?, ?, 'scenario', 'Synthetic', 'inactive', 2, 2)",
            (self.h.character_id,scope_id))
        scope={'kind':'scenario','scope_id':scope_id}
        self.conversation.add_user_message('My favorite color is green.',truth_scope=scope)
        self.conversation.add_assistant_message('Understood.',truth_scope=scope)
        self.conversation.save()
        self.learn('Actually, my favorite color is blue.')
        result=self.ask('What favorite color did I say before I changed it to blue?')
        self.assertIn('clarify',result.reply)
        self.assertNotIn('was green',result.reply)

    def test_family_is_not_color_specific_and_order_is_carried_to_provider(self):
        self.learn('My favorite animal is a fox.', 'Actually, my favorite animal is a rabbit.')
        self.llm.response='You said your favorite animal was a fox.'
        result=self.ask('What favorite animal did I say before I updated it to a rabbit?')
        self.assertTrue(result.succeeded,result.error)
        self.assertIn('fox',result.reply)
        context=str(self.llm.calls[-1])
        self.assertIn('source_order',context)
        self.assertIn('before',context)

    def test_candidate_page_exhaustion_is_unavailable(self):
        scope=self.service.truth_scope_provenance()
        for i in range(66):
            self.conversation.add_user_message(f'My favorite color is green. Extra note {i}.',truth_scope=scope)
            self.conversation.add_assistant_message('Understood.',truth_scope=scope)
        self.conversation.add_user_message('Actually, my favorite color is blue.',truth_scope=scope)
        self.conversation.add_assistant_message('Understood.',truth_scope=scope)
        self.conversation.save()
        for _ in range(6):self.service.maintain_canonical_observers()
        result=self.ask('What favorite color did I say before I changed it to blue?')
        self.assertFalse(result.succeeded)
        self.assertIn("can't check",result.error)

    def test_provider_cannot_reverse_the_proven_relation(self):
        self.learn('My favorite color is green.', 'Actually, my favorite color is blue.')
        self.llm.response='You said your favorite color was green after you changed it to blue.'
        result=self.ask('What favorite color did I say before I changed it to blue?')
        self.assertTrue(result.succeeded,result.error)
        self.assertIn('green',result.reply)
        self.assertNotIn('after',result.reply)
        self.assertTrue(self.service._last_memory_authority_diagnostics['fallback_used'])
