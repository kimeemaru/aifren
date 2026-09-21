"""Native platform primitives, using only disposable character directories."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from aifren.runtime import file_lock
from aifren.character.character_registry import CharacterRegistry, CharacterStorageError
from aifren.character.character_storage_runtime import (
    CharacterStorageBusy, acquire_runtime_lease, maintenance_lease,
)


class PortableOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='portable ownership Ω ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.registry = CharacterRegistry(self.root)
        self.a = self.registry.create('Synthetic Quartz')
        self.b = self.registry.create('Synthetic Copper')

    def test_shared_readers_exclude_maintenance_until_both_close(self):
        first = acquire_runtime_lease(self.registry, self.a.character_id)
        second = acquire_runtime_lease(self.registry, self.a.character_id)
        self.addCleanup(first.close); self.addCleanup(second.close)
        for lease in (first, second):
            with self.assertRaises(CharacterStorageBusy):
                with maintenance_lease(self.registry, self.a.character_id):
                    self.fail('Live character must stay locked')
            with maintenance_lease(self.registry, self.b.character_id):
                pass
            lease.close()
        with maintenance_lease(self.registry, self.a.character_id):
            with self.assertRaises(CharacterStorageBusy):
                acquire_runtime_lease(self.registry, self.a.character_id)

    def test_other_process_cannot_enter_maintenance(self):
        lease = acquire_runtime_lease(self.registry, self.a.character_id)
        self.addCleanup(lease.close)
        source = Path(__file__).resolve().parents[1]
        code = (
            'import sys; sys.path.insert(0,sys.argv[1]); '
            'from aifren.character.character_registry import CharacterRegistry; '
            'from aifren.character.character_storage_runtime import maintenance_lease,CharacterStorageBusy; '
            'r=CharacterRegistry(sys.argv[2]);\n'
            'try:\n with maintenance_lease(r,sys.argv[3]): pass\n'
            'except CharacterStorageBusy: sys.exit(23)\n')
        args = [sys.executable, '-I', '-c', code, str(source), str(self.root), self.a.character_id]
        result = subprocess.run(args, capture_output=True, timeout=20)
        self.assertEqual(23, result.returncode, result.stderr.decode(errors='replace'))
        lease.close()
        self.assertEqual(0, subprocess.run(args, capture_output=True, timeout=20).returncode)

    def test_close_reopen_keeps_character_paths_and_database_identity(self):
        paths = self.registry.runtime_paths(self.a.character_id)
        original = paths['memory_v2'].read_bytes()
        self.registry.select(self.b.character_id)
        self.registry.select(self.a.character_id)
        reopened = CharacterRegistry(self.root)
        self.assertEqual(paths, reopened.runtime_paths(self.a.character_id))
        lease = acquire_runtime_lease(reopened, self.a.character_id)
        try:
            self.assertEqual(paths, lease.assert_current())
        finally:
            lease.close()
        self.assertEqual(original, paths['memory_v2'].read_bytes())

    def test_lock_hard_link_is_rejected(self):
        path = self.registry.runtime_paths(self.a.character_id)['directory'] / '.continuity.lock'
        external = self.root / 'unowned-lock'
        external.write_bytes(b'unchanged')
        os.link(external, path)
        with self.assertRaises(CharacterStorageError):
            acquire_runtime_lease(self.registry, self.a.character_id)
        self.assertEqual(b'unchanged', external.read_bytes())

    @unittest.skipUnless(os.name == 'nt', 'Windows reparse point contract')
    def test_junction_parent_cannot_redirect_an_owned_lock(self):
        target = self.root / 'destination'; target.mkdir()
        junction = self.root / 'junction'
        result = subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(junction), str(target)],
                                capture_output=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr.decode(errors='replace'))
        try:
            with self.assertRaises(OSError):
                file_lock.open_lock_file(junction / '.test.lock')
            self.assertFalse((target / '.test.lock').exists())
        finally:
            junction.rmdir()


if __name__ == '__main__':
    unittest.main()
