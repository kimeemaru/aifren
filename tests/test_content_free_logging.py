from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from aifren.runtime.development_flight_recorder import ProcessOutputCapture
from aifren.memory.memory import Memory
from test_character_memory_v2_shadow import _Embedding
from test_windows_packaging import LAUNCHER as FRIEND
from test_linux_backend_launcher import LAUNCHER as LINUX


class ContentFreeLoggingTests(unittest.TestCase):
    def test_v1_observation_success_and_failure_do_not_print_memory_or_provider_errors(self):
        sentinel = "SYNTHETIC_PRIVATE_SENTINEL"
        class Provider:
            fail = False
            def generate(self, *_args):
                if self.fail:
                    raise RuntimeError(sentinel + " /private/path key=sentinel")
                return json.dumps([{"action": "ADD", "category": "preference", "importance": 7,
                                    "content": "The user likes " + sentinel}])
        with tempfile.TemporaryDirectory() as directory:
            provider = Provider()
            memory = Memory(provider, str(Path(directory) / "memories.json"), _Embedding())
            output = io.StringIO()
            with redirect_stdout(output):
                memory.process("I like " + sentinel, "Synthetic response")
                provider.fail = True
                memory.process("I like " + sentinel, "Synthetic response")
            self.assertEqual(1, len(memory.memories))
            self.assertNotIn(sentinel, output.getvalue())
            self.assertNotIn("/private", output.getvalue())
            self.assertIn("observation failed", output.getvalue())

    def test_prolonged_child_output_is_drained_without_content_or_unbounded_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = ProcessOutputCapture(development=True, directory=Path(directory))
            program = "import sys\nfor _ in range(4096): sys.stdout.buffer.write(b'SENTINEL'*1024)\n"
            child = subprocess.Popen([sys.executable, "-B", "-c", program], stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT)
            reader = threading.Thread(target=capture.drain, args=(child.stdout,), daemon=True)
            reader.start()
            try:
                self.assertEqual(0, child.wait(timeout=15))
                reader.join(timeout=3)
                self.assertFalse(reader.is_alive())
                self.assertEqual(4096 * 8192, capture.byte_count)
                self.assertLessEqual(len(capture.records), 64)
                files = list(Path(directory).iterdir())
                self.assertEqual(2, len(files))
                for file in files:
                    self.assertLessEqual(file.stat().st_size, 65536)
                    self.assertNotIn(b"SENTINEL", file.read_bytes())
                self.assertNotIn("SENTINEL", "".join(capture.take_records()))
                self.assertEqual((), capture.take_records())
            finally:
                if child.poll() is None:
                    child.kill()  # Only this finite synthetic child.
                    child.wait(timeout=3)

    def test_release_and_failed_optional_counter_sink_keep_draining(self):
        with tempfile.TemporaryDirectory() as directory:
            for development in (False, True):
                with self.subTest(development=development):
                    capture = ProcessOutputCapture(development=development, directory=Path(directory))
                    stream = io.BytesIO(b"SYNTHETIC_SECRET" * 10000)
                    with patch('aifren.runtime.development_flight_recorder.os.open', side_effect=OSError("PRIVATE")):
                        capture.drain(stream)
                    self.assertEqual(160000, capture.byte_count)
                    self.assertTrue(stream.closed)
                    self.assertLessEqual(len(capture.records), 64 if development else 0)
                    self.assertEqual([], list(Path(directory).iterdir()))

    def test_detached_linux_backend_keeps_no_raw_stdout_file(self):
        class Child:
            pid = 12345
            def poll(self): return None
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(LINUX, "listener_pid", return_value=None), \
                    patch.object(LINUX, "run_protocol_check", return_value=0), \
                    patch.object(LINUX.subprocess, "Popen", return_value=Child()) as spawn:
                LINUX.start_backend(Path(sys.executable), root, root / "owner.pid")
            self.assertEqual(subprocess.DEVNULL, spawn.call_args.kwargs["stdout"])
            self.assertEqual(subprocess.DEVNULL, spawn.call_args.kwargs["stderr"])
            self.assertFalse((root / "logs").exists())

    def test_friend_backend_lifetime_keeps_no_raw_capture_or_private_path_error(self):
        class Child:
            pid = 12345
            returncode = 9
            def poll(self): return self.returncode
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout = FRIEND.FriendPackageLayout.from_package_root(root / "package")
            with patch.object(FRIEND, "_checker", return_value=subprocess.CompletedProcess([], 1)), \
                    patch.object(FRIEND, "_backend_port_is_open", return_value=False), \
                    patch.object(FRIEND.subprocess, "Popen", return_value=Child()) as spawn:
                with self.assertRaises(RuntimeError) as raised:
                    FRIEND.ensure_backend(Path(sys.executable), layout, root / "data")
            self.assertEqual(subprocess.DEVNULL, spawn.call_args.kwargs["stdout"])
            self.assertNotIn(str(root), str(raised.exception))
            self.assertFalse((root / "data/logs").exists())

if __name__ == "__main__":
    unittest.main()
