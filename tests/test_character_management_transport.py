"""Normal manager commands against synthetic production service/storage owners."""
import asyncio
import json
import uuid
from contextlib import ExitStack
from pathlib import Path
import unittest
from unittest.mock import patch
from backend_host import AIFrenWebSocketHost
from character_unavailable import CharacterUnavailableService
from character_operations import CharacterOperationService
from character_registry import CharacterStorageError
from test_character_switch_ownership import CharacterSwitchOwnershipTests

class CharacterManagementTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture=CharacterSwitchOwnershipTests();self.fixture.setUp()
        self.root=self.fixture.root;self.registry=self.fixture.registry
        self.host=AIFrenWebSocketHost(service=self.fixture.service,application_dir=self.root,
            service_factory=lambda:self.fixture._new_service(self.registry.active().character_id))
        self.host._character_registry=self.registry
        self.sent=[]
        async def send(ws,message):self.sent.append(message)
        self.host._send_json=send
        self.stack=ExitStack()
        for name in ('_companion_snapshot','_model_snapshot','_tts_snapshot'):
            self.stack.enter_context(patch.object(self.host,name,return_value={}))
    async def asyncTearDown(self):
        self.host.service.close();self.stack.close();self.fixture.tearDown()
    async def command(self, command, **data):
        await self.host._handle_command(object(), __import__('json').dumps({'command':command,**data}))

    async def test_reset_local_c_then_live_switch_a_restores_history_and_memory_pages(self):
        a=self.fixture.a.character_id
        c=self.fixture.b.character_id
        # Represent a pre-management shared-store character in this synthetic
        # fixture; both A and C must exercise the actual selected migration.
        self.registry.runtime_paths(c)['memory_v2'].unlink()
        with self.registry.locked():
            row=next(v for v in self.registry._data['characters'] if v['character_id']==c)
            row.update(storage_layout='legacy_shared',timeline_generation='legacy')
            self.registry._save()
        for text in ('My favorite color is green.', 'I put a blue glove on your left hand.'):
            result=self.host.service.process_text_turn(text,speak=False)
            self.assertTrue(result.succeeded,result.error)
        # Both characters migrate through the ordinary confirmed commands.
        await self.command('character_operation_preview',character_id=a,action='migrate')
        preview=[m['event']['data'] for m in self.sent
                 if m.get('event',{}).get('type')=='character_operation_preview'][-1]
        await self.command('character_operation_confirm',**{k:preview[k] for k in ('character_id','token','revision')})
        self.assertEqual('local',self.registry.get(a).storage_layout)
        expected=json.loads(json.dumps(self.host.service.conversation.messages))
        a_path=self.registry.runtime_paths(a)['conversation']
        a_bytes=a_path.read_bytes()
        store=self.host.service._memory_v2_shadow_writer.store
        original_events=[tuple(r) for r in store.connection.execute(
            'SELECT event_id,source_reference,content_text FROM events WHERE character_id=? ORDER BY event_id',(a,))]
        await self.command('select_character',character_id=c)
        for text in ('My favorite color is red.', 'I put a silver ring on your right index finger.'):
            self.assertTrue(self.host.service.process_text_turn(text,speak=False).succeeded)
        await self.command('character_operation_preview',character_id=c,action='migrate')
        preview=[m['event']['data'] for m in self.sent
                 if m.get('event',{}).get('type')=='character_operation_preview'][-1]
        await self.command('character_operation_confirm',**{k:preview[k] for k in ('character_id','token','revision')})
        self.assertEqual('local',self.registry.get(c).storage_layout)
        old_c_generation=self.registry.get(c).timeline_generation
        await self.command('character_operation_preview',character_id=c,action='reset')
        preview=[m['event']['data'] for m in self.sent
                 if m.get('event',{}).get('type')=='character_operation_preview'][-1]
        await self.command('character_operation_confirm',**{k:preview[k] for k in ('character_id','token','revision')})
        self.assertNotEqual(old_c_generation,self.registry.get(c).timeline_generation)
        self.assertEqual([],self.host.service.conversation.messages)
        for identity in (a,c,a):
            await self.command('select_character',character_id=identity)
            self.assertEqual(identity,self.registry.active().character_id)
            snapshot=[m for m in self.sent if m.get('type')=='snapshot'][-1]
            self.assertEqual(identity,snapshot['character_id'])
            self.assertEqual(snapshot['character_session'],self.host.service.character_binding()['character_session'])
            self.assertEqual(expected if identity==a else [],self.host.service.conversation.messages)
            self.assertEqual(len(expected) if identity==a else 0,len(snapshot['data']['conversation']))
            request=str(uuid.uuid4())
            await self.command('memory_view_query',request_id=request,memory_lane='v2_claims',
                               **self.host.service.character_binding())
            page=[m for m in self.sent if m.get('event',{}).get('type')=='memory_view_page'
                  and m['event']['data']['request_id']==request][-1]
            self.assertEqual(identity,page['character_id'])
            items=page['event']['data']['memory_page']['items']
            self.assertEqual(identity==a,bool(items),page)
        self.assertEqual(a_bytes,a_path.read_bytes())
        store=self.host.service._memory_v2_shadow_writer.store
        self.assertEqual(original_events,[tuple(r) for r in store.connection.execute(
            'SELECT event_id,source_reference,content_text FROM events WHERE character_id=? ORDER BY event_id',(a,))])
        self.assertFalse([m for m in self.sent if m.get('type')=='command_error'])
        # A fresh process owner must observe the same preserved/reset outcomes.
        self.host.service.close()
        for identity in (c,a):
            self.registry.select(identity)
            self.host._service=self.fixture._new_service(identity)
            self.assertEqual(expected if identity==a else [],self.host.service.conversation.messages)
            page=self.host.service.memory_view_page(character_id=identity,lane='v2_claims')
            self.assertEqual(identity==a,bool(page['items']))
            self.host.service.close()
        self.assertEqual(a_bytes,a_path.read_bytes())
    async def operation(self, identity, action):
        await self.host._prepare_character_operation_preview(identity)
        preview=await asyncio.to_thread(self.host._operations().preview,identity,action)
        await self.host._confirm_character_operation(object(),preview)
        return preview
    async def test_active_reset_retires_old_bindings_publishes_empty_matching_snapshot_and_reopens(self):
        self.fixture._turn('I put a blue glove on your left hand.')
        old=self.host.service;binding=old.character_binding()
        await self.operation(self.fixture.a.character_id,'reset')
        current=self.host.service
        self.assertIsNot(old,current)
        self.assertEqual(self.fixture.a.character_id,current.character_id)
        self.assertEqual([],current.conversation.messages)
        self.assertEqual([],current.continuity_snapshot()['scene_relations'])
        with self.assertRaises(RuntimeError):old.require_character_binding(**binding)
        self.assertEqual('local',self.registry.get(current.character_id).storage_layout)
        snapshots=[v['data'] for v in self.sent if v.get('type')=='snapshot']
        self.assertEqual([],snapshots[-1]['conversation'])
        self.assertFalse(snapshots[-1]['storage_unavailable'])
    async def test_stale_confirmation_does_not_close_current_service(self):
        p=self.host._operations().preview(self.fixture.a.character_id,'reset')
        self.registry.update(self.fixture.b.character_id,display_name='New synthetic B label')
        old=self.host.service
        with self.assertRaises(CharacterStorageError):await self.host._confirm_character_operation(object(),p)
        self.assertFalse(old._closed);self.assertIs(old,self.host.service)
    async def test_delete_last_character_exposes_manager_and_fresh_create_rebinds(self):
        await self.operation(self.fixture.b.character_id,'delete')
        await self.operation(self.fixture.a.character_id,'delete')
        self.assertIsNone(self.registry.active_or_none())
        self.assertIsInstance(self.host.service,CharacterUnavailableService)
        self.assertEqual('no-character',self.host.service.character_id)
        self.assertFalse(self.host.service.process_text_turn('hello').succeeded)
        empty_snapshot=[m for m in self.sent if m.get('type')=='snapshot'][-1]
        self.sent.clear()
        # Same actual command dispatcher as the player, including request ID.
        await self.host._handle_command(object(), __import__('json').dumps({'command':'create_character',
            'display_name':'Fresh friend','personality':'Synthetic human companion.','request_id':'fresh'}))
        self.assertIsNotNone(self.registry.active_or_none())
        self.assertFalse(getattr(self.host.service,'storage_unavailable',False))
        self.assertEqual([],self.host.service.conversation.messages)
        # A native client already bound to the empty-library snapshot rejects
        # a different identity/session at the same switch generation. Creation
        # must use the ordinary rebind protocol, not just replace the service.
        switching=[m for m in self.sent if m.get('event',{}).get('type')=='character_switching']
        self.assertEqual(1,len(switching))
        self.assertGreater(switching[0]['character_generation'],empty_snapshot['character_generation'])
        snapshots=[m for m in self.sent if m.get('type')=='snapshot']
        self.assertTrue(snapshots)
        for snapshot in snapshots:
            self.assertEqual(switching[0]['character_generation'],snapshot['character_generation'])
            self.assertEqual(self.host.service.character_id,snapshot['character_id'])
            self.assertNotEqual(empty_snapshot['character_session'],snapshot['character_session'])
            self.assertFalse(snapshot['data']['storage_unavailable'])
    async def test_creation_beside_a_healthy_character_does_not_switch_its_binding(self):
        old=self.host.service;binding=old.character_binding();generation=self.host._character_generation
        await self.command('create_character',display_name='Another friend',
                           personality='Synthetic human companion.',request_id='another')
        self.assertIs(old,self.host.service)
        self.assertEqual(binding,old.character_binding())
        self.assertEqual(generation,self.host._character_generation)
        self.assertEqual(old.character_id,self.registry.active().character_id)
        self.assertFalse(any(m.get('event',{}).get('type')=='character_switching' for m in self.sent))
    async def test_empty_library_create_with_runtime_failure_publishes_new_unavailable_binding(self):
        await self.operation(self.fixture.b.character_id,'delete')
        await self.operation(self.fixture.a.character_id,'delete')
        generation=self.host._character_generation;self.sent.clear()
        with patch.object(self.host,'_service_factory',side_effect=RuntimeError('Synthetic provider failure')):
            await self.command('create_character',display_name='Unavailable friend',
                               personality='Synthetic human companion.',request_id='unavailable')
        self.assertIsInstance(self.host.service,CharacterUnavailableService)
        self.assertFalse(self.host._character_switching)
        snapshot=[m for m in self.sent if m.get('type')=='snapshot'][-1]
        self.assertGreater(snapshot['character_generation'],generation)
        self.assertEqual(self.registry.active().character_id,snapshot['character_id'])
        self.assertTrue(snapshot['data']['storage_unavailable'])
        self.assertEqual('storage_unavailable',snapshot['data']['status']['state'])
    async def test_missing_local_database_is_management_state_never_legacy_fallback(self):
        await self.operation(self.fixture.a.character_id,'migrate')
        self.host.service.close()
        self.registry.runtime_paths(self.fixture.a.character_id)['memory_v2'].unlink()
        shell=self.host._create_service_or_management()
        self.assertIsInstance(shell,CharacterUnavailableService)
        self.assertTrue(shell.storage_unavailable)
        self.assertEqual(self.fixture.a.character_id,shell.character_id)
    async def test_active_migrated_character_can_cleanup_its_verified_old_copy(self):
        self.fixture._turn('I put a blue glove on your left hand.')
        await self.operation(self.fixture.a.character_id,'migrate')
        service=self.host.service
        for _ in range(3): service.maintain_canonical_observers()
        operations=self.host._operations()
        identity=self.fixture.a.character_id
        stable=operations._selected_state(identity)
        service.maintain_canonical_observers()
        self.assertEqual(stable,operations._selected_state(identity))
        await self.host._prepare_character_operation_preview(identity)
        preview=operations.preview(identity,'cleanup')
        reviewed=operations._selected_state(identity)
        # The ordinary idle poll must not invalidate review merely because
        # preview saved unchanged canonical bytes through a new inode.
        service.maintain_canonical_observers()
        self.assertEqual(reviewed,operations._selected_state(identity))
        result=service.process_text_turn('I put a red ring on your right index finger.',speak=False)
        self.assertTrue(result.succeeded,result.error)
        with self.assertRaises(CharacterStorageError):
            await self.host._confirm_character_operation(object(),preview)
        self.assertFalse(service._closed)
        for _ in range(3): service.maintain_canonical_observers()
        await self.host._prepare_character_operation_preview(identity)
        preview=operations.preview(identity,'cleanup')
        service.maintain_canonical_observers()
        await self.host._confirm_character_operation(object(),preview)
        self.assertIsNone(self.registry.get(self.fixture.a.character_id).migration_source)
        scene=self.host.service.continuity_snapshot()
        self.assertIn('blue glove on left hand',__import__('json').dumps(scene))
    async def test_post_operation_runtime_failure_keeps_manager_reachable(self):
        with patch.object(self.host,'_service_factory',side_effect=RuntimeError('synthetic provider unavailable')):
            await self.operation(self.fixture.a.character_id,'reset')
        self.assertFalse(self.host._character_switching)
        self.assertIsInstance(self.host.service,CharacterUnavailableService)
        self.assertEqual([],self.host.service.conversation.messages)
        self.assertEqual('ready',self.registry.get(self.fixture.a.character_id).storage_status)
        preview=self.host._operations().preview(self.fixture.a.character_id,'delete')
        self.assertEqual(self.fixture.a.character_id,preview['character_id'])
    async def test_root_canonical_location_migration_keeps_observer_identity_and_original_sources(self):
        self.fixture._turn('My favorite color is green.')
        self.fixture._turn('Actually, my favorite color is blue.')
        old=self.host.service
        namespace=old._canonical_observation_recovery.source_key
        old_path=Path(old.conversation.conversation_file)
        canonical=old_path.read_bytes()
        original=[tuple(r) for r in old._memory_v2_shadow_writer.store.connection.execute(
            'SELECT event_id,source_reference,content_text FROM events WHERE character_id=? ORDER BY event_id',(old.character_id,))]
        await self.operation(old.character_id,'migrate')
        new=self.host.service
        self.assertNotEqual(old_path,Path(new.conversation.conversation_file))
        self.assertEqual(canonical,Path(new.conversation.conversation_file).read_bytes())
        self.assertEqual(canonical,old_path.read_bytes())
        self.assertEqual(namespace,new._canonical_observation_recovery.source_key)
        current=[tuple(r) for r in new._memory_v2_shadow_writer.store.connection.execute(
            'SELECT event_id,source_reference,content_text FROM events WHERE character_id=? ORDER BY event_id',(new.character_id,))]
        self.assertEqual(original,current)
        new.llm.companion_memory_realization=True
        result=new.process_text_turn('What is my favorite color now?',speak=False)
        self.assertTrue(result.succeeded,result.error)
        self.assertIn('blue',result.reply.casefold())
