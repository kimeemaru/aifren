"""Promotion uses ordinary default selection and the established persistent owners."""
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch
import unittest

from assistant_service import AssistantService
from conversation.conversation import Conversation
from memory_v2_authority import DevelopmentV2MemoryAuthority
from memory_v2_initialization import initialize_v2_derived_state, install_validated_episode_acceleration
from memory_v2_episode_compaction import EpisodeCompactionCache, EpisodeCompactor, EPISODE_SOURCE_HISTORICAL, EPISODE_PURPOSE_HISTORICAL
from memory_v2_shadow_writer import MemoryV2ShadowWriter
from memory_v2_store import MemoryV2Store, MemoryV2Repository
from test_v2_runtime_recovery import V2RuntimeRecoveryTests
from test_assistant_service_v2_authority import _LLM, _Memory, _TTS
from test_memory_v2_embeddings import ToyEmbeddingProvider
from test_memory_v2_historical_episodes import _DeterministicHistoricalCompactor


class DefaultV2InitializationTests(V2RuntimeRecoveryTests):
    def open_service(self):
        self.h.writer.compare = lambda *_a, **_k: {}
        self.h.writer._embedding_provider = ToyEmbeddingProvider()
        self.conversation = Conversation(self.llm, conversation_file=self.h.conversation_file,
            summary_file=self.h.root/'summary.json', memory_authority='v2')
        self.authority = DevelopmentV2MemoryAuthority(self.h.writer.store,self.h.character_id,
            self.conversation.messages,embedding_provider=ToyEmbeddingProvider())
        with patch.dict(os.environ,{'AIFREN_MEMORY_AUTHORITY':'','AIFREN_ENABLE_DEVELOPMENT_QA':''}):
            service = AssistantService(self.llm,_Memory(),self.conversation,object(),
                {'_character_id':self.h.character_id},'Synthetic companion.',_TTS(),
                character_id=self.h.character_id,memory_v2_shadow_writer=self.h.writer,
                memory_v2_authority=self.authority)
        self.assertEqual('v2',service.memory_authority_status()['mode'])
        self.assertFalse(service.memory_authority_status()['development_only'])
        return service

    def test_default_cannot_fall_back_when_v2_owner_is_missing(self):
        with patch.dict(os.environ,{'AIFREN_MEMORY_AUTHORITY':''}),self.assertRaises(RuntimeError):
            AssistantService(self.llm,_Memory(),self.conversation,object(),{},'Synthetic',_TTS())

    def test_partial_derived_catchup_resumes_and_second_pass_is_noop(self):
        # Mature retained V1 file exists, but contributes neither truth nor writes.
        self.h.memory_file.write_text('[{"id":1,"content":"Unverified retained compatibility record."}]')
        original_v1=self.h.memory_file.read_bytes()
        messages=[]
        for index in range(42):
            messages.extend([{'role':'user','content':f'We watched a kite beside the bridge, item {index}.','timestamp':'2026-08-20T12:00:00+00:00'},
                             {'role':'assistant','content':'That sounds nice.','timestamp':'2026-08-20T12:01:00+00:00'}])
        self.conversation.messages=messages;self.conversation.save()
        original=self.h.conversation_file.read_bytes()
        first=initialize_v2_derived_state(self.h.writer,self.conversation,embedding_provider=ToyEmbeddingProvider(),maximum_pages=1)
        self.assertEqual('pending',first['state'])
        resumed=initialize_v2_derived_state(self.h.writer,self.conversation,embedding_provider=ToyEmbeddingProvider())
        self.assertEqual(len(messages),resumed['consumers']['history']['next_index'])
        self.assertFalse(resumed['missing_vectors'])
        cancelled=initialize_v2_derived_state(self.h.writer,self.conversation,embedding_provider=ToyEmbeddingProvider(),cancelled=lambda:True)
        self.assertEqual('cancelled',cancelled['state'])
        count=self.h.writer.store.connection.execute('SELECT count(*) FROM events').fetchone()[0]
        again=initialize_v2_derived_state(self.h.writer,self.conversation,embedding_provider=ToyEmbeddingProvider())
        self.assertEqual(0,again['embedded']);self.assertEqual(1,again['pages'])
        self.assertTrue(all(v['processed']==0 for v in again['last_observer_page']['consumers'].values()))
        self.assertEqual(count,self.h.writer.store.connection.execute('SELECT count(*) FROM events').fetchone()[0])
        self.assertEqual(original,self.h.conversation_file.read_bytes());self.assertEqual(original_v1,self.h.memory_file.read_bytes())

    def test_revisiting_partial_progress_preserves_live_state_threads_and_scopes(self):
        for text in ("My favorite color is green.", "Actually, my favorite color is purple.",
                     "Let's roleplay that we are in the Blue Room.",
                     "You are wearing a green hat.", "The hat is blue now.",
                     "I'm waiting for my parcel to arrive."):
            self.assertTrue(self.service.process_text_turn(text).succeeded)
        store = self.h.writer.store
        tables = ('truth_scopes', 'active_scene_subjects', 'active_scene_relations',
                  'open_threads', 'claim_status_events')
        original = {table: [tuple(r) for r in store.connection.execute(
            'SELECT * FROM '+table+' WHERE character_id=?', (self.h.character_id,))]
            for table in tables}
        self.assertTrue(original['open_threads'])
        self.assertTrue(original['active_scene_subjects'])
        scope = store.active_truth_scope_id(self.h.character_id)
        store.connection.execute('DELETE FROM canonical_observation_progress WHERE character_id=?',
                                 (self.h.character_id,))
        initialize_v2_derived_state(self.h.writer, self.conversation,
                                    embedding_provider=ToyEmbeddingProvider())
        self.assertEqual(scope, store.active_truth_scope_id(self.h.character_id))
        for table, rows in original.items():
            self.assertEqual(rows, [tuple(r) for r in store.connection.execute(
                'SELECT * FROM '+table+' WHERE character_id=?', (self.h.character_id,))], table)
        self.assertIn('purple', str(self.h.repository.lookup_durable_core(
            self.h.character_id, 'preference.color')))

    def test_explicit_rollback_factory_does_not_open_or_mutate_v2(self):
        components=(self.llm,_Memory(),self.conversation,object(),
                    {'_character_id':self.h.character_id},'Synthetic',_TTS(),None)
        with patch.dict(os.environ,{'AIFREN_MEMORY_AUTHORITY':'v1'}),patch('assistant.initialize',return_value=components) as init,patch('memory_v2_shadow_writer.MemoryV2ShadowWriter',side_effect=AssertionError('Rollback must not open V2')):
            service=AssistantService.create_default()
            self.assertEqual('v1',service.memory_authority_status()['mode'])
            self.assertIsNone(service._memory_v2_shadow_writer)
            init.assert_called_once_with(prepare_v1_memory=True)
            service.close()

    def test_ordinary_factory_ignores_legacy_shadow_toggle_and_never_imports_v1(self):
        memory=_Memory();memory.memory_file=self.h.memory_file
        components=(self.llm,memory,self.conversation,object(),
                    {'_character_id':self.h.character_id},'Synthetic',_TTS(),None)
        with patch.dict(os.environ,{'AIFREN_MEMORY_AUTHORITY':'','AIFREN_ENABLE_DEVELOPMENT_QA':''}),patch('assistant.initialize',return_value=components) as init,patch('config.MEMORY_V2_SHADOW_WRITE_ENABLED',False),patch('memory_v2_store.MiniLMEmbeddingProvider',return_value=ToyEmbeddingProvider()),patch('memory_v2_authority.MiniLMEmbeddingProvider',return_value=ToyEmbeddingProvider()),patch.object(MemoryV2ShadowWriter,'reconcile',side_effect=AssertionError('V1 import is compatibility-only')):
            service=AssistantService.create_default()
            self.assertEqual('v2',service.memory_authority_status()['mode'])
            self.assertEqual(0,memory.retrieval_calls);self.assertEqual([],memory.processed)
            init.assert_called_once_with(prepare_v1_memory=False)
            service.close();self.assertEqual(0,memory.save_calls)

    def test_default_local_realizer_keeps_core_when_optional_reaction_fails(self):
        self.llm.companion_memory_realization=True
        self.service.process_text_turn('My favorite color is blue.')
        self.llm.response='You loved it again in the rain.'
        result=self.service.process_text_turn('What is my favorite color?')
        self.assertTrue(result.succeeded,result.error)
        self.assertIn('blue',result.reply);self.assertNotIn('rain',result.reply)
        self.assertEqual('deterministic_grounded_core',self.service._last_memory_authority_diagnostics['memory_realization'])
        self.assertEqual(0,self.service.memory.retrieval_calls)
        self.assertEqual([],self.service.memory.processed)


