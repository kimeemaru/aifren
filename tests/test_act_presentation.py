"""Fresh provider boundary; synthetic service state, never private output."""
from dataclasses import replace
import json
import threading
import unittest
from unittest.mock import patch

from aifren.dialogue.act_presentation import ActPrefixStream, parse_fresh_act_response, act_character_prompt
from aifren.dialogue.presentation_metadata import parse_assistant_response, response_contract_prompt
import test_lean_ordinary_dialogue as fixture


class ActPrefixTests(unittest.TestCase):
    def project(self, raw, split):
        p = ActPrefixStream()
        visible = p.feed(raw[:split]) + p.feed(raw[split:]) + p.finish()
        return visible, p

    def test_every_character_boundary_and_every_single_character_chunk(self):
        for body in ('emotion=happy', 'gesture=agreement', 'intensity=0.6',
                     'emotion=neutral', 'emotion=sad;intensity=0.45',
                     'emotion=relaxed;gesture=thinking',
                     'emotion=happy;intensity=0.6;gesture=agreement'):
            raw = ' \n<|ACT:' + body + '|>Hello there.'
            for split in range(len(raw)+1):
                with self.subTest(body=body, split=split):
                    visible, p = self.project(raw, split)
                    self.assertEqual('Hello there.', visible)
                    self.assertEqual('valid', p.status)
                    self.assertEqual('act', p.presentation.origin)
            p = ActPrefixStream()
            self.assertEqual('Hello there.', ''.join(p.feed(c) for c in raw)+p.finish())

    def test_plain_prose_emits_immediately_and_absent_prefix_is_valid(self):
        p = ActPrefixStream()
        self.assertEqual('Hello', p.feed('Hello'))
        self.assertEqual(' there.', p.feed(' there.'))
        self.assertIsNone(p.presentation)

    def test_invalid_control_drops_metadata_not_separable_dialogue(self):
        bodies = ['', 'vision=normal', 'emotion=joy', 'gesture=wave', 'emotion=happy;',
                  'emotion=happy;emotion=sad', 'intensity=0.2;intensity=0.3',
                  'posture=standing', 'companion_action=wave']
        bodies += ['intensity='+v for v in ('-1','1.2','NaN','inf','.6','1e-1','0.６')]
        for body in bodies:
            raw = '<|ACT:'+body+'|>That sounds good.'
            for split in range(len(raw)+1):
                text, p = self.project(raw, split)
                self.assertEqual('That sounds good.', text)
                self.assertIsNone(p.presentation)
                self.assertNotEqual('valid', p.status)

    def test_oversize_and_repeated_prefix_recovery_is_bounded(self):
        for raw, status in (
            ('<|ACT:intensity=0.'+'0'*300+'|>Okay.', 'over_bound'),
            ('<|ACT:emotion=happy|>\n<|ACT:emotion=sad|>Okay.', 'repeated')):
            for split in range(len(raw)+1):
                text,p=self.project(raw,split)
                self.assertEqual('Okay.',text);self.assertEqual(status,p.status)
                self.assertIsNone(p.presentation)
        p=ActPrefixStream()
        for c in '<|ACT:'+'x'*9000:
            self.assertEqual('',p.feed(c));self.assertLessEqual(len(p.buffer),256)
        self.assertEqual('recovery_bound',p.status)
        self.assertEqual('',p.feed('|>No guessed boundary.'))

    def test_missing_close_has_no_safe_dialogue_boundary(self):
        for raw in ('<|ACT:emotion=happy', '<|AC'):
            parsed,status=parse_fresh_act_response(raw)
            self.assertEqual('',parsed.dialogue);self.assertIsNone(parsed.presentation)
            self.assertIn(status,('missing_close','incomplete_prefix'))

    def test_literals_and_nonprefix_data_are_preserved_not_executed(self):
        marker='<|ACT:emotion=angry|>'
        for raw in ('"'+marker+'" is an example.', '`'+marker+'`',
                    'The historical example was '+marker, 'Hello. '+marker,
                    json.dumps({'dialogue':marker+' is literal data.'})):
            parsed,status=parse_fresh_act_response(raw)
            self.assertEqual('omitted',status)
            self.assertEqual(parse_assistant_response(raw).dialogue,parsed.dialogue)
            self.assertIsNone(parsed.presentation)

    def test_act_is_one_provider_owner_and_structured_obligations_survive(self):
        envelope=json.dumps({'dialogue':'*nods* Yes.', 'response_mode':'normal_conversation',
                             'presentation':{'emotion':'angry','gesture':'disagreement','pose':'sleeping'}})
        parsed,status=parse_fresh_act_response('<|ACT:emotion=happy|>'+envelope)
        self.assertEqual('happy',parsed.presentation.emotion)
        self.assertIsNone(parsed.presentation.gesture)
        self.assertIsNone(parsed.presentation.pose)
        self.assertTrue(parsed.response_mode_supplied)
        self.assertEqual('*nods* Yes.',parsed.dialogue)
        self.assertEqual('angry',parse_fresh_act_response(envelope)[0].presentation.emotion)

    def test_source_and_user_parser_do_not_execute_act(self):
        # The general parser has no ACT opt-in. Historical cores use it unchanged.
        raw='<|ACT:emotion=angry|>literal archived data'
        parsed=parse_assistant_response(raw)
        self.assertEqual(raw,parsed.dialogue);self.assertIsNone(parsed.presentation)
        self.assertIsNone(act_character_prompt('Custom machine instructions.'))

    def test_control_is_validated_before_unicode_or_reasoning_normalization(self):
        parsed,status=parse_fresh_act_response('<|ACT:emotion=happy😊|>Hello.')
        self.assertEqual('Hello.',parsed.dialogue)
        self.assertIsNone(parsed.presentation);self.assertEqual('value',status)
        parsed,status=parse_fresh_act_response('<think>private</think><|ACT:emotion=happy|>Hello.')
        self.assertEqual('',parsed.dialogue);self.assertEqual('non_outer_prefix',status)


