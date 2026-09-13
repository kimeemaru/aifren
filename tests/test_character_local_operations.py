"""Selected synthetic migration/reset/delete through production operation owners."""
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import uuid
from character_registry import CharacterRegistry, Character, CharacterStorageError, CharacterRegistryError
from character_operations import CharacterOperationService
from memory_v2_store.character_copy import selected_character_inventory
from memory_v2_shadow_writer import MemoryV2ShadowWriter
from character_storage_runtime import acquire_runtime_lease
import test_stopped_character_folder_wipe as wipe

class CharacterLocalOperationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=wipe.StoppedCharacterFolderWipeTests();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.root; self.a=self.fixture.a; self.b=self.fixture.b
        self.registry=CharacterRegistry(self.root)
        self.registry._data['characters']=[asdict(Character(i,n,f'characters/{i}','2026-01-01T00:00:00Z',
            source_namespace=f'characters/{i}/conversation.json')) for i,n in ((self.a,'Mira A'),(self.b,'Noa B'))]
        self.registry._data['active_character_id']=self.a;self.registry._save()
        self.ops=CharacterOperationService(self.registry)
    def perform(self, identity,action):
        preview=self.ops.preview(identity,action)
        return self.ops.execute(token=preview['token'],character_id=identity,revision=preview['revision'])
    def inventory(self, identity, path=None):
        return selected_character_inventory(path or self.registry.runtime_paths(identity)['memory_v2'],identity)
    def test_migration_preserves_original_rows_source_namespace_and_independent_b(self):
        original=self.inventory(self.a); other=self.inventory(self.b)
        paths=self.registry.runtime_paths(self.a); before={k:paths[k].read_bytes() for k in ('conversation','memory','summary','personality','character')}
        self.assertEqual('completed',self.perform(self.a,'migrate')['status'])
        self.assertEqual('local',self.registry.get(self.a).storage_layout)
        self.assertEqual(original,self.inventory(self.a))
        self.assertEqual(other,self.inventory(self.b))
        self.assertEqual(original,self.inventory(self.a,self.fixture.database))
        self.assertTrue(self.registry.get(self.a).migration_source)
        self.assertEqual(before,{k:self.registry.runtime_paths(self.a)[k].read_bytes() for k in before})
        self.assertEqual('already_local',self.perform(self.a,'migrate')['status'])
        # Unavailable old shared DB cannot affect ordinary local A operation.
        hidden=self.fixture.database.with_suffix('.offline');self.fixture.database.rename(hidden)
        try:
            service=self.fixture.open_service(self.a)
            self.assertEqual(self.registry.runtime_paths(self.a)['memory_v2'],service._memory_v2_shadow_writer.database_path)
            self.assertEqual(f'characters/{self.a}/conversation.json#0',service._memory_v2_shadow_writer._canonical_source_reference(paths['conversation'],0))
            self.fixture.turn(service,'Your blue glove is now green.')
            self.assertIn('green glove',str(service.continuity_snapshot()))
            service.close()
        finally: hidden.rename(self.fixture.database)
        self.assertEqual(other,self.inventory(self.b))
    def test_local_missing_wrong_identity_and_retained_copy_never_fall_back(self):
        self.perform(self.a,'migrate'); local=self.registry.runtime_paths(self.a)['memory_v2']
        saved=local.with_suffix('.temporarily-offline');local.rename(saved)
        try:
            with self.assertRaises(CharacterStorageError): self.registry.assert_storage_ready(self.a)
            with self.assertRaises(CharacterStorageError): self.fixture.open_service(self.a)
        finally: saved.rename(local)
        self.perform(self.b,'migrate')
        wrong=local.with_suffix('.wrong');shutil.copyfile(self.registry.runtime_paths(self.b)['memory_v2'],wrong)
        local.rename(saved);wrong.rename(local)
        try:
            with self.assertRaises(CharacterStorageError): self.registry.assert_storage_ready(self.a)
        finally: local.unlink();saved.rename(local)
    def test_reset_removes_both_legacy_imports_and_new_original_state_and_retained_copy(self):
        other=self.inventory(self.b);self.perform(self.a,'migrate')
        c=self.registry.get(self.a);paths=self.registry.runtime_paths(self.a)
        profile={k:paths[k].read_bytes() for k in ('character','personality')}
        paths['presentation'].write_text('{"avatar":"shared-asset-reference","portrait":1.2}')
        old=self.fixture.open_service(self.a);old_conversation=old.conversation
        with self.assertRaises(CharacterStorageError): self.perform(self.a,'reset')
        old.close()
        self.perform(self.a,'reset')
        self.assertNotEqual(c.timeline_generation,self.registry.get(self.a).timeline_generation)
        self.assertEqual(profile,{k:paths[k].read_bytes() for k in profile})
        self.assertEqual([],json.loads(paths['conversation'].read_text()))
        self.assertEqual([],json.loads(paths['memory'].read_text()))
        self.assertTrue(paths['presentation'].exists())
        self.assertEqual(0,dict(self.inventory(self.a).counts)['claims'])
        self.assertFalse(self.inventory(self.a,self.fixture.database).character_exists)
        self.assertEqual(other,self.inventory(self.b))
        old_conversation.messages.append({'role':'assistant','content':'STALE WORKER CANARY'})
        with self.assertRaises(CharacterStorageError): old_conversation.save()
        fresh=self.fixture.open_service(self.a)
        self.assertEqual([],fresh.conversation.messages)
        self.assertEqual([],fresh.continuity_snapshot()['scene_relations'])
        fresh.close()
        self.assertEqual([],json.loads(paths['conversation'].read_text()))
    def test_delete_last_empty_registry_and_recreate_name_fresh_identity(self):
        self.perform(self.a,'delete');self.perform(self.b,'delete')
        self.assertEqual([],CharacterRegistry(self.root).list_characters())
        self.assertIsNone(CharacterRegistry(self.root).active_or_none())
        self.assertFalse((self.root/f'characters/{self.a}').exists())
        fresh=self.registry.create('Mira A',personality='New synthetic profile.',request_id='recreate')
        self.assertNotEqual(self.a,fresh.character_id)
        self.assertEqual('local',fresh.storage_layout)
        self.assertEqual(0,dict(self.inventory(fresh.character_id).counts)['claims'])
    def test_confirmed_target_and_revision_cannot_follow_selection(self):
        receipt=self.ops.preview(self.a,'reset'); before=self.inventory(self.a)
        self.registry.select(self.b)
        with self.assertRaises(CharacterStorageError):
            self.ops.execute(token=receipt['token'],character_id=self.a,revision=receipt['revision'])
        self.assertEqual(before,self.inventory(self.a))
    def test_interrupted_migration_is_visible_and_resumes_same_destination(self):
        before=self.inventory(self.a); original=self.registry._save
        def crash():
            c=self.registry.get(self.a)
            if c.storage_layout=='local': raise OSError('synthetic pointer publication failure')
            original()
        with patch.object(self.registry,'_save',side_effect=crash):
            with self.assertRaises(CharacterStorageError): self.perform(self.a,'migrate')
        self.registry.refresh(); self.assertIsNotNone(self.registry.get(self.a).operation)
        with self.assertRaises(CharacterStorageError): self.registry.assert_storage_ready(self.a)
        self.ops=CharacterOperationService(self.registry)
        self.perform(self.a,'resume')
        self.assertEqual(before,self.inventory(self.a))
    def test_cleanup_removes_only_confirmed_retained_character_rows(self):
        before=self.inventory(self.b);self.perform(self.a,'migrate');local=self.inventory(self.a)
        self.perform(self.a,'cleanup')
        self.assertEqual(local,self.inventory(self.a))
        self.assertEqual(before,self.inventory(self.b))
        self.assertFalse(self.inventory(self.a,self.fixture.database).character_exists)
        self.assertIsNone(self.registry.get(self.a).migration_source)

    def test_tampered_retained_path_cannot_inspect_or_delete_b(self):
        other=self.inventory(self.b);self.perform(self.a,'migrate')
        canary=self.registry.runtime_paths(self.b)['conversation'];before=canary.read_bytes()
        self.registry._entry(self.a)['migration_source']['files']=[str(canary.relative_to(self.root))]
        self.registry._save()
        for action in ('cleanup','reset','delete'):
            with self.assertRaises(CharacterStorageError): self.ops.preview(self.a,action)
        self.assertEqual(before,canary.read_bytes());self.assertEqual(other,self.inventory(self.b))

    def test_old_copy_change_after_migration_blocks_cleanup(self):
        from memory_v2_store import MemoryV2Store
        self.perform(self.a,'migrate')
        store=MemoryV2Store(str(self.fixture.database))
        try:
            with store.transaction():
                store.connection.execute("UPDATE characters SET display_name=? WHERE character_id=?",('NEW UNEXPORTED VALUE',self.a))
        finally: store.close()
        with self.assertRaises(CharacterStorageError): self.perform(self.a,'cleanup')
        self.registry.refresh();self.assertIsNotNone(self.registry.get(self.a).operation)
        self.assertTrue(self.inventory(self.a,self.fixture.database).character_exists)
        # This incomplete cleanup intentionally remains blocked; no alternate
        # write authority or data-erasing automatic retry was activated.

    def test_interrupted_reset_partial_db_is_resumable_and_old_workers_stay_retired(self):
        self.perform(self.a,'migrate');other=self.inventory(self.b)
        initialize=self.registry.initialize_empty_continuity
        def partial(identity):
            path=self.registry.runtime_paths(identity)['memory_v2']
            path.write_bytes(b'UNFINISHED SQLITE DESTINATION')
            raise OSError('synthetic interrupted initialization')
        with patch.object(self.registry,'initialize_empty_continuity',side_effect=partial):
            with self.assertRaises(CharacterStorageError):self.perform(self.a,'reset')
        self.registry.refresh();self.assertEqual('incomplete',self.registry.get(self.a).storage_status)
        self.perform(self.a,'resume')
        self.registry.assert_storage_ready(self.a)
        self.assertEqual(0,dict(self.inventory(self.a).counts)['claims'])
        self.assertEqual(other,self.inventory(self.b))

    def test_interrupted_delete_after_directory_removal_can_finish_registry_pointer(self):
        before=self.inventory(self.b);original=self.registry._save
        def crash():
            if self.registry.get(self.a) is None:raise OSError('pointer publication interrupted')
            original()
        with patch.object(self.registry,'_save',side_effect=crash):
            with self.assertRaises(CharacterStorageError):self.perform(self.a,'delete')
        self.registry.refresh();self.assertFalse(self.registry.owned_directory(self.a).exists())
        self.perform(self.a,'resume')
        self.assertIsNone(self.registry.get(self.a));self.assertEqual(before,self.inventory(self.b))

    def test_preview_inventories_unknown_owned_files_and_never_follows_shared_asset_links(self):
        paths=self.registry.runtime_paths(self.a)
        (paths['directory']/'unexpected-note.txt').write_text('synthetic owned extra')
        preview=self.ops.preview(self.a,'delete')
        self.assertIn(str((paths['directory']/'unexpected-note.txt').relative_to(self.root)),preview['files'])
        canary=self.registry.runtime_paths(self.b)['personality'];before=canary.read_bytes()
        (paths['directory']/'external').symlink_to(canary)
        with self.assertRaises(CharacterStorageError):self.ops.preview(self.a,'delete')
        self.assertEqual(before,canary.read_bytes())

class CharacterCreationSafetyTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.r=CharacterRegistry(self.root)
    def test_duplicate_normalization_idempotence_and_readable_stable_directory(self):
        a=self.r.create('  Test Friend  ',personality='Synthetic.',request_id='same')
        self.assertEqual(a,self.r.create('Test Friend',personality='Synthetic.',request_id='same'))
        for name in ('test friend',' TEST  FRIEND ','Ｔｅｓｔ Friend'):
            with self.assertRaises(CharacterRegistryError): self.r.create(name,personality='Synthetic.')
        self.assertIn('test-friend--'+a.character_id,a.config_directory)
        self.assertEqual('local',a.storage_layout);self.r.assert_storage_ready(a.character_id)
        with self.assertRaises(CharacterRegistryError): self.r.create('Different',request_id='same')
        with self.assertRaises(CharacterRegistryError): self.r.create('Test Friend',personality='Different authored content',request_id='same')
    def test_create_collision_or_failure_never_deletes_an_existing_directory(self):
        identity=uuid.uuid4();path=self.root/'characters'/('collision--'+str(identity));path.mkdir()
        (path/'canary').write_text('owned elsewhere')
        with patch('character_registry.uuid.uuid4',return_value=identity):
            with self.assertRaises(FileExistsError):self.r.create('collision')
        self.assertEqual('owned elsewhere',(path/'canary').read_text())
        self.assertFalse(any(c.character_id==str(identity) for c in self.r.list_characters()))

