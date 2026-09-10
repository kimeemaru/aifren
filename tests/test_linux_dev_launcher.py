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

    def test_only_v1_rollback_requires_development_player(self):
        accepted = subprocess.run(
            [str(SHELL), "current", "development", "v2-memory", "--validate-arguments"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(0, accepted.returncode, accepted.stderr)
        rejected = subprocess.run(
            [str(SHELL), "current", "v1-memory", "--validate-arguments"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(2, rejected.returncode)

    def test_acceptance_diagnostic_hook_runs_before_owned_backend_cleanup(self):
        payload = SHELL.read_text(encoding="utf-8")
        capture = payload.index("--capture-running-backend")
        stop = payload.index("--stop")
        self.assertLess(capture, stop)
        self.assertIn('trap \'exit 130\' INT', payload)
        self.assertIn('trap \'exit 143\' TERM', payload)
        self.assertLess(
            payload.index("inherited_staged_data_root="),
            payload.index('source "$repository_root/.env"'),
        )
        self.assertGreater(
            payload.index(
                'export AIFREN_DEVELOPMENT_STAGED_DATA_ROOT="$inherited_staged_data_root"'
            ),
            payload.index('source "$repository_root/.env"'),
        )
        self.assertLess(
            payload.index("--v2-acceptance-ready"),
            payload.index('echo "Launching Linux player:'),
        )

        normal = subprocess.run(
            [str(SHELL), "current", "--validate-arguments"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(0, normal.returncode, normal.stderr)

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

class DevelopmentButtonRouteTests(unittest.TestCase):
    def test_actual_button_construction_and_callbacks_choose_only_development(self):
        from unittest.mock import patch, MagicMock
        from contextlib import ExitStack
        launcher = load_launcher_module()
        class Variable:
            def __init__(self, value=None, **kwargs): self.value = value
            def get(self): return self.value
            def set(self, value): self.value = value
        class Widget:
            def __init__(self, *args, **kwargs): self.options = kwargs
            def pack(self, **kwargs): pass
            def configure(self, **kwargs): self.options.update(kwargs)
        with TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory); (root / 'scripts').mkdir()
            script = root / 'scripts/aifren_dev_linux.sh';script.write_text('synthetic')
            for name in ['Frame','Checkbutton','Button','Menubutton','Menu','Label']:
                cls = type(name,(Widget,),{'add_command':lambda self,**kwargs:None})
                stack.enter_context(patch.object(launcher.tk,name,cls))
            for name in ['BooleanVar','StringVar']: stack.enter_context(patch.object(launcher.tk,name,Variable))
            stack.enter_context(patch.object(launcher.scrolledtext,'ScrolledText',Widget))
            stack.enter_context(patch.object(launcher.tk.Tk,'__init__',lambda self:None))
            for name in ['title','geometry','minsize','protocol','after','append']:
                stack.enter_context(patch.object(launcher.AIFrenDevLauncher,name,lambda *a,**k:None))
            stack.enter_context(patch.object(launcher.AIFrenDevLauncher,'load_vrma_qa_folder',return_value=str(root)))
            stack.enter_context(patch.object(launcher.threading,'Thread'))
            process = stack.enter_context(patch.object(launcher.subprocess,'Popen'))
            process.return_value.poll.return_value = 0
            instance = launcher.AIFrenDevLauncher(root)
            instance.preferences_path = root / 'launcher.json'
            self.assertEqual([button.options['text'] for button in instance.launch_buttons],
                ['Start Development Build','Rebuild Development + Start'])
            self.assertFalse(instance.reset_console.get());self.assertFalse(instance.reset_ui.get())
            for button, action in zip(instance.launch_buttons,['current','rebuild']):
                button.options['command']()
                args = process.call_args.args[0]
                self.assertEqual(args,[str(script),action,'development'])
                self.assertEqual(process.call_args.kwargs['env']['AIFREN_VRMA_QA_DIR'],str(root))
                instance.poll_process()
            instance.reset_ui.set(True);instance.reset_console.set(True)
            instance.launch_buttons[0].options['command']()
            self.assertIn('reset-ui',process.call_args.args[0]);self.assertIn('reset-console',process.call_args.args[0])
            instance.poll_process()
            instance.launch_buttons[0].options['command']()
            self.assertNotIn('reset-ui',process.call_args.args[0]);self.assertNotIn('reset-console',process.call_args.args[0])
            instance.poll_process()
            process.side_effect = OSError('synthetic failure')
            instance.reset_ui.set(True);instance.launch_buttons[0].options['command']()
            self.assertFalse(instance.reset_ui.get());self.assertIsNone(instance.stop_request_file)
            self.assertIn('Launch failed',instance.status.get())

    def test_production_shell_selects_same_target_and_preserves_it_on_build_failure(self):
        import json,sys,shutil
        with TemporaryDirectory() as directory:
            root=Path(directory); scripts=root/'scripts';scripts.mkdir()
            shell=scripts/SHELL.name;shutil.copyfile(SHELL,shell);shell.chmod(0o700)
            runtime=root/'.venv-aifren/bin/python';runtime.parent.mkdir(parents=True)
            # Controlled child hooks, no backend, Unity, network or model launch.
            runtime.write_text('#!'+sys.executable+'\nimport json,os,sys\nfrom pathlib import Path\nwith open(os.environ["QA_RECORD"],"a") as f:f.write(json.dumps({"kind":"backend","args":sys.argv[1:],"authority":os.environ.get("AIFREN_MEMORY_AUTHORITY")})+"\\n")\n')
            runtime.chmod(0o700)
            player=root/'unity/AIFrenUnityPoc/Builds/LinuxDevelopment/AIFrenPoc.x86_64';player.parent.mkdir(parents=True)
            player.write_text('#!'+sys.executable+'\nimport json,os,sys\nwith open(os.environ["QA_RECORD"],"a") as f:f.write(json.dumps({"kind":"player","exe":sys.argv[0],"args":sys.argv[1:],"authority":os.environ.get("AIFREN_MEMORY_AUTHORITY")})+"\\n")\n')
            player.chmod(0o700);before=player.read_bytes()
            build=scripts/'build_aifren_linux.sh'
            build.write_text('#!/bin/sh\n[ "${1:-}" = "--development" ] || exit 8\n[ "${QA_BUILD_FAIL:-0}" = "0" ] || exit 9\nexit 0\n');build.chmod(0o700)
            (root/'.env').write_text('AIFREN_MEMORY_AUTHORITY=v1\n')
            record=root/'calls.jsonl';environment={k:v for k,v in os.environ.items() if not k.startswith('AIFREN_')}
            environment['QA_RECORD']=str(record)
            for action in ['current','rebuild']:
                result=subprocess.run([str(shell),action,'development'],cwd=root,env=environment,capture_output=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
            calls=[json.loads(line) for line in record.read_text().splitlines()]
            players=[call for call in calls if call['kind']=='player']
            self.assertEqual(len(players),2)
            self.assertTrue(all(call['exe']==str(player) and call['authority']=='v2' for call in players))
            self.assertTrue(all('-aifren-qa-plan' not in call['args'] and '-aifren-reset-ui' not in call['args'] for call in players))
            self.assertEqual(len([call for call in calls if call['kind']=='backend' and '--start' in call['args']]),2)
            self.assertEqual(len([call for call in calls if call['kind']=='backend' and '--stop' in call['args']]),2)
            rollback=subprocess.run([str(shell),'current','development','v1-memory'],cwd=root,env=environment,capture_output=True,timeout=10)
            self.assertEqual(rollback.returncode,0,rollback.stderr)
            rollback_calls=[json.loads(line) for line in record.read_text().splitlines()]
            self.assertEqual('v1',[call for call in rollback_calls if call['kind']=='player'][-1]['authority'])
            normal=subprocess.run([str(shell),'current','development'],cwd=root,env=environment,capture_output=True,timeout=10)
            self.assertEqual(normal.returncode,0,normal.stderr)
            self.assertEqual('v2',[json.loads(line) for line in record.read_text().splitlines() if json.loads(line)['kind']=='player'][-1]['authority'])
            record_before=record.read_bytes()
            result=subprocess.run([str(shell),'rebuild','development'],cwd=root,env=dict(environment,QA_BUILD_FAIL='1'),capture_output=True,timeout=10)
            self.assertEqual(result.returncode,9)
            self.assertEqual(player.read_bytes(),before);self.assertEqual(record.read_bytes(),record_before)
