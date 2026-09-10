"""Ordinary factory startup needs neither tracked character data nor a QA seed."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from assistant_service import AssistantService
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
                before = Path('conversation.json').read_bytes() if mature else None
                service = AssistantService.create_default()
                try:
                    self.assertEqual('v2',service.memory_authority_status()['mode'])
                    self.assertEqual('AIFren',service.character['name'])
                    if mature: self.assertEqual(before,Path('conversation.json').read_bytes())
                    self.assertEqual(records,service.conversation.messages)
                    self.assertIsNotNone(service._memory_v2_shadow_writer)
                    self.assertTrue(Path('characters/registry.json').is_file())
                    self.assertFalse(Path('characters/default/character.json').exists())
                finally: service.close()
                for spy in spies: spy.assert_not_called()
