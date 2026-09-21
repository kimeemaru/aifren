"""Delivery preferences cannot take ownership of memory, state, or history."""
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aifren.assistant import build_character_prompt
from aifren.dialogue.conversation_style import natural_character_prompt, NATURAL_POLICY
from aifren.runtime.model_settings import companion_preferences, set_companion_preferences
from aifren.dialogue.presentation_metadata import ParsedAssistantResponse, ResponsePresentationMetadata


class CompanionPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / 'settings.json'
        p = patch('aifren.runtime.model_settings.LOCAL_SETTINGS_FILE', self.file); p.start(); self.addCleanup(p.stop)

    def test_defaults_and_atomic_partial_save_preserve_unrelated_data(self):
        self.assertEqual(companion_preferences(), dict(conversation_style='roleplay',
            responsive_speech=True, automatic_expressions=False))
        self.file.write_text(json.dumps({'unrelated': {'color':'violet'}, 'explicit_avatar_cues':True}))
        set_companion_preferences(conversation_style='natural')
        set_companion_preferences(automatic_expressions=True)
        self.assertEqual(companion_preferences()['conversation_style'], 'natural')
        self.assertEqual(json.loads(self.file.read_text())['unrelated'], {'color':'violet'})
        self.assertTrue(json.loads(self.file.read_text())['explicit_avatar_cues'])

    def test_invalid_preferences_cannot_partially_save(self):
        self.file.write_text('{}'); before = self.file.read_bytes()
        for values in ({'conversation_style':'invented'}, {'responsive_speech':1},
                       {'automatic_expressions':'true'}, {'new_memory':'fact'}, {}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                set_companion_preferences(**values)
            self.assertEqual(before, self.file.read_bytes())


class ManagedTemplateAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_ready_managed_matching_model_admits_reviewed_template_role(self):
        from aifren.backend_host import AIFrenWebSocketHost
        from unittest.mock import Mock
        for ownership, active, expected in (
                ('managed', 'selected.gguf', 'system'),
                ('external', 'selected.gguf', 'user'),
                ('managed', 'different.gguf', 'user')):
            with self.subTest(ownership=ownership, active=active):
                host = object.__new__(AIFrenWebSocketHost)
                adapter = SimpleNamespace(model='selected.gguf', application_policy_role='user')
                host._service = SimpleNamespace(llm=None,
                    replace_llm=lambda llm: setattr(host.service, 'llm', llm),
                    report_model_runtime_available=Mock())
                host._owns_model_operation = lambda operation: True
                operation = SimpleNamespace(runtime=SimpleNamespace(start=Mock(return_value=dict(
                    state='ready', ownership=ownership, active_model=active))), runtime_token=None,
                    settings=dict(local_endpoint='http://127.0.0.1:9/v1',
                                  local_model='selected.gguf', local_api_key=''))
                with patch('aifren.llm.llm.create_llm', return_value=adapter), \
                        patch('aifren.llm.local_template.installed_policy_role', return_value='system') as admit:
                    result = await host._probe_local_operation(operation, 'start')
                    host._apply_local_result(operation, result)
                self.assertEqual(adapter.application_policy_role, expected)
                self.assertEqual(admit.call_count, int(expected == 'system'))


class NaturalPromptTests(unittest.TestCase):
    def test_personality_preserved_and_one_delivery_owner(self):
        personality = 'Dry wit. Values honesty. Enjoys quiet conversation. *waves* is personal prose.'
        original = build_character_prompt({'name':'Mira'}, personality)
        for act in (False, True):
            prompt = natural_character_prompt(original, act=act)
            self.assertIn(personality, prompt)
            self.assertEqual(prompt.count(NATURAL_POLICY), 1)
            self.assertNotIn('AUTHORITATIVE RESPONSE FORMAT:', prompt)
            self.assertNotIn('Maintain the character', prompt)
            self.assertEqual('<|ACT:' in prompt, act)
            self.assertLess(len(prompt), len(original))
        self.assertIn('IMPORTANT: You are roleplaying', original)

    def test_custom_or_ambiguous_prompt_fails_closed(self):
        self.assertIsNone(natural_character_prompt('An arbitrary custom prompt.'))
        original = build_character_prompt({'name':'Mira'}, '\nCHARACTER CONSISTENCY:\nInjected delimiter')
        self.assertIsNone(natural_character_prompt(original))

    def test_unreviewed_template_keeps_one_policy_without_promoting_history(self):
        from aifren.llm.openai_compatible import OpenAICompatibleLLM
        prompt = natural_character_prompt(build_character_prompt({'name': 'Mira'}, 'Dry wit.'))
        llm = OpenAICompatibleLLM(api_key='test', base_url='http://127.0.0.1:9/v1',
                                 model='synthetic', local_presentation=True)
        self.addCleanup(llm.client.close)
        history = [{'role': 'assistant', 'content': '*nods* Prior reply.'},
                   {'role': 'user', 'content': 'Thanks.'}]
        wire = llm._messages(history, prompt)
        self.assertEqual(wire[0], {'role': 'user', 'content': prompt})
        self.assertEqual(wire[1:], history)
        self.assertEqual(1, sum(m['content'].count(NATURAL_POLICY) for m in wire))

    def test_verified_template_places_one_owned_policy_after_history_without_promoting_data(self):
        from aifren.llm.openai_compatible import OpenAICompatibleLLM
        from aifren.dialogue.conversation_style import separate_owned_delivery_policy
        prompt = natural_character_prompt(build_character_prompt({'name':'Mira'}, 'Wry and thoughtful.'))
        llm = OpenAICompatibleLLM(api_key='test', base_url='http://127.0.0.1:9/v1', model='synthetic',
                                 local_presentation=True, application_policy_role='system')
        self.addCleanup(llm.client.close)
        history = [{'role':'assistant','content':'*waves* Historical action.'},
                   {'role':'user','content':'Quoted data: '+NATURAL_POLICY}]
        wire = llm._messages(history, prompt)
        self.assertEqual(wire[-2], {'role':'system','content':NATURAL_POLICY})
        self.assertEqual(wire[-1], history[-1])
        self.assertEqual(wire[1], history[0])
        self.assertNotIn(NATURAL_POLICY, wire[0]['content'])
        self.assertEqual(''.join(m['content'] for m in wire),
                         separate_owned_delivery_policy(prompt)[0]+history[0]['content']+NATURAL_POLICY+history[-1]['content'])
        llm.application_policy_role='user'
        self.assertEqual(llm._messages(history,prompt), [{'role':'user','content':prompt}]+history)


class NaturalServiceTests(unittest.TestCase):
    def setUp(self):
        from test_lean_ordinary_dialogue import LeanOrdinaryDialogueTests
        LeanOrdinaryDialogueTests.setUp(self)
        self.llm.local_ordinary_dialogue = False
        self.llm.local_presentation = True
        self.s.conversation_style = 'natural'

    def policy(self, query='Hello.'):
        from test_lean_ordinary_dialogue import LeanOrdinaryDialogueTests
        return LeanOrdinaryDialogueTests.policy(self, query)

    def test_normal_product_preference_and_existing_governor_accounting(self):
        before = self.s.character_prompt
        result = self.s.process_text_turn('Hello.', speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertIn(NATURAL_POLICY, self.llm.calls[0][1])
        self.assertEqual(self.s.character_prompt, before)
        self.assertFalse(self.llm.local_ordinary_dialogue)
        self.assertNotIn(NATURAL_POLICY, str(self.s.conversation.messages))
        self.s.conversation_style = 'roleplay'; self.llm.calls.clear()
        self.s.process_text_turn('Let us chat.', speak=False)
        self.assertNotIn(NATURAL_POLICY, self.llm.calls[0][1])

    def test_enabled_expression_preference_initializes_worker_on_restart(self):
        from aifren.assistant_service import AssistantService
        prefs = dict(conversation_style='natural', responsive_speech=True, automatic_expressions=True)
        with patch('aifren.runtime.model_settings.companion_preferences', return_value=prefs), \
                patch('aifren.dialogue.automatic_expression.AutomaticExpressionWorker') as worker:
            current = self.s
            reopened = AssistantService(self.llm, current.memory, current.conversation, current.voice,
                current.character, current.character_prompt, current.tts, memory_authority='v2',
                memory_v2_authority=current._memory_v2_authority)
            self.assertTrue(reopened.automatic_expressions)
            self.assertIs(reopened._automatic_expression_worker, worker.return_value)
            worker.return_value.set_enabled.assert_called_with(True)
            # Rebinding recovery must not initialize a second presentation owner.
            reopened._bind_canonical_observation_recovery()
            self.assertEqual(worker.call_count, 1)
            reopened.close()

    def test_machine_obligations_and_memory_are_unchanged(self):
        for query in ('What is my favorite color?', 'What did I tell you about my project?',
                      'Can you see me?', 'What are you wearing?'):
            with self.subTest(query=query):
                self.assertNotIn(NATURAL_POLICY, self.s._response_character_prompt(ordinary_policy=self.policy(query)))
        p = self.policy()
        self.assertNotIn(NATURAL_POLICY, self.s._response_character_prompt(
            ordinary_policy=replace(p, effects=replace(p.effects, speech_mode='unavailable'))))

    def test_selection_diagnostics_distinguish_applied_from_unsupported_or_required_contract(self):
        ordinary = self.policy()
        with patch('aifren.assistant_service.development_flight_recorder') as recorder:
            prompt = self.s._response_character_prompt(ordinary_policy=ordinary)
            self.assertEqual(1, prompt.count(NATURAL_POLICY))
            self.assertEqual(self.s._last_delivery_diagnostics, {
                'source': 'natural', 'state': 'applied', 'reason': 'ordinary_local'})
            recorder.return_value.mark.assert_called_once_with(
                'conversation_delivery', **self.s._last_delivery_diagnostics)

        self.s.character_prompt = 'An unsupported custom prompt with private payload.'
        with patch('aifren.assistant_service.development_flight_recorder') as recorder:
            prompt = self.s._response_character_prompt(ordinary_policy=self.policy())
            self.assertNotIn(NATURAL_POLICY, prompt)
            self.assertEqual(self.s._last_delivery_diagnostics['reason'], 'unrecognized_prompt')
            self.assertEqual(self.s._last_delivery_diagnostics['state'], 'not_applied')
            self.assertNotIn('private payload', str(recorder.mock_calls))

        self.llm.local_presentation = False
        self.s._response_character_prompt(ordinary_policy=self.policy())
        self.assertEqual(self.s._last_delivery_diagnostics['reason'], 'local_provider_required')
        self.llm.local_presentation = True
        self.s._response_character_prompt(ordinary_policy=self.policy('What is my favorite color?'))
        self.assertEqual(self.s._last_delivery_diagnostics['reason'], 'structured_obligation')
        self.s.conversation_style = 'roleplay'
        self.s._response_character_prompt(ordinary_policy=self.policy())
        self.assertEqual(self.s._last_delivery_diagnostics['reason'], 'roleplay_selected')
        self.s.conversation_style = 'unrecognized synthetic value'
        self.s._response_character_prompt(ordinary_policy=ordinary)
        self.assertEqual(self.s._last_delivery_diagnostics['source'], 'roleplay')

    def test_quoted_ordinary_reply_is_published_without_style_changing_repair(self):
        raw = '"A small victory. That helps."'
        self.llm.response = raw
        for style, streaming in (('natural', False), ('natural', True), ('roleplay', True)):
            with self.subTest(style=style, streaming=streaming):
                self.s.conversation_style = style
                self.llm.calls.clear()
                if streaming:
                    def stream(context, prompt, **_kwargs):
                        # Exercise service plain/structured selection, not the
                        # envelope-only StreamingResponseDialogue helper.
                        yield from self.llm.generate(context, prompt)
                    self.llm.stream_generate = stream
                with patch.object(self.s, '_repair_governed_response', return_value=None) as repair:
                    result = self.s.process_text_turn('I am sorting spare bolts into two trays.', speak=False)
                self.assertTrue(result.succeeded, result.error)
                repair.assert_not_called()
                self.assertEqual(raw, result.reply)
                self.assertEqual(raw, result.spoken_text)
                self.assertEqual(raw, self.s.conversation.messages[-1]['content'])
                self.assertEqual(1, len(self.llm.calls))

    def test_quoted_prose_does_not_satisfy_constrained_speech_obligations(self):
        from aifren.dialogue.presentation_metadata import parse_assistant_response
        p = self.policy()
        parsed = parse_assistant_response('"I can speak normally."')
        for mode in ('unavailable', 'constrained'):
            with self.subTest(mode=mode):
                policy = replace(p, effects=replace(p.effects, speech_mode=mode),
                                 enforce_before_presentation=True)
                self.assertFalse(self.s._validate_governed_response(parsed, policy)[0])

    def test_body_restriction_does_not_impose_nonverbal_caption_length_on_speech(self):
        from aifren.state.capability_policy import capability_context_block
        p = self.policy('Explain how an audio queue works in detail.')
        text = ('A queue preserves the order of pending work while one component prepares '
                'the next unit and another component uses the current one. ') * 6
        for effects in (replace(p.effects, hands_mode='partially_occupied'),
                        replace(p.effects, vision_mode='unavailable')):
            with self.subTest(effects=effects):
                governed = replace(p, effects=effects, enforce_before_presentation=True)
                parsed = ParsedAssistantResponse(dialogue=text, contract_status='plain_text')
                accepted, reason, spoken, _ = self.s._validate_governed_response(parsed, governed)
                self.assertTrue(accepted, reason)
                self.assertEqual(spoken, text.strip())
                self.assertNotIn('maximum 100', capability_context_block(effects))
        blind = replace(p, effects=replace(p.effects, vision_mode='unavailable'))
        self.assertFalse(self.s._validate_governed_response(
            ParsedAssistantResponse(dialogue='I can see you clearly.'), blind)[0])
        constrained = replace(p, effects=replace(p.effects, speech_mode='constrained'))
        self.assertIn('maximum 100', capability_context_block(constrained.effects))
        self.assertEqual(self.s._validate_governed_response(
            ParsedAssistantResponse(dialogue=text), constrained)[1], 'response_length')

    def test_untriggered_memory_guard_cannot_supply_empty_ordinary_fallback(self):
        p = self.policy()
        self.assertFalse(p.memory_answer_requirement.triggered)
        self.assertEqual(p.memory_answer_requirement.fallback_dialogue, '')
        self.assertTrue(self.s._governed_fallback_response(p).dialogue.strip())

    def test_failed_ordinary_repair_publishes_nonempty_safe_response(self):
        original = self.s._validate_governed_response
        count = 0
        def reject_primary(parsed, policy):
            nonlocal count
            count += 1
            return (False, 'synthetic_rejection', '', None) if count == 1 else original(parsed, policy)
        with patch.object(self.s, '_validate_governed_response', side_effect=reject_primary), \
                patch.object(self.s, '_repair_governed_response', return_value=None):
            result = self.s.process_text_turn('Let us chat.', speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertTrue(result.reply.strip())
        self.assertEqual(self.s.conversation.messages[-1]['content'], result.reply)

    def test_auto_input_excludes_explicit_neutral_and_memory_core(self):
        self.s.automatic_expressions = True
        p = self.policy()
        parsed = ParsedAssistantResponse(dialogue='That is wonderful.')
        self.assertEqual(self.s._automatic_expression_input(parsed, p), parsed.dialogue)
        self.assertEqual(self.s._automatic_expression_input(replace(parsed,
            presentation=ResponsePresentationMetadata(emotion='neutral')), p), '')
        realized = SimpleNamespace(reaction='', core=SimpleNamespace(dialogue='A remembered fact.'))
        self.assertEqual(self.s._automatic_expression_input(parsed,
            replace(p, memory_realized_response=realized)), '')

    def test_auto_presentation_does_not_inherit_output_format_gate(self):
        self.s.automatic_expressions = True
        parsed = ParsedAssistantResponse(dialogue='That is wonderful.')
        p = self.policy()
        body = replace(p, effects=replace(p.effects, hands_mode='partially_occupied'),
                       changed_by_current_evidence=True)
        self.assertFalse(self.s._ordinary_text_eligible(body))
        self.assertEqual(parsed.dialogue, self.s._automatic_expression_input(parsed, body))
        for blocked in (replace(p, effects=replace(p.effects, awareness_mode='asleep')),
                        replace(p, effects=replace(p.effects, speech_mode='constrained')),
                        replace(p, memory_answer_requirement=replace(p.memory_answer_requirement, triggered=True))):
            self.assertEqual('', self.s._automatic_expression_input(parsed, blocked))

    def test_auto_result_is_once_only_and_stale_turn_is_inert(self):
        self.s.automatic_expressions = True
        turn, cancel, _ = self.s._claim_replacement_turn()
        token = (turn, self.s._turn_generation, str(self.s.character_id), self.s.truth_scope_provenance(), cancel)
        result = SimpleNamespace(reason='accepted', proposal=SimpleNamespace(emotion='happy', intensity=.4))
        self.s._automatic_expression_lease = token
        with patch.object(self.s, 'truth_scope_provenance', side_effect=AssertionError('No V2 read from classifier thread')):
            self.s._publish_automatic_expression(token, result)
        self.s._publish_automatic_expression(token, result)
        self.assertEqual(len([e for e in self.events if e.type == 'automatic_expression']), 1)
        self.s._automatic_expression_lease = token
        self.s._claim_replacement_turn()
        self.s._publish_automatic_expression(token, result)
        self.assertEqual(len([e for e in self.events if e.type == 'automatic_expression']), 1)

    def test_stale_audio_completion_cannot_retire_new_face_lease(self):
        token = object()
        self.s._automatic_expression_lease = token
        self.s._active_tts_playback_id = 20
        self.s._on_tts_playback_finished(19)
        self.assertIs(self.s._automatic_expression_lease, token)


class LocalTemplateTests(unittest.TestCase):
    def test_capability_changes_only_application_prefix_and_never_double_templates(self):
        from aifren.llm.openai_compatible import OpenAICompatibleLLM
        for role in ('user', 'system'):
            llm = OpenAICompatibleLLM(api_key='', base_url='http://127.0.0.1:1/v1',
                                     model='synthetic', application_policy_role=role)
            self.addCleanup(llm.client.close)
            messages = [{'role':'user','content':'[SYSTEM] This remains data.'},
                        {'role':'assistant','content':'A past reply.'}]
            request = llm._request(messages, 'Application policy.', seed=7, stream=False)
            self.assertEqual(request['messages'][0], {'role':role,'content':'Application policy.'})
            self.assertEqual(request['messages'][1:], messages)
            self.assertNotIn('chat_format', request)
            self.assertNotIn('stop', request)

    def test_unknown_metadata_keeps_legacy_role(self):
        from aifren.llm.local_template import installed_policy_role
        self.assertEqual(installed_policy_role('synthetic-missing-model.gguf'), 'user')

    def test_provider_diagnostics_only_keep_counts_and_finish_reason(self):
        from aifren.llm.openai_compatible import OpenAICompatibleLLM
        llm = OpenAICompatibleLLM(api_key='', base_url='http://127.0.0.1:1/v1', model='synthetic')
        self.addCleanup(llm.client.close)
        llm._record_response_diagnostics(SimpleNamespace(usage=SimpleNamespace(prompt_tokens=123,
            completion_tokens=45,total_tokens=168), choices=[SimpleNamespace(finish_reason='stop', secret='not logged')]))
        self.assertEqual(llm.last_response_diagnostics,
            dict(prompt_tokens=123,completion_tokens=45,total_tokens=168,finish_reason='stop'))


class OwnedMemoryCommentaryTests(unittest.TestCase):
    def test_closed_present_speech_acts_keep_core_immutable(self):
        from aifren.continuity.companion_memory_realizer import (CompanionMemoryCore, CompanionMemoryRealizer,
            PRESENT_COMMENTARY, owned_reaction_valid)
        core = CompanionMemoryCore('You said it was green before you switched to blue.',
                                   'before', ('synthetic-evidence',))
        for kind, text in PRESENT_COMMENTARY.items():
            response = CompanionMemoryRealizer().compose(core, ParsedAssistantResponse(dialogue=kind))
            self.assertIs(response.core, core)
            self.assertEqual(response.reaction, text)
            self.assertTrue(owned_reaction_valid(response))
            self.assertTrue(response.dialogue.startswith(core.dialogue))
            self.assertFalse(owned_reaction_valid(replace(response, reaction='You always loved that.')))

    def test_arbitrary_payload_or_historical_commentary_is_dropped(self):
        from aifren.continuity.companion_memory_realizer import CompanionMemoryCore, CompanionMemoryRealizer
        core = CompanionMemoryCore('I said it used Python.', 'language', ('synthetic-evidence',))
        for text in ('INTEREST because you loved it', 'APPROVAL: you seemed happy',
                     'I think you always wanted it.', 'It must have been raining.',
                     'We could go there again.', '{"reaction":"INTEREST","fact":"invented"}'):
            response = CompanionMemoryRealizer().compose(core, ParsedAssistantResponse(dialogue=text))
            self.assertEqual(response.dialogue, core.dialogue)
        self.assertEqual(CompanionMemoryRealizer().compose(core,
            ParsedAssistantResponse(dialogue='NONE')).dialogue, core.dialogue)


class CommittedServiceSpeechTests(unittest.TestCase):
    def setUp(self):
        NaturalServiceTests.setUp(self)
        from test_responsive_speech import SyntheticKokoro, StreamFactory, audio_owner
        self.provider = SyntheticKokoro(); self.device = StreamFactory()
        p = patch.object(audio_owner.sd, 'OutputStream', self.device); p.start(); self.addCleanup(p.stop)
        self.s.tts = self.provider; self.s._configure_tts_playback_events()
        self.s.responsive_speech = True
        self.queues = []
        self.addCleanup(self.cleanup_speech)

    def cleanup_speech(self):
        self.s.stop_speaking()
        for queue in self.queues:
            queue.cancel(); queue.join(3)

    def test_only_exact_committed_spoken_reply_reaches_one_continuous_utterance(self):
        from test_responsive_speech import TEXT
        self.llm.response = '*smiles* ' + TEXT
        commit_seen = []
        def before(_index, _text):
            c = self.s.conversation
            commit_seen.append(c.is_message_persisted(len(c.messages)-1, c.messages[-1]))
        self.provider.before_unit = before
        result = self.s.process_text_turn('Please explain the process.', speak=True)
        queue = self.s._streaming_speech_queue
        if queue is not None:
            self.queues.append(queue); queue.join(3)
        self.assertTrue(result.succeeded, result.error)
        self.assertTrue(commit_seen and all(commit_seen))
        self.assertEqual(result.spoken_text, ''.join(self.provider.units))
        starts = [e.data for e in self.events if e.type == 'tts_state' and e.data.get('state') == 'playback_started']
        self.assertTrue(starts)
        self.assertEqual(1, len({e['playback_id'] for e in starts}))
        self.assertTrue(all(e['committed_stream'] and e['complete_text'] == result.spoken_text for e in starts))
        self.assertEqual(1, len([e for e in self.events if e.type == 'assistant_response']))
        stops = [e.data for e in self.events if e.type == 'tts_state' and e.data.get('state') == 'stopped'
                 and e.data.get('playback_id') == starts[0]['playback_id']]
        self.assertEqual(1, len(stops))
        self.assertTrue(stops[0]['committed_stream'])
        self.assertEqual(starts[0]['turn_id'], stops[0]['turn_id'])

    def test_stop_before_first_audio_keeps_committed_turn_identity(self):
        self.s._set_pending_stream_speech('Pending reply.', 'Pending reply.', 0, 7,
            self.s._speech_generation, committed_stream=True)
        self.s.stop_speaking(interrupted=True)
        event = [e.data for e in self.events if e.type == 'tts_state'][-1]
        self.assertEqual(event['turn_id'], 7)
        self.assertTrue(event['committed_stream'])
        self.assertTrue(event['interrupted'])

    def test_failed_queue_keeps_terminal_identity_after_service_cleanup(self):
        owner = SimpleNamespace(playback_id=17, _committed_text='One accepted reply.')
        self.s._streaming_speech_queue = None
        self.s._stream_tts_failed('tts_chunk_failed', self.s._speech_generation, 7, queue=owner)
        event = [e.data for e in self.events if e.type == 'tts_state'][-1]
        self.assertEqual((event['turn_id'], event['playback_id']), (7, 17))
        self.assertTrue(event['committed_stream'])
        before = len(self.events)
        self.s._stream_tts_failed('tts_chunk_failed', self.s._speech_generation-1, 7, queue=owner)
        self.assertEqual(before, len(self.events))

    def test_old_stop_cannot_cancel_a_concurrent_replacement(self):
        from unittest.mock import Mock
        replacement = SimpleNamespace(cancel=Mock(), owns_continuous_playback=True, playback_id=18)
        def replacement_starts():
            # Simulate a new accepted speech owner after invalidation, while
            # the old queue's nonblocking provider cancellation is returning.
            with self.s._speech_generation_lock:
                self.s._streaming_speech_queue = replacement
                self.s._set_pending_stream_speech('New.', 'New.', 0, 8,
                    self.s._speech_generation, committed_stream=True)
        old = SimpleNamespace(cancel=Mock(side_effect=replacement_starts),
                              owns_continuous_playback=True, playback_id=17)
        self.s._streaming_speech_queue = old
        with patch.object(self.provider, 'stop') as stop:
            self.s.stop_speaking(interrupted=True)
            stop.assert_not_called()
        self.assertIs(self.s._streaming_speech_queue, replacement)
        self.assertEqual(self.s._pending_stream_speech['turn_id'], 8)
        replacement.cancel.assert_not_called()
        self.s._streaming_speech_queue = None

    def test_stale_chunk_metadata_cannot_reopen_cancelled_playback(self):
        self.s._speech_generation = 2
        self.s._set_pending_stream_speech('Old.', 'Old.', 0, 1, 1)
        before = len(self.events)
        self.s._on_tts_playback_started(.5, [], [], 4,
            chunk_metadata={'committed_stream':True,'complete_text':'Old.'})
        self.assertEqual(before, len(self.events))
        self.assertEqual(0, self.s._active_tts_playback_id)
