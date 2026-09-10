from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from runtime_layout import (
    initialize_data_root,
    packaged_user_data_root,
    resolve_runtime_roots,
    windows_user_data_root,
)


class RuntimeLayoutTests(unittest.TestCase):
    def test_windows_data_root_uses_local_app_data_with_spaces(self) -> None:
        root = windows_user_data_root({"LOCALAPPDATA": r"C:\Users\Test User\AppData\Local"})
        self.assertEqual(Path(r"C:\Users\Test User\AppData\Local") / "AIFren", root)

    def test_packaged_data_override_is_independent_of_platform_default(self) -> None:
        result = packaged_user_data_root(
            platform="win32",
            environment={
                "LOCALAPPDATA": r"C:\Ignored",
                "AIFREN_DATA_ROOT": r"D:\AIFren QA\User Data",
            },
        )
        self.assertEqual(Path(r"D:\AIFren QA\User Data"), result)

    def test_legacy_direct_layout_keeps_linux_resource_and_data_root_together(self) -> None:
        with TemporaryDirectory() as directory:
            resources, data, seed = resolve_runtime_roots(directory)
            self.assertEqual(Path(directory), resources)
            self.assertEqual(resources, data)
            self.assertIsNone(seed)

    def test_explicit_packaged_roots_do_not_depend_on_current_working_directory(self) -> None:
        with TemporaryDirectory(prefix="AIFren package with spaces ") as directory:
            root = Path(directory)
            resources, data, seed = resolve_runtime_roots(
                root / "installed files",
                resource_root=root / "installed files",
                data_root=root / "user data",
                seed_data_root=root / "installed files" / "seed data",
            )
            self.assertEqual(root / "installed files", resources)
            self.assertEqual(root / "user data", data)
            self.assertEqual(root / "installed files" / "seed data", seed)

    def test_development_staged_root_is_ephemeral_gated_and_resource_separate(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / "staged data"
            staged.mkdir()
            with mock.patch.dict("os.environ", {
                "AIFREN_ENABLE_DEVELOPMENT_QA": "1",
                "AIFREN_DEVELOPMENT_STAGED_DATA_ROOT": str(staged),
            }, clear=False):
                resources, data, seed = resolve_runtime_roots(
                    root / "resources",
                )
            self.assertEqual(root / "resources", resources)
            self.assertEqual(staged, data)
            self.assertIsNone(seed)

            with mock.patch.dict("os.environ", {
                "AIFREN_ENABLE_DEVELOPMENT_QA": "0",
                "AIFREN_DEVELOPMENT_STAGED_DATA_ROOT": str(staged),
            }, clear=False):
                with self.assertRaisesRegex(RuntimeError, "Development QA gate"):
                    resolve_runtime_roots(root / "resources")

            resources, data, _ = resolve_runtime_roots(root / "resources")
            self.assertEqual(resources, data)

    def test_seed_initialization_never_rewrites_existing_canonical_data(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            seed = root / "package" / "seed_data"
            data = root / "user data"
            (seed / "characters" / "default").mkdir(parents=True)
            (seed / "conversation.json").write_text("[]\n", encoding="utf-8")
            (seed / "characters" / "default" / "personality.md").write_text(
                "seed personality\n", encoding="utf-8"
            )
            data.mkdir()
            (data / "conversation.json").write_text('[{"durable": true}]\n', encoding="utf-8")

            initialize_data_root(data, seed)

            self.assertEqual(
                '[{"durable": true}]\n',
                (data / "conversation.json").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "seed personality\n",
                (data / "characters" / "default" / "personality.md").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
