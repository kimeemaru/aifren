"""Production request assembly keeps salience outside authoritative answers."""
from datetime import datetime, timezone
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aifren.assistant import build_character_prompt
from aifren.assistant_service import AssistantService
from aifren.context.companion_context import CompanionContextContribution
from aifren.conversation.conversation import Conversation
from aifren.continuity.memory_v2_authority import DevelopmentV2MemoryAuthority
from test_assistant_service_v2_authority import _LLM, _TTS
from test_continuity_companion_tranche import _Harness
from test_memory_routing_partial_evidence import HealthyRecall
from test_return_continuity import Memory


class _Source:
    def __init__(self): self.calls=0
    def build(self, request):
        self.calls+=1
        return SimpleNamespace(contributions=(CompanionContextContribution(
            'recent_pulse','continuity_hint',request.character_id,request.truth_scope_id,
            request.turn_key,50,'Past discussion topic: hiking trip',('synthetic-source',)),), diagnostics={})


class CompanionContextServiceTests(unittest.TestCase):
    def setUp(self):
        self.h=_Harness(); self.addCleanup(self.h.close)
        self.addCleanup(os.chdir,Path.cwd()); os.chdir(self.h.root)
        self.llm=_LLM();self.llm.response='I am listening.'
        self.h.writer.compare=lambda *a,**k:{}
        self.source=_Source(); self.events=[]

    def service(self, mode='v2'):
        c=Conversation(self.llm,conversation_file=self.h.conversation_file,summary_file=self.h.root/'summary.json',
            memory_authority=mode,clock=lambda:datetime(2026,9,10,12,tzinfo=timezone.utc))
        authority=DevelopmentV2MemoryAuthority(self.h.writer.store,self.h.character_id,c.messages,recall=HealthyRecall()) if mode=='v2' else None
        character={'name':'Mira','_character_id':self.h.character_id}
        service=AssistantService(self.llm,Memory(),c,object(),character,
            build_character_prompt(character,'A friendly synthetic companion.'),_TTS(),
            character_id=self.h.character_id,memory_v2_shadow_writer=self.h.writer,memory_authority=mode,
            memory_v2_authority=authority,recent_pulse_enabled=False,companion_context_sources=(self.source,))
        service.subscribe(self.events.append);self.addCleanup(service.close)
        return service

    def test_ordinary_actual_request_has_data_but_canonical_publication_does_not(self):
        s=self.service()
        result=s.process_text_turn('Let us chat for a moment.',speak=False)
        self.assertTrue(result.succeeded,result.error)
        self.assertEqual(1,self.source.calls)
        self.assertIn('[RECENT CONTINUITY]',str(self.llm.calls[-1]))
        self.assertNotIn('RECENT CONTINUITY',str(s.conversation.messages))
        self.assertNotIn('hiking trip',str([e.data for e in self.events if e.type=='assistant_response']))
        self.assertLessEqual(s._last_companion_context_diagnostics['emitted_characters'],1200)

    def test_stream_and_complete_paths_receive_same_non_authoritative_block(self):
        s=self.service()
        def stream(context,prompt,**kw): yield self.llm.generate(context,prompt)
        self.llm.stream_generate=stream
        result=s.process_text_turn('Let us chat for a moment.',speak=False)
        self.assertTrue(result.succeeded,result.error)
        self.assertEqual(1,self.source.calls)
        self.assertIn('[RECENT CONTINUITY]',str(self.llm.calls[-1]))
        self.assertNotIn('[RECENT CONTINUITY]',str(self.events))

    def test_explicit_history_current_fact_and_exact_followup_never_collect(self):
        s=self.service()
        for query in ('What did I tell you about my project?', 'What is my favorite color?',
                      'Which place was that?', 'What did I say before I corrected it to blue?'):
            with self.subTest(query=query):
                self.llm.calls.clear()
                s.process_text_turn(query,speak=False)
                self.assertEqual(0,self.source.calls)
                self.assertTrue(all('[RECENT CONTINUITY]' not in str(call) for call in self.llm.calls))
                self.assertNotIn('hiking trip',str(s._last_memory_authority_diagnostics))

    def test_memory_requirement_and_realizer_are_hard_omission_even_without_query_grammar(self):
        s=self.service()
        for kwargs in ({'memory_answer_requirement':SimpleNamespace(triggered=True)},
                       {'memory_realization':SimpleNamespace(dialogue='Authoritative core.')}):
            self.assertEqual('',s._ordinary_companion_context('Continue.',**kwargs))
        self.assertEqual(0,self.source.calls)

    def test_v1_rollback_cannot_use_pulse(self):
        s=self.service('v1');s.process_text_turn('Let us chat.',speak=False)
        self.assertEqual(0,self.source.calls)

    def test_generated_scene_response_omits_optional_context(self):
        s=self.service()
        s._generate_reply('A scene control was applied.',companion_context_allowed=False)
        self.assertEqual(0,self.source.calls)
        self.assertNotIn('[RECENT CONTINUITY]',self.llm.calls[-1][1])

    def test_previous_character_scope_or_turn_contribution_cannot_be_reused(self):
        from dataclasses import replace
        s=self.service()
        original=self.source.build
        for field,value in (('character_id','previous'),('truth_scope_id','previous'),('turn_key','previous')):
            def stale(request):
                result=original(request)
                return SimpleNamespace(contributions=(replace(result.contributions[0],**{field:value}),),diagnostics={})
            with patch.object(self.source,'build',side_effect=stale):
                result=s.process_text_turn('Let us chat.',speak=False)
            self.assertTrue(result.succeeded,result.error)
            self.assertNotIn('[RECENT CONTINUITY]',self.llm.calls[-1][1])

    def test_default_disabled_and_optional_source_failure_do_not_change_turn_success(self):
        s=self.service();s._companion_context_sources=()
        s.process_text_turn('Let us chat.',speak=False)
        self.assertNotIn('[RECENT CONTINUITY]',self.llm.calls[-1][1])
        s._companion_context_sources=(self.source,)
        with patch.object(self.source,'build',side_effect=RuntimeError('unavailable')):
            result=s.process_text_turn('Let us continue.',speak=False)
        self.assertTrue(result.succeeded,result.error)
        self.assertTrue(s._last_companion_context_diagnostics['source_failed'])
        self.assertNotIn('[RECENT CONTINUITY]',self.llm.calls[-1][1])

    def test_v2_pulse_causes_no_v1_prompt_or_write_access(self):
        s=self.service();s._recent_pulse_enabled=True
        with (patch.object(s.memory,'get_relevant_memories',side_effect=AssertionError('V1 read')),
              patch.object(s.memory,'process',side_effect=AssertionError('V1 write'))):
            result=s.process_text_turn('Let us chat.',speak=False)
        self.assertTrue(result.succeeded,result.error)


if __name__ == '__main__': unittest.main()
