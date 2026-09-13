"""Synthetic lifetime and cross-process guards for registry-owned characters."""
from contextlib import closing
import json
import os
from pathlib import Path
import select
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from aifren.character.character_registry import CharacterRegistry, CharacterStorageError, write_json_atomic
from aifren.character.character_storage_runtime import (
    CharacterStorageBusy, acquire_runtime_lease, maintenance_lease,
)


class CharacterStorageRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="aifren-synthetic-lease-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.registry = CharacterRegistry(self.root)
        self.a = self.registry.create("Synthetic Ada")
        self.b = self.registry.create("Synthetic Bea")

    def lease(self, character=None):
        lease = acquire_runtime_lease(self.registry, (character or self.a).character_id)
        self.addCleanup(lease.close)
        return lease

    def test_runtime_lease_is_reusable_until_closed_or_retired(self):
        lease = self.lease()
        self.assertEqual(self.a.character_id, lease.character_id)
        self.assertEqual(self.a.timeline_generation, lease.timeline_generation)
        self.assertEqual("local", lease.storage_layout)
        self.assertEqual(lease.paths, lease.assert_current())
        lease.retire()
        with self.assertRaises(CharacterStorageError):
            lease.assert_current()
        lease.close()  # Idempotent cleanup.

    def test_runtime_and_exclusive_maintenance_are_nonblocking_and_character_local(self):
        lease = self.lease()
        second = self.lease()
        started = time.monotonic()
        with self.assertRaises(CharacterStorageBusy):
            with maintenance_lease(self.registry, self.a.character_id):
                self.fail("A live writer must exclude selected maintenance")
        self.assertLess(time.monotonic() - started, 1)
        with maintenance_lease(self.registry, self.b.character_id):
            lease.assert_current()
        self.registry.select(self.b.character_id)
        lease.assert_current()  # Independent selected entry, not global button state.
        second.close()
        lease.close()
        with maintenance_lease(self.registry, self.a.character_id):
            with self.assertRaises(CharacterStorageBusy):
                self.lease()
        self.lease().assert_current()

    def test_other_process_runtime_blocks_maintenance_then_releases_on_exit(self):
        script = (
            "import sys; from aifren.character.character_registry import CharacterRegistry; "
            "from aifren.character.character_storage_runtime import acquire_runtime_lease; "
            "r=CharacterRegistry(sys.argv[1]); l=acquire_runtime_lease(r,sys.argv[2]); "
            "print('leased',flush=True); sys.stdin.readline(); l.close()"
        )
        environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        process = subprocess.Popen([sys.executable, "-c", script, str(self.root), self.a.character_id],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, env=environment)
        try:
            ready, _, _ = select.select([process.stdout], [], [], 5)
            self.assertTrue(ready, "Synthetic lease process failed to reach its boundary")
            self.assertEqual("leased", process.stdout.readline().strip())
            with self.assertRaises(CharacterStorageBusy):
                with maintenance_lease(self.registry, self.a.character_id):
                    pass
            with maintenance_lease(self.registry, self.b.character_id):
                pass
            process.stdin.write("done\n")
            process.stdin.flush()
            _, error = process.communicate(timeout=5)
            self.assertEqual(0, process.returncode, error)
            with maintenance_lease(self.registry, self.a.character_id):
                pass
        finally:
            if process.poll() is None:
                process.terminate()  # Only the synthetic child owned by this test.
                process.communicate(timeout=5)

    def test_registry_transition_retires_old_generation_layout_or_operation(self):
        changes = ({"timeline_generation": str(uuid.uuid4())},
                   {"storage_layout": "legacy_shared"},
                   {"storage_status": "incomplete"},
                   {"operation": {"kind": "reset", "phase": "prepared"}})
        for change in changes:
            with self.subTest(change=change):
                lease = self.lease()
                with self.registry.locked():
                    entry = self.registry._entry(self.a.character_id)
                    original = {key: entry[key] for key in change}
                    entry.update(change)
                    self.registry._save()
                try:
                    with self.assertRaises(CharacterStorageError):
                        lease.assert_current()
                finally:
                    lease.close()
                    with self.registry.locked():
                        self.registry._entry(self.a.character_id).update(original)
                        self.registry._save()

    def test_missing_or_replaced_local_database_fails_without_recreation(self):
        lease = self.lease()
        database = lease.paths["memory_v2"]
        original = database.read_bytes()
        database.unlink()
        with self.assertRaises(CharacterStorageError):
            lease.assert_current()
        self.assertFalse(database.exists())
        lease.close()
        with self.assertRaises(CharacterStorageError):
            self.lease()
        self.assertFalse(database.exists())
        database.write_bytes(original)
        lease = self.lease()
        replacement = database.with_name("replacement.sqlite3")
        shutil.copyfile(database, replacement)
        os.replace(replacement, database)
        with self.assertRaises(CharacterStorageError):
            lease.assert_current()

    def test_database_uuid_and_timeline_are_verified_before_runtime_admission(self):
        database = self.registry.runtime_paths(self.a.character_id)["memory_v2"]
        for key, correct in (("storage_character_id", self.a.character_id),
                             ("timeline_generation", self.a.timeline_generation)):
            with self.subTest(key=key):
                with closing(sqlite3.connect(database)) as connection, connection:
                    connection.execute("UPDATE database_meta SET value=? WHERE key=?", (str(uuid.uuid4()), key))
                with self.assertRaises(CharacterStorageError):
                    self.lease()
                with closing(sqlite3.connect(database)) as connection, connection:
                    connection.execute("UPDATE database_meta SET value=? WHERE key=?", (correct, key))

    def test_symlink_or_hardlinked_lock_is_rejected_without_touching_target(self):
        directory = self.registry.owned_directory(self.a.character_id)
        lock = directory / ".continuity.lock"
        target = self.root / "unrelated.txt"
        target.write_text("unchanged", encoding="utf-8")
        lock.symlink_to(target)
        for acquire in (acquire_runtime_lease, maintenance_lease):
            with self.subTest(acquire=acquire.__name__), self.assertRaises(CharacterStorageError):
                with acquire(self.registry, self.a.character_id):
                    pass
        self.assertEqual("unchanged", target.read_text())
        lock.unlink()
        os.link(target, lock)
        with self.assertRaises(CharacterStorageError):
            self.lease()
        self.assertEqual("unchanged", target.read_text())

    def test_replaced_lock_and_directory_retire_existing_lease(self):
        lease = self.lease()
        lock = lease.paths["directory"] / ".continuity.lock"
        replacement = lock.with_name("replacement.lock")
        replacement.write_text("")
        os.replace(replacement, lock)
        with self.assertRaises(CharacterStorageError):
            lease.assert_current()
        lease.close()
        lease = self.lease()
        directory = lease.paths["directory"]
        directory.rename(directory.with_name(directory.name + "-retired"))
        directory.mkdir()
        with self.assertRaises(CharacterStorageError):
            lease.assert_current()

    def test_already_locked_registry_and_restart_do_not_nest_file_flocks(self):
        with self.registry.locked():
            lease = acquire_runtime_lease(self.registry, self.a.character_id, registry_locked=True)
            try:
                lease.assert_current(registry_locked=True)
                with self.assertRaises(CharacterStorageBusy):
                    with maintenance_lease(self.registry, self.a.character_id, registry_locked=True):
                        pass
            finally:
                lease.close()
            with maintenance_lease(self.registry, self.a.character_id, registry_locked=True):
                pass
        reopened = CharacterRegistry(self.root)
        with acquire_runtime_lease(reopened, self.a.character_id) as lease:
            self.assertEqual(self.a.timeline_generation, lease.timeline_generation)
            lease.assert_current()

    def test_atomic_canonical_save_is_allowed_but_missing_saved_archive_is_not(self):
        lease = self.lease()
        archive = lease.paths["conversation"]
        write_json_atomic(archive, [{"role": "user", "content": "Synthetic only."}])
        lease.assert_current()
        self.assertEqual(1, len(json.loads(archive.read_text())))
        archive.unlink()
        with self.assertRaises(CharacterStorageError):
            lease.assert_current()
        self.assertFalse(archive.exists())

    def test_legacy_missing_initial_archive_remains_existing_first_run_policy(self):
        legacy = next(c for c in self.registry.list_characters() if c.storage_layout == "legacy_shared")
        self.registry.owned_directory(legacy.character_id).mkdir()
        with acquire_runtime_lease(self.registry, legacy.character_id) as lease:
            lease.assert_current()
            self.assertFalse(lease.paths["memory_v2"].exists())
            write_json_atomic(lease.paths["conversation"], [])
            lease.assert_current()
            lease.paths["conversation"].unlink()
            with self.assertRaises(CharacterStorageError):
                lease.assert_current()

    def test_maintenance_can_resume_incomplete_selected_storage_without_recreating_it(self):
        database = self.registry.runtime_paths(self.a.character_id)["memory_v2"]
        database.unlink()
        with self.registry.locked():
            entry = self.registry._entry(self.a.character_id)
            entry.update(storage_status="incomplete", operation={"kind": "reset", "phase": "prepared"})
            self.registry._save()
        with maintenance_lease(self.registry, self.a.character_id):
            self.assertFalse(database.exists())
        with self.assertRaises(CharacterStorageError):
            self.lease()