class ActServiceTests(unittest.TestCase):
    def setUp(self):
        fixture.LeanOrdinaryDialogueTests.setUp(self)
        self.llm.local_ordinary_dialogue=False
        self.llm.local_presentation=True
        self.s.explicit_avatar_cues=True

    policy=fixture.LeanOrdinaryDialogueTests.policy

    def test_normal_provider_canonical_tts_and_final_presentation_once(self):
        self.llm.response='<|ACT:emotion=happy;intensity=0.6;gesture=agreement|>That sounds good.'
        with patch.object(self.s,'_repair_governed_response',side_effect=AssertionError('No ACT repair')):
            r=self.s.process_text_turn('Let us chat.',speak=False)
        self.assertTrue(r.succeeded,r.error);self.assertEqual('That sounds good.',r.reply)
        self.assertEqual('That sounds good.',r.spoken_text)
        self.assertEqual('happy',r.presentation.emotion)
        self.assertEqual('act',r.presentation.origin)
        self.assertEqual(1,len([e for e in self.events if e.type=='assistant_response']))
        self.assertNotIn('<|ACT:',str(self.s.conversation.messages))
        self.assertNotIn('<|ACT:',str([e.data for e in self.events if e.type in {'assistant_delta','assistant_response','conversation_message'}]))
        self.assertIn('OPTIONAL AVATAR CUES:',self.llm.calls[0][1])
        self.assertNotIn(response_contract_prompt(),self.llm.calls[0][1])

    def test_invalid_marker_preserves_answer_without_optional_repair(self):
        self.llm.response='<|ACT:emotion=happy😊|>I am listening.'
        with patch.object(self.s,'_repair_governed_response',side_effect=AssertionError('No ACT repair')):
            r=self.s.process_text_turn('Let us chat.',speak=False)
        self.assertTrue(r.succeeded,r.error);self.assertEqual('I am listening.',r.reply)
        self.assertIsNone(r.presentation.emotion)

    def test_ambiguous_marker_fails_without_another_inference(self):
        self.llm.response='<|ACT:emotion=happy unfinished'
        r=self.s.process_text_turn('Hello.',speak=False)
        self.assertFalse(r.succeeded);self.assertEqual(1,len(self.llm.calls))
        self.assertFalse(any(e.type=='assistant_response' for e in self.events))

    def test_streaming_prefix_never_exposes_control_and_plain_has_no_envelope_wait(self):
        p=self.policy(); chunks=[]
        def stream(*args,**kwargs):
            for c in '<|ACT:emotion=happy|>':
                yield c
                self.assertEqual([],chunks)
            yield 'Hello'
            self.assertEqual(['Hello'],chunks)
            yield ' there.'
        self.llm.stream_generate=stream
        raw=self.s._stream_reply('Hello.',chunks.append,None,threading.Event(),ordinary_policy=p,
            memory_answer_requirement=p.memory_answer_requirement,memory_query_decision=p.memory_query_decision)
        self.assertEqual('Hello there.',''.join(chunks))
        self.assertEqual('happy',parse_fresh_act_response(raw)[0].presentation.emotion)

    def test_streaming_cleanup_cannot_expose_non_outer_control(self):
        p=self.policy(); chunks=[]
        def stream(*a,**k):
            for c in '<think>private</think><|ACT:emotion=happy|>Hello.': yield c
        self.llm.stream_generate=stream
        raw=self.s._stream_reply('Hello.',chunks.append,None,threading.Event(),ordinary_policy=p,
            memory_answer_requirement=p.memory_answer_requirement,memory_query_decision=p.memory_query_decision)
        self.assertNotIn('<|ACT:', ''.join(chunks))
        self.assertNotIn('private', ''.join(chunks))
        self.assertEqual('',parse_fresh_act_response(raw)[0].dialogue)

    def test_memory_capability_scene_and_unknown_policies_stay_existing(self):
        for query in ('What is my favorite color?','What did I tell you about my project?',
                      'Which place was that?','Can you see me?','What time is it?'):
            self.assertFalse(self.s._act_eligible(self.policy(query)),query)
        p=self.policy()
        for field,value in (('speech_mode','unavailable'),('speech_mode','constrained'),('awareness_mode','asleep')):
            self.assertFalse(self.s._act_eligible(replace(p,effects=replace(p.effects,**{field:value}))))
        for field,value in (('action_plan',object()),('memory_realization',object()),('requirement',object())):
            self.assertFalse(self.s._act_eligible(replace(p,**{field:value})))
        self.assertFalse(self.s._act_eligible(None))

    def test_setting_off_and_online_keep_original_prompt(self):
        p=self.policy()
        self.s.explicit_avatar_cues=False
        self.assertIn(response_contract_prompt(),self.s._response_character_prompt(ordinary_policy=p))
        self.s.explicit_avatar_cues=True;self.llm.local_presentation=False
        self.assertIn(response_contract_prompt(),self.s._response_character_prompt(ordinary_policy=p))

    def test_user_and_history_marker_do_not_dispatch(self):
        self.s.conversation.add_assistant_message('The example was "<|ACT:emotion=angry|>".')
        self.llm.response='That is a text example.'
        r=self.s.process_text_turn('Explain "<|ACT:emotion=sad|>".',speak=False)
        self.assertTrue(r.succeeded,r.error);self.assertIsNone(r.presentation.emotion)
        self.assertIn('<|ACT:emotion=angry|>',self.s.conversation.messages[-3]['content'])

    def test_cancel_failure_and_replacement_cannot_publish_old_cues(self):
        self.llm.response='<|ACT:emotion=angry|>I am listening.'
        fixture.LeanOrdinaryDialogueTests.test_cancelled_and_failed_persistence_have_no_assistant_publication(self)
        self.events.clear();self.llm.response='<|ACT:emotion=neutral|>Hello.'
        r=self.s.process_text_turn('Hello.',speak=False)
        self.assertTrue(r.succeeded,r.error);self.assertEqual('neutral',r.presentation.emotion)
        self.assertEqual(1,len([e for e in self.events if e.type=='assistant_response']))

    def test_v1_spies_unchanged(self):
        fixture.LeanOrdinaryDialogueTests.test_v1_spies_remain_zero(self)

    def test_existing_semantic_repair_separates_its_own_fresh_prefix(self):
        replies=iter(['<|ACT:emotion=angry|>You told me your favorite planet is Neptune.',
                      '<|ACT:emotion=relaxed|>I am listening.'])
        with patch.object(self.llm,'generate',side_effect=lambda *a,**k:next(replies)):
            r=self.s.process_text_turn('Let us chat.',speak=False)
        self.assertTrue(r.succeeded,r.error)
        self.assertEqual('I am listening.',r.reply)
        self.assertEqual('relaxed',r.presentation.emotion)
        self.assertNotIn('<|ACT:',str(self.s.conversation.messages))

    def test_saved_setting_is_loaded_by_recreated_service(self):
        from aifren.runtime.model_settings import set_explicit_avatar_cues
        from aifren.assistant_service import AssistantService
        set_explicit_avatar_cues(True)
        old=self.s
        s=AssistantService(self.llm,old.memory,old.conversation,object(),old.character,
            old.character_prompt,old.tts,character_id=old.character_id,
            memory_v2_shadow_writer=self.h.writer,memory_authority='v2',
            memory_v2_authority=old._memory_v2_authority)
        self.addCleanup(s.close)
        self.assertTrue(s.explicit_avatar_cues)
        self.assertTrue(s._act_eligible(self.policy()))
