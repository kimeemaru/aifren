"""Regression coverage for the Linux backend launcher path handling."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "ensure_aifren_backend_linux.py"
SPEC = importlib.util.spec_from_file_location("ensure_aifren_backend_linux", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
LAUNCHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAUNCHER)


class LinuxBackendLauncherTests(unittest.TestCase):
    def test_absolute_path_preserves_venv_python_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            system_python = root / "system-python"
            system_python.touch()
            venv_python = root / ".venv-aifren" / "bin" / "python"
            venv_python.parent.mkdir(parents=True)
            venv_python.symlink_to(system_python)

            result = LAUNCHER.absolute_path(venv_python)

            self.assertEqual(result, Path(os.path.abspath(venv_python)))
            self.assertTrue(result.is_symlink())
            self.assertNotEqual(result, venv_python.resolve())
