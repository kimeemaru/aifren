"""Complete historical answers must prove descriptions, including in questions."""
import json
import unittest
from dataclasses import replace

import test_memory_v2_ordering as ordering_tests
from aifren.continuity.memory_v2_answer_governance import (
    MemoryAnswerEvidence, compose_memory_answer_requirement,
    validate_memory_answer_response,
)


class HistoricalCommitmentTests(unittest.TestCase):
    source = 'We watched a bronze lantern beside the stone bridge during our quiet picnic.'
    core = 'You said we watched a bronze lantern beside the stone bridge during our quiet picnic.'

    def requirement(self, source=None, *, extra=(), speaker='user'):
        item = MemoryAnswerEvidence('lantern-source', 'historical_conversation_only',
                                   speaker, 'real_world', 'assertion', source or self.source)
        return compose_memory_answer_requirement('Do you remember the bronze lantern?', (item, *extra))

    def assert_admitted(self, text, *, source=None, extra=(), accepted=True):
        result = validate_memory_answer_response(self.requirement(source, extra=extra), text)
        self.assertEqual(accepted, result.accepted, result)

    def test_unattributed_event_with_invented_modifier_is_rejected(self):
        self.assert_admitted('The bronze lantern? How could I forget that shiny thing? '
                             'We watched it beside the stone bridge during our picnic.', accepted=False)

    def test_confirmation_question_cannot_hide_modifier(self):
        self.assert_admitted('You mean the place beside the stone bridge where we watched '
                             'that sparkling bronze lantern?', accepted=False)

    def test_correct_core_cannot_license_extra_condition(self):
        for tail in ('Was the stone bridge flooded?', 'The bronze lantern was cracked.',
                     'It was... was it very bright this time?'):
            with self.subTest(tail=tail):
                self.assert_admitted(self.core + ' ' + tail, accepted=False)

    def test_short_invented_attribute_and_temporal_modifier_are_rejected(self):
        for text in ('You said we watched a red bronze lantern beside the stone bridge.',
                     self.core + ' We watched it yesterday.',
                     self.core + ' The picnic lasted hours.'):
            with self.subTest(text=text):
                self.assert_admitted(text, accepted=False)

    def test_recurrence_needs_source_support(self):
        for tail in ('We watched the bronze lantern again.',
                     'Was that another time we watched the bronze lantern?',
                     'The bronze lantern was still beside the stone bridge.'):
            with self.subTest(tail=tail):
                self.assert_admitted(self.core + ' ' + tail, accepted=False)

    def test_explicit_repeat_in_same_source_is_supported(self):
        self.assert_admitted('You said we watched the bronze lantern again beside the stone bridge.',
            source='We watched the bronze lantern again beside the stone bridge.')

    def test_supported_descriptive_phrase_and_question_remain_valid(self):
        for text in ('You said you wore the green hat.',
                     'You said you wore a green hat. The green hat?',
                     'You said you wore a green hat. Was it green?'):
            with self.subTest(text=text):
                self.assert_admitted(text, source='I wore a green hat.')

    def test_present_reaction_and_typed_action_need_no_historical_witness(self):
        for tail in ('That sounds lovely.', 'I am glad you told me.',
                     '*smiles warmly*', 'Was that right?', 'What happened with the lantern?'):
            with self.subTest(tail=tail):
                self.assert_admitted(self.core + ' ' + tail)

    def test_reaction_wrapper_cannot_hide_extra_remembered_property(self):
        self.assert_admitted(self.core + ' That sounds lovely because the lantern was golden.', accepted=False)

    def test_supported_ellipsis_and_topic_question_preserve_source_polarity(self):
        self.assert_admitted('You said... ...you promised not to disrupt me. Was that right?',
                             source='I promise not to disrupt you.')
        self.assert_admitted('You said you could not finish the project. What happened with the project?',
                             source='I could not finish the project.')

    def test_present_question_wrapper_cannot_hide_added_object_description(self):
        self.assert_admitted(self.core + ' What kind of things are you doing with the golden lantern?', accepted=False)

    def test_other_source_cannot_donate_modifier(self):
        other = MemoryAnswerEvidence('balloon-source', 'historical_conversation_only',
            'user', 'real_world', 'assertion', 'We watched a sparkling balloon beside the stone bridge.')
        self.assert_admitted('The sparkling bronze lantern beside the stone bridge?',
                             extra=(other,), accepted=False)

    def test_unattributed_first_person_and_shared_actor_cannot_change_owner(self):
        for text in ('I watched a bronze lantern beside the stone bridge.',
                     'We watched a bronze lantern beside the stone bridge.'):
            self.assert_admitted(text,source='I watched a bronze lantern beside the stone bridge.',accepted=False)

    def test_other_scope_or_current_support_cannot_donate_historical_property(self):
        for authority,scope in (('historical_conversation_only','scenario'),
                                ('governed_current_fact','real_world')):
            req=self.requirement()
            other=MemoryAnswerEvidence('other-context',authority,'user',scope,'assertion',
                                       'We watched a sparkling bronze lantern beside the stone bridge.')
            req=replace(req,support_ledger=(*req.support_ledger,other))
            result=validate_memory_answer_response(req,'The sparkling bronze lantern beside the stone bridge?')
            self.assertFalse(result.accepted,result)

    def test_ambiguous_description_cannot_pick_a_different_source(self):
        other = MemoryAnswerEvidence('other-lantern', 'historical_conversation_only',
            'user', 'real_world', 'assertion', 'We watched a sparkling bronze lantern beside the stone bridge.')
        self.assert_admitted('We watched the bronze lantern beside the stone bridge. '
                             'Was it sparkling?', extra=(other,), accepted=False)

    def test_modifiers_cannot_move_between_objects_in_one_source(self):
        self.assert_admitted('You said you wore the blue hat.',
                             source='I wore a blue coat and a green hat.', accepted=False)

    def test_every_descriptive_phrase_retains_its_attachment(self):
        self.assert_admitted('You said you wore a red hat and the blue hat.',
                             source='I wore a red hat and a blue coat.',accepted=False)

    def test_attributed_source_relative_day_is_not_a_new_calendar_claim(self):
        source="I couldn't finish the project today."
        self.assert_admitted("You told me you couldn't finish the project that day.",source=source)
        for time in ('yesterday','another day','last week'):
            self.assert_admitted("You told me you couldn't finish the project "+time+'.',source=source,accepted=False)

    def test_generic_recall_is_not_the_remembered_actors_identity(self):
        self.assert_admitted('I remember the bronze lantern.',source='I watched a bronze lantern.')
        self.assert_admitted('I remember I watched the bronze lantern.',source='I watched a bronze lantern.',accepted=False)

    def test_uncertain_source_cannot_become_certain(self):
        self.assert_admitted('You said you wore the green hat.',
                             source='Perhaps I wore a green hat.', accepted=False)

    def test_exact_source_fallback_remains_available(self):
        requirement = self.requirement()
        self.assertTrue(validate_memory_answer_response(requirement, requirement.fallback_dialogue).accepted)