class EpisodeAccelerationTests(unittest.TestCase):
    def setUp(self):
        import uuid
        self.cid=str(uuid.uuid4());self.source=MemoryV2Store();self.target=MemoryV2Store()
        self.addCleanup(self.source.close);self.addCleanup(self.target.close)
        for store in (self.source,self.target):MemoryV2Repository(store).ensure_character(self.cid,'Synthetic')
        self.messages=[{'role':'user','content':'We watched a green kite beside the river.','timestamp':'2026-08-20T12:00:00Z'},
                       {'role':'assistant','content':'That sounds peaceful.','timestamp':'2026-08-20T12:00:01Z'}]
        self.messages = [dict(record) for _ in range(30) for record in self.messages]
        self.cached=EpisodeCompactionCache(self.source,self.cid);self.live=EpisodeCompactionCache(self.target,self.cid)
        self.cached.rebuild(self.messages,EpisodeCompactor(_DeterministicHistoricalCompactor()),source_authority=EPISODE_SOURCE_HISTORICAL,generation_purpose=EPISODE_PURPOSE_HISTORICAL,preserve_prior_generations=True)

    def test_validated_derived_acceleration_is_idempotent_and_preserves_originals(self):
        self.target.add_summary(self.cid,'old-unrelated','Original retained account',source_count=1,provenance_state='unverifiable_legacy',generator_name='legacy',generator_version='1')
        result=install_validated_episode_acceleration(self.live,self.cached,self.messages)
        self.assertEqual('installed',result['state'])
        self.assertIsNotNone(self.target.connection.execute("SELECT 1 FROM summaries WHERE summary_id='old-unrelated'").fetchone())
        self.assertEqual('unchanged',install_validated_episode_acceleration(self.live,self.cached,self.messages)['state'])

    def test_changed_source_character_or_generation_cannot_be_adopted(self):
        changed=[dict(r) for r in self.messages];changed[0]['content']='A different event.'
        with self.assertRaises(ValueError):install_validated_episode_acceleration(self.live,self.cached,changed)
        with self.assertRaises(ValueError):install_validated_episode_acceleration(self.live,self.cached,self.messages,source_unchanged=lambda:False)
        self.assertEqual(0,self.target.connection.execute('SELECT count(*) FROM summaries').fetchone()[0])
        self.cached.character_id='another'
        with self.assertRaises(ValueError):install_validated_episode_acceleration(self.live,self.cached,self.messages)


