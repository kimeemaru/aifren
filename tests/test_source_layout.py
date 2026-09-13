"""Code location is independent of runtime data, caller CWD and legacy modules."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from aifren.runtime.runtime_layout import source_root

ROOT = Path(__file__).resolve().parents[1]


class SourceLayoutTests(unittest.TestCase):
    def test_one_source_root_and_no_developer_imports_in_application(self):
        self.assertEqual(ROOT, source_root())
        for source in (ROOT / "aifren").rglob("*.py"):
            tree = ast.parse(source.read_text())
            for node in ast.walk(tree):
                modules = ([node.module] if isinstance(node, ast.ImportFrom) and not node.level
                           else [a.name for a in node.names] if isinstance(node, ast.Import) else [])
                for module in modules:
                    self.assertNotIn((module or "").split(".")[0], {"tools", "tests", "benchmarks"}, str(source))

    def test_stored_compactor_identity_survives_provider_code_relocation(self):
        from aifren.continuity.memory_v2_episode_compaction import EpisodeCompactor, compactor_identity
        from test_memory_v2_episode_compaction import EpisodeCompactionFoundationTests, _Provider, exchanges

        fixture = EpisodeCompactionFoundationTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        provider_type = type("SyntheticProvider", (_Provider,), {"__module__": "llm.openai_compatible"})
        provider = provider_type()
        messages = exchanges(100)
        old_identity = compactor_identity(provider)
        fixture.cache.rebuild(messages, EpisodeCompactor(provider))
        stored = fixture.lower_snapshot()
        before = fixture.cache.select_for_context(messages)
        self.assertIsNotNone(before)
        calls = provider.calls
        # A subsequent explicit rebuild records the real new provider location.
        # Existing derived records validate their stored identity, not today's class path.
        provider_type.__module__ = "aifren.llm.openai_compatible"
        self.assertNotEqual(old_identity, compactor_identity(provider))
        after = fixture.cache.select_for_context(messages)
        self.assertIsNotNone(after)
        self.assertEqual(before.context_block, after.context_block)
        self.assertEqual(stored, fixture.lower_snapshot())
        self.assertEqual(calls, provider.calls, "Reading continuity must not regenerate it")
        messages[0] = dict(messages[0], content="Changed synthetic source")
        self.assertIsNone(fixture.cache.select_for_context(messages), "Source validation remains strict")

    def test_supported_commands_work_outside_checkout_without_initializing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            legacy = root / "legacy"
            legacy.mkdir()
            for name in ("assistant_service", "config", "character_registry", "runtime_layout"):
                (legacy / (name + ".py")).write_text("raise RuntimeError('Wrong source implementation')\n")
            environment = dict(os.environ, PYTHONPATH=str(legacy),
                               AIFREN_DATA_ROOT=str(data), AIFREN_RESOURCE_ROOT=str(root / "resources"),
                               PYNPUT_BACKEND="dummy", PYTHONDONTWRITEBYTECODE="1")
            for command in ("backend_host.py", "scripts/launch_friend.py",
                            "scripts/aifren_dev_launcher_linux.py",
                            "scripts/index_memory_v2_historical_evidence.py"):
                with self.subTest(command=command):
                    result = subprocess.run([sys.executable, "-B", str(ROOT / command), "--help"],
                                            cwd=root, env=environment, capture_output=True, text=True, timeout=60)
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertIn("usage:", result.stdout.lower())
            self.assertEqual([], list(data.iterdir()), "Help/import must not initialize a timeline")

    def test_package_entry_uses_this_checkout_with_separate_resources(self):
        # Import the wrapper through its supported absolute script directory,
        # then change CWD before importing the remaining provider owners.
        code = """
import json, os, runpy, sys
from pathlib import Path
source = Path(sys.argv[1])
sys.path.insert(0, str(source))
namespace = runpy.run_path(str(source / 'backend_host.py'), run_name='entry_probe')
from aifren import backend_host, assistant_service
from aifren.runtime import runtime_layout, config
from aifren.memory import embeddings
from aifren.dialogue import automatic_expression
print(json.dumps({'modules': [m.__file__ for m in (backend_host, assistant_service, runtime_layout, config, embeddings, automatic_expression)],
                  'source': str(runtime_layout.source_root()),
                  'resources': str(runtime_layout.resource_path('models/example')),
                  'main': namespace['main'].__module__}))
"""
        with tempfile.TemporaryDirectory() as directory:
            environment = dict(os.environ, PYTHONPATH="", AIFREN_RESOURCE_ROOT=directory,
                               AIFREN_DATA_ROOT=directory, PYNPUT_BACKEND="dummy")
            result = subprocess.run([sys.executable, "-B", "-c", code, str(ROOT)],
                                    cwd=directory, env=environment, capture_output=True, text=True, timeout=60, check=True)
            values = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertEqual(str(ROOT), values["source"])
            self.assertEqual("aifren.backend_host", values["main"])
            self.assertTrue(all(Path(p).is_relative_to(ROOT / "aifren") for p in values["modules"]))
            self.assertEqual(str(Path(directory) / "models/example"), values["resources"])
            self.assertEqual([], list(Path(directory).iterdir()))


if __name__ == "__main__":
    unittest.main()
