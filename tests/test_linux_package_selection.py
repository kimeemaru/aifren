from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("linux_package_selector", ROOT / "scripts/package_linux.py")
PACKAGE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PACKAGE)


class LinuxPackageSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aifren-package-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.staging = self.root / "staging"
        self.output = self.root / "output"
        for name in PACKAGE.application_inputs():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# synthetic allowed input\n")
        self.inputs = []
        for name in ("AIFrenPoc.x86_64", "UnityPlayer.so", "AIFrenPoc_Data/sharedassets0.assets",
                     "runtime/python/bin/python", "runtime/app/models/kokoro-82m/config.json"):
            path = self.staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"allowed resource")
            path.chmod(0o755)
            self.inputs.append({"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})

    def compose(self):
        return PACKAGE.compose_linux_package(self.source, self.staging, self.output, self.inputs)

    def test_production_selection_ignores_all_unlisted_private_inputs_and_writes_clean_seed(self):
        for name in (".aifren_local_settings.json", "config_secret.py", "conversation.json",
                     "data/store.sqlite3", "logs/diagnostic.json", "cache/private.json",
                     "characters/private-fixture/personality.md", "private-assets/voice.wav",
                     "scripts/new_unapproved.py", "memory/private_settings.py"):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("SENTINEL_CREDENTIAL_OR_PRIVATE_CONTENT")
        (self.staging / "runtime/python/private.json").write_text("SENTINEL_CREDENTIAL_OR_PRIVATE_CONTENT")
        self.compose()
        self.assertTrue((self.output / "runtime/app/backend_host.py").is_file())
        self.assertTrue((self.output / "runtime/app/models/kokoro-82m/config.json").is_file())
        for path in self.output.rglob("*"):
            if path.is_file():
                self.assertNotIn(b"SENTINEL", path.read_bytes())
        seed = self.output / "runtime/app/seed_data"
        self.assertEqual([], json.loads((seed / "conversation.json").read_text()))
        self.assertFalse((self.output / "runtime/app/.aifren_local_settings.json").exists())
        subprocess.run(["bash", "-n", str(self.output / "run-aifren.sh")], check=True)

    def test_symlink_source_file_or_parent_rejected_before_copy(self):
        for relative in ("backend_host.py", "conversation"):
            with self.subTest(relative=relative):
                original = self.source / relative
                saved = self.root / ("saved-" + relative)
                original.rename(saved)
                original.symlink_to(saved, target_is_directory=saved.is_dir())
                with self.assertRaisesRegex(ValueError, "symlink"):
                    self.compose()
                self.assertFalse(self.output.exists())
                original.unlink()
                saved.rename(original)

    def test_staged_path_escape_symlink_private_destination_and_hash_are_rejected(self):
        for name in ("../escape", "/tmp/escape", "runtime/python/.env", "runtime/app/.aifren_local_settings.json",
                     "runtime/app/models/data/private.db", "runtime/python/../escape"):
            with self.subTest(name=name):
                self.inputs.append({"path": name, "sha256": "0" * 64})
                with self.assertRaises(ValueError):
                    self.compose()
                self.inputs.pop()
                self.assertFalse(self.output.exists())
        path = self.staging / self.inputs[0]["path"]
        path.unlink()
        path.symlink_to(self.source / "backend_host.py")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.compose()
        path.unlink()
        path.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "digest"):
            self.compose()

    def test_fresh_output_and_unique_destinations_required(self):
        self.inputs.append(self.inputs[0])
        with self.assertRaises(ValueError):
            self.compose()
        self.inputs.pop()
        self.output.mkdir()
        with self.assertRaisesRegex(ValueError, "fresh"):
            self.compose()

    def test_manifest_resolves_only_reviewed_source_files(self):
        for name in PACKAGE.application_inputs():
            self.assertTrue((ROOT / name).is_file(), name)
        shell = (ROOT / "scripts/package_friend_linux.sh").read_text()
        self.assertIn("scripts/package_linux.py", shell)
        self.assertNotIn("rsync", shell)
        self.assertNotIn("INCLUDE_LOCAL_PRESENTATION", shell)

    def test_selected_application_sources_include_their_local_import_dependencies(self):
        # Inspect only approved application source, never discover runtime data.
        selected = set(PACKAGE.application_inputs())
        allowed_roots = {"benchmarks", "scripts", "conversation", "memory", "memory_v2_store",
                         "llm", "tts", "stt", "voice"}
        for name in sorted(selected):
            if not name.endswith(".py"):
                continue
            for node in ast.walk(ast.parse((ROOT / name).read_text())):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    base = node.module or ""
                    if node.level:
                        prefix = Path(name).parts[:-node.level]
                        base = ".".join((*prefix, base)).strip(".")
                    modules = [base, *(base + "." + alias.name for alias in node.names)]
                for module in modules:
                    parts = module.split(".")
                    if not all(part.isidentifier() for part in parts):
                        continue
                    if len(parts) > 1 and parts[0] not in allowed_roots:
                        continue
                    if parts[0] == "config_secret":
                        continue  # Optional credentials must never be packaged.
                    candidates = ["/".join(parts) + ".py"]
                    candidates += ["/".join(parts[:end]) + "/__init__.py"
                                   for end in range(1, len(parts) + 1)]
                    for candidate in candidates:
                        if (ROOT / candidate).is_file():
                            self.assertIn(candidate, selected, f"{name} imports {module}")


if __name__ == "__main__":
    unittest.main()
