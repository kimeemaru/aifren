"""Real turn parsing, governance, canonical saves and publication own leases."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from assistant import build_character_prompt
from assistant_service import AssistantService
from companion_context import TransientImpulsePayload
from conversation.conversation import Conversation
from conversation.persistence import ConversationPersistenceError
from memory_v2_authority import DevelopmentV2MemoryAuthority
from presentation_metadata import parse_assistant_response
from transient_impulses import TransientImpulse, TransientImpulseStore
from test_assistant_service_v2_authority import _LLM, _TTS
from test_continuity_companion_tranche import _Harness
from test_memory_routing_partial_evidence import HealthyRecall
from test_return_continuity import Memory


class ImpulseServiceTests(unittest.TestCase):
    def setUp(self):
        self.h=_Harness();self.addCleanup(self.h.close)
        self.addCleanup(os.chdir,Path.cwd());os.chdir(self.h.root)
        self.now=datetime(2026,9,10,12,tzinfo=timezone.utc)
        self.llm=_LLM('I am listening.');self.h.writer.compare=lambda *a,**k:{}
        self.c=Conversation(self.llm,conversation_file=self.h.conversation_file,
            summary_file=self.h.root/'summary.json',memory_authority='v2',clock=lambda:self.now)
        self.store=TransientImpulseStore(self.h.root/'attention.sqlite',character_id=self.h.character_id,
                                       conversation_file=self.c.conversation_file)
        self.addCleanup(self.store.close)
        authority=DevelopmentV2MemoryAuthority(self.h.writer.store,self.h.character_id,self.c.messages,recall=HealthyRecall())
        character={'name':'Mira','_character_id':self.h.character_id}
        self.s=AssistantService(self.llm,Memory(),self.c,object(),character,
            build_character_prompt(character,'A friendly synthetic companion.'),_TTS(),
            character_id=self.h.character_id,memory_v2_shadow_writer=self.h.writer,memory_authority='v2',
            memory_v2_authority=authority,recent_pulse_enabled=False,transient_impulse_store=self.store)
        self.addCleanup(self.s.close);self.events=[];self.s.subscribe(self.events.append)
        scope=self.s.truth_scope_provenance()['scope_id']
        self.impulse=TransientImpulse('thought',self.h.character_id,scope,self.now,self.now+timedelta(hours=1),70,
            TransientImpulsePayload('subjective_reflection','Imagining walking through Tokyo together, only a thought.'))
        self.store.stage(self.impulse,now=self.now)

    def turn(self):return self.s.process_text_turn('Let us chat for a moment.',speak=False)

    def test_success_ignoring_impulse_consumes_only_after_canonical_and_final_event(self):
        states=[]
        def event(e):
            if e.type=='assistant_response':
                states.append((self.store.diagnostics()['leased_count'],self.c.is_message_persisted(len(self.c.messages)-1,self.c.messages[-1])))
        self.s.subscribe(event)
        result=self.turn();self.assertTrue(result.succeeded,result.error)
        self.assertEqual([(1,True)],states)
        self.assertEqual(1,self.store.diagnostics()['consumed_count'])
        self.assertIn('[COMPANION ATTENTION',str(self.llm.calls[0]))
        self.assertNotIn('Tokyo',str(self.c.messages));self.assertNotIn('Tokyo',str(self.events))
        self.assertNotIn('COMPANION ATTENTION',result.spoken_text)
        self.turn();self.assertNotIn('COMPANION ATTENTION',str(self.llm.calls[-1]))

    @patch.dict('os.environ', {'AIFREN_CONTEXT_GOVERNOR':'1'})
    def test_budget_excluded_impulse_releases_without_consuming(self):
        from context_governor import plan_context
        def without_optional(**kwargs):
            kwargs['items']=[i for i in kwargs['items'] if i.owner!='companion_context']
            return plan_context(**kwargs)
        with patch('conversation.governed_context.plan_context',side_effect=without_optional):
            result=self.turn()
        self.assertTrue(result.succeeded,result.error)
        self.assertEqual(0,self.store.diagnostics()['consumed_count'])
        self.assertEqual(0,self.store.diagnostics()['leased_count'])
        self.assertNotIn('COMPANION ATTENTION',str(self.llm.calls[0]))

    def test_stream_has_same_transaction_and_clean_dialogue(self):
        def stream(context,prompt,**kw):
            yield self.llm.generate(context,prompt)
        self.llm.stream_generate=stream
        result=self.turn();self.assertTrue(result.succeeded,result.error)
        self.assertEqual(1,self.store.diagnostics()['consumed_count'])
        self.assertEqual('I am listening.',result.reply)

    def test_provider_failure_releases(self):
        with patch.object(self.llm,'generate',side_effect=RuntimeError('provider failed')):result=self.turn()
        self.assertFalse(result.succeeded)
        self.assertEqual(1,self.store.diagnostics()['pending_count'])
        self.assertTrue(self.turn().succeeded)
        self.assertEqual(1,self.store.diagnostics()['consumed_count'])

    def test_cancelled_generation_releases_and_cannot_publish(self):
        def cancel(*a,**k):self.s._cancel_active_turn();return 'Old reply.'
        with patch.object(self.llm,'generate',side_effect=cancel):result=self.turn()
        self.assertFalse(result.succeeded)
        self.assertEqual(1,self.store.diagnostics()['pending_count'])
        self.assertFalse(any(e.type=='assistant_response' for e in self.events))

    def test_replacement_cannot_spend_old_lease(self):
        entered=threading.Event();leave=threading.Event();results=[];calls=[]
        def generate(context,prompt,**kw):
            calls.append(self.s._impulse_lease.token)
            if len(calls)==1:
                entered.set();leave.wait(5)
            return 'I am listening.'
        with patch.object(self.llm,'generate',side_effect=generate):
            old=threading.Thread(target=lambda:results.append(self.turn()));old.start()
            self.assertTrue(entered.wait(5))
            new=threading.Thread(target=lambda:results.append(self.turn()));new.start()
            # Wait for replacement ownership, without sleeping or hitting the provider.
            for _ in range(10000):
                if self.s._active_turn_id==2:break
                threading.Event().wait(.001)
            self.assertEqual(2,self.s._active_turn_id);leave.set();old.join(5);new.join(5)
        self.assertFalse(old.is_alive());self.assertFalse(new.is_alive())
        self.assertEqual(1,sum(r.succeeded for r in results));self.assertEqual(2,len(set(calls)))
        self.assertEqual(1,self.store.diagnostics()['consumed_count'])

    def test_primary_validation_rejection_releases_even_if_safe_response_publishes(self):
        original=self.s._validate_governed_response;seen=[]
        def reject_first(*args,**kwargs):
            seen.append(1)
            if len(seen)==1:return False,'synthetic_rejection',None,None
            return original(*args,**kwargs)
        with patch.object(self.s,'_validate_governed_response',side_effect=reject_first):
            result=self.turn()
        self.assertTrue(result.succeeded,result.error)
        self.assertEqual(1,self.store.diagnostics()['pending_count'])
        self.assertTrue(all('COMPANION ATTENTION' not in call[1] for call in self.llm.calls[1:]))

    def test_failed_assistant_save_releases_without_hiding_payload_in_archive(self):
        from conversation.conversation import save_json
        def fail_assistant(path,data,**kwargs):
            if kwargs.get('record_kind')=='conversation' and data and data[-1]['role']=='assistant':
                raise ConversationPersistenceError(record_kind='conversation',stage='write')
            return save_json(path,data,**kwargs)
        with patch('conversation.conversation.save_json',side_effect=fail_assistant):result=self.turn()
        self.assertFalse(result.succeeded)
        self.assertEqual(1,self.store.diagnostics()['pending_count'])
        self.assertFalse(any(m['role']=='assistant' for m in self.c.messages))

    def test_late_post_action_rejection_and_application_failure_release(self):
        # The primary draft passes, but a later application/revalidation branch
        # replaces it. No rejected generation gets to spend its attention lease.
        for action_state in ('applied','failed'):
            with self.subTest(action_state=action_state):
                plan=SimpleNamespace(proposal=SimpleNamespace(family='posture',operation='set',value='sitting'))
                results=iter(((True,'accepted','I am listening.',None),
                              (False,'post_action_rejected',None,None),
                              (True,'accepted','',None)))
                with (patch.object(self.s,'_plan_companion_action',side_effect=lambda q,p:replace(p,action_plan=plan)),
                      patch.object(self.s,'_apply_companion_action_plan',return_value={'state':action_state}),
                      patch.object(self.s,'_validate_governed_response',side_effect=lambda *a:next(results)),
                      patch.object(self.s,'_governed_fallback_response',return_value=parse_assistant_response('I am listening.'))):
                    result=self.turn()
                self.assertTrue(result.succeeded,result.error)
                self.assertEqual(1,self.store.diagnostics()['pending_count'])

    def test_scope_changes_during_generation_cannot_consume_old_opportunity(self):
        original=self.llm.generate
        def generate(*a,**k):
            self.s.truth_scope_provenance=lambda:{'kind':'roleplay','scope_id':'new'}
            return original(*a,**k)
        with patch.object(self.llm,'generate',side_effect=generate):self.turn()
        self.assertEqual(1,self.store.diagnostics()['pending_count'])

    def test_memory_questions_and_realizer_omit_without_spending(self):
        for question in ('What is my favorite color?','What did I say before I corrected it to blue?',
                         'Which place was that?'):
            self.s.process_text_turn(question,speak=False)
        for kwargs in ({'memory_realization':SimpleNamespace(dialogue='core')},
                       {'memory_answer_requirement':SimpleNamespace(triggered=True)}):
            self.assertEqual('',self.s._ordinary_companion_context('Continue.',**kwargs))
        self.assertEqual(1,self.store.diagnostics()['pending_count'])
        self.assertNotIn('Tokyo',str(self.s._last_memory_authority_diagnostics))
        self.assertTrue(all('COMPANION ATTENTION' not in prompt for _,prompt in self.llm.calls))

    def test_v1_spies_zero_and_staging_itself_does_not_mutate_v2(self):
        before=list(self.h.writer.store.connection.iterdump())
        self.s._ordinary_companion_context('Continue.') # Not a current owned turn: no lease.
        self.assertEqual(before,list(self.h.writer.store.connection.iterdump()))
        with (patch.object(self.s.memory,'get_relevant_memories',side_effect=AssertionError('V1 read')),
              patch.object(self.s.memory,'process',side_effect=AssertionError('V1 write'))):
            self.assertTrue(self.turn().succeeded)

    def test_consumption_write_failure_preserves_receipt_for_restart(self):
        with patch.object(self.store,'published',side_effect=OSError('unavailable')):result=self.turn()
        self.assertTrue(result.succeeded,result.error)
        self.assertEqual(1,self.store.diagnostics()['leased_count'])
        self.store.close()
        reopened=TransientImpulseStore(self.h.root/'attention.sqlite',character_id=self.h.character_id,conversation_file=self.c.conversation_file)
        self.addCleanup(reopened.close);counts=reopened.recover(self.c)
        self.assertEqual(1,counts['recovered_committed'])
        self.s._transient_impulse_store=None


if __name__=='__main__':unittest.main()
