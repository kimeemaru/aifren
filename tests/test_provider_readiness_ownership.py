"""Host/settings races with synthetic service storage and controlled runtimes."""

import asyncio
import io
import json
import os
from pathlib import Path
import threading
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from aifren.backend_host import AIFrenWebSocketHost
from benchmarks.active_state.production_session import ProductionSession, response_envelope
from aifren.runtime import model_settings
from aifren.runtime.local_model_runtime import LocalModelRuntime
import test_local_model_runtime as runtime_fixture


class RecordingClient:
    def __init__(self):
        self.messages = []

    async def send(self, payload):
        self.messages.append(json.loads(payload))

    async def close(self, **_kwargs):
        pass


class BlockedRuntime:
    """The worker deliberately ignores task cancellation; no process or I/O."""
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.returned = threading.Event()
        self.result = "error"
        self.state = {"state": "off", "ownership": "none", "active_model": ""}
        self.token = threading.Event()

    def reserve_operation(self):
        self.token.set()
        self.token = threading.Event()
        return self.token

    def cancel_operation(self, operation):
        if operation is self.token:
            operation.set()

    def snapshot(self, *, selected_model=""):
        return {**self.state, "selected_model": selected_model, "installed_models": []}

    def discover_installed(self):
        return ()

    def begin_start(self, *, selected_model, operation=None):
        self.state["state"] = "starting"

    def start(self, *, endpoint, selected_model, api_key, operation=None):
        entered, release, returned, result = self.entered, self.release, self.returned, self.result
        entered.set()
        try:
            if not release.wait(5):
                raise TimeoutError("Synthetic startup gate timed out")
            if result == "raise":
                raise RuntimeError("fake-secret /synthetic-private-path")
            state = {"state": result, "ownership": "managed", "active_model": selected_model}
            if operation is self.token and not operation.is_set():
                self.state = state
            return state
        finally:
            returned.set()

    def stop(self, *, operation=None):
        if operation is not None and (operation is not self.token or operation.is_set()):
            return {"state": "superseded"}
        self.state = {"state": "off", "ownership": "none", "active_model": ""}
        return self.snapshot()

    def refresh_external(self, endpoint, api_key, *, selected_model, operation=None):
        return self.start(endpoint=endpoint, selected_model=selected_model, api_key=api_key, operation=operation)


class ProviderReadinessOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for name in tuple(os.environ):
            if name.startswith("AIFREN_"):
                del os.environ[name]
        self.addCleanup(os.chdir, Path.cwd())
        self.session = ProductionSession(self.id())
        self.addCleanup(self.session.close)
        self.service = self.session.service
        self.session.turn("I'm waiting for my GPU to arrive.", response_envelope("I'll keep that in mind."))
        self.session.now += timedelta(hours=8)
        self.service._response_generator = None
        self.runtime = BlockedRuntime()
        self.addCleanup(self.runtime.release.set)
        self.host = AIFrenWebSocketHost(
            service=self.service, application_dir=self.session.root, port=0,
            local_model_runtime=self.runtime, proactive_startup_grace_seconds=0,
        )
        self.created = []

        def provider():
            settings = model_settings.get_model_settings()
            value = SimpleNamespace(
                is_available=True, mode=settings["mode"], model=settings["local_model"],
                generate=Mock(return_value=response_envelope("Hello again.")),
            )
            self.created.append(value)
            return value

        factory = patch('aifren.llm.llm.create_llm', side_effect=provider)
        factory.start()
        self.addCleanup(factory.stop)
        await self.host.start()
        self.addAsyncCleanup(self.host.stop)
        self.client = RecordingClient()
        self.host._client = self.client

    async def command(self, command, **fields):
        await asyncio.wait_for(self.host._handle_command(
            self.client, json.dumps({"command": command, **fields}),
        ), 3)

    async def local_start(self):
        await self.command("set_model_settings", mode="local", local_model="A.gguf",
                           local_endpoint="http://127.0.0.1:8000/v1")
        await self.command("start_local_model")
        self.assertTrue(await asyncio.to_thread(self.runtime.entered.wait, 3))
        return next(iter(self.host._local_model_tasks))

    async def online(self):
        await self.command("set_model_settings", mode="online", provider="openai_compatible",
                           online_model="synthetic-online", online_base_url="https://example.invalid/v1",
                           api_key="synthetic-key")
        self.service.report_model_runtime_available()
        return self.service.llm

    async def late_local(self, result):
        self.runtime.result = result
        task = await self.local_start()
        replacement = await self.online()
        count = len(self.client.messages)
        self.runtime.release.set()
        await asyncio.wait_for(task, 3)
        self.assertIs(replacement, self.service.llm)
        self.assertEqual("configured", self.service.model_runtime_availability())
        self.assertTrue(self.host._proactive_provider_ready())
        self.assertTrue(self.service.proactive_eligibility().eligible)
        self.assertNotEqual("provider_not_ready", self.host._companion_snapshot()["proactive_eligibility"])
        self.assertEqual(count, len(self.client.messages), "Retired work must not publish")
        result = await asyncio.to_thread(self.service.process_text_turn, "Hello.", speak=False)
        self.assertTrue(result.succeeded)
        self.assertEqual("Hello again.", self.session.conversation.messages[-1]["content"])

    async def test_local_to_online_late_failure_cannot_poison_replacement(self):
        await self.late_local("error")

    async def test_local_to_online_late_success_cannot_reinstall_replacement(self):
        await self.late_local("ready")

    async def test_local_to_online_late_exception_is_private_and_discarded(self):
        await self.late_local("raise")

    async def test_template_metadata_read_does_not_block_transport_or_install_after_retirement(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        main_thread = threading.get_ident()

        def slow_metadata(_model):
            self.assertNotEqual(threading.get_ident(), main_thread)
            entered.set()
            release.wait(5)
            return "system"

        self.runtime.result = "ready"
        with patch('aifren.llm.local_template.installed_policy_role', side_effect=slow_metadata):
            task = await self.local_start()
            self.runtime.release.set()
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            await self.command("get_snapshot")
            self.assertFalse(task.done())
            replacement = await self.online()
            count = len(self.client.messages)
            release.set()
            await asyncio.wait_for(task, 3)
            self.assertIs(self.service.llm, replacement)
            self.assertEqual(len(self.client.messages), count)

    async def test_cancelled_superseded_worker_raises_after_online_replacement(self):
        self.runtime.result = "raise"
        old = await self.local_start()
        replacement = await self.online()
        old.cancel()
        await asyncio.gather(old, return_exceptions=True)
        before = list(self.client.messages)
        self.runtime.release.set()
        self.assertTrue(await asyncio.to_thread(self.runtime.returned.wait, 3))
        self.assertEqual(before, self.client.messages)
        self.assertIs(replacement, self.service.llm)
        self.assertTrue(self.host._proactive_provider_ready())

    async def test_current_task_cancellation_cannot_publish_worker_success_later(self):
        self.runtime.result = "ready"
        old = await self.local_start()
        old.cancel()
        await asyncio.gather(old, return_exceptions=True)
        self.assertEqual("unavailable", self.host._model_snapshot()["current"]["availability"])
        replacement = await self.online()
        count = len(self.client.messages)
        self.runtime.release.set()
        self.assertTrue(await asyncio.to_thread(self.runtime.returned.wait, 3))
        self.assertEqual(count, len(self.client.messages))
        self.assertIs(replacement, self.service.llm)

    async def second_local(self, name):
        self.runtime.entered, self.runtime.release, self.runtime.returned = (
            threading.Event(), threading.Event(), threading.Event(),
        )
        self.addCleanup(self.runtime.release.set)
        self.runtime.result = "ready"
        await self.command("set_model_settings", mode="local", local_model=name)
        await self.command("start_local_model")
        self.assertTrue(await asyncio.to_thread(self.runtime.entered.wait, 3))
        return next(task for task in self.host._local_model_tasks if task is not self.old)

    async def out_of_order(self, *, aba=False):
        self.runtime.result = "ready" if aba else "error"
        self.old = await self.local_start()
        old_release = self.runtime.release
        if aba:
            await self.online()
        current = await self.second_local("A.gguf" if aba else "B.gguf")
        self.runtime.release.set()
        await asyncio.wait_for(current, 3)
        replacement = self.service.llm
        snapshot = self.host._model_snapshot()
        self.assertEqual("configured", snapshot["current"]["availability"])
        count = len(self.client.messages)
        old_release.set()
        await asyncio.wait_for(self.old, 3)
        self.assertEqual(count, len(self.client.messages))
        self.assertEqual(snapshot, self.host._model_snapshot())
        self.assertIs(replacement, self.service.llm)
        self.assertTrue(self.host._proactive_provider_ready())

    async def test_out_of_order_models_preserve_b_health_and_status(self):
        await self.out_of_order()

    async def test_local_online_local_aba_rejects_first_a(self):
        await self.out_of_order(aba=True)

    async def test_current_failure_is_recoverable_then_start_succeeds(self):
        self.runtime.result = "raise"
        task = await self.local_start()
        self.runtime.release.set()
        await asyncio.wait_for(task, 3)
        snapshot = self.host._model_snapshot()
        self.assertEqual("unavailable", snapshot["current"]["availability"])
        self.assertFalse(self.host._proactive_provider_ready())
        serialized = json.dumps(self.client.messages)
        for private in ("fake-secret", "/synthetic-private-path", str(self.session.root)):
            self.assertNotIn(private, serialized)
        self.assertIn("local_model_readiness_failed", serialized)
        self.runtime.result = "ready"
        await self.command("start_local_model")
        await asyncio.wait_for(asyncio.gather(*list(self.host._local_model_tasks)), 3)
        self.assertTrue(self.host._proactive_provider_ready())
        self.assertEqual("local", model_settings.get_model_settings()["mode"])
        result = await asyncio.to_thread(self.service.process_text_turn, "Hello.", speak=False)
        self.assertTrue(result.succeeded)

    async def test_shutdown_retires_before_blocked_worker_returns(self):
        self.runtime.result = "ready"
        await self.local_start()
        await asyncio.wait_for(self.host.stop(), 3)
        count = len(self.client.messages)
        self.runtime.release.set()
        self.assertTrue(await asyncio.to_thread(self.runtime.returned.wait, 3))
        self.assertEqual(count, len(self.client.messages))
        self.assertEqual("off", self.runtime.snapshot()["state"])

    async def test_probe_completion_cannot_poison_online_replacement(self):
        await self.command("set_model_settings", mode="local", local_model="A.gguf",
                           local_endpoint="http://127.0.0.1:8000/v1")
        probe = asyncio.create_task(self.command("discover_local_models"))
        self.assertTrue(await asyncio.to_thread(self.runtime.entered.wait, 3))
        replacement = await self.online()
        count = len(self.client.messages)
        self.runtime.release.set()
        await asyncio.wait_for(probe, 3)
        self.assertEqual(count, len(self.client.messages))
        self.assertIs(replacement, self.service.llm)
        self.assertTrue(self.host._proactive_provider_ready())

    async def test_cancel_before_worker_entry_is_recoverable_without_starting(self):
        await self.command("set_model_settings", mode="local", local_model="A.gguf",
                           local_endpoint="http://127.0.0.1:8000/v1")
        self.host._schedule_local_model_start()
        task = next(iter(self.host._local_model_tasks))
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(*list(self.host._event_tasks))
        self.assertFalse(self.runtime.entered.is_set())
        self.assertEqual("error", self.host._model_snapshot()["local_runtime"]["state"])
        self.assertEqual("unavailable", self.host._model_snapshot()["current"]["availability"])
        await self.online()
        self.assertTrue(self.host._proactive_provider_ready())

    async def test_superseded_settings_cleanup_error_cannot_disable_new_online(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        stop = self.runtime.stop
        calls = []

        def blocked_stop(**kwargs):
            calls.append(True)
            if len(calls) == 1:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("Synthetic stop timed out")
                raise RuntimeError("fake-secret /synthetic-private-path")
            return stop(**kwargs)

        with patch.object(self.runtime, "stop", side_effect=blocked_stop):
            previous = asyncio.create_task(self.online())
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            replacement = await self.online()
            count = len(self.client.messages)
            release.set()
            await asyncio.wait_for(previous, 3)
        self.assertEqual(count, len(self.client.messages))
        self.assertIs(replacement, self.service.llm)
        self.assertTrue(self.host._proactive_provider_ready())

    async def test_current_local_cleanup_error_keeps_accepted_online_usable(self):
        with patch.object(self.runtime, "stop", side_effect=RuntimeError("fake-secret")):
            replacement = await self.online()
        self.assertIs(replacement, self.service.llm)
        self.assertTrue(self.host._proactive_provider_ready())
        self.assertIn("local_model_stop_failed", json.dumps(self.client.messages))
        self.assertNotIn("fake-secret", json.dumps(self.client.messages))

    async def test_online_inventory_probe_cannot_change_selected_provider_health(self):
        replacement = await self.online()
        before = self.host._model_snapshot()
        with patch('aifren.llm.llm.discover_local_models', side_effect=RuntimeError("fake-secret")):
            await self.command("discover_local_models", local_endpoint="http://127.0.0.1:8001/v1")
        self.assertIs(replacement, self.service.llm)
        self.assertEqual(before, self.host._model_snapshot())
        self.assertTrue(self.host._proactive_provider_ready())
        self.assertNotIn("fake-secret", json.dumps(self.client.messages))

    async def test_send_yield_cannot_emit_an_old_followup_snapshot(self):
        self.runtime.result = "ready"
        task = await self.local_start()
        entered, release = asyncio.Event(), asyncio.Event()
        send = self.client.send

        async def send_with_backpressure(payload):
            await send(payload)  # WebSocket sends enqueue their frame before drain.
            if json.loads(payload).get("event", {}).get("type") == "local_model_runtime":
                entered.set()
                await asyncio.wait_for(release.wait(), 3)

        with patch.object(self.client, "send", side_effect=send_with_backpressure):
            self.runtime.release.set()
            await asyncio.wait_for(entered.wait(), 3)
            replacement = await self.online()
            count = len(self.client.messages)
            release.set()
            await asyncio.wait_for(task, 3)
        self.assertEqual(count, len(self.client.messages))
        self.assertIs(replacement, self.service.llm)

    async def test_successor_waits_outside_settings_lock_for_unadopted_launch(self):
        root = self.session.root / "models"
        root.mkdir()
        (root / "A.gguf").write_bytes(b"GGUF")
        processes = []
        launched, release, successor_waiting = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def factory(*_args, **_kwargs):
            process = runtime_fixture._Process()
            processes.append(process)
            if len(processes) == 1:
                launched.set()
                if not release.wait(5):
                    raise TimeoutError("Synthetic factory gate timed out")
            return process

        def probe(*_args, **_kwargs):
            if not processes or processes[-1].terminated:
                raise OSError("Synthetic offline endpoint")
            return runtime_fixture._Response({"data": [{"id": "A.gguf"}]})

        self.runtime = LocalModelRuntime(
            self.session.root, model_directory=root, process_factory=factory,
            urlopen=probe, gpu_offload_probe=lambda: False,
        )
        self.runtime.entered = launched
        self.host._local_model_runtime = self.runtime
        old = await self.local_start()
        await self.online()  # Must finish while process_factory is still blocked.
        self.assertFalse(release.is_set())
        wait = self.runtime._wait_for_process_work

        def observe_wait(operation):
            successor_waiting.set()
            return wait(operation)

        with patch.object(self.runtime, "_wait_for_process_work", side_effect=observe_wait):
            await self.command("set_model_settings", mode="local")
            await self.command("start_local_model")
            self.assertTrue(await asyncio.to_thread(successor_waiting.wait, 3))
            await self.command("get_snapshot")
            self.assertFalse(release.is_set())
            release.set()
            await asyncio.wait_for(asyncio.gather(*list(self.host._local_model_tasks)), 3)
        await asyncio.wait_for(old, 3)
        self.assertEqual(2, len(processes))
        self.assertTrue(processes[0].terminated)
        self.assertFalse(processes[1].terminated)
        self.assertEqual("managed", self.runtime.snapshot()["ownership"])
        self.assertTrue(self.host._proactive_provider_ready())

    async def test_real_runtime_initial_probe_does_not_block_settings_or_shutdown(self):
        entered, release = threading.Event(), threading.Event()
        worker_finished = threading.Event()
        self.addCleanup(release.set)
        factory = Mock(side_effect=AssertionError("A retired probe must not launch"))

        def probe(*_args, **_kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Synthetic initial probe timed out")
            return runtime_fixture._Response({"data": [{"id": "A.gguf"}]})

        self.runtime = LocalModelRuntime(
            self.session.root, model_directory=self.session.root / "models",
            urlopen=probe, process_factory=factory, gpu_offload_probe=lambda: False,
        )
        self.runtime.entered = entered
        self.host._local_model_runtime = self.runtime
        start = self.runtime.start

        def observed_start(**kwargs):
            try:
                return start(**kwargs)
            finally:
                worker_finished.set()

        self.runtime.start = observed_start
        task = await self.local_start()
        await self.online()
        await self.command("get_snapshot")
        self.assertTrue(self.host._proactive_provider_ready())
        await asyncio.wait_for(self.host.stop(), 3)
        self.assertFalse(release.is_set())
        count = len(self.client.messages)
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(await asyncio.to_thread(worker_finished.wait, 3))
        self.assertEqual(count, len(self.client.messages))
        self.assertEqual("off", self.runtime.snapshot()["state"])
        factory.assert_not_called()

    async def test_current_adapter_install_failure_reports_safe_unavailable_status(self):
        self.runtime.result = "ready"
        task = await self.local_start()
        with patch('aifren.llm.llm.create_llm', side_effect=RuntimeError("fake-secret /synthetic-private-path")):
            self.runtime.release.set()
            await asyncio.wait_for(task, 3)
        snapshot = self.host._model_snapshot()
        self.assertEqual("unavailable", snapshot["current"]["availability"])
        self.assertEqual("error", snapshot["local_runtime"]["state"])
        self.assertFalse(self.host._proactive_provider_ready())
        self.assertNotIn("fake-secret", json.dumps(self.client.messages))
        await self.online()
        self.assertTrue(self.host._proactive_provider_ready())

    async def test_stale_stop_cleanup_cannot_detach_or_misidentify_the_successor(self):
        root = self.session.root / "models"
        root.mkdir()
        (root / "A.gguf").write_bytes(b"GGUF")
        processes = []
        stopping, release, successor_waiting = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def factory(*_args, **_kwargs):
            process = runtime_fixture._Process()
            processes.append(process)
            if len(processes) == 1:
                terminate = process.terminate

                def blocked_stop():
                    stopping.set()
                    if not release.wait(5):
                        raise TimeoutError("Synthetic termination gate timed out")
                    terminate()

                process.terminate = blocked_stop
            return process

        def probe(*_args, **_kwargs):
            if not processes or processes[-1].terminated:
                raise OSError("Synthetic offline endpoint")
            name = "wrong.gguf" if len(processes) == 1 and not stopping.is_set() else "A.gguf"
            return runtime_fixture._Response({"data": [{"id": name}]})

        self.runtime = LocalModelRuntime(
            self.session.root, model_directory=root, process_factory=factory,
            urlopen=probe, gpu_offload_probe=lambda: False,
        )
        self.runtime.entered = stopping
        self.host._local_model_runtime = self.runtime
        old = await self.local_start()
        wait = self.runtime._wait_for_process_work

        def observe_wait(operation):
            successor_waiting.set()
            return wait(operation)

        with patch.object(self.runtime, "_wait_for_process_work", side_effect=observe_wait):
            await self.command("start_local_model")
            self.assertTrue(await asyncio.to_thread(successor_waiting.wait, 3))
            await self.command("get_snapshot")
            self.assertFalse(release.is_set())
            release.set()
            await asyncio.wait_for(asyncio.gather(*list(self.host._local_model_tasks)), 3)
        await old
        self.assertEqual(2, len(processes))
        self.assertTrue(processes[0].terminated)
        self.assertFalse(processes[1].terminated)
        self.assertEqual("managed", self.runtime.snapshot()["ownership"])
        self.assertTrue(self.host._proactive_provider_ready())

    async def test_old_readiness_mismatch_cannot_stop_replacement_process(self):
        root = self.session.root / "models"
        root.mkdir()
        for name in ("A.gguf", "B.gguf"):
            (root / name).write_bytes(b"GGUF")
        processes = []
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def factory(command, **_kwargs):
            process = runtime_fixture._Process()
            process.model = command[command.index("--model_alias") + 1]
            processes.append(process)
            return process

        def probe(_request, **_kwargs):
            if not processes:
                raise OSError("Synthetic offline endpoint")
            if not entered.is_set():
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("Synthetic readiness gate timed out")
                # The old A probe returns B after B has become current.
                return runtime_fixture._Response({"data": [{"id": "B.gguf"}]})
            return runtime_fixture._Response({"data": [{"id": processes[-1].model}]})

        self.runtime = LocalModelRuntime(
            self.session.root, model_directory=root, process_factory=factory,
            urlopen=probe, gpu_offload_probe=lambda: False,
        )
        self.runtime.entered = entered
        self.host._local_model_runtime = self.runtime
        old = await self.local_start()
        await self.command("set_model_settings", mode="local", local_model="B.gguf")
        current = next(task for task in self.host._local_model_tasks if task is not old)
        await asyncio.wait_for(current, 3)
        self.assertEqual("B.gguf", self.runtime.snapshot()["active_model"])
        release.set()
        await asyncio.wait_for(old, 3)
        self.assertFalse(processes[-1].terminated, "Old probe cleanup killed replacement")
        self.assertEqual("ready", self.runtime.snapshot()["state"])
        self.assertEqual("B.gguf", self.runtime.snapshot()["active_model"])
        before = self.runtime.snapshot()
        processes[0].stdout = io.StringIO("load_tensors: offloading 29 layers to GPU\n")
        await asyncio.to_thread(self.runtime._drain_output, processes[0])
        self.assertEqual(before, self.runtime.snapshot())

    async def test_failed_retired_cleanup_retains_handle_without_restoring_old_status(self):
        root = self.session.root / "models"
        root.mkdir()
        (root / "A.gguf").write_bytes(b"GGUF")
        processes = []
        stopping, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def factory(*_args, **_kwargs):
            process = runtime_fixture._Process()
            processes.append(process)
            if len(processes) == 1:
                terminate = process.terminate

                def failed_stop():
                    stopping.set()
                    if not release.wait(5):
                        raise TimeoutError("Synthetic cleanup timed out")
                    process.terminate = terminate
                    raise RuntimeError("fake-secret /synthetic-private-path")

                process.terminate = failed_stop
            return process

        def probe(*_args, **_kwargs):
            if not processes or processes[-1].terminated:
                raise OSError("Synthetic offline endpoint")
            name = "wrong.gguf" if not stopping.is_set() else "A.gguf"
            return runtime_fixture._Response({"data": [{"id": name}]})

        self.runtime = LocalModelRuntime(
            self.session.root, model_directory=root, process_factory=factory,
            urlopen=probe, gpu_offload_probe=lambda: False,
        )
        self.runtime.entered = stopping
        self.host._local_model_runtime = self.runtime
        old = await self.local_start()
        replacement = await self.online()
        before = self.host._model_snapshot()
        count = len(self.client.messages)
        release.set()
        await asyncio.wait_for(old, 3)
        self.assertEqual(before, self.host._model_snapshot())
        self.assertEqual(count, len(self.client.messages))
        self.assertIs(replacement, self.service.llm)
        self.assertTrue(self.host._proactive_provider_ready())
        self.assertFalse(processes[0].terminated)
        await self.command("set_model_settings", mode="local")
        await self.command("start_local_model")
        await asyncio.wait_for(asyncio.gather(*list(self.host._local_model_tasks)), 3)
        self.assertTrue(self.host._proactive_provider_ready())
        self.assertTrue(processes[0].terminated)
        self.assertFalse(processes[-1].terminated)


if __name__ == "__main__":
    unittest.main()
