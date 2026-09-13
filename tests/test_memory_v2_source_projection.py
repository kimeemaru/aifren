"""Relevant canonical substance must survive the bounded production prompt."""
import unittest
from unittest.mock import patch
import test_v2_runtime_recovery as recovery_tests


class HistoricalProjectionServiceTests(unittest.TestCase):
    setUp = recovery_tests.V2RuntimeRecoveryTests.setUp
    open_service = recovery_tests.V2RuntimeRecoveryTests.open_service
    reopen = recovery_tests.V2RuntimeRecoveryTests.reopen

    def seed(self, *sources):
        scope = self.service.truth_scope_provenance()
        for source in sources:
            self.conversation.add_user_message('Tell me about your activity.', truth_scope=scope)
            self.conversation.add_assistant_message(source, truth_scope=scope)
        self.conversation.save()
        self.service.maintain_canonical_observers()

    def ask(self, query):
        with patch.object(self.authority.recall.semantic, '_semantic_rows', return_value=[]):
            return self.service.process_text_turn(query, speak=False)

    def test_long_source_middle_assertion_survives_restart_and_fallback(self):
        self.seed('The morning was quiet. ' * 15 +
                  'I built a tower beside the window, but it fell onto the blue rug. ' +
                  'The evening was quiet. ' * 35)
        self.reopen()
        self.llm.response = 'I do not remember.'
        result = self.ask('What did you tell me about a tower earlier?')
        self.assertTrue(result.succeeded, result.error)
        self.assertIn('blue rug', result.reply)
        self.assertIn('fell', result.reply)
        self.assertNotIn('morning was quiet', result.reply)
        self.assertTrue(self.service._last_memory_authority_diagnostics['fallback_used'])

    def test_action_and_question_do_not_displace_requested_substance(self):
        self.seed('*looks toward the tower* Would you like to build a tower?',
                  'I built a tower beside the window, but it fell onto the blue rug.')
        self.llm.response = 'I said I built a tower beside the window, but it fell onto the blue rug.'
        result = self.ask('What did you tell me about a tower earlier?')
        self.assertTrue(result.succeeded, result.error)
        self.assertIn('blue rug', result.reply)
        self.assertNotIn('Would you', result.reply)

    def test_unsafe_long_sentence_does_not_become_unrelated_prefix(self):
        self.seed('I discussed the observatory ' + 'in careful detail ' * 70 +
                  'but I never visited it.')
        self.llm.response = 'I visited the observatory.'
        result = self.ask('What did you tell me about the observatory earlier?')
        self.assertFalse(result.succeeded)
        self.assertIn("can't check", result.error)
        self.assertEqual([], self.llm.calls)

    def test_disjoint_passages_stay_separate_and_both_required(self):
        self.seed('I built the tower beside the window. ' + 'The day was quiet. ' * 15 +
                  'I painted the bridge silver.')
        self.llm.response = 'I do not remember.'
        result = self.ask('What did you tell me about the tower and bridge earlier?')
        self.assertTrue(result.succeeded, result.error)
        self.assertIn('window', result.reply)
        self.assertIn('silver', result.reply)
        items = self.service._last_memory_authority_diagnostics['admitted_items']
        self.assertEqual(2, len(items))
        self.assertEqual(items[0]['canonical_record_id'], items[1]['canonical_record_id'])
        self.assertLess(items[0]['source_segments'][0]['end'], items[1]['source_segments'][0]['start'])
        self.assertIn('additional_source_segments', str(self.llm.calls))

    def test_partial_callback_projection_is_unavailable_not_empty_recall(self):
        self.seed('I built the tower beside the window. ' + 'The day was quiet. ' * 15 +
                  'I painted the bridge silver.')
        with patch('aifren.continuity.memory_v2_prompt_admission_shadow.MAX_PROMPT_ITEMS', 1):
            result = self.ask('What did you tell me about the tower and bridge earlier?')
        self.assertFalse(result.succeeded)
        self.assertIn("can't check", result.error)
        self.assertEqual([], self.llm.calls)

    def test_neighboring_qualification_is_not_discarded(self):
        self.seed('The morning was quiet. ' * 10 +
                  'I visited the observatory. That was only a dream. ' +
                  'The evening was quiet. ' * 20)
        self.llm.response = 'I do not remember.'
        result = self.ask('What did you tell me about the observatory earlier?')
        self.assertTrue(result.succeeded, result.error)
        self.assertIn('only a dream', result.reply)

    def test_topical_conditional_and_question_do_not_beat_substantive_account(self):
        self.seed('Just remember: if you build a tower, I might come over to help!',
                  'Quick, what are we chasing next?!',
                  'I like chasing butterflies beside the brook.',
                  'I built a tower beside the window, but it fell onto the blue rug.')
        self.llm.response = 'I do not remember.'
        first = self.ask('What did you tell me about a tower earlier?')
        self.assertIn('blue rug', first.reply)
        second = self.ask('What did you tell me about chasing earlier?')
        self.assertIn('butterflies', second.reply)
        self.assertNotIn('what are we', second.reply)


