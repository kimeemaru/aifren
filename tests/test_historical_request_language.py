"""Recall request wording is not evidence of the requested historical subject."""
import unittest
from unittest.mock import patch
import test_v2_runtime_recovery as recovery_tests


class HistoricalRequestLanguageTests(unittest.TestCase):
    setUp=recovery_tests.V2RuntimeRecoveryTests.setUp
    open_service=recovery_tests.V2RuntimeRecoveryTests.open_service

    def seed(self, include_subject=False):
        scope=self.service.truth_scope_provenance()
        for i in range(8):
            self.conversation.add_user_message('Okay.',truth_scope=scope)
            self.conversation.add_assistant_message(
                f'I remember that earlier conversation together. Unrelated note {i}.',truth_scope=scope)
        if include_subject:
            self.conversation.add_user_message('Tell me about your activity.',truth_scope=scope)
            self.conversation.add_assistant_message('I built a tower beside the window.',truth_scope=scope)
        self.conversation.save()
        self.service.maintain_canonical_observers()

    def test_request_adverbs_do_not_supply_a_missing_subject(self):
        self.seed()
        # Use the existing controlled semantic override: the lexical path is
        # actually healthy and there is no source about an observatory.
        with patch.object(self.authority.recall.semantic,'_semantic_rows',return_value=[]):
            result=self.service.process_text_turn(
                'Earlier in our conversation, what did you say about the observatory?',speak=False)
        self.assertTrue(result.succeeded,result.error)
        self.assertTrue(self.service._last_memory_authority_diagnostics['authoritative_no_evidence'])
        self.assertNotIn('Unrelated note',result.reply)
        self.assertEqual([],self.llm.calls)

    def test_specific_subject_survives_earlier_and_conversation_variants(self):
        self.seed(include_subject=True)
        self.llm.response='I said, "I built a tower beside the window."'
        for query in ('What did you tell me about a tower earlier?',
                      'Earlier in our conversation, what did you say about the tower?'):
            with self.subTest(query=query),patch.object(self.authority.recall.semantic,'_semantic_rows',return_value=[]):
                result=self.service.process_text_turn(query,speak=False)
                self.assertTrue(result.succeeded,result.error)
                self.assertIn('window',result.reply)
                self.assertNotIn('Unrelated note',result.reply)
                admitted=self.service._last_memory_authority_diagnostics['admitted_items']
                self.assertTrue(admitted)
                self.assertTrue(all(r['canonical_record_id'].startswith('conversation-record-17-') for r in admitted))

    def test_raw_inflected_topic_reaches_historical_fts_before_normalized_admission(self):
        scope = self.service.truth_scope_provenance()
        self.conversation.add_user_message('Tell me about your activity.', truth_scope=scope)
        self.conversation.add_assistant_message('I enjoy chasing butterflies beside the brook.', truth_scope=scope)
        self.conversation.save()
        self.service.maintain_canonical_observers()
        self.llm.response = 'I said, "I enjoy chasing butterflies beside the brook."'
        with patch.object(self.authority.recall.semantic, '_semantic_rows', return_value=[]):
            result = self.service.process_text_turn('What did you tell me about chasing earlier?', speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertIn('butterflies', result.reply)
        self.assertFalse(self.service._last_memory_authority_diagnostics.get('authoritative_no_evidence', False))
        self.assertEqual(1, len(self.llm.calls))

    def test_relevant_record_beyond_budget_is_unavailable_not_healthy_absence(self):
        scope = self.service.truth_scope_provenance()
        self.conversation.add_user_message('Tell me about your activity.', truth_scope=scope)
        self.conversation.add_assistant_message(
            'I described the observatory ' + 'in careful detail ' * 70 + 'but I never visited it.',
            truth_scope=scope)
        self.conversation.save()
        self.service.maintain_canonical_observers()
        committed = list(self.conversation.messages)
        with patch.object(self.authority.recall.semantic, '_semantic_rows', return_value=[]):
            result = self.service.process_text_turn('What did you tell me about the observatory earlier?', speak=False)
        self.assertFalse(result.succeeded)
        self.assertEqual("I can't check that memory right now. Please try again in a moment.", result.error)
        self.assertEqual(committed, self.conversation.messages)
        self.assertFalse(self.service._last_memory_authority_diagnostics.get('authoritative_no_evidence', False))
        self.assertEqual('incomplete', self.service._last_memory_authority_diagnostics['retrieval_health'])
        self.assertEqual('candidate_bound', self.service._last_memory_authority_diagnostics['retrieval_error_code'])
        self.assertEqual([], self.llm.calls)
        self.assertNotIn("don't remember", str(result.reply).lower())
        self.assertTrue(self.service.process_text_turn('Hello again.', speak=False).succeeded)
