"""Regression coverage for the Linux backend launcher path handling."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "ensure_aifren_backend_linux.py"
SPEC = importlib.util.spec_from_file_location("ensure_aifren_backend_linux", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
LAUNCHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAUNCHER)

CHECKER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_backend_protocol.py"
CHECKER_SPEC = importlib.util.spec_from_file_location("check_backend_protocol", CHECKER_PATH)
assert CHECKER_SPEC is not None and CHECKER_SPEC.loader is not None
CHECKER = importlib.util.module_from_spec(CHECKER_SPEC)
CHECKER_SPEC.loader.exec_module(CHECKER)


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

    def test_transport_readiness_accepts_newer_v2_compatible_snapshots(self) -> None:
        self.assertTrue(CHECKER.is_compatible_transport_version(2))
        self.assertTrue(CHECKER.is_compatible_transport_version(3))
        self.assertFalse(CHECKER.is_compatible_transport_version(1))
        self.assertFalse(CHECKER.is_compatible_transport_version(True))

    def test_zombie_backend_is_treated_as_stopped(self) -> None:
        with mock.patch.object(LAUNCHER, "process_state", return_value="Z"):
            self.assertTrue(LAUNCHER.wait_for_exit(12345, 0.1))
