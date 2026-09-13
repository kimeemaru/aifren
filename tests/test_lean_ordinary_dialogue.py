"""Experimental ordinary style never replaces a machine obligation or authority."""
from dataclasses import replace
from datetime import datetime, timezone
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from assistant import build_character_prompt
from assistant_service import AssistantService, _ResponsePolicy
from conversation.conversation import Conversation
from memory_query_decision import decide_memory_query
from memory_v2_authority import DevelopmentV2MemoryAuthority
from presentation_metadata import lean_ordinary_character_prompt, response_contract_prompt
from response_requirements import ResponseRequirement, RequiredFact
from test_assistant_service_v2_authority import _LLM, _TTS
from test_continuity_companion_tranche import _Harness
from test_memory_routing_partial_evidence import HealthyRecall
from test_return_continuity import Memory


class LeanOrdinaryDialogueTests(unittest.TestCase):
    def setUp(self):
        self.h = _Harness(); self.addCleanup(self.h.close)
        self.addCleanup(os.chdir, Path.cwd()); os.chdir(self.h.root)
        self.llm = _LLM(); self.llm.response = 'That sounds good.'
        self.llm.local_ordinary_dialogue = True
        self.h.writer.compare = lambda *a, **k: {}
        self.events = []
        c = Conversation(self.llm, conversation_file=self.h.conversation_file,
            summary_file=self.h.root/'summary.json', memory_authority='v2',
            clock=lambda: datetime(2026, 9, 10, 12, tzinfo=timezone.utc))
        authority = DevelopmentV2MemoryAuthority(self.h.writer.store, self.h.character_id,
            c.messages, recall=HealthyRecall())
        character = {'name': 'Mira', '_character_id': self.h.character_id}
        self.s = AssistantService(self.llm, Memory(), c, object(), character,
            build_character_prompt(character, 'A dry-witted synthetic companion.'), _TTS(),
            character_id=self.h.character_id, memory_v2_shadow_writer=self.h.writer,
            memory_authority='v2', memory_v2_authority=authority, recent_pulse_enabled=False)
        self.s.subscribe(self.events.append); self.addCleanup(self.s.close)

    def policy(self, query='Hello.'):
        decision = decide_memory_query(query)
        p = self.s._response_policy(query)
        return self.s._apply_memory_authority_policy(query, p,
            active_truth_scope=self.s.truth_scope_provenance(), memory_query_decision=decision)

    def test_eight_ordinary_categories_use_actual_service_plain_path(self):
        for query in ('Hello.', 'Let us chat.', 'What do you think of puzzles?',
                      'Tell me a silly joke.', 'Could use a little encouragement.',
                      'Why does a triangle have three sides?', 'Tell me more.',
                      '*grins* You win this round.'):
            with self.subTest(query=query):
                self.llm.calls.clear()
                result = self.s.process_text_turn(query, speak=False)
                self.assertTrue(result.succeeded, result.error)
                prompt = self.llm.calls[0][1]
                self.assertIn('NATURAL COMPANION DIALOGUE:', prompt)
                self.assertNotIn('AUTHORITATIVE RESPONSE FORMAT:', prompt)
                self.assertNotIn('[Response expression continuity]', prompt)
                self.assertEqual('That sounds good.', result.reply)

    def test_explicit_memory_current_fact_and_followup_retain_existing_contract(self):
        for query in ('What did I tell you about my project?', 'What is my favorite color?',
                      'Which place was that?', 'What did I say before I corrected it to blue?'):
            with self.subTest(query=query):
                p = self.policy(query)
                self.assertFalse(self.s._lean_ordinary_eligible(p))
                self.llm.calls.clear(); self.s.process_text_turn(query, speak=False)
                self.assertTrue(all('NATURAL COMPANION DIALOGUE:' not in call[1] for call in self.llm.calls))

    def test_direct_state_and_exact_spoken_content_obligations_fail_closed(self):
        for query in ('What are you wearing?', 'Can you see me?', 'What time is it?'):
            with self.subTest(query=query):
                self.assertFalse(self.s._lean_ordinary_eligible(self.policy(query)))
        p = self.policy()
        for intent in ('exact_spoken_content', 'waking', 'future_unknown_obligation'):
            requirement = ResponseRequirement(intent, (), '', 'Typed obligation.')
            self.assertFalse(self.s._lean_ordinary_eligible(replace(p, requirement=requirement)))

    def test_every_constrained_capability_stays_structured(self):
        p = self.policy()
        for field, value in (('vision_mode','unavailable'), ('hearing_mode','unavailable'),
                ('smell_mode','constrained'), ('taste_mode','constrained'), ('touch_mode','unavailable'),
                ('speech_mode','unavailable'), ('speech_mode','constrained'), ('speech_mode','nonverbal'),
                ('awareness_mode','asleep'), ('awareness_mode','waking'), ('hands_mode','occupied'),
                ('left_hand_mode','occupied'), ('right_arm_mode','unavailable'),
                ('locomotion_constraint','immobile'), ('posture_mode','unknown_future_mode')):
            with self.subTest(field=field, value=value):
                self.assertFalse(self.s._lean_ordinary_eligible(replace(p, effects=replace(p.effects, **{field:value}))))

    def test_static_state_without_format_obligation_is_retained(self):
        p = self.policy()
        p = replace(p, effects=replace(p.effects, posture_mode='sitting'),
                    context_block='Existing authoritative sitting context.', enforce_before_presentation=True)
        self.assertTrue(self.s._lean_ordinary_eligible(p))
        self.s._generate_reply('Let us chat.', ordinary_policy=p, response_policy_context=p.context_block)
        self.assertIn(p.context_block, str(self.llm.calls[0][0]))

    def test_return_respect_is_preserved_but_factual_return_obligation_stays_structured(self):
        p = self.policy()
        r = ResponseRequirement('return_continuity', (), '', 'Respect return.', mode='must_respect')
        self.assertTrue(self.s._lean_ordinary_eligible(replace(p, requirement=r)))
        self.assertFalse(self.s._lean_ordinary_eligible(replace(p,
            requirement=replace(r, facts=(RequiredFact('time','today',('today',)),)))))

    def test_action_mutation_error_reaction_and_unknown_owners_fail_closed(self):
        p = self.policy()
        for field, value in (('action_plan',object()), ('action_decision_category','abstained'),
                ('policy_error','unavailable'), ('changed_by_current_evidence',True),
                ('hearing_input_unavailable',True), ('current_user_projection','redacted'),
                ('memory_realization',object()), ('memory_realized_response',object()),
                ('authoritative_memory_response','Owned answer.'), ('memory_query_decision',None),
                ('memory_answer_requirement',None), ('effects',None)):
            with self.subTest(field=field):
                self.assertFalse(self.s._lean_ordinary_eligible(replace(p, **{field:value})))
        self.assertFalse(self.s._lean_ordinary_eligible(None))
        self.assertFalse(self.s._lean_ordinary_eligible(object()))

    def test_explicit_local_opt_in_only_and_adapter_default_stays_disabled(self):
        from llm.openai_compatible import OpenAICompatibleLLM
        provider = OpenAICompatibleLLM(api_key='', base_url='http://127.0.0.1:1/v1', model='synthetic')
        self.assertFalse(provider.local_ordinary_dialogue)
        self.addCleanup(provider.client.close)
        p = self.policy()
        for flag in (False, None, 'true'):
            self.llm.local_ordinary_dialogue = flag
            self.assertFalse(self.s._lean_ordinary_eligible(p))
        self.llm.local_ordinary_dialogue = True
        self.s._memory_authority = 'v1'
        self.assertFalse(self.s._lean_ordinary_eligible(p))

    def test_actual_local_and_online_factories_leave_experiment_disabled(self):
        from llm.llm import create_llm
        settings = dict(mode='local', local_api_key='', local_endpoint='http://127.0.0.1:1/v1',
            local_model='synthetic', online_provider='openai_compatible', api_key='synthetic-test-key',
            online_base_url='http://127.0.0.1:1/v1', online_model='synthetic')
        for mode in ('local', 'online'):
            with self.subTest(mode=mode), patch('llm.llm.get_model_settings', return_value={**settings, 'mode':mode}):
                provider = create_llm()
                self.addCleanup(provider.client.close)
                self.assertFalse(provider.local_ordinary_dialogue)

    def test_disabled_request_is_byte_identical_to_original_prompt_owner(self):
        from presentation_metadata import response_expression_context
        p = self.policy(); self.llm.local_ordinary_dialogue = False
        expected = self.s.character_prompt + '\n\n' + response_expression_context(None)
        self.assertEqual(expected, self.s._response_character_prompt(ordinary_policy=p))

    def test_only_exact_owned_suffix_is_replaced_and_personality_is_verbatim(self):
        prompt = self.s.character_prompt
        lean = lean_ordinary_character_prompt(prompt)
        self.assertIn('A dry-witted synthetic companion.', lean)
        self.assertEqual(prompt.split(response_contract_prompt())[0], lean.split('NATURAL COMPANION DIALOGUE:')[0])
        for custom in ('Custom prompt.', prompt+'New machine obligation.', response_contract_prompt()+prompt):
            self.assertIsNone(lean_ordinary_character_prompt(custom))

    @patch.dict('os.environ', {'AIFREN_CONTEXT_GOVERNOR':'1'})
    def test_complete_and_stream_assembly_keep_same_authority_and_personality(self):
        p = self.policy(); query = 'Let us chat.'
        self.s._generate_reply(query, ordinary_policy=p, memory_answer_requirement=p.memory_answer_requirement,
                               memory_query_decision=p.memory_query_decision)
        complete = self.llm.calls[-1]
        chunks = []
        def stream(context, prompt, **kwargs):
            self.llm.calls.append((tuple(context),prompt)); yield 'Hello'
            self.assertEqual(['Hello'], chunks)  # Plain prose visible before completion, no JSON wait.
            yield ' there.'
        self.llm.stream_generate = stream
        result = self.s._stream_reply(query, chunks.append, None, threading.Event(), ordinary_policy=p,
            memory_answer_requirement=p.memory_answer_requirement, memory_query_decision=p.memory_query_decision)
        self.assertEqual('Hello there.', result)
        # Complete generation appends the existing retrospective system boundary;
        # the streaming owner is used only where that full-draft guard permits it.
        self.assertEqual(complete[0], self.llm.calls[-1][0])
        self.assertIn(self.llm.calls[-1][1], complete[1])

    def test_lean_plain_reply_needs_no_repair_and_never_persists_prompt(self):
        with patch.object(self.s, '_repair_governed_response', side_effect=AssertionError('unnecessary repair')):
            result = self.s.process_text_turn('Hello.', speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(1, len(self.llm.calls))
        self.assertNotIn('NATURAL COMPANION', str(self.s.conversation.messages))
        self.assertNotIn('NATURAL COMPANION', str(self.events))

    def test_final_validation_is_unchanged_and_unsafe_draft_is_not_published(self):
        self.llm.response = 'You told me that your favorite planet is Neptune.'
        result = self.s.process_text_turn('Hello.', speak=False)
        self.assertNotEqual(self.llm.response, result.reply)
        self.assertNotIn(self.llm.response, str([e.data for e in self.events if e.type=='assistant_response']))

    def test_cancelled_and_failed_persistence_have_no_assistant_publication(self):
        original = self.llm.generate
        def cancelled(*args, **kwargs):
            answer = original(*args, **kwargs); self.s._cancel_active_turn(); return answer
        with patch.object(self.llm, 'generate', side_effect=cancelled):
            self.s.process_text_turn('Hello.', speak=False)
        self.assertFalse(any(e.type=='assistant_response' for e in self.events))
        self.events.clear()
        save = self.s.conversation.save
        def failed_save():
            if self.s.conversation.messages[-1].get('role')=='assistant': raise OSError('synthetic failure')
            return save()
        with patch.object(self.s.conversation, 'save', side_effect=failed_save):
            result = self.s.process_text_turn('Hello again.', speak=False)
        self.assertFalse(result.succeeded)
        self.assertFalse(any(e.type=='assistant_response' for e in self.events))

    def test_scene_or_direct_helper_call_without_reviewed_turn_policy_stays_original(self):
        self.s._generate_reply('A scene event.', companion_context_allowed=False)
        self.assertNotIn('NATURAL COMPANION DIALOGUE:', self.llm.calls[0][1])

    def test_v1_spies_remain_zero(self):
        with patch.object(self.s.memory, 'get_relevant_memories', side_effect=AssertionError('V1 read')), \
             patch.object(self.s.memory, 'process', side_effect=AssertionError('V1 write')):
            self.assertTrue(self.s.process_text_turn('Hello.', speak=False).succeeded)


if __name__ == '__main__': unittest.main()
