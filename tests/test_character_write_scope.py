"""Synthetic writes keep their lifetime lease until rename/commit finishes."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from character_operations import CharacterOperationService
from character_registry import CharacterRegistry, CharacterStorageError
from character_storage_runtime import (
    CharacterStorageBusy, acquire_runtime_lease, maintenance_lease,
)
from conversation import conversation as conversation_module
from memory import memory as memory_module
from memory_v2_store import MemoryV2Store


class CharacterWriteScopeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="aifren-synthetic-write-scope-")
        self.addCleanup(temporary.cleanup)
        self.registry = CharacterRegistry(Path(temporary.name))
        self.character = self.registry.create("Synthetic write scope", personality="Synthetic.")
        self.paths = self.registry.runtime_paths(self.character.character_id)
        self.lease = acquire_runtime_lease(self.registry, self.character.character_id)
        self.addCleanup(self.lease.close)

    def conversation(self, authority="v2"):
        result = conversation_module.Conversation(
            None, conversation_file=str(self.paths["conversation"]),
            summary_file=str(self.paths["summary"]), memory_authority=authority)
        result.continuity_write_guard = self.lease.assert_current
        return result

    def _race(self, write, reached, release, *, retire=None, expected_error=None):
        """Close starts after the actual write owns its already-admitted data."""
        errors = []
        closing_started = threading.Event()
        closed = threading.Event()

        def writer():
            try:
                write()
            except BaseException as error:
                errors.append(error)

        def closer():
            closing_started.set()
            try:
                (retire or self.lease.close)()
            except BaseException as error:
                errors.append(error)
            finally:
                closed.set()

        worker = threading.Thread(target=writer, name="synthetic-owned-write")
        retirement = threading.Thread(target=closer, name="synthetic-owned-close")
        worker.start()
        try:
            self.assertTrue(reached.wait(2), "Write did not reach its real persistence boundary")
            retirement.start()
            self.assertTrue(closing_started.wait(2))
            close_waited = not closed.wait(.05)
            maintenance_blocked = False
            try:
                with maintenance_lease(self.registry, self.character.character_id):
                    pass
            except CharacterStorageBusy:
                maintenance_blocked = True
        finally:
            release.set()
            worker.join(2)
            if retirement.ident is not None:
                retirement.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(retirement.is_alive())
        self.assertTrue(close_waited, "Close released the lifetime lease during an admitted write")
        self.assertTrue(maintenance_blocked, "Reset/delete could begin while an old write was still admitted")
        if expected_error is None:
            self.assertEqual([], errors)
        else:
            self.assertEqual(1, len(errors))
            self.assertIsInstance(errors[0], expected_error)
        with maintenance_lease(self.registry, self.character.character_id):
            pass

    def _paused_io(self, module, name, action, *, expected_error=None):
        reached = threading.Event()
        release = threading.Event()
        original = getattr(module, name)

        def paused(*args, **kwargs):
            reached.set()
            if not release.wait(3):
                raise TimeoutError("Synthetic write barrier timed out")
            if expected_error is not None:
                raise expected_error("Synthetic cancelled/failed write")
            return original(*args, **kwargs)

        with patch.object(module, name, side_effect=paused):
            self._race(action, reached, release, expected_error=expected_error)

    def test_canonical_save_holds_lease_through_rename_and_reset_stays_empty(self):
        archive = self.conversation()
        archive.messages.append({"role": "user", "content": "Synthetic old timeline"})
        self._paused_io(conversation_module, "save_json", archive.save)
        self.assertEqual(archive.messages, json.loads(self.paths["conversation"].read_text()))
        operations = CharacterOperationService(self.registry)
        preview = operations.preview(self.character.character_id, "reset")
        operations.execute(token=preview["token"], character_id=self.character.character_id,
                           revision=preview["revision"])
        self.assertEqual([], json.loads(self.paths["conversation"].read_text()))
        with self.assertRaises(CharacterStorageError):
            archive.save()
        self.assertEqual([], json.loads(self.paths["conversation"].read_text()))

    def test_compatibility_summary_holds_lease_through_rename(self):
        archive = self.conversation("v1")
        archive.summary_data = {"summary": "Synthetic compatibility summary", "summarized_messages": 0}
        self._paused_io(conversation_module, "save_json", archive.save_summary)
        self.assertEqual(archive.summary_data, json.loads(self.paths["summary"].read_text()))

    def test_compatibility_memory_holds_lease_through_rename(self):
        memory = memory_module.Memory(None, memory_file=str(self.paths["memory"]), embedding_model=object())
        memory.continuity_write_guard = self.lease.assert_current
        self._paused_io(memory_module, "save_memories", memory.save)
        self.assertEqual([], json.loads(self.paths["memory"].read_text()))

    def test_store_transaction_holds_lease_through_commit_and_resource_close(self):
        store = MemoryV2Store(str(self.paths["memory_v2"]))
        self.addCleanup(store.close)
        store.continuity_write_guard = self.lease.assert_current
        reached = threading.Event()
        release = threading.Event()

        def write():
            with store.transaction():
                with store.transaction():
                    store.connection.execute("INSERT INTO database_meta(key,value) VALUES ('synthetic_write','committed')")
                reached.set()
                if not release.wait(3):
                    raise TimeoutError("Synthetic transaction barrier timed out")

        def retire():
            try:
                store.close()
            finally:
                self.lease.close()

        self._race(write, reached, release, retire=retire)
        with closing(sqlite3.connect(self.paths["memory_v2"])) as database:
            self.assertEqual(("committed",), database.execute(
                "SELECT value FROM database_meta WHERE key='synthetic_write'").fetchone())

    def test_cancelled_write_releases_scope_without_canonical_mutation(self):
        archive = self.conversation()
        archive.messages.append({"role": "user", "content": "Never published"})
        self._paused_io(conversation_module, "save_json", archive.save, expected_error=InterruptedError)
        self.assertEqual([], json.loads(self.paths["conversation"].read_text()))

    def test_transaction_failure_rolls_back_before_retirement(self):
        store = MemoryV2Store(str(self.paths["memory_v2"]))
        self.addCleanup(store.close)
        store.continuity_write_guard = self.lease.assert_current
        reached = threading.Event()
        release = threading.Event()

        def write():
            with store.transaction():
                store.connection.execute("INSERT INTO database_meta(key,value) VALUES ('synthetic_failed','uncommitted')")
                reached.set()
                if not release.wait(3):
                    raise TimeoutError("Synthetic rollback barrier timed out")
                raise InterruptedError("Synthetic cancelled mutation")

        self._race(write, reached, release, expected_error=InterruptedError)
        self.assertIsNone(store.connection.execute(
            "SELECT value FROM database_meta WHERE key='synthetic_failed'").fetchone())

    def test_reentrant_retirement_defers_unlock_until_outer_write_ends(self):
        with self.lease.writing():
            with self.lease.writing():
                self.lease.close()
                self.assertTrue(self.lease.closed)
                with self.assertRaises(CharacterStorageError):
                    self.lease.assert_current()
                with self.assertRaises(CharacterStorageBusy):
                    with maintenance_lease(self.registry, self.character.character_id):
                        pass
            with self.assertRaises(CharacterStorageBusy):
                with maintenance_lease(self.registry, self.character.character_id):
                    pass
        with maintenance_lease(self.registry, self.character.character_id):
            pass

    def test_mocked_preflight_and_v2_summary_policy_remain_compatible(self):
        archive = self.conversation()
        guard = Mock()
        archive.continuity_write_guard = guard
        archive.save()
        guard.assert_called_once_with()
        guard.reset_mock()
        archive.save_summary()
        guard.assert_not_called()
        guard.side_effect = CharacterStorageError("Synthetic retired guard")
        with patch.object(conversation_module, "save_json") as persistence:
            with self.assertRaises(CharacterStorageError):
                archive.save()
            persistence.assert_not_called()


if __name__ == "__main__":
    unittest.main()