class RuntimeAttestationTests(unittest.TestCase):
    def test_normal_completed_prefix_attests_and_rejects_changed_identity(self):
        from development_staged_runtime import _runtime_history_attested
        from memory_v2_runtime_observation import EMPTY_DIGEST, extend_digest
        from memory_v2_historical_evidence import HISTORICAL_EVIDENCE_POLICY_VERSION
        path=Path('characters/synthetic/conversation.json')
        messages=[{'role':'user','content':'A supported historical statement.'}, {'role':'assistant','content':'Okay.'}]
        digest=EMPTY_DIGEST
        for record in messages:digest=extend_digest(digest,record)
        row=['synthetic',hashlib.sha256(path.as_posix().encode()).hexdigest(),'history',HISTORICAL_EVIDENCE_POLICY_VERSION+':runtime_append_v1',2,digest,'complete','',1]
        check=lambda rows,records=messages: _runtime_history_attested(rows,records,character_id='synthetic',relative_path=path)
        self.assertTrue(check([row]));self.assertTrue(check([row],messages+[{'role':'user','content':'New append'}]))
        for field,value in ((0,'other'),(1,'wrong-path'),(2,'durable'),(3,'old-policy'),(4,3),(5,'0'*64),(6,'pending')):
            changed=list(row);changed[field]=value;self.assertFalse(check([changed]))
        self.assertFalse(check([row],[{'role':'user','content':'Edited history.'},messages[1]]))
