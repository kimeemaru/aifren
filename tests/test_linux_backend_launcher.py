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

    def test_v2_acceptance_requires_authority_and_live_selected_provider(self) -> None:
        ready = {
            "memory_authority": {"mode": "v2"},
            "models": {
                "current": {
                    "mode": "local", "configured": True,
                    "availability": "configured",
                },
                "local_runtime": {
                    "state": "ready", "active_model": "model.gguf",
                    "selected_model": "model.gguf",
                },
            },
        }
        self.assertTrue(CHECKER.is_v2_acceptance_ready(ready))
        ready["models"]["local_runtime"]["active_model"] = "other.gguf"
        self.assertFalse(CHECKER.is_v2_acceptance_ready(ready))
        ready["models"]["local_runtime"]["active_model"] = "model.gguf"
        ready["memory_authority"]["mode"] = "v1"
        self.assertFalse(CHECKER.is_v2_acceptance_ready(ready))

    def test_readiness_requires_the_selected_authority(self):
        self.assertTrue(CHECKER.is_expected_memory_authority({'memory_authority':{'mode':'v2'}}, 'v2'))
        self.assertFalse(CHECKER.is_expected_memory_authority({'memory_authority':{'mode':'v1'}}, 'v2'))
        self.assertFalse(CHECKER.is_expected_memory_authority({}, 'v2'))
        self.assertTrue(CHECKER.is_expected_memory_authority({'memory_authority':{'mode':'v1'}}, 'v1'))

    def test_ensure_does_not_reuse_or_stop_wrong_authority(self):
        with mock.patch.object(LAUNCHER,'listener_pid',return_value=123),mock.patch.object(LAUNCHER,'is_expected_backend',return_value=True),mock.patch.object(LAUNCHER,'run_protocol_check',return_value=3),mock.patch.object(LAUNCHER,'start_backend') as start,mock.patch.object(LAUNCHER,'stop_expected_backend') as stop:
            with self.assertRaises(RuntimeError):
                LAUNCHER.ensure_backend(Path('/python'),Path('/repo'),Path('/owned'))
            start.assert_not_called();stop.assert_not_called()

    def test_zombie_backend_is_treated_as_stopped(self) -> None:
        with mock.patch.object(LAUNCHER, "process_state", return_value="Z"):
            self.assertTrue(LAUNCHER.wait_for_exit(12345, 0.1))
