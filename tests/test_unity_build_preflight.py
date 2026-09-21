"""Exercise the supported build entry without launching an Editor or using host preferences."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class UnityBuildPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.script = self.root / "scripts/build_aifren_linux.sh"
        self.script.parent.mkdir()
        shutil.copyfile(ROOT / "scripts/build_aifren_linux.sh", self.script)
        self.script.chmod(0o700)
        self.player = self.root / "unity/AIFrenUnityPoc/Builds/LinuxDevelopment/AIFrenPoc.x86_64"
        self.player.parent.mkdir(parents=True)
        self.player.write_bytes(b"last completed synthetic player")
        self.record = self.root / "invocation.json"
        self.editor = self.root / "synthetic editor"
        self.editor.write_text("#!" + sys.executable + '''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
Path(os.environ['QA_RECORD']).write_text(json.dumps({
    'args': args, 'HOME': os.environ.get('HOME'),
    'XDG_CONFIG_HOME': os.environ.get('XDG_CONFIG_HOME'),
    'DISPLAY': os.environ.get('DISPLAY')}))
log = Path(args[args.index('-logFile') + 1])
mode = os.environ.get('QA_RESULT', 'success')
if mode == 'success':
    assert args[args.index('-executeMethod') + 1].endswith('.PreflightEditorExecution')
    log.write_text('AIFren Editor execution preflight passed.\\n')
elif mode == 'failure':
    log.write_text('No valid Unity Editor license found.\\n')
    sys.exit(1)
''')
        self.editor.chmod(0o700)
        self.log = self.root / "build.log"
        self.env = dict(os.environ, UNITY_EDITOR=str(self.editor),
                        AIFREN_UNITY_BUILD_LOG=str(self.log), QA_RECORD=str(self.record))

    def run_preflight(self, result):
        return subprocess.run([str(self.script), "--development", "--preflight"],
                              cwd=self.temp.name, env=dict(self.env, QA_RESULT=result),
                              capture_output=True, text=True, timeout=10)

    def test_executes_project_method_in_normal_environment_without_replacing_player(self):
        result = self.run_preflight("success")
        self.assertEqual(0, result.returncode, result.stderr)
        record = json.loads(self.record.read_text())
        self.assertIn("-batchmode", record["args"])
        self.assertEqual(record["args"][record["args"].index("-projectPath") + 1],
                         str(self.root / "unity/AIFrenUnityPoc"))
        for key in ("HOME", "XDG_CONFIG_HOME", "DISPLAY"):
            self.assertEqual(record[key], os.environ.get(key))
        self.assertIn("separate gates", result.stdout)
        self.assertEqual(b"last completed synthetic player", self.player.read_bytes())

    def test_failed_license_or_missing_completion_is_not_success(self):
        for mode in ("failure", "no_execution"):
            with self.subTest(mode=mode):
                result = self.run_preflight(mode)
                self.assertNotEqual(0, result.returncode)
                self.assertEqual(b"last completed synthetic player", self.player.read_bytes())

    def test_previous_successful_log_cannot_mask_current_failure(self):
        self.log.write_text("AIFren Editor execution preflight passed.\n")
        self.assertEqual(0, self.run_preflight("success").returncode)
        self.assertNotEqual(0, self.run_preflight("no_execution").returncode)

    def test_rejects_unknown_options_before_launch(self):
        result = subprocess.run([str(self.script), "--activate-license"], env=self.env,
                                capture_output=True, timeout=10)
        self.assertEqual(2, result.returncode)
        self.assertFalse(self.record.exists())
