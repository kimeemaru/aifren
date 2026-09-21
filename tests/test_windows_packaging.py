from __future__ import annotations
import tempfile

import importlib.util
import json
import hashlib
import shutil
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock
import subprocess
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PACKAGER = load_module("package_unity", ROOT / "scripts" / "package_unity.py")
LAUNCHER = load_module("launch_friend", ROOT / "scripts" / "launch_friend.py")


class WindowsPackagingTests(unittest.TestCase):
    def _fake_repository(self, root: Path) -> tuple[Path, Path]:
        repository = root / "Source Checkout"
        player = repository / "unity" / "AIFrenUnityPoc" / "Builds" / "Windows"
        player.mkdir(parents=True)
        (player / "AIFrenPoc.exe").write_bytes(b"player")
        (player / "AIFrenPoc_Data").mkdir()
        (player / "UnityPlayer.dll").write_bytes(b"unity")

        (repository / 'backend_host.py').write_text("# backend\n", encoding="utf-8")
        scripts = repository / "scripts"
        scripts.mkdir()
        for name in ("launch_friend.py", "check_backend_protocol.py"):
            (scripts / name).write_text(f"# {name}\n", encoding="utf-8")
        (scripts / "launch_friend_windows.cmd").write_text(
            "@echo off\r\necho launcher\r\n", encoding="utf-8"
        )
        (repository / 'aifren/runtime').mkdir(parents=True)
        (repository / 'aifren/runtime/runtime_layout.py').write_text("# layout\n", encoding="utf-8")
        for name in PACKAGER._selector.application_inputs():
            path = repository / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists(): path.write_text("# synthetic allowed source\n")

        models = repository / "models"
        (models / "kokoro-82m").mkdir(parents=True)
        (models / "kokoro-82m" / "config.json").write_text("{}", encoding="utf-8")
        (models / "llama").mkdir()
        (models / "llama" / "test.gguf").write_bytes(b"GGUF")

        character = repository / "characters" / "default"
        character.mkdir(parents=True)
        (character / "character.json").write_text('{"name":"Synthetic"}', encoding="utf-8")
        (character / "personality.md").write_text("Synthetic fixture.\n", encoding="utf-8")
        private = repository / "characters" / "private-fixture"
        private.mkdir()
        (private / "must-not-package.txt").write_text("private", encoding="utf-8")
        (repository / "conversation.json").write_text("private history", encoding="utf-8")
        (repository / ".env").write_text("SECRET=value", encoding="utf-8")

        runtime = root / "Staged Windows Runtime"
        (runtime / "Scripts").mkdir(parents=True)
        (runtime / "Scripts" / "python.exe").write_bytes(b"python")
        return repository, runtime

    def test_windows_package_composition_separates_resources_and_seed_data(self) -> None:
        with TemporaryDirectory(prefix="AIFren package test ") as directory:
            root = Path(directory)
            repository, runtime = self._fake_repository(root)
            output = root / "Output With Spaces" / "AIFren"
            staging = root / "Reviewed staging"
            sources = {
                "AIFrenPoc.exe": repository / "unity/AIFrenUnityPoc/Builds/Windows/AIFrenPoc.exe",
                "UnityPlayer.dll": repository / "unity/AIFrenUnityPoc/Builds/Windows/UnityPlayer.dll",
                "runtime/python/python.exe": runtime / "Scripts/python.exe",
                "runtime/app/models/kokoro-82m/config.json": repository / "models/kokoro-82m/config.json",
                "runtime/app/models/llama/test.gguf": repository / "models/llama/test.gguf",
            }
            inputs = []
            for name, source in sources.items():
                target = staging / name; target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                inputs.append({"path": name, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
            result = PACKAGER.compose_windows_package(repository, staging, output, inputs)

            self.assertEqual(output, result)
            self.assertTrue((output / "AIFrenPoc.exe").is_file())
            self.assertTrue((output / "runtime" / "python" / "python.exe").is_file())
            app = output / "runtime" / "app"
            self.assertTrue((app / 'backend_host.py').is_file())
            self.assertTrue((app / "models" / "kokoro-82m" / "config.json").is_file())
            self.assertTrue((app / "models" / "llama" / "test.gguf").is_file())
            self.assertFalse((app / "characters" / "private-fixture").exists())
            self.assertFalse((app / "conversation.json").exists())
            self.assertFalse((app / ".env").exists())
            self.assertEqual(
                [], json.loads((app / "seed_data" / "conversation.json").read_text(encoding="utf-8"))
            )
            self.assertEqual(
                "You are a friendly AI companion.\n",
                (app / "seed_data" / "characters" / "default" / "personality.md").read_text(encoding="utf-8"),
            )
            self.assertTrue((output / "Launch AIFren.cmd").is_file())
            metadata = json.loads((output / "package.json").read_text(encoding="utf-8"))
            self.assertEqual("windows-x64", metadata["platform"])
            self.assertIn("--portable", metadata["writable_data"])
            self.assertEqual(inputs, metadata["inputs"])
            with self.assertRaises(ValueError):
                PACKAGER.compose_windows_package(repository, staging, output, inputs)

    def test_backend_command_is_an_argv_list_with_space_safe_absolute_roots(self) -> None:
        root = Path(tempfile.gettempdir()) / "Package With Spaces"
        layout = LAUNCHER.FriendPackageLayout.from_package_root(root)
        command = LAUNCHER.backend_command(
            Path("/tmp/Python Runtime/python.exe"), layout, Path("/tmp/User Data/AIFren")
        )
        self.assertEqual("/tmp/Python Runtime/python.exe", command[0])
        self.assertEqual("-I", command[1])
        self.assertEqual("-c", command[2])
        self.assertIn("aifren.backend_host", command[3])
        self.assertEqual(str(layout.resource_root), command[4])
        self.assertEqual(str(layout.resource_root), command[6])
        self.assertEqual("/tmp/User Data/AIFren", command[8])
        self.assertEqual(str(layout.seed_data_root), command[10])
        self.assertNotIn("shell", " ".join(command).lower())

    def test_package_rejects_older_player_before_it_can_use_host_preferences(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            layout = LAUNCHER.FriendPackageLayout.from_package_root(root)
            for path in (layout.player, layout.backend, layout.checker):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"synthetic")
            layout.seed_data_root.mkdir()
            assembly = root / "AIFrenPoc_Data/Managed/AIFren.UnityPoc.dll"
            assembly.parent.mkdir(parents=True)
            assembly.write_bytes(b"old client")
            with self.assertRaisesRegex(RuntimeError, "isolated persistent preferences"):
                layout.validate()
            assembly.write_bytes("-aifren-preferences-file".encode("utf-16le"))
            with self.assertRaisesRegex(RuntimeError, "isolated persistent preferences"):
                layout.validate()
            assembly.write_bytes(assembly.read_bytes() + b"get_ManagedDataRoot")
            layout.validate()

    def test_deep_unicode_phoneme_resources_are_private_temporary_and_reused(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / ("Synthetic voice path " + "音" * 45)
            data = root / "runtime/python/espeak-ng-data"
            data.mkdir(parents=True)
            (data / "phontab").write_bytes(b"synthetic phonemes")
            (data / "lang").mkdir()
            (data / "lang/en").write_bytes(b"synthetic language")
            loader = SimpleNamespace(get_data_path=lambda: str(data))
            with mock.patch.dict(sys.modules, espeakng_loader=loader), \
                    mock.patch.object(LAUNCHER, "APPLICATION_ROOT", root / "runtime/app"), \
                    mock.patch.object(LAUNCHER, "_phonemizer_data", None):
                temporary = LAUNCHER.prepare_packaged_phonemizer()
                destination = Path(loader.get_data_path())
                self.assertLessEqual(len(str(destination).encode("utf-8")), 128)
                self.assertEqual(b"synthetic phonemes", (destination / "phontab").read_bytes())
                self.assertEqual(b"synthetic language", (destination / "lang/en").read_bytes())
                self.assertIs(temporary, LAUNCHER.prepare_packaged_phonemizer())
                self.assertEqual(b"synthetic phonemes", (data / "phontab").read_bytes())
                temporary.cleanup()
                self.assertFalse(destination.exists())

    def test_phonemizer_cannot_escape_bundle_or_follow_resource_links(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "package"
            source = root / "outside"
            source.mkdir()
            loader = SimpleNamespace(get_data_path=lambda: str(source))
            with mock.patch.dict(sys.modules, espeakng_loader=loader), \
                    mock.patch.object(LAUNCHER, "APPLICATION_ROOT", package / "runtime/app"), \
                    mock.patch.object(LAUNCHER, "_phonemizer_data", None):
                with self.assertRaisesRegex(RuntimeError, "outside this bundle"):
                    LAUNCHER.prepare_packaged_phonemizer()
                package.mkdir()
                link = package / "linked-data"
                link.symlink_to(source, target_is_directory=True)
                loader.get_data_path = lambda: str(link)
                with self.assertRaisesRegex(RuntimeError, "cannot follow links"):
                    LAUNCHER.prepare_packaged_phonemizer()

    def test_existing_windows_developer_powershell_test_path_remains_available(self) -> None:
        ensure = ROOT / "scripts" / "ensure_aifren_backend.ps1"
        stop = ROOT / "scripts" / "stop_aifren_backend.ps1"
        self.assertTrue(ensure.is_file())
        self.assertTrue(stop.is_file())
        self.assertTrue((ROOT / "scripts" / "run_aifren_test.bat").is_file())
        self.assertNotIn("Test-LegacyAIFrenProtocol", ensure.read_text(encoding="utf-8"))
        self.assertIn("$expectedBackend", ensure.read_text(encoding="utf-8"))
        self.assertIn("$expectedBackend", stop.read_text(encoding="utf-8"))

    def test_ready_nonce_proven_backend_is_recovered_without_spawning_a_duplicate(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            layout = LAUNCHER.FriendPackageLayout.from_package_root(root / "AIFren")
            data = root / "User Data"
            token = "40fd381e-cd5a-4a95-b46a-cde05539e492"
            LAUNCHER._write_descriptor(data, layout, token, 1234)
            ready = subprocess.CompletedProcess([], 0, "aifren\n", "")
            owned = subprocess.CompletedProcess([], 0, "owned\n", "")

            with mock.patch.object(LAUNCHER, "_checker", return_value=owned) as checker, \
                    mock.patch.object(LAUNCHER, "_backend_port_is_open", return_value=True), \
                    mock.patch.object(LAUNCHER.subprocess, "Popen") as popen:
                authority = LAUNCHER.ensure_backend(Path("python.exe"), layout, data)

            self.assertTrue(authority.owned)
            self.assertTrue(authority.recovered)
            popen.assert_not_called()
            self.assertEqual(1, checker.call_count)

    def test_compatible_external_backend_is_never_reused_claimed_or_stopped(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            layout = LAUNCHER.FriendPackageLayout.from_package_root(root / "AIFren")
            ready = subprocess.CompletedProcess([], 0, "aifren\n", "")
            with mock.patch.object(LAUNCHER, "_checker", return_value=ready) as checker, \
                    mock.patch.object(LAUNCHER, "_backend_port_is_open", return_value=True):
                with self.assertRaisesRegex(RuntimeError, "not be stopped or reused"):
                    LAUNCHER.ensure_backend(Path("python.exe"), layout, root / "data")
            self.assertEqual(0, checker.call_count)

    def test_package_discards_developer_environment_without_changing_host(self):
        layout = LAUNCHER.FriendPackageLayout.from_package_root(Path(tempfile.gettempdir()) / "Synthetic bundle")
        values = {"AIFREN_DATA_ROOT": "/unrelated/data", "AIFREN_LOCAL_LLM_MODEL_DIR": "/unrelated/models",
                  "HF_HUB_CACHE": "/unrelated/cache", "HF_ASSETS_CACHE": "/unrelated/assets",
                  "HF_TOKEN_PATH": "/unrelated/token", "HF_XET_CACHE": "/unrelated/xet",
                  "PYTHONPATH": "/unrelated/code", "HF_HOME": "/unrelated/cache", "HOME": "/synthetic/home"}
        with mock.patch.dict(LAUNCHER.os.environ, values, clear=True):
            env = LAUNCHER.package_environment(layout, Path("/synthetic/data"))
            self.assertEqual(env["AIFREN_DATA_ROOT"], "/synthetic/data")
            self.assertNotIn("PYTHONPATH", env)
            self.assertNotIn("AIFREN_LOCAL_LLM_MODEL_DIR", env)
            for key in ("HF_HUB_CACHE", "HF_ASSETS_CACHE", "HF_TOKEN_PATH", "HF_XET_CACHE"):
                self.assertNotIn(key, env)
            self.assertEqual(env["HF_HUB_DISABLE_IMPLICIT_TOKEN"], "1")
            self.assertEqual(env["HF_HOME"], "/synthetic/data/model-cache")
            self.assertEqual(env["HOME"], values["HOME"])
            self.assertEqual(dict(LAUNCHER.os.environ), values)

    def test_new_backend_is_not_authoritative_until_protocol_readiness(self) -> None:
        class Process:
            pid = 4321
            returncode = None
            def poll(self): return None

        with TemporaryDirectory() as directory:
            root = Path(directory)
            layout = LAUNCHER.FriendPackageLayout.from_package_root(root / "AIFren")
            data = root / "User Data"
            absent = subprocess.CompletedProcess([], 1, "", "offline")
            ready = subprocess.CompletedProcess([], 0, "ready", "")
            with mock.patch.object(LAUNCHER, "_checker", side_effect=(absent, absent, ready)) as checker, \
                    mock.patch.object(LAUNCHER, "_backend_port_is_open", return_value=False), \
                    mock.patch.object(LAUNCHER.subprocess, "Popen", return_value=Process()):
                authority = LAUNCHER.ensure_backend(
                    Path("python.exe"), layout, data, ready_timeout=1,
                )
            self.assertTrue(authority.owned)
            self.assertFalse(authority.recovered)
            self.assertEqual(3, checker.call_count)

    def test_unready_existing_listener_never_causes_a_competing_spawn(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            layout = LAUNCHER.FriendPackageLayout.from_package_root(root / "AIFren")
            unavailable = subprocess.CompletedProcess([], 1, "", "busy")
            with mock.patch.object(LAUNCHER, "_checker", return_value=unavailable), \
                    mock.patch.object(LAUNCHER, "_backend_port_is_open", return_value=True), \
                    mock.patch.object(LAUNCHER.subprocess, "Popen") as popen:
                with self.assertRaisesRegex(RuntimeError, "no competing backend"):
                    LAUNCHER.ensure_backend(Path("python.exe"), layout, root / "data")
            popen.assert_not_called()

    def test_windows_local_model_launch_uses_creation_flags_not_posix_sessions(self) -> None:
        source = (ROOT / 'aifren/runtime/local_model_runtime.py').read_text(encoding="utf-8")
        self.assertIn('self._process_platform == "nt"', source)
        self.assertIn('process_options["creationflags"]', source)
        self.assertIn('process_options["start_new_session"]', source)
        setup = (ROOT / "setup_aifren_runtime.bat").read_text(encoding="utf-8")
        self.assertIn('llama-cpp-python[server]==0.3.35', setup)
        self.assertIn('/whl/cpu', setup)


if __name__ == "__main__":
    unittest.main()
