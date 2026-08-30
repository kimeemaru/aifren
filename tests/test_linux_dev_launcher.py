import importlib.util
from pathlib import Path
import os
import subprocess
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "aifren_dev_launcher_linux.py"
SHELL = ROOT / "scripts" / "aifren_dev_linux.sh"
INSTALLER = ROOT / "scripts" / "install_aifren_dev_launcher_linux.sh"
SHORTCUT_INSTALLER = ROOT / "scripts" / "install_aifren_dev_shortcut_linux.sh"


def load_launcher_module():
    spec = importlib.util.spec_from_file_location("aifren_linux_dev_launcher", LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class LinuxDevLauncherTests(unittest.TestCase):
    def test_smoke_test_locates_current_shell_launcher_and_validates_modes(self):
        result = subprocess.run([str(LAUNCHER), "--smoke-test"], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("arguments validated", result.stdout)

    def test_each_control_generates_current_supported_shell_arguments(self):
        launcher = load_launcher_module()
        for action in ("current", "rebuild"):
            for development in (False, True):
                arguments = launcher.build_launch_arguments(SHELL, action, development, reset_console=True, reset_ui=True)
                result = subprocess.run([*arguments, "--validate-arguments"], cwd=ROOT, text=True, capture_output=True)
                self.assertEqual(0, result.returncode, result.stderr)
        with self.assertRaises(ValueError):
            launcher.build_launch_arguments(SHELL, "obsolete", False)

    def test_shortcut_installer_writes_a_valid_desktop_entry_for_dev_controls(self):
        with TemporaryDirectory() as directory:
            environment = dict(os.environ, XDG_DATA_HOME=directory)
            result = subprocess.run([str(INSTALLER)], cwd=ROOT, text=True, capture_output=True, env=environment)
            self.assertEqual(0, result.returncode, result.stderr)
            desktop = Path(directory) / "applications" / "aifren-dev.desktop"
            payload = desktop.read_text(encoding="utf-8")
            self.assertIn("Exec=\"" + str(LAUNCHER) + "\"", payload)
            self.assertIn("StartDevelopment", payload)
            validator = subprocess.run(["desktop-file-validate", str(desktop)], text=True, capture_output=True)
            self.assertEqual(0, validator.returncode, validator.stderr)

    def test_shortcut_installer_alias_uses_the_same_dev_desktop_entry(self):
        with TemporaryDirectory() as directory:
            environment = dict(os.environ, XDG_DATA_HOME=directory)
            result = subprocess.run([str(SHORTCUT_INSTALLER)], cwd=ROOT, text=True, capture_output=True, env=environment)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue((Path(directory) / "applications" / "aifren-dev.desktop").is_file())

    def test_no_companion_tkinter_or_piper_path_is_restored(self):
        self.assertFalse((ROOT / "gui.py").exists())
        self.assertNotIn("piper", (ROOT / "tts" / "tts.py").read_text(encoding="utf-8").lower())