class ExactSourceProjectionTests(unittest.TestCase):
    def project(self, content, query='tower', source_id=None):
        from aifren.continuity.memory_v2_episode_compaction import canonical_record_id
        from aifren.continuity.memory_v2_source_projection import project_source
        from aifren.memory_v2_store.retrieval import _tokens
        messages = [{'role': 'assistant', 'content': content}]
        return project_source(messages, 0, source_id or canonical_record_id(0, messages[0]),
                              'assistant', _tokens(query), _tokens)

    def test_source_edits_are_unavailable_even_with_same_index_and_role(self):
        from aifren.continuity.memory_v2_episode_compaction import canonical_record_id
        old = canonical_record_id(0, {'role': 'assistant', 'content': 'I built a tower.'})
        self.assertEqual('source_changed', self.project('I broke a tower.', source_id=old).reason)

    def test_exact_offsets_preserve_emphasis_punctuation_and_negation(self):
        raw = '*waves* I **never** built the tower; I only planned it. *smiles*'
        result = self.project(raw)
        self.assertEqual(1, len(result.segments))
        segment = result.segments[0]
        self.assertEqual(raw[segment.start:segment.end], segment.text)
        self.assertIn('**never**', segment.text)
        self.assertNotIn('waves', segment.text)

    def test_more_required_passages_than_bound_are_unavailable(self):
        text = 'I built a tower. Other things happened. I visited a bridge. The day ended. I saw a comet.'
        self.assertEqual('candidate_bound', self.project(text, 'tower bridge comet').reason)

    def test_emote_only_topic_never_becomes_spoken_assertion(self):
        self.assertFalse(self.project('*looks at the tower* Hello there!').segments)

    def test_leading_emphasis_keeps_its_original_balanced_markers(self):
        result = self.project('**Never** build that tower.')
        self.assertEqual('**Never** build that tower.', result.segments[0].text)

    def test_cross_sentence_hypothetical_frame_is_retained(self):
        result = self.project('I imagined this scene. I built a tower beside the window.')
        self.assertIn('imagined', result.segments[0].text)

    def test_generic_context_cannot_keep_half_a_source_projection(self):
        from aifren.memory_v2_store.models import HistoricalSourceSegment, RetrievalHealth, RetrievalLaneHealth
        from aifren.continuity.memory_v2_hybrid_recall import HybridRecallCandidate
        from aifren.continuity.memory_v2_replacement_shadow import compose_v2_replacement_context
        text = 'I built a tower. Then I painted the bridge.'
        a, b = text.index('Then'), len(text)
        candidate = HybridRecallCandidate('c', 'historical_evidence', text[:a].strip(), 8, (), 's',
            'historical_real_world', speaker_role='user', speech_act='assertion',
            source_class='ordinary_conversation', scope_state='real_world',
            canonical_record_id='exact-canonical', canonical_index=1,
            source_segments=(HistoricalSourceSegment(0, a-1, text[:a-1], b),
                             HistoricalSourceSegment(a, b, text[a:b], b)))
        with patch('aifren.continuity.memory_v2_replacement_shadow.MAX_V2_REPLACEMENT_ITEMS', 1):
            context = compose_v2_replacement_context([], 'Do you remember my tower and bridge?',
                [candidate], active_truth_scope_id='s',
                lookup_health=RetrievalHealth((RetrievalLaneHealth('claims', 'complete'),)))
        self.assertEqual((), context.items)
        self.assertTrue(context.memory_answer_requirement.lookup_unavailable)
        self.assertIn("can't check", context.memory_answer_requirement.fallback_dialogue)