class CharacterLocalDamagedStorageTests(unittest.TestCase):
    setUp=CharacterCreationSafetyTests.setUp
    def perform(self, identity, action):
        ops=CharacterOperationService(self.r);p=ops.preview(identity,action)
        return ops.execute(token=p['token'],character_id=identity,revision=p['revision'])
    def test_corrupt_local_database_has_explicit_reset_without_shared_fallback(self):
        a=self.r.create('Damaged synthetic local',personality='Synthetic.')
        p=self.r.runtime_paths(a.character_id)
        p['memory_v2'].write_bytes(b'NOT A DATABASE')
        with self.assertRaises(CharacterStorageError):self.r.assert_storage_ready(a.character_id)
        self.perform(a.character_id,'reset')
        self.r.assert_storage_ready(a.character_id)
        self.assertEqual([],json.loads(p['conversation'].read_text()))
    def test_seed_cannot_revive_an_empty_registry_after_last_delete(self):
        from runtime_layout import initialize_data_root
        a=self.r.create('Only friend',personality='Synthetic.')
        self.r._data['characters']=[asdict(a)];self.r._data['active_character_id']=a.character_id;self.r._save()
        self.perform(a.character_id,'delete')
        seed=self.root/'synthetic_seed';(seed/'characters/default').mkdir(parents=True)
        (seed/'characters/default/personality.md').write_text('DO NOT REVIVE')
        (seed/'conversation.json').write_text('[{"role":"user","content":"OLD SEED"}]')
        initialize_data_root(self.root,seed)
        self.assertIsNone(CharacterRegistry(self.root).active_or_none())
        self.assertFalse((self.root/'conversation.json').exists())

class CharacterMissingProfileTests(unittest.TestCase):
    setUp=CharacterLocalOperationTests.setUp
    perform=CharacterLocalOperationTests.perform
    inventory=CharacterLocalOperationTests.inventory
    def test_missing_profile_blocks_reset_before_any_selected_row_deletion(self):
        original=self.inventory(self.a);other=self.inventory(self.b)
        self.registry.runtime_paths(self.a)['character'].unlink()
        for action in ('reset','migrate'):
            with self.assertRaises(CharacterStorageError): self.ops.preview(self.a,action)
        self.assertIsNone(self.registry.get(self.a).operation)
        self.assertEqual(original,self.inventory(self.a));self.assertEqual(other,self.inventory(self.b))
        self.perform(self.a,'delete')
        self.assertIsNone(self.registry.get(self.a));self.assertEqual(other,self.inventory(self.b))
