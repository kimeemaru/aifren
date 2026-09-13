"""Clock-controlled service turns and persisted synthetic canonical evidence."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import json
import threading
from zoneinfo import ZoneInfo

from aifren.assistant_service import AssistantService
from aifren.conversation.conversation import Conversation
from aifren.conversation.temporal_context import derive_temporal_context_facts, build_temporal_context_block
from test_assistant_service_v2_authority import _LLM, _TTS
from test_continuity_companion_tranche import _Harness
from test_memory_routing_partial_evidence import HealthyRecall
from aifren.continuity.memory_v2_authority import DevelopmentV2MemoryAuthority


class Memory:
    memories = []
    def __init__(self): self.reads = 0; self.writes = 0
    def get_relevant_memories(self, *a, **k): self.reads += 1; return []
    def process(self, *a): self.writes += 1
    def save(self): pass


class ReturnContinuityTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ);env.start();self.addCleanup(env.stop)
        for key in tuple(os.environ):
            if key.startswith('AIFREN_'): del os.environ[key]
        self.h=_Harness();self.addCleanup(self.h.close)
        self.addCleanup(os.chdir,Path.cwd());os.chdir(self.h.root)
        self.h.writer.compare=lambda *a,**k:{}
        self.now=datetime(2026,8,27,8,tzinfo=timezone.utc)
        self.llm=_LLM("Welcome back! How did you sleep?")
        self.memory=Memory();self.events=[]

    def service(self, mode='v2'):
        c=Conversation(self.llm,conversation_file=self.h.conversation_file,
            summary_file=self.h.root/'summary.json',memory_authority=mode,clock=lambda:self.now)
        authority=DevelopmentV2MemoryAuthority(self.h.writer.store,self.h.character_id,c.messages,recall=HealthyRecall()) if mode=='v2' else None
        s=AssistantService(self.llm,self.memory,c,object(),{'_character_id':self.h.character_id},
            'You are a warm, natural companion.',_TTS(),character_id=self.h.character_id,
            memory_v2_shadow_writer=self.h.writer,memory_authority=mode,memory_v2_authority=authority)
        s.subscribe(self.events.append);self.addCleanup(s.close)
        return s

    def seed(self,s,text="I'm going to bed",hours=10):
        scope=s.truth_scope_provenance()
        s.conversation.messages[:]=[
            {'role':'user','content':text,'timestamp':(self.now-timedelta(hours=hours)).isoformat(),'truth_scope':scope},
            {'role':'assistant','content':'Good night.','timestamp':(self.now-timedelta(hours=hours)+timedelta(seconds=1)).isoformat(),'truth_scope':scope}]
        s.conversation.save()

    def block(self):
        return next(m['content'] for m in self.llm.calls[-1][0] if m['content'].startswith('[Current turn temporal facts]'))

    def test_v1_and_v2_actual_request_offer_return_then_stop_repeating(self):
        for mode in ('v1','v2'):
            with self.subTest(mode=mode):
                s=self.service(mode);self.seed(s)
                result=s.process_text_turn("I'm back",speak=False)
                self.assertTrue(result.succeeded,result.error)
                self.assertIn('Return opportunity',self.block())
                self.assertIn('bedtime',self.block())
                self.assertIn('10 hours',self.block())
                self.now+=timedelta(seconds=30)
                s.process_text_turn('Tell me a joke.',speak=False)
                self.assertNotIn('Return opportunity',self.block())

    def test_generated_scene_user_cannot_replace_previous_human_anchor(self):
        s=self.service();self.seed(s)
        s.conversation.add_user_message("The hat was removed.",truth_scope=s.truth_scope_provenance(),
            origin={'kind':'scene_ui','generated_event':True,'operation':'clear_relation'})
        s.conversation.save()
        result=s.process_text_turn("I'm back",speak=False)
        self.assertTrue(result.succeeded,result.error)
        self.assertIn('10 hours',self.block())

    def test_inaccessible_current_input_has_no_return_opportunity(self):
        s=self.service();self.seed(s)
        s.conversation.add_user_message("I'm back",truth_scope=s.truth_scope_provenance())
        s.conversation.messages[-1]['semantic_admission']={'channel':'hearing','state':'unavailable','understood':False}
        facts=derive_temporal_context_facts(s.conversation.messages,"I'm back",clock=lambda:self.now)
        self.assertIsNone(facts.elapsed_since_previous_user_interaction)

    def test_wrong_scope_cannot_supply_departure(self):
        s=self.service();self.seed(s)
        s.conversation.messages[0]['truth_scope']={'kind':'scenario','scope_id':'scope-22222222-2222-4222-8222-222222222222'}
        s.conversation.add_user_message("I'm back",truth_scope=s.truth_scope_provenance())
        facts=derive_temporal_context_facts(s.conversation.messages,"I'm back",clock=lambda:self.now)
        self.assertIsNone(facts.elapsed_since_previous_user_interaction)

    def test_generic_multi_day_return_and_short_reconnect(self):
        s=self.service();self.seed(s,'I am reading a book.',hours=72)
        self.assertTrue(s.process_text_turn('Hi, how are you?',speak=False).succeeded)
        self.assertIn('3 days',self.block());self.assertIn('Return opportunity',self.block())
        self.assertNotIn('recorded bedtime',self.block())
        self.now+=timedelta(minutes=8)
        # Snapshots/reconnects have no canonical input and no return consumption.
        s.continuity_snapshot()
        self.assertTrue(s.process_text_turn('How are you?',speak=False).succeeded)
        self.assertNotIn('Return opportunity',self.block())

    def test_aged_departure_support_survives_generated_context_containment(self):
        s=self.service();self.seed(s)
        for _ in range(16):
            s.conversation.add_user_message('The hat was removed.',truth_scope=s.truth_scope_provenance(),
                origin={'kind':'scene_ui','generated_event':True,'operation':'clear_relation'})
        s.conversation.save()
        self.llm.response='Welcome back! You said you were going to bed. How did you sleep?'
        r=s.process_text_turn("I'm back",speak=False)
        self.assertTrue(r.succeeded,r.error);self.assertEqual(self.llm.response,r.reply)
        self.assertEqual(1,len(self.llm.calls),'Exact departure must not need a false-memory repair')
        self.assertEqual(0,self.memory.reads)

    def test_persisted_uncompleted_return_survives_reopen_but_committed_reply_consumes(self):
        s=self.service();self.seed(s)
        s.conversation.add_user_message("I'm back",truth_scope=s.truth_scope_provenance());s.conversation.save()
        self.now+=timedelta(minutes=1)
        reopened=self.service()
        self.assertTrue(reopened.process_text_turn('Hello again.',speak=False).succeeded)
        self.assertIn('Return opportunity',self.block());self.assertIn('10 hours',self.block())
        self.now+=timedelta(minutes=1)
        again=self.service();again.process_text_turn('Another question.',speak=False)
        self.assertNotIn('Return opportunity',self.block())

    def test_failed_assistant_save_does_not_consume_committed_user_return(self):
        s=self.service();self.seed(s)
        # An explicit mutation can commit the human record before generation.
        s.conversation.add_user_message("I'm back",truth_scope=s.truth_scope_provenance());s.conversation.save()
        original=s.conversation.save
        def fail_assistant():
            if s.conversation.messages[-1]['role']=='assistant':
                with patch('aifren.conversation.persistence.os.replace',side_effect=OSError('synthetic')):return original()
            return original()
        with patch.object(s.conversation,'save',side_effect=fail_assistant):
            r=s.process_text_turn('Hello again.',speak=False)
        self.assertFalse(r.succeeded)
        self.assertFalse(any(e.type=='assistant_response' for e in self.events))
        self.now+=timedelta(seconds=30)
        r=s.process_text_turn('Are you there?',speak=False)
        self.assertTrue(r.succeeded,r.error);self.assertIn('Return opportunity',self.block())

    def test_cancelled_provider_cannot_consume_or_publish_return(self):
        s=self.service();self.seed(s)
        s.conversation.add_user_message("I'm back",truth_scope=s.truth_scope_provenance());s.conversation.save()
        entered=threading.Event();release=threading.Event();result=[]
        def blocked(*args,**kwargs):
            entered.set()
            if not release.wait(3):raise TimeoutError('synthetic gate')
            return 'Welcome back.'
        with patch.object(self.llm,'generate',side_effect=blocked):
            worker=threading.Thread(target=lambda:result.append(s.process_text_turn('Hello again.',speak=False)))
            worker.start()
            try:
                self.assertTrue(entered.wait(3));s._cancel_active_turn()
            finally:release.set();worker.join(4)
        self.assertFalse(worker.is_alive());self.assertFalse(result[0].succeeded)
        self.assertFalse(any(e.type=='assistant_response' for e in self.events))
        self.now+=timedelta(seconds=30);s.process_text_turn('Are you there?',speak=False)
        self.assertIn('Return opportunity',self.block())

    def test_proactive_receipt_does_not_acknowledge_pending_human_return(self):
        from aifren.context.proactive_companion import record_displayed_checkin, ProactiveReason
        s=self.service();self.seed(s)
        s.conversation.add_user_message("I'm back",truth_scope=s.truth_scope_provenance());s.conversation.save()
        s.conversation.add_assistant_message('A synthetic check-in.',truth_scope=s.truth_scope_provenance());s.conversation.save()
        record_displayed_checkin(self.h.writer.store,self.h.character_id,ProactiveReason('open_thread', 'synthetic', '', int(self.now.timestamp()*1e6)),
            displayed_at_us=int(self.now.timestamp()*1e6),conversation_index=3,assistant_content='A synthetic check-in.')
        self.h.writer.store.connection.commit()
        self.assertFalse(s._temporal_reply_is_human_owned(3,s.conversation.messages[3]))
        self.now+=timedelta(seconds=30);s.process_text_turn('Hello again.',speak=False)
        self.assertIn('Return opportunity',self.block())

    def test_direct_adjacent_interval_uses_return_endpoint(self):
        s=self.service();self.seed(s);s.process_text_turn("I'm back",speak=False)
        self.now+=timedelta(seconds=17)
        self.llm.response="It's been about 10 hours since we last interacted."
        r=s.process_text_turn('How long was I away?',speak=False)
        self.assertTrue(r.succeeded,r.error);self.assertIn('10 hours',r.reply)
        self.assertEqual(2,len(self.llm.calls))
        self.assertIn('less than 1 minute',self.block())

    def test_invented_sleep_duration_is_private_then_repaired_or_fallback(self):
        s=self.service();self.seed(s)
        self.llm.response=['You slept for 10 hours. Welcome back!','Welcome back! How did you sleep?']
        r=s.process_text_turn("I'm back",speak=False)
        self.assertTrue(r.succeeded,r.error);self.assertNotIn('slept for',r.reply)
        self.assertEqual(2,len(self.llm.calls))
        self.assertFalse(any('slept for' in str(e.data.get('content','')) for e in self.events))

    def test_current_explicit_activity_duration_remains_valid_evidence(self):
        s=self.service();self.seed(s)
        self.llm.response='You slept for 8 hours. How do you feel?'
        r=s.process_text_turn('I slept for 8 hours.',speak=False)
        self.assertTrue(r.succeeded,r.error);self.assertEqual(self.llm.response,r.reply)

    def test_ambiguous_timestamp_and_backward_clock_never_invent_gap(self):
        for stamp in ('unparseable','2026-11-01T01:30:00','2026-12-01T08:00:00-05:00'):
            with self.subTest(stamp=stamp):
                s=self.service();self.seed(s)
                s.conversation.messages[0]['timestamp']=stamp
                self.now=datetime(2026,11,1,8,tzinfo=ZoneInfo('America/Toronto'))
                s.conversation.add_user_message("I'm back",truth_scope=s.truth_scope_provenance())
                f=s.conversation.temporal_context_facts("I'm back")
                self.assertIsNone(f.return_opportunity);self.assertIsNone(f.elapsed_since_previous_user_interaction)

    def test_actual_bedtime_and_explicit_return_clear_only_user_sleep(self):
        s=self.service();self.now-=timedelta(hours=10)
        self.llm.response='Good night.'
        self.assertTrue(s.process_text_turn("I'm going to bed",speak=False).succeeded)
        self.assertEqual('sleeping',self.h.repository.lookup_actor_state(self.h.character_id,'user','activity').state.value)
        self.now+=timedelta(hours=10);self.llm.response='Welcome back! How did you sleep?'
        self.assertTrue(s.process_text_turn("I'm back",speak=False).succeeded)
        self.assertIsNone(self.h.repository.lookup_actor_state(self.h.character_id,'user','activity').state)
        self.assertIn('bedtime',self.block())
        self.assertIsNone(self.h.repository.lookup_actor_state(self.h.character_id,'companion','activity').state)

    def test_resumed_greeting_is_awake_evidence_without_clearing_other_activities(self):
        s=self.service();self.now-=timedelta(hours=10);self.llm.response='Good night.'
        s.process_text_turn("I'm going to bed",speak=False)
        self.now+=timedelta(hours=10);self.llm.response='Hello! How did you sleep?'
        self.assertTrue(s.process_text_turn('Hello!',speak=False).succeeded)
        self.assertIsNone(self.h.repository.lookup_actor_state(self.h.character_id,'user','activity').state)
        self.assertIn('Return opportunity',self.block())

    def test_elapsed_time_alone_does_not_clear_user_work(self):
        s=self.service();self.now-=timedelta(days=3)
        self.llm.response='All right.'
        self.assertTrue(s.process_text_turn("I'm working",speak=False).succeeded)
        self.now+=timedelta(days=3);self.llm.response='Good to hear from you. What can I help with?'
        r=s.process_text_turn('Can you help me think through a problem?',speak=False)
        self.assertTrue(r.succeeded,r.error)
        self.assertEqual('working',self.h.repository.lookup_actor_state(self.h.character_id,'user','activity').state.value)
        self.assertIn('current request first',self.block())

    def test_dst_elapsed_in_service_request_uses_real_instants(self):
        s=self.service();self.seed(s)
        s.conversation.messages[0]['timestamp']='2026-03-08T00:00:00-05:00'
        s.conversation.messages[1]['timestamp']='2026-03-08T00:00:01-05:00'
        self.now=datetime(2026,3,8,8,tzinfo=ZoneInfo('America/Toronto'))
        self.assertTrue(s.process_text_turn("I'm back",speak=False).succeeded)
        self.assertIn('7 hours',self.block());self.assertNotIn('8 hours',self.block())

    def test_wrong_absence_answer_repairs_without_publishing_false_duration(self):
        s=self.service();self.seed(s);s.process_text_turn("I'm back",speak=False)
        self.now+=timedelta(seconds=17);self.llm.calls.clear();self.events.clear()
        self.llm.response=['You were away for 19 hours.',"It's been ten hours since we last interacted."]
        r=s.process_text_turn('How long was I away?',speak=False)
        self.assertTrue(r.succeeded,r.error);self.assertIn('ten hours',r.reply)
        self.assertEqual(2,len(self.llm.calls))
        self.assertFalse(any('19 hours' in str(e.data.get('content','')) for e in self.events))

    def test_failed_duration_repair_has_finite_safe_fallback(self):
        s=self.service();self.seed(s)
        self.llm.response='You slept for 10 hours.'
        r=s.process_text_turn("I'm back",speak=False)
        self.assertTrue(r.succeeded,r.error);self.assertEqual('Welcome back.',r.reply)
        self.assertEqual(2,len(self.llm.calls))
        self.assertEqual(1,sum(e.type=='assistant_response' for e in self.events))

    def test_new_character_does_not_inherit_previous_opportunity(self):
        s=self.service();self.seed(s)
        # A separately bound character service is the same construction used
        # after retiring character-owned state; no global absence cache exists.
        other=_Harness()
        try:
            c=Conversation(self.llm,conversation_file=other.conversation_file,summary_file=other.root/'summary.json',clock=lambda:self.now)
            other_service=AssistantService(self.llm,Memory(),c,object(),{'_character_id':other.character_id},
                'Synthetic second character.',_TTS(),character_id=other.character_id,memory_v2_shadow_writer=other.writer, memory_authority="v1")
            try:
                other_service.process_text_turn("I'm back",speak=False)
                self.assertNotIn('Return opportunity',self.block())
                self.assertNotIn('10 hours',self.block())
            finally:other_service.close()
        finally:other.close()

    def test_post_replace_sync_failure_keeps_reply_and_consumes_opportunity(self):
        s=self.service();self.seed(s)
        original=s.conversation.save
        def fail_after_commit():
            if s.conversation.messages[-1]['role']=='assistant':
                with patch('aifren.conversation.persistence._sync_directory',side_effect=OSError('synthetic')):return original()
            return original()
        with patch.object(s.conversation,'save',side_effect=fail_after_commit):
            r=s.process_text_turn("I'm back",speak=False)
        self.assertFalse(r.succeeded)
        saved=json.loads(self.h.conversation_file.read_text())
        self.assertEqual('assistant',saved[-1]['role']);self.assertIn('Welcome back',saved[-1]['content'])
        self.now+=timedelta(seconds=30)
        reopened=self.service();reopened.process_text_turn('Another question.',speak=False)
        self.assertNotIn('Return opportunity',self.block())

    def test_scope_transition_keeps_clock_without_old_scope_departure(self):
        s=self.service();self.seed(s)
        self.llm.response='We can imagine the garden.'
        r=s.process_text_turn("Let's roleplay in a garden.",speak=False)
        self.assertTrue(r.succeeded,r.error)
        self.assertNotIn('Return opportunity',self.block());self.assertNotIn('bedtime',self.block())

    def test_unknown_interval_falls_back_without_making_up_duration(self):
        s=self.service();self.llm.response="It's been 10 hours."
        r=s.process_text_turn('How long was I away?',speak=False)
        self.assertTrue(r.succeeded,r.error)
        self.assertIn("can't reliably tell",r.reply);self.assertNotIn('10 hours',r.reply)

    def test_older_duplicate_text_cannot_be_mistaken_for_current_human_input(self):
        s=self.service();self.seed(s)
        s.conversation.add_user_message("I'm back",truth_scope=s.truth_scope_provenance())
        s.conversation.add_assistant_message('A standalone synthetic output.',truth_scope=s.truth_scope_provenance())
        self.assertIsNone(s.conversation.temporal_context_facts("I'm back").return_opportunity)
