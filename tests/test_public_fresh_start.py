"""Ordinary factory startup needs neither tracked character data nor a QA seed."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from assistant_service import AssistantService
from character_registry import CharacterRegistry
from test_assistant_service_v2_authority import _LLM, _TTS
from test_character_memory_v2_shadow import _Embedding
from test_memory_v2_embeddings import ToyEmbeddingProvider


class PublicFreshStartTests(unittest.TestCase):
    def test_empty_and_legacy_only_roots_use_the_normal_factory_without_seed(self):
        for mature in (False, True):
            with self.subTest(mature=mature), tempfile.TemporaryDirectory(prefix='public-start-') as directory, ExitStack() as stack:
                previous = Path.cwd()
                self.addCleanup(os.chdir, previous)
                os.chdir(directory)
                stack.callback(os.chdir, previous)
                stack.enter_context(patch.dict(os.environ, {'AIFREN_MEMORY_AUTHORITY':'v2',
                    'AIFREN_ENABLE_DEVELOPMENT_QA':'', 'AIFREN_DEVELOPMENT_STAGED_DATA_ROOT':''}))
                stack.enter_context(patch('assistant.create_llm', return_value=_LLM()))
                stack.enter_context(patch('assistant.VoiceInput', return_value=object()))
                stack.enter_context(patch('assistant.TextToSpeech', return_value=_TTS()))
                stack.enter_context(patch('memory.memory.EmbeddingModel', _Embedding))
                for module in ('memory_v2_store', 'memory_v2_authority'):
                    stack.enter_context(patch(module+'.MiniLMEmbeddingProvider', return_value=ToyEmbeddingProvider()))
                spies = [stack.enter_context(patch('memory.memory.Memory.'+method,
                         side_effect=AssertionError('V1 must remain inactive'))) for method in
                         ('get_relevant_memories','process','save','generate_missing_embeddings','generate_missing_metadata')]
                records = [{'role':'user','content':'My favorite color is green.',
                            'timestamp':'2030-01-01T12:00:00+00:00'},
                           {'role':'assistant','content':'Understood.',
                            'timestamp':'2030-01-01T12:01:00+00:00'}] if mature else []
                if mature:
                    Path('conversation.json').write_text(json.dumps(records))
                    Path('memories.json').write_text('[]')
                    profile=Path('characters/default');profile.mkdir(parents=True)
                    (profile/'character.json').write_text('{"name":"AIFren"}')
                    (profile/'personality.md').write_text('Synthetic legacy companion.')
                else:
                    registry=CharacterRegistry(directory)
                    created=registry.create('Public Companion',personality='Synthetic companion.')
                    registry.select(created.character_id)
                    self.assertEqual('local',created.storage_layout)
                before = Path('conversation.json').read_bytes() if mature else None
                service = AssistantService.create_default()
                try:
                    self.assertEqual('v2',service.memory_authority_status()['mode'])
                    self.assertEqual('AIFren' if mature else 'Public Companion',service.character['name'])
                    if mature: self.assertEqual(before,Path('conversation.json').read_bytes())
                    self.assertEqual(records,service.conversation.messages)
                    self.assertIsNotNone(service._memory_v2_shadow_writer)
                    self.assertTrue(Path('characters/registry.json').is_file())
                    self.assertEqual(mature,Path('characters/default/character.json').exists())
                finally: service.close()
                for spy in spies: spy.assert_not_called()

    def test_missing_legacy_profile_stays_visible_without_recreating_or_overwriting_data(self):
        from backend_host import AIFrenWebSocketHost
        from character_registry import CharacterStorageError
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); canonical=root/'conversation.json'
            canonical.write_text('[{"role":"user","content":"Synthetic retained record."}]')
            original=canonical.read_bytes();registry=CharacterRegistry(root)
            # Normal adapter catches the selected factory's missing-directory failure;
            # it must retain manager access instead of inventing a replacement timeline.
            host=AIFrenWebSocketHost(application_dir=root,
                service_factory=lambda: (_ for _ in ()).throw(CharacterStorageError('Missing profile')))
            service=host._create_service_or_management()
            self.assertTrue(service.storage_unavailable)
            self.assertEqual('storage_unavailable',host._status['state'])
            self.assertEqual(original,canonical.read_bytes())
            self.assertFalse((root/'characters/default').exists())
            fresh=registry.create('New Companion',personality='Synthetic fresh character.')
            self.assertEqual('local',fresh.storage_layout)
            self.assertEqual([],json.loads(registry.runtime_paths(fresh.character_id)['conversation'].read_text()))
            self.assertEqual(original,canonical.read_bytes())
