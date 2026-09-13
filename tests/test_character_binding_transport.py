"""Synthetic production switch boundary; no live registry or database reads."""
import asyncio
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from aifren.backend_host import AIFrenWebSocketHost
from aifren.character.character_registry import CharacterRegistry
from test_websocket_transport import FakeService

class CharacterBindingTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_registry_and_snapshot_do_not_publish_destination_before_rebind(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); registry=CharacterRegistry(root); a=registry.active(); b=registry.create('Synthetic B')
            started,release=threading.Event(),threading.Event()
            class Service(FakeService):
                def prepare_character_switch(self): pass
                def wait_for_character_switch_idle(self,timeout): return True
                def switch_character_state(self,**kw):
                    started.set(); release.wait(3)
                    kw["publish_selection"]()
                    self.character={'name':'Synthetic B','_character_id':b.character_id}
                    self.character_id=b.character_id
                    self.conversation.messages=[]
            service=Service();service.character_id=a.character_id;service.character={'name':'Synthetic A','_character_id':a.character_id}
            host=AIFrenWebSocketHost(service=service,application_dir=root);host._character_registry=registry
            sent=[]
            async def send(ws,message):sent.append(message)
            host._send_json=send
            with patch.object(host,'_companion_snapshot',return_value={}),patch.object(host,'_model_snapshot',return_value={}),patch.object(host,'_tts_snapshot',return_value={}):
                task=asyncio.create_task(host._select_character(object(),b.character_id))
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait,2))
                    self.assertEqual(a.character_id,registry.active().character_id,'Durable selection moved before live binding')
                    await host._send_snapshot(object())
                    self.assertFalse(any(m.get('type')=='snapshot' for m in sent),'No mixed transitional snapshot')
                finally:
                    release.set();await task
            snapshots=[m for m in sent if m.get('type')=='snapshot']
            self.assertEqual(1,len(snapshots))
            self.assertEqual(b.character_id,snapshots[0]['data']['character']['character_id'])
            self.assertEqual([],snapshots[0]['data']['conversation'])

import test_character_switch_ownership as _fixture
import uuid

class RetiredCharacterMutationTests(unittest.TestCase):
    setUp= _fixture.CharacterSwitchOwnershipTests.setUp
    tearDown= _fixture.CharacterSwitchOwnershipTests.tearDown
    _new_service= _fixture.CharacterSwitchOwnershipTests._new_service
    _turn= _fixture.CharacterSwitchOwnershipTests._turn
    _switch= _fixture.CharacterSwitchOwnershipTests._switch

    def test_a_b_a_same_rows_do_not_reauthorize_old_scene_or_viewer_controls(self):
        self._turn("I slip a blue glove onto your right hand.")
        old=self.service.character_binding();scene=self.service.continuity_snapshot()
        self._switch(self.b);self._switch(self.a)
        before=json.dumps(self.service.continuity_snapshot(),sort_keys=True)
        self.assertNotEqual(old['character_session'],self.service.character_binding()['character_session'])
        with self.assertRaisesRegex(RuntimeError,'retired'):
            self.service.apply_continuity_control(command_id=str(uuid.uuid4()),
                action='clear_scene_relation',expected_revision=scene['revision'],
                action_token=scene['scene_relations'][0]['clear_token'],**old)
        with self.assertRaisesRegex(RuntimeError,'retired'):
            self.service.apply_memory_view_mutation(action='hide',record_id='never-opened',**old)
        with self.assertRaisesRegex(RuntimeError,'retired'):
            self.service.process_text_turn('Remove your hat.',speak=False,**old)
        self.assertEqual(before,json.dumps(self.service.continuity_snapshot(),sort_keys=True))

    def test_events_capture_character_session_when_produced(self):
        events=[];self.service.subscribe(events.append)
        old=self.service.character_binding();self.service._emit('continuity_changed',continuity={})
        self._switch(self.b)
        self.assertEqual(old['character_id'],events[0].data['character_id'])
        self.assertEqual(old['character_session'],events[0].data['character_session'])