class HistoricalCommitmentServiceTests(unittest.TestCase):
    setUp = ordering_tests.HistoricalOrderingServiceTests.setUp
    open_service = ordering_tests.HistoricalOrderingServiceTests.open_service
    reopen = ordering_tests.HistoricalOrderingServiceTests.reopen
    ask = ordering_tests.HistoricalOrderingServiceTests.ask

    def record_event(self):
        scope = self.service.truth_scope_provenance()
        self.conversation.add_user_message(HistoricalCommitmentTests.source, truth_scope=scope)
        self.conversation.add_assistant_message('The bronze lantern!', truth_scope=scope)
        self.conversation.save()

    def test_normalized_unsafe_complete_reply_uses_safe_fallback_without_zero_yield_repair(self):
        self.record_event()
        events=[]
        self.service.subscribe(events.append)
        for description in ('How could I forget that shiny thing?',
                            'Was that the sparkling bronze lantern another time?'):
            self.llm.response = json.dumps({'dialogue':
                'We watched the bronze lantern beside the stone bridge during our picnic. ' + description
            }) + '\n{"presentation":{}}'
            before=len(self.llm.calls)
            result=self.ask('Do you remember when we watched the bronze lantern?')
            self.assertTrue(result.succeeded, result.error)
            self.assertTrue(self.service._last_memory_authority_diagnostics['fallback_used'])
            self.assertEqual(1,len(self.llm.calls)-before)
            self.assertEqual('bounded_memory_semantic_rejection',
                self.service._last_memory_authority_diagnostics['repair_skipped_reason'])
            self.assertNotIn('shiny',result.reply)
            self.assertNotIn('sparkling',result.reply)
            self.assertEqual('presentation_fragments',
                             self.service._last_memory_authority_diagnostics['primary_format_normalization'])
        self.assertEqual(2,len([e for e in events if e.type=='assistant_response']))

    def test_normalized_supported_description_is_still_one_call_after_restart(self):
        self.record_event()
        self.reopen()
        dialogue='You said we watched a bronze lantern beside the stone bridge during our quiet picnic.'
        self.llm.response=json.dumps({'dialogue':dialogue+' That sounds lovely.'})+'\n{"presentation":{}}'
        before=len(self.llm.calls)
        result=self.ask('What did I say about the bronze lantern?')
        self.assertTrue(result.succeeded,result.error)
        self.assertEqual(dialogue+' That sounds lovely.',result.reply)
        self.assertEqual(1,len(self.llm.calls)-before)


if __name__ == '__main__':
    unittest.main()
