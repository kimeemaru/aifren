"""Same-inference expression context through committed, synthetic service turns."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from assistant import build_character_prompt
from assistant_service import AssistantService
from conversation.conversation import Conversation
from memory_v2_authority import DevelopmentV2MemoryAuthority
from test_assistant_service_v2_authority import _LLM, _TTS
from test_continuity_companion_tranche import _Harness
from test_memory_routing_partial_evidence import HealthyRecall
from test_return_continuity import Memory


class ResponseExpressionContextTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ); env.start(); self.addCleanup(env.stop)
        for key in tuple(os.environ):
            if key.startswith('AIFREN_'): del os.environ[key]
        self.h = _Harness(); self.addCleanup(self.h.close)
        self.addCleanup(os.chdir, Path.cwd()); os.chdir(self.h.root)
        self.h.writer.compare = lambda *a, **k: {}
        self.llm = _LLM(); self.memory = Memory(); self.events = []

    def service(self, mode='v1'):
        c = Conversation(self.llm, conversation_file=self.h.conversation_file,
            summary_file=self.h.root/'summary.json', memory_authority=mode,
            clock=lambda: datetime(2026, 9, 8, 12, tzinfo=timezone.utc))
        authority = DevelopmentV2MemoryAuthority(self.h.writer.store, self.h.character_id,
            c.messages, recall=HealthyRecall()) if mode == 'v2' else None
        character = {'name':'Mira', '_character_id':self.h.character_id}
        s = AssistantService(self.llm, self.memory, c, object(), character,
            build_character_prompt(character, 'You are a friendly test companion.'), _TTS(),
            character_id=self.h.character_id, memory_v2_shadow_writer=self.h.writer,
            memory_authority=mode, memory_v2_authority=authority)
        s.subscribe(self.events.append); self.addCleanup(s.close)
        return s

    def reply(self, s, emotion=None, text='Glad to hear it.'):
        self.llm.response = json.dumps({'dialogue':text, **({'presentation':{
            'emotion':emotion, 'intensity':0.45}} if emotion else {})})
        result = s.process_text_turn('Let us continue.', speak=False)
        self.assertTrue(result.succeeded, result.error)
        return result

    def context(self):
        return self.llm.calls[-1][1].split('[Response expression continuity]')[-1]

    def test_v1_and_v2_same_call_context_tracks_only_published_expression(self):
        for mode in ('v1','v2'):
            with self.subTest(mode=mode):
                s = self.service(mode); self.llm.calls.clear()
                self.reply(s, 'happy')
                self.assertIn('No model-metadata facial request has been published', self.context())
                self.reply(s)
                self.assertIn('Last published model-metadata facial request: happy (intensity 0.45)', self.context())
                self.reply(s, 'neutral', 'Let us consider that carefully.')
                self.assertIn('Last published model-metadata facial request: happy', self.context())
                self.reply(s, text='A square has four equal sides.')
                self.assertIn('Last published model-metadata facial request: neutral', self.context())
                self.assertEqual(4, len(self.llm.calls))  # No emotion inference/omission repair.
                self.assertNotIn('scope-', self.context())
                self.assertNotIn(self.h.character_id, self.context())
                self.assertFalse(any('presentation' in m for m in s.conversation.messages))

    def test_streaming_request_gets_context_without_exposing_metadata_deltas(self):
        s = self.service(); self.reply(s, 'happy'); self.events.clear()
        def stream(context, prompt, **kw):
            yield self.llm.generate(context, prompt)
        self.llm.stream_generate = stream
        result = self.reply(s, 'sad', 'That is disappointing.')
        self.assertIn('Last published model-metadata facial request: happy', self.context())
        deltas = ''.join(e.data.get('content','') for e in self.events if e.type=='assistant_delta')
        self.assertNotIn('emotion', deltas)
        self.assertEqual('sad', result.presentation.emotion)

    def test_plain_omission_is_valid_and_preserves_prior_request(self):
        s = self.service(); self.reply(s, 'relaxed')
        self.llm.response = 'I am listening.'
        result = s.process_text_turn('Here is another thought.', speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertIsNone(result.presentation.emotion)
        self.reply(s)
        self.assertIn('Last published model-metadata facial request: relaxed', self.context())
        self.assertEqual(3, len(self.llm.calls))

    def test_failed_assistant_save_does_not_advance_expression(self):
        s = self.service(); self.reply(s, 'happy'); self.events.clear()
        self.llm.response = '{"dialogue":"That sounds hard.","presentation":{"emotion":"sad"}}'
        dump = json.dump
        def fail_assistant(records, handle, **kwargs):
            if records and records[-1].get('content') == 'That sounds hard.':
                handle.write('[{'); raise OSError('synthetic write failure')
            return dump(records, handle, **kwargs)
        with patch('conversation.persistence.json.dump', side_effect=fail_assistant):
            result = s.process_text_turn('Let us continue.', speak=False)
        self.assertFalse(result.succeeded)
        self.assertFalse(any(e.type=='assistant_response' for e in self.events))
        self.reply(s)
        self.assertIn('Last published model-metadata facial request: happy', self.context())
        self.assertNotIn('That sounds hard.', [m['content'] for m in s.conversation.messages])

    def test_cancelled_generation_does_not_advance_expression_and_replacement_works(self):
        s = self.service(); self.reply(s, 'happy'); self.events.clear()
        entered = threading.Event(); release = threading.Event(); results = []
        def blocked(*a, **k):
            entered.set()
            if not release.wait(3): raise TimeoutError('synthetic gate')
            return '{"dialogue":"That sounds hard.","presentation":{"emotion":"sad"}}'
        with patch.object(self.llm, 'generate', side_effect=blocked):
            worker = threading.Thread(target=lambda: results.append(s.process_text_turn('Continue.', speak=False)))
            worker.start()
            try:
                self.assertTrue(entered.wait(3)); s._cancel_active_turn()
            finally:
                release.set(); worker.join(4)
        self.assertFalse(worker.is_alive()); self.assertFalse(results[0].succeeded)
        self.assertFalse(any(e.type=='assistant_response' for e in self.events))
        self.reply(s)
        self.assertIn('Last published model-metadata facial request: happy', self.context())

    def test_scope_change_and_return_do_not_resurrect_old_request(self):
        s = self.service(); self.reply(s, 'happy')
        old = s.truth_scope_provenance()
        with patch.object(s, 'truth_scope_provenance', return_value={
            'kind':'scenario','scope_id':'scope-22222222-2222-4222-8222-222222222222'}):
            self.reply(s)
            self.assertIn('No model-metadata facial request has been published', self.context())
        self.reply(s)
        self.assertEqual(old, s.truth_scope_provenance())
        self.assertIn('No model-metadata facial request has been published', self.context())

    def test_restart_does_not_reconstruct_face_from_canonical_prose(self):
        s = self.service(); self.reply(s, 'happy', '*Smiles.* That is lovely.')
        # Separate service instance over the persisted canonical source.
        reopened = self.service(); self.reply(reopened)
        self.assertIn('No model-metadata facial request has been published', self.context())

    def test_guidance_reaches_actual_provider_request_with_optional_examples(self):
        s = self.service(); self.reply(s)
        prompt = self.llm.calls[-1][1]
        self.assertIn('the client may also project a few explicit current self-directed facial action spans', prompt)
        self.assertIn('"presentation":{"emotion":"neutral"}', prompt)
        self.assertIn('plain canonical dialogue', prompt)
        self.assertEqual(1, len(self.llm.calls))

    def test_facial_action_remains_canonical_text_not_invented_model_metadata(self):
        for mode in ('v1', 'v2'):
            with self.subTest(mode=mode):
                s = self.service(mode); self.llm.calls.clear()
                self.reply(s, 'sad')
                self.llm.response = '*smiles warmly* I appreciate it.'
                result = s.process_text_turn('Let us continue.', speak=False)
                self.assertTrue(result.succeeded, result.error)
                self.assertEqual('*smiles warmly* I appreciate it.', s.conversation.messages[-1]['content'])
                self.assertIsNone(result.presentation.emotion)
                final = [e for e in self.events if e.type == 'assistant_response'][-1]
                self.assertNotIn('emotion', final.data.get('presentation', {}))
                self.reply(s)
                self.assertIn('Last published model-metadata facial request: sad', self.context())
                self.assertIn('not client emote projection, manual choices', self.context())
                self.assertEqual(3, len(self.llm.calls))

    def test_failed_persistence_of_facial_action_has_no_final_publication(self):
        s = self.service(); self.events.clear()
        text = '*smiles warmly* I appreciate it.'
        self.llm.response = text
        dump = json.dump
        def fail_assistant(records, handle, **kwargs):
            if records and records[-1].get('content') == text:
                raise OSError('synthetic precommit failure')
            return dump(records, handle, **kwargs)
        with patch('conversation.persistence.json.dump', side_effect=fail_assistant):
            result = s.process_text_turn('Let us continue.', speak=False)
        self.assertFalse(result.succeeded)
        self.assertFalse(any(e.type == 'assistant_response' for e in self.events))
        self.assertNotIn(text, [m['content'] for m in s.conversation.messages])
        self.reply(s)
        self.assertIn('No model-metadata facial request has been published', self.context())

    def test_cancelled_facial_action_cannot_reach_native_final_path(self):
        s = self.service(); self.events.clear()
        entered = threading.Event(); release = threading.Event(); results = []
        draft = '*smiles warmly* I appreciate it.'
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(3): raise TimeoutError('synthetic gate')
            return draft
        with patch.object(self.llm, 'generate', side_effect=blocked):
            worker = threading.Thread(target=lambda: results.append(s.process_text_turn('Continue.', speak=False)))
            worker.start()
            try:
                self.assertTrue(entered.wait(3)); s._cancel_active_turn()
            finally:
                release.set(); worker.join(4)
        self.assertFalse(worker.is_alive()); self.assertFalse(results[0].succeeded)
        self.assertFalse(any(e.type == 'assistant_response' for e in self.events))
        self.assertNotIn(draft, [m['content'] for m in s.conversation.messages])
        self.reply(s)
        self.assertIn('No model-metadata facial request has been published', self.context())

    def test_rejected_facial_draft_is_not_the_published_repair(self):
        s = self.service('v2'); self.events.clear()
        draft = '*smiles warmly* You previously told me about your pampered cat.'
        repaired = 'Let us think about something pleasant.'
        self.llm.response = [draft, repaired]
        result = s.process_text_turn('What do you think about pleasant activities?', speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(2, len(self.llm.calls))
        finals = [e for e in self.events if e.type == 'assistant_response']
        self.assertEqual(1, len(finals))
        self.assertEqual(repaired, finals[0].data['content'])
        self.assertNotIn(draft, [m['content'] for m in s.conversation.messages])

    def test_repair_uses_previous_published_request_not_rejected_draft(self):
        s = self.service('v2'); self.reply(s, 'happy'); self.llm.calls.clear()
        self.llm.response = [
            '{"dialogue":"You previously told me about your pampered cat.",'
            '"presentation":{"emotion":"sad"}}',
            '{"dialogue":"Let us think about something pleasant."}',
        ]
        result = s.process_text_turn('What do you think about pleasant activities?', speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(2, len(self.llm.calls))
        self.assertIn('Last published model-metadata facial request: happy', self.context())
        self.assertIsNone(result.presentation.emotion)
        self.reply(s)
        self.assertIn('Last published model-metadata facial request: happy', self.context())

    def test_committed_expression_survives_audio_retirement(self):
        s = self.service(); self.reply(s, 'happy')
        s.stop_speaking(interrupted=True)
        self.reply(s)
        self.assertIn('Last published model-metadata facial request: happy', self.context())

    def test_unknown_metadata_cannot_enter_expression_context(self):
        s = self.service(); self.reply(s, 'happy')
        self.llm.response = '{"dialogue":"I am listening.","presentation":{"emotion":"invented-model-id"}}'
        self.assertTrue(s.process_text_turn('Continue.', speak=False).succeeded)
        self.reply(s)
        self.assertIn('Last published model-metadata facial request: happy', self.context())
        self.assertNotIn('invented-model-id', self.llm.calls[-1][1])


if __name__ == '__main__': unittest.main()
