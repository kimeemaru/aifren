import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from aifren.runtime.local_model_runtime import LocalModelRuntime


class _Response:
    def __init__(self, payload): self.payload = json.dumps(payload).encode("utf-8")
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self): return self.payload


class _Process:
    def __init__(self):
        self.returncode = None
        self.stdout = io.StringIO("")
        self.terminated = False
    def poll(self): return self.returncode
    def terminate(self): self.terminated = True; self.returncode = 0
    def wait(self, timeout=None): return self.returncode
    def kill(self): self.returncode = -9


class _PidProcess(_Process):
    def __init__(self, pid):
        super().__init__()
        self.pid = pid


class _SlowProcess(_Process):
    def __init__(self): super().__init__(); self.kill_calls = 0
    def terminate(self): self.terminated = True
    def wait(self, timeout=None):
        if self.returncode is None and timeout == 10:
            raise __import__("subprocess").TimeoutExpired("llama", timeout)
        return self.returncode
    def kill(self): self.kill_calls += 1; super().kill()


class LocalModelRuntimeTests(unittest.TestCase):
    def _runtime(self, directory, *, models=(), process=None, gpu=False):
        root = Path(directory)
        model_dir = root / "models" / "llama"; model_dir.mkdir(parents=True)
        for name in models:
            (model_dir / name).write_bytes(b"GGUF")
        process = process or _Process()
        state = {"ready": False}
        def open_url(_request, timeout):
            if not state["ready"]:
                raise OSError("offline")
            return _Response({"data": [{"id": "first.gguf"}, {"id": "second.gguf"}]})
        def factory(*_args, **_kwargs):
            state["ready"] = True
            return process
        return LocalModelRuntime(root, model_directory=model_dir, process_factory=factory,
            urlopen=open_url, gpu_offload_probe=lambda: gpu, readiness_timeout_seconds=.25), process, state

    def _recoverable_runtime(self, root, identities, processes, signals, *, advertised, probe_enabled, factory_calls):
        model_dir = Path(root) / "models" / "llama"
        model_dir.mkdir(parents=True, exist_ok=True)
        for name in advertised:
            (model_dir / name).write_bytes(b"GGUF")

        def identity_reader(pid):
            return identities.get(pid)

        def process_factory(command, **kwargs):
            factory_calls.append(tuple(command))
            pid = 4100 + len(factory_calls)
            process = _PidProcess(pid)
            processes[pid] = process
            identities[pid] = {
                "pid": pid, "process_group_id": pid, "session_id": pid,
                "start_ticks": 9000 + pid, "boot_id": "synthetic-boot",
                "argv": tuple(command),
                "owner_token": kwargs["env"]["AIFREN_MANAGED_RUNTIME_TOKEN"],
            }
            probe_enabled["value"] = True
            return process

        def open_url(_request, timeout):
            if not probe_enabled["value"]:
                raise OSError("offline")
            return _Response({"data": [{"id": value} for value in advertised]})

        def signal_group(pid, signal_value):
            signals.append((pid, signal_value))
            process = processes[pid]
            process.returncode = 0 if signal_value == __import__("signal").SIGTERM else -9
            identities.pop(pid, None)
            probe_enabled["value"] = False

        return LocalModelRuntime(
            root, model_directory=model_dir, process_factory=process_factory,
            urlopen=open_url, gpu_offload_probe=lambda: False,
            readiness_timeout_seconds=.25, process_identity_reader=identity_reader,
            recovered_process_factory=lambda pid, _reader: processes[pid],
            process_group_signaler=signal_group,
        )

    def test_discovers_multiple_generic_gguf_models_when_server_is_off(self):
        with TemporaryDirectory() as directory:
            runtime, _, _ = self._runtime(directory, models=(
                "Qwen3.5-0.8B-M-TS-Q4_K_M.gguf", "Qwen_Qwen3.5-4B-Q4_K_M.gguf", "future-model.gguf",
            ))
            found = runtime.discover_installed()
            self.assertEqual(3, len(found))
            self.assertIn("Qwen3.5-0.8B-M-TS-Q4_K_M.gguf", {item.identifier for item in found})
            self.assertTrue(any(item.display_name.endswith("Q4_K_M") for item in found))
            self.assertEqual("off", runtime.snapshot()["state"])

    def test_start_marks_ready_only_after_endpoint_readiness(self):
        with TemporaryDirectory() as directory:
            runtime, _, _ = self._runtime(directory, models=("first.gguf",))
            result = runtime.start(endpoint="http://127.0.0.1:8000/v1", selected_model="first.gguf")
            self.assertEqual("ready", result["state"])
            self.assertEqual("managed", result["ownership"])
            self.assertEqual("first.gguf", result["active_model"])

    def test_missing_model_is_truthful_and_never_substitutes_another(self):
        with TemporaryDirectory() as directory:
            runtime, _, _ = self._runtime(directory, models=("first.gguf",))
            result = runtime.start(endpoint="http://127.0.0.1:8000/v1", selected_model="missing.gguf")
            self.assertEqual("error", result["state"])
            self.assertIn("missing", result["error"])
            self.assertEqual("", result["active_model"])

    def test_external_endpoint_is_detected_but_never_owned_or_stopped(self):
        with TemporaryDirectory() as directory:
            calls = []
            def open_url(_request, timeout):
                calls.append(timeout)
                return _Response({"data": [{"id": "external-model"}]})
            runtime = LocalModelRuntime(directory, model_directory=Path(directory) / "models", urlopen=open_url)
            result = runtime.start(endpoint="http://127.0.0.1:8000/v1", selected_model="anything")
            self.assertEqual("external", result["ownership"])
            stopped = runtime.stop()
            self.assertEqual("external", stopped["ownership"])
            self.assertEqual("mismatch", stopped["state"])
            self.assertTrue(calls)

    def test_external_model_mismatch_is_truthful_and_not_generatable_as_selected(self):
        with TemporaryDirectory() as directory:
            runtime = LocalModelRuntime(
                directory, model_directory=Path(directory) / "models",
                urlopen=lambda *_args, **_kwargs: _Response({"data": [{"id": "small.gguf"}]}),
            )
            result = runtime.refresh_external("http://127.0.0.1:8000/v1", selected_model="large.gguf")
            self.assertEqual("mismatch", result["state"])
            self.assertEqual("external", result["ownership"])
            self.assertEqual("small.gguf", result["active_model"])
            self.assertIn("different model", result["error"])

    def test_owned_model_stop_only_terminates_the_owned_process(self):
        with TemporaryDirectory() as directory:
            runtime, process, _ = self._runtime(directory, models=("first.gguf",))
            runtime.start(endpoint="http://127.0.0.1:8000/v1", selected_model="first.gguf")
            result = runtime.stop()
            self.assertTrue(process.terminated)
            self.assertEqual("off", result["state"])

    def test_switching_selected_models_restarts_only_the_owned_process(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); model_dir = root / "models"; model_dir.mkdir()
            for name in ("small.gguf", "larger.gguf"): (model_dir / name).write_bytes(b"GGUF")
            processes, ready = [], {"value": False}
            def factory(*_args, **_kwargs):
                ready["value"] = True
                process = _Process(); processes.append(process); return process
            def open_url(_request, timeout):
                if not ready["value"]: raise OSError("offline")
                return _Response({"data": [{"id": "small.gguf"}, {"id": "larger.gguf"}]})
            runtime = LocalModelRuntime(root, model_directory=model_dir, process_factory=factory,
                urlopen=open_url, readiness_timeout_seconds=.25)
            first = runtime.start(endpoint="http://127.0.0.1:8000/v1", selected_model="small.gguf")
            second = runtime.start(endpoint="http://127.0.0.1:8000/v1", selected_model="larger.gguf")
            self.assertEqual("small.gguf", first["active_model"])
            self.assertEqual("larger.gguf", second["active_model"])
            self.assertEqual(2, len(processes))
            self.assertTrue(processes[0].terminated)
            self.assertFalse(processes[1].terminated)

            runtime.stop()
            self.assertTrue(processes[1].terminated)
            self.assertEqual("off", runtime.snapshot()["state"])

    def test_unexpected_owned_process_exit_becomes_recoverable_error(self):
        with TemporaryDirectory() as directory:
            runtime, process, _ = self._runtime(directory, models=("first.gguf",))
            runtime.start(endpoint="http://127.0.0.1:8000/v1", selected_model="first.gguf")
            process.returncode = 7
            result = runtime.snapshot(selected_model="first.gguf")
            self.assertEqual("error", result["state"])
            self.assertIn("exited", result["error"])

    def test_non_loopback_managed_endpoint_is_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); model_dir = root / "models"; model_dir.mkdir()
            (model_dir / "first.gguf").write_bytes(b"GGUF")
            runtime = LocalModelRuntime(root, model_directory=model_dir,
                urlopen=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")))
            result = runtime.start(endpoint="http://example.test/v1", selected_model="first.gguf")
            self.assertEqual("error", result["state"])
            self.assertIn("loopback", result["error"])

    def test_gpu_capability_requests_offload_but_reports_cuda_only_after_server_confirmation(self):
        with TemporaryDirectory() as directory:
            commands = []
            process = _Process()
            process.stdout = io.StringIO("load_tensors: offloading 29 layers to GPU\n")
            root = Path(directory); model_dir = root / "models"; model_dir.mkdir()
            (model_dir / "first.gguf").write_bytes(b"GGUF")
            ready = {"value": False}
            def factory(command, **_kwargs):
                commands.append(command); ready["value"] = True; return process
            def open_url(_request, timeout):
                if not ready["value"]: raise OSError("offline")
                return _Response({"data": [{"id": "first.gguf"}]})
            runtime = LocalModelRuntime(root, model_directory=model_dir, process_factory=factory,
                urlopen=open_url, gpu_offload_probe=lambda: True, readiness_timeout_seconds=.25)
            result = runtime.start(endpoint="http://127.0.0.1:8000/v1", selected_model="first.gguf")
            self.assertEqual("CUDA", result["compute"])
            self.assertEqual(["--n_gpu_layers", "-1"], commands[0][-2:])
            self.assertNotIn("--chat_format", commands[0])
            logits_index = commands[0].index("--logits_all")
            self.assertEqual("false", commands[0][logits_index + 1])
            template_index = commands[0].index("--chat_template_kwargs")
            self.assertEqual({"enable_thinking": False}, json.loads(commands[0][template_index + 1]))

    def test_per_token_cuda_graph_reuse_is_drained_but_not_forwarded_normally(self):
        with TemporaryDirectory() as directory:
            forwarded = []
            runtime = LocalModelRuntime(
                directory, model_directory=Path(directory) / "models", log=forwarded.append,
            )
            process = _Process()
            process.stdout = io.StringIO(
                "load_tensors: offloading 29 layers to GPU\n"
                "CUDA Graph id 303 reused\n"
                "llama_perf_context_print: prompt eval time = 12 ms\n"
            )

            runtime._process = process
            runtime._drain_output(process)

            self.assertEqual(2, len(forwarded))
            self.assertFalse(any("CUDA Graph" in line for line in forwarded))
            self.assertTrue(any("offloading" in line for line in forwarded))
            self.assertTrue(any("prompt eval" in line for line in forwarded))

    def test_verbose_process_logging_can_restore_cuda_graph_trace(self):
        with TemporaryDirectory() as directory:
            forwarded = []
            runtime = LocalModelRuntime(
                directory, model_directory=Path(directory) / "models", log=forwarded.append,
                verbose_process_logs=True,
            )
            process = _Process(); process.stdout = io.StringIO("CUDA Graph id 8 reused\n")

            runtime._process = process
            runtime._drain_output(process)

            self.assertEqual(1, len(forwarded))

    def test_cpu_fallback_does_not_request_gpu_layers(self):
        with TemporaryDirectory() as directory:
            commands = []
            root = Path(directory); model_dir = root / "models"; model_dir.mkdir()
            (model_dir / "first.gguf").write_bytes(b"GGUF")
            ready = {"value": False}
            def factory(command, **_kwargs):
                commands.append(command); ready["value"] = True; return _Process()
            def open_url(_request, timeout):
                if not ready["value"]: raise OSError("offline")
                return _Response({"data": [{"id": "first.gguf"}]})
            runtime = LocalModelRuntime(root, model_directory=model_dir, process_factory=factory,
                urlopen=open_url, gpu_offload_probe=lambda: False, readiness_timeout_seconds=.25)
            result = runtime.start(endpoint="http://127.0.0.1:8000/v1", selected_model="first.gguf")
            self.assertEqual("CPU", result["compute"])
            self.assertNotIn("--n_gpu_layers", commands[0])
            self.assertEqual(16384, LocalModelRuntime(root, model_directory=model_dir, context_size=16384).context_size)

    def test_windows_managed_launch_uses_native_creation_flags_and_direct_owned_stop(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); model_dir = root / "models"; model_dir.mkdir()
            (model_dir / "first.gguf").write_bytes(b"GGUF")
            captured, ready = [], {"value": False}
            process = _Process()

            def factory(command, **options):
                captured.append((command, options))
                ready["value"] = True
                return process

            def open_url(_request, timeout):
                if not ready["value"]:
                    raise OSError("offline")
                return _Response({"data": [{"id": "first.gguf"}]})

            runtime = LocalModelRuntime(
                root,
                model_directory=model_dir,
                process_factory=factory,
                urlopen=open_url,
                gpu_offload_probe=lambda: False,
                readiness_timeout_seconds=.25,
                process_platform="nt",
            )
            result = runtime.start(
                endpoint="http://127.0.0.1:8000/v1", selected_model="first.gguf",
            )

            self.assertEqual(("ready", "managed"), (result["state"], result["ownership"]))
            self.assertIn("creationflags", captured[0][1])
            self.assertNotIn("start_new_session", captured[0][1])
            self.assertFalse((root / ".aifren_managed_local_runtime.json").exists())
            runtime.stop()
            self.assertTrue(process.terminated)

    def test_slow_owned_shutdown_escalates_only_that_owned_process(self):
        with TemporaryDirectory() as directory:
            process = _SlowProcess()
            runtime, _, _ = self._runtime(directory, models=("first.gguf",), process=process)
            runtime.start(endpoint="http://127.0.0.1:8000/v1", selected_model="first.gguf")
            result = runtime.stop()
            self.assertEqual(1, process.kill_calls)
            self.assertEqual("off", result["state"])

    def test_backend_crash_next_runtime_recovers_same_healthy_owned_process(self):
        with TemporaryDirectory() as directory:
            identities, processes, signals, calls = {}, {}, [], []
            probe = {"value": False}
            first = self._recoverable_runtime(directory, identities, processes, signals,
                advertised=("first.gguf",), probe_enabled=probe, factory_calls=calls)
            started = first.start(endpoint="http://127.0.0.1:8000/v1", selected_model="first.gguf")
            self.assertEqual("managed", started["ownership"])
            self.assertTrue((Path(directory) / ".aifren_managed_local_runtime.json").is_file())

            # Simulate abrupt backend death: do not call first.stop().
            second = self._recoverable_runtime(directory, identities, processes, signals,
                advertised=("first.gguf",), probe_enabled=probe, factory_calls=calls)
            recovered = second.start(endpoint="http://127.0.0.1:8000/v1", selected_model="first.gguf")
            self.assertEqual(("ready", "managed", "recovered"),
                (recovered["state"], recovered["ownership"], recovered["compute"]))
            self.assertEqual(1, len(calls))
            self.assertEqual([], signals)
            second.stop()

    def test_repeated_start_on_live_owner_keeps_original_process_handle(self):
        with TemporaryDirectory() as directory:
            identities, processes, signals, calls = {}, {}, [], []
            probe = {"value": False}
            runtime = self._recoverable_runtime(directory, identities, processes, signals,
                advertised=("first.gguf",), probe_enabled=probe, factory_calls=calls)
            first = runtime.start(endpoint="http://127.0.0.1:8000/v1", selected_model="first.gguf")
            original_process = runtime._process
            second = runtime.start(endpoint="http://127.0.0.1:8000/v1", selected_model="first.gguf")
            self.assertEqual(("ready", "managed"), (first["state"], first["ownership"]))
            self.assertEqual(("ready", "managed"), (second["state"], second["ownership"]))
            self.assertIs(original_process, runtime._process)
            self.assertEqual(1, len(calls))
            self.assertEqual([], signals)
            runtime.stop()

    def test_unhealthy_owned_process_is_cleaned_then_restarted(self):
        with TemporaryDirectory() as directory:
            identities, processes, signals, calls = {}, {}, [], []
            probe = {"value": False}
            first = self._recoverable_runtime(directory, identities, processes, signals,
                advertised=("first.gguf",), probe_enabled=probe, factory_calls=calls)
            first.start(endpoint="http://127.0.0.1:8000/v1", selected_model="first.gguf")
            probe["value"] = False
            second = self._recoverable_runtime(directory, identities, processes, signals,
                advertised=("first.gguf",), probe_enabled=probe, factory_calls=calls)
            restarted = second.start(endpoint="http://127.0.0.1:8000/v1", selected_model="first.gguf")
            self.assertEqual("ready", restarted["state"])
            self.assertEqual(2, len(calls))
            self.assertEqual(1, len(signals))
            second.stop()

    def test_incompatible_owned_process_is_restarted_for_selected_model(self):
        with TemporaryDirectory() as directory:
            identities, processes, signals, calls = {}, {}, [], []
            probe = {"value": False}
            first = self._recoverable_runtime(directory, identities, processes, signals,
                advertised=("first.gguf", "second.gguf"), probe_enabled=probe, factory_calls=calls)
            first.start(endpoint="http://127.0.0.1:8000/v1", selected_model="first.gguf")
            second = self._recoverable_runtime(directory, identities, processes, signals,
                advertised=("first.gguf", "second.gguf"), probe_enabled=probe, factory_calls=calls)
            switched = second.start(endpoint="http://127.0.0.1:8000/v1", selected_model="second.gguf")
            self.assertEqual("second.gguf", switched["active_model"])
            self.assertEqual(2, len(calls))
            self.assertEqual(1, len(signals))
            second.stop()

    def test_invalid_stale_metadata_never_signals_unrelated_expected_port_server(self):
        with TemporaryDirectory() as directory:
            identities, processes, signals, calls = {}, {}, [], []
            probe = {"value": True}
            root = Path(directory)
            model_dir = root / "models" / "llama"; model_dir.mkdir(parents=True)
            (model_dir / "first.gguf").write_bytes(b"GGUF")
            metadata = {
                "version": 1, "pid": 4999, "process_group_id": 4999, "session_id": 4999,
                "start_ticks": 1, "boot_id": "synthetic-boot", "owner_token": "expected",
                "argv": ["python", "-m", "llama_cpp.server"],
            }
            (root / ".aifren_managed_local_runtime.json").write_text(json.dumps(metadata), encoding="utf-8")
            identities[4999] = {
                "pid": 4999, "process_group_id": 4999, "session_id": 4999,
                "start_ticks": 2, "boot_id": "synthetic-boot", "owner_token": "unrelated",
                "argv": tuple(metadata["argv"]),
            }
            runtime = LocalModelRuntime(
                root, model_directory=model_dir,
                urlopen=lambda *_args, **_kwargs: _Response({"data": [{"id": "first.gguf"}]}),
                process_identity_reader=lambda pid: identities.get(pid),
                process_group_signaler=lambda pid, sig: signals.append((pid, sig)),
            )
            result = runtime.start(endpoint="http://127.0.0.1:8000/v1", selected_model="first.gguf")
            self.assertEqual("external", result["ownership"])
            self.assertEqual([], signals)

    def test_recovered_owner_pid_reuse_is_rechecked_before_stop_signal(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            model_dir = root / "models" / "llama"
            model_dir.mkdir(parents=True)
            model_path = model_dir / "first.gguf"
            model_path.write_bytes(b"GGUF")
            pid = 4888
            token = "b71dbb55-b690-46e9-8996-b6bdb0c779e9"
            command = ("python", "-m", "llama_cpp.server", "--model", str(model_path))
            identity = {
                "pid": pid, "process_group_id": pid, "session_id": pid,
                "start_ticks": 12345, "boot_id": "synthetic-boot",
                "owner_token": token, "argv": command,
            }
            identities = {pid: identity}
            metadata = {
                "version": 1, **identity, "argv": list(command),
                "application_dir": str(root),
                "endpoint": "http://127.0.0.1:8000/v1",
                "selected_model": "first.gguf", "model_path": str(model_path),
                "context_size": 4096, "enable_thinking": False,
            }
            (root / ".aifren_managed_local_runtime.json").write_text(
                json.dumps(metadata), encoding="utf-8",
            )
            signals = []
            runtime = LocalModelRuntime(
                root, model_directory=model_dir,
                urlopen=lambda *_args, **_kwargs: _Response({"data": [{"id": "first.gguf"}]}),
                process_identity_reader=lambda candidate: identities.get(candidate),
                process_group_signaler=lambda candidate, value: signals.append((candidate, value)),
            )

            recovered = runtime.start(
                endpoint="http://127.0.0.1:8000/v1", selected_model="first.gguf",
            )
            self.assertEqual(("ready", "managed"), (recovered["state"], recovered["ownership"]))
            identities[pid] = {**identity, "start_ticks": 54321, "owner_token": "unrelated"}

            stopped = runtime.stop()
            self.assertEqual("off", stopped["state"])
            self.assertEqual([], signals)

    def test_normal_owned_stop_removes_recovery_metadata(self):
        with TemporaryDirectory() as directory:
            identities, processes, signals, calls = {}, {}, [], []
            probe = {"value": False}
            runtime = self._recoverable_runtime(directory, identities, processes, signals,
                advertised=("first.gguf",), probe_enabled=probe, factory_calls=calls)
            runtime.start(endpoint="http://127.0.0.1:8000/v1", selected_model="first.gguf")
            metadata = Path(directory) / ".aifren_managed_local_runtime.json"
            self.assertTrue(metadata.exists())
            runtime.stop()
            self.assertFalse(metadata.exists())
