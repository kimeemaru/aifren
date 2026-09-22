"""Small owned-process manager for optional local OpenAI-compatible models.

It deliberately owns only a process it started.  Existing compatible servers
remain external endpoints: they can be used and discovered, never terminated.
The provider adapter still receives an ordinary OpenAI-compatible endpoint.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Callable
from urllib import request
from urllib.parse import urlparse
import json
import re
import tempfile
import uuid


_OWNERSHIP_METADATA_VERSION = 1
_OWNERSHIP_METADATA_NAME = ".aifren_managed_local_runtime.json"
_OWNERSHIP_TOKEN_ENV = "AIFREN_MANAGED_RUNTIME_TOKEN"


def _linux_process_identity(pid: int) -> dict[str, object] | None:
    """Read enough immutable Linux identity to reject PID reuse/unrelated ports."""
    try:
        proc = Path(f"/proc/{int(pid)}")
        stat = (proc / "stat").read_text(encoding="ascii")
        closing = stat.rfind(")")
        fields = stat[closing + 2:].split()
        argv = tuple(
            value.decode("utf-8", "surrogateescape")
            for value in (proc / "cmdline").read_bytes().split(b"\0") if value
        )
        environment = {}
        for value in (proc / "environ").read_bytes().split(b"\0"):
            if b"=" in value:
                key, content = value.split(b"=", 1)
                environment[key.decode("utf-8", "ignore")] = content.decode("utf-8", "ignore")
        return {
            "pid": int(pid),
            "process_group_id": int(fields[2]),
            "session_id": int(fields[3]),
            "start_ticks": int(fields[19]),
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip(),
            "argv": argv,
            "owner_token": environment.get(_OWNERSHIP_TOKEN_ENV, ""),
        }
    except (OSError, ValueError, IndexError):
        return None


class _RecoveredProcess:
    """Minimal Popen-compatible handle for a proven surviving process group."""

    def __init__(self, pid: int, identity_reader: Callable[[int], dict[str, object] | None]) -> None:
        self.pid = int(pid)
        self.stdout = None
        self.returncode = None
        self._identity_reader = identity_reader

    def poll(self):
        if self._identity_reader(self.pid) is None:
            self.returncode = 0
        return self.returncode

    def terminate(self):
        os.killpg(self.pid, signal.SIGTERM)

    def kill(self):
        os.killpg(self.pid, signal.SIGKILL)

    def wait(self, timeout=None):
        deadline = time.monotonic() + (float(timeout) if timeout is not None else 60.0)
        while self.poll() is None and time.monotonic() < deadline:
            time.sleep(.05)
        if self.returncode is None:
            raise subprocess.TimeoutExpired("aifren-managed-llama", timeout)
        return self.returncode


@dataclass(frozen=True)
class LocalModelDescriptor:
    identifier: str
    display_name: str

    def snapshot(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class LocalModelRuntimeStatus:
    state: str = "off"  # off, starting, switching, ready, mismatch, error
    ownership: str = "none"  # none, managed, external
    active_model: str = ""
    # CUDA is reported only after managed-server output confirms layer offload.
    compute: str = "unknown"
    error: str = ""
    endpoint_models: tuple[str, ...] = ()

    def snapshot(self) -> dict[str, str]:
        return asdict(self)


class LocalModelRuntime:
    """Manage one local llama.cpp-python server without owning external ones."""

    def __init__(
        self,
        application_dir: Path | str,
        *,
        model_directory: Path | str,
        context_size: int = 4096,
        process_factory: Callable = subprocess.Popen,
        urlopen: Callable = request.urlopen,
        gpu_offload_probe: Callable[[], bool] | None = None,
        log: Callable[[str], None] | None = None,
        verbose_process_logs: bool | None = None,
        enable_thinking: bool = False,
        readiness_timeout_seconds: float = 90.0,
        process_identity_reader: Callable[[int], dict[str, object] | None] | None = None,
        recovered_process_factory: Callable[[int, Callable], object] = _RecoveredProcess,
        process_group_signaler: Callable[[int, int], None] | None = None,
        process_platform: str | None = None,
    ) -> None:
        self.application_dir = Path(application_dir).resolve()
        self.model_directory = Path(model_directory)
        if not self.model_directory.is_absolute():
            self.model_directory = (self.application_dir / self.model_directory).resolve()
        self.context_size = max(512, int(context_size))
        self._process_factory, self._urlopen = process_factory, urlopen
        self._gpu_offload_probe = gpu_offload_probe or self._probe_gpu_offload
        self._log = log or (lambda _message: None)
        if verbose_process_logs is None:
            verbose_process_logs = str(os.getenv("AIFREN_LOCAL_MODEL_VERBOSE_LOGS", "")).strip().lower() in {
                "1", "true", "yes", "on",
            }
        self._verbose_process_logs = bool(verbose_process_logs)
        self._enable_thinking = bool(enable_thinking)
        self.readiness_timeout_seconds = float(readiness_timeout_seconds)
        self._process_platform = os.name if process_platform is None else process_platform
        self._persisted_recovery_supported = self._process_platform != "nt"
        self._process_identity_reader = process_identity_reader or _linux_process_identity
        self._recovered_process_factory = recovered_process_factory
        self._process_group_signaler = (
            process_group_signaler
            if process_group_signaler is not None
            else (getattr(os, "killpg", None) if self._persisted_recovery_supported else None)
        )
        self._ownership_metadata_path = self.application_dir / _OWNERSHIP_METADATA_NAME
        self._process = None
        self._gpu_offload_confirmed = False
        self._gpu_offload_event = threading.Event()
        self._status = LocalModelRuntimeStatus()
        self._lock = threading.RLock()
        self._operation = threading.Event()
        self._pending_process_work: set[threading.Event] = set()
        self._retiring: dict[int, tuple[threading.Event, LocalModelRuntimeStatus, threading.Event]] = {}

    def reserve_operation(self) -> threading.Event:
        """Invalidate late workers before a configuration/task can be replaced."""
        with self._lock:
            self._operation.set()
            self._operation = threading.Event()
            return self._operation

    def cancel_operation(self, operation: threading.Event) -> None:
        with self._lock:
            if operation is self._operation:
                operation.set()

    def _owns_operation(self, operation: threading.Event) -> bool:
        # Call under _lock when applying a result or taking a process handle.
        return operation is self._operation and not operation.is_set()

    @staticmethod
    def _superseded() -> dict[str, object]:
        return {"state": "superseded", "ownership": "none"}

    def _wait_for_process_work(self, operation: threading.Event) -> bool:
        # A newly spawned or retiring owned endpoint must not be mistaken for
        # an external server by its successor. Only the worker waits here;
        # settings, snapshot, cancellation and shutdown can acquire the state lock.
        while True:
            with self._lock:
                if not self._owns_operation(operation):
                    return False
                pending = next(iter(self._pending_process_work), None)
            if pending is None:
                return True
            pending.wait(0.2)

    @staticmethod
    def _display_name(path: Path) -> str:
        stem = path.stem.replace("-M-TS-", "-")
        # This is presentation only; identifiers remain stable relative paths.
        return re.sub(r"-(Q\d(?:_[A-Za-z0-9]+)+)$", r" \1", stem)

    def discover_installed(self) -> tuple[LocalModelDescriptor, ...]:
        if not self.model_directory.is_dir():
            return ()
        records: list[LocalModelDescriptor] = []
        for path in self.model_directory.rglob("*.gguf"):
            try:
                resolved = path.resolve()
                resolved.relative_to(self.model_directory)
            except (OSError, ValueError):
                continue
            if not resolved.is_file():
                continue
            records.append(LocalModelDescriptor(
                identifier=resolved.relative_to(self.model_directory).as_posix(),
                display_name=self._display_name(resolved),
            ))
        return tuple(sorted(records, key=lambda item: (item.display_name.lower(), item.identifier)))

    def resolve_installed(self, identifier: str) -> Path | None:
        wanted = str(identifier or "").strip()
        for record in self.discover_installed():
            if record.identifier == wanted:
                candidate = (self.model_directory / record.identifier).resolve()
                try:
                    candidate.relative_to(self.model_directory)
                except ValueError:
                    return None
                return candidate if candidate.is_file() else None
        return None

    def _load_ownership_metadata(self) -> dict[str, object] | None:
        try:
            value = json.loads(self._ownership_metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _remove_ownership_metadata(self) -> None:
        try:
            self._ownership_metadata_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _write_ownership_metadata(
        self,
        *,
        process,
        owner_token: str,
        command: list[str],
        endpoint: str,
        selected_model: str,
        model_path: Path,
    ) -> None:
        if not self._persisted_recovery_supported:
            # Windows clean shutdown retains the direct Popen handle. After an
            # abnormal backend death, a surviving responsive endpoint is
            # deliberately treated as external instead of weakening process
            # identity proof or signaling a PID based on stale metadata.
            self._remove_ownership_metadata()
            return
        pid = getattr(process, "pid", None)
        if not pid:
            return
        identity = self._process_identity_reader(int(pid))
        if identity is None or identity.get("owner_token") != owner_token:
            raise RuntimeError("managed process identity could not be recorded")
        payload = {
            "version": _OWNERSHIP_METADATA_VERSION,
            "pid": int(pid),
            "process_group_id": int(identity.get("process_group_id", -1)),
            "session_id": int(identity.get("session_id", -1)),
            "start_ticks": int(identity.get("start_ticks", -1)),
            "boot_id": str(identity.get("boot_id") or ""),
            "owner_token": owner_token,
            "argv": list(command),
            "application_dir": str(self.application_dir),
            "endpoint": str(endpoint).rstrip("/"),
            "selected_model": str(selected_model),
            "model_path": str(model_path),
            "context_size": self.context_size,
            "enable_thinking": self._enable_thinking,
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".aifren-managed-runtime-", suffix=".tmp", dir=self.application_dir,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, self._ownership_metadata_path)
        finally:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass

    @staticmethod
    def _metadata_identity_matches(metadata: dict[str, object], identity: dict[str, object]) -> bool:
        try:
            pid = int(metadata["pid"])
            owner_token = str(metadata["owner_token"])
            uuid.UUID(owner_token)
            return (
                metadata.get("version") == _OWNERSHIP_METADATA_VERSION
                and int(identity.get("pid", -1)) == pid
                and int(metadata["process_group_id"]) == pid
                and int(metadata["session_id"]) == pid
                and int(identity.get("process_group_id", -1)) == pid
                and int(identity.get("session_id", -1)) == pid
                and int(metadata["start_ticks"]) == int(identity.get("start_ticks", -2))
                and str(metadata["boot_id"]) == str(identity.get("boot_id") or "")
                and owner_token == str(identity.get("owner_token") or "")
                and bool(str(metadata["boot_id"]))
                and tuple(str(value) for value in metadata["argv"])
                == tuple(str(value) for value in identity.get("argv", ()))
            )
        except (KeyError, TypeError, ValueError):
            return False

    def _recover_owned_runtime(
        self, *, endpoint: str, selected_model: str, api_key: str,
        operation: threading.Event,
    ) -> str:
        """Prove identity under the state lock; probe/wait outside it."""
        with self._lock:
            if not self._owns_operation(operation):
                return "superseded"
            if not self._persisted_recovery_supported:
                self._remove_ownership_metadata()
                return "none"
            metadata = self._load_ownership_metadata()
            if metadata is None:
                return "none"
            try:
                pid = int(metadata["pid"])
            except (KeyError, TypeError, ValueError):
                self._remove_ownership_metadata()
                return "none"
            identity = self._process_identity_reader(pid)
            if identity is None:
                self._remove_ownership_metadata()
                return "none"
            if not self._metadata_identity_matches(metadata, identity):
                # The metadata file is ours, but the live PID is not provably the
                # same AIFren process. Forget the stale reference and never signal it.
                self._remove_ownership_metadata()
                self._log("Managed local model ownership proof was rejected; live process left untouched.")
                return "none"

            # Keep proving the same immutable identity at every later poll.  If
            # the owned server exits and Linux reuses its PID before stop(), the
            # recovered handle must look exited and no process-group signal may be
            # sent to the replacement.
            def verified_identity_reader(candidate_pid: int) -> dict[str, object] | None:
                current = self._process_identity_reader(candidate_pid)
                if current is None or not self._metadata_identity_matches(metadata, current):
                    return None
                return current

            self._process = self._recovered_process_factory(pid, verified_identity_reader)
            self._status = LocalModelRuntimeStatus(
                state="starting", ownership="managed", active_model=str(metadata.get("selected_model") or ""),
                compute="unknown",
            )
            resolved = self.resolve_installed(selected_model)
            try:
                configuration_matches = (
                    resolved is not None
                    and str(metadata.get("application_dir") or "") == str(self.application_dir)
                    and str(metadata.get("endpoint") or "") == str(endpoint).rstrip("/")
                    and str(metadata.get("selected_model") or "") == str(selected_model)
                    and str(metadata.get("model_path") or "") == str(resolved)
                    and int(metadata.get("context_size", -1)) == self.context_size
                    and metadata.get("enable_thinking") is self._enable_thinking
                )
            except (TypeError, ValueError):
                configuration_matches = False
            process = self._process
        models: tuple[str, ...] = ()
        if configuration_matches:
            try:
                models = self._probe_models(endpoint, api_key)
            except Exception:
                pass
        with self._lock:
            if not self._owns_operation(operation) or self._process is not process:
                return "superseded"
            if configuration_matches and selected_model in models:
                self._status = LocalModelRuntimeStatus(
                    state="ready", ownership="managed", active_model=selected_model,
                    compute="recovered", endpoint_models=models,
                )
                self._log("Recovered healthy AIFren-managed local model runtime.")
                return "ready"
            self._log("Cleaning stale or incompatible AIFren-managed local model runtime.")
            retired = self._detach_owned_locked()
            self._status = LocalModelRuntimeStatus()
        self._stop_process(retired)
        return "cleaned"

    @staticmethod
    def _endpoint_url(endpoint: str) -> str:
        return str(endpoint).rstrip("/") + "/models"

    def _probe_models(self, endpoint: str, api_key: str = "") -> tuple[str, ...]:
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = "Bearer " + api_key
        req = request.Request(self._endpoint_url(endpoint), headers=headers, method="GET")
        with self._urlopen(req, timeout=3.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
        values = payload.get("data", []) if isinstance(payload, dict) else []
        models = tuple(sorted(str(item.get("id")) for item in values if isinstance(item, dict) and item.get("id")))
        if not models:
            raise RuntimeError("the local endpoint did not advertise a model")
        return models

    @staticmethod
    def _local_server_address(endpoint: str) -> tuple[str, int] | None:
        parsed = urlparse(str(endpoint))
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or host not in {"127.0.0.1", "localhost", "::1"}:
            return None
        try:
            return host, parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError:
            return None

    @staticmethod
    def _probe_gpu_offload() -> bool:
        """Probe in a child process so a broken CUDA driver cannot kill AIFren.

        ``llama_supports_gpu_offload`` is a native call.  Some constrained
        shells can abort while loading CUDA even though the normal host is
        healthy, so this deliberately treats that failure as CPU fallback.
        """
        try:
            result = subprocess.run(
                [sys.executable, "-c", "import llama_cpp; print(int(llama_cpp.llama_cpp.llama_supports_gpu_offload()))"],
                capture_output=True, text=True, timeout=10,
            )
            return result.returncode == 0 and result.stdout.strip().endswith("1")
        except Exception:
            return False

    def _gpu_compute(self) -> str:
        from aifren.runtime.config import configured_inference_device
        if configured_inference_device() == "cpu":
            return "CPU"
        try:
            return "GPU offload requested" if self._gpu_offload_probe() else "CPU"
        except Exception:
            return "CPU"

    def _set_error(self, message: str) -> None:
        self._status.state = "error"
        self._status.ownership = "none"
        self._status.error = str(message)[:240]

    def _observe_process_locked(self) -> None:
        if self._process is not None and self._process.poll() is not None:
            code = self._process.returncode
            self._process = None
            self._gpu_offload_confirmed = False
            self._remove_ownership_metadata()
            self._set_error(f"managed local model exited ({code})")
            self._log(f"Managed local model exited with code {code}.")

    def snapshot(self, *, selected_model: str = "") -> dict[str, object]:
        with self._lock:
            self._observe_process_locked()
            data: dict[str, object] = self._status.snapshot()
            data["selected_model"] = str(selected_model or "")
            data["effective_context_tokens"] = self.context_size
            data["installed_models"] = [record.snapshot() for record in self.discover_installed()]
            return data

    def _drain_output(self, process) -> None:
        stream = getattr(process, "stdout", None)
        if stream is None:
            return
        try:
            for line in stream:
                text = str(line).strip()
                if text:
                    normalized = text.lower()
                    with self._lock:
                        if process is not self._process:
                            continue
                        from aifren.runtime.config import configured_inference_device
                        complete = re.search(r"offloaded\s+(\d+)\s*/\s*(\d+)\s+layers to gpu", normalized)
                        confirmed = (bool(complete and int(complete[1]) > 0 and complete[1] == complete[2])
                                     if configured_inference_device() == "cuda" else True)
                        if confirmed and "offload" in normalized and "gpu" in normalized and (
                            "offloaded" in normalized or "offloading" in normalized
                        ):
                            self._gpu_offload_confirmed = True
                            self._gpu_offload_event.set()
                            if self._status.ownership == "managed":
                                self._status.compute = "CUDA"
                        elif "failed to initialize cuda" in normalized or "cuda" in normalized and "error" in normalized:
                            if self._status.ownership == "managed":
                                self._status.compute = "CPU"
                    # llama.cpp 0.3.35 can emit one CUDA graph reuse diagnostic
                    # per generated token. Always drain the child pipe, but do
                    # not turn that low-level trace into file I/O, console
                    # history, transport payload, and Unity allocations at
                    # normal product verbosity. Startup/offload, errors, and
                    # performance summaries remain visible. Developers can opt
                    # back into the raw stream explicitly.
                    if self._verbose_process_logs or not self._is_noisy_process_trace(text):
                        self._log("local model: " + text[:350])
        except Exception:
            return

    @staticmethod
    def _is_noisy_process_trace(text: str) -> bool:
        return re.search(r"\bCUDA Graph id \d+ reused\b", str(text), re.IGNORECASE) is not None

    def _detach_owned_locked(self):
        """Transfer just this owned handle to cleanup; never detach a later one."""
        process = self._process
        if process is not None:
            completed = threading.Event()
            self._pending_process_work.add(completed)
            self._retiring[id(process)] = (completed, self._status, self._operation)
        self._process = None
        self._gpu_offload_confirmed = False
        return process

    def _stop_process(self, process) -> None:
        """Potentially blocking termination of an already captured owned handle."""
        try:
            self._terminate_process(process)
        finally:
            with self._lock:
                retiring = self._retiring.pop(id(process), None)
                if retiring is not None:
                    completed, previous, operation = retiring
                    if process.poll() is None and self._process is None:
                        # Failed termination retains proven ownership for a
                        # later retry. It cannot replace another live handle.
                        self._process = process
                        if self._owns_operation(operation):
                            self._status.ownership = "managed"
                            self._status.active_model = previous.active_model
                    elif self._process is None:
                        self._remove_ownership_metadata()
                    self._pending_process_work.discard(completed)
                    completed.set()

    def _terminate_process(self, process) -> None:
        if process is None:
            return
        if process.poll() is None:
            # The process object is retained only for a server this runtime
            # launched in a new session.  Prefer its process group so a
            # launcher child cannot survive AIFren shutdown; fake/test process
            # seams and any platform without a PID fall back to terminate().
            pid = getattr(process, "pid", None)
            try:
                if pid and self._process_group_signaler is not None:
                    self._process_group_signaler(int(pid), signal.SIGTERM)
                else:
                    process.terminate()
            except (OSError, ProcessLookupError):
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    if pid and self._process_group_signaler is not None:
                        self._process_group_signaler(int(pid), getattr(signal, "SIGKILL", signal.SIGTERM))
                    else:
                        process.kill()
                except (OSError, ProcessLookupError):
                    process.kill()
                process.wait(timeout=5)

    def stop(self, *, operation: threading.Event | None = None) -> dict[str, object]:
        operation = operation if operation is not None else self.reserve_operation()
        with self._lock:
            if not self._owns_operation(operation):
                return self._superseded()
            self._observe_process_locked()
            if self._status.ownership == "external":
                return self.snapshot()
            process = self._detach_owned_locked()
            self._status = LocalModelRuntimeStatus()
        self._stop_process(process)
        with self._lock:
            if not self._owns_operation(operation):
                return self._superseded()
            self._log("Managed local model stopped.")
            return self.snapshot()

    def begin_start(self, *, selected_model: str, operation: threading.Event | None = None) -> dict[str, object]:
        """Publish an immediate UI transition before blocking readiness work."""
        with self._lock:
            if operation is not None and not self._owns_operation(operation):
                return self._superseded()
            self._observe_process_locked()
            switching = self._process is not None and self._status.active_model != selected_model
            self._status.state = "switching" if switching else "starting"
            self._status.error = ""
            return self.snapshot(selected_model=selected_model)

    @staticmethod
    def _external_status(models: tuple[str, ...], selected_model: str) -> LocalModelRuntimeStatus:
        active = models[0] if models else ""
        if selected_model and selected_model not in models:
            return LocalModelRuntimeStatus(
                state="mismatch", ownership="external", active_model=active,
                compute="external", endpoint_models=models,
                error="external server is running a different model",
            )
        return LocalModelRuntimeStatus(
            state="ready", ownership="external", active_model=active,
            compute="external", endpoint_models=models,
        )

    def refresh_external(
        self, endpoint: str, api_key: str = "", *, selected_model: str = "",
        operation: threading.Event | None = None,
    ) -> dict[str, object]:
        operation = operation if operation is not None else self.reserve_operation()
        if not self._wait_for_process_work(operation):
            return self._superseded()
        with self._lock:
            if not self._owns_operation(operation):
                return self._superseded()
            self._observe_process_locked()
            process = self._process
        try:
            models = self._probe_models(endpoint, api_key)
        except Exception:
            models = ()
        with self._lock:
            if not self._owns_operation(operation) or self._process is not process:
                return self._superseded()
            if not models:
                self._status.state = "error"
                self._status.error = "local endpoint is unavailable"
            elif process is not None:
                self._status.state = "mismatch" if selected_model and selected_model not in models else "ready"
                self._status.ownership = "managed"
                self._status.active_model = selected_model if selected_model in models else models[0]
                self._status.error = "managed server did not advertise the selected model" if self._status.state == "mismatch" else ""
                self._status.endpoint_models = models
            else:
                self._status = self._external_status(models, selected_model)
            return self.snapshot(selected_model=selected_model)

    def start(
        self, *, endpoint: str, selected_model: str, api_key: str = "",
        operation: threading.Event | None = None,
    ) -> dict[str, object]:
        """Blocking work never owns the state lock; each completion must still own it."""
        operation = operation if operation is not None else self.reserve_operation()
        if not self._wait_for_process_work(operation):
            return self._superseded()
        with self._lock:
            if not self._owns_operation(operation):
                return self._superseded()
            self._observe_process_locked()
            recover = self._process is None
        if recover:
            recovery = self._recover_owned_runtime(
                endpoint=endpoint, selected_model=selected_model, api_key=api_key,
                operation=operation,
            )
            if recovery in {"ready", "superseded"}:
                with self._lock:
                    return self.snapshot(selected_model=selected_model) if self._owns_operation(operation) else self._superseded()
        try:
            models = self._probe_models(endpoint, api_key)
        except Exception:
            models = ()
        with self._lock:
            if not self._owns_operation(operation):
                return self._superseded()
            # A responding endpoint does not confer process ownership.
            if models and self._process is None:
                self._status = self._external_status(models, selected_model)
                return self.snapshot(selected_model=selected_model)
            reuse = self._process is not None and self._status.active_model == selected_model
            retired = None if reuse else self._detach_owned_locked()
        if reuse:
            return self.refresh_external(endpoint, api_key, selected_model=selected_model, operation=operation)
        self._stop_process(retired)
        model_path = self.resolve_installed(selected_model)
        address = self._local_server_address(endpoint)
        compute = self._gpu_compute() if model_path is not None and address is not None else "unknown"
        from aifren.runtime.config import configured_inference_device
        require_gpu = configured_inference_device() == "cuda"
        with self._lock:
            if not self._owns_operation(operation):
                return self._superseded()
            if model_path is None or address is None:
                self._set_error("selected local model is missing" if model_path is None else "managed local models require a loopback HTTP endpoint")
                return self.snapshot(selected_model=selected_model)
            if require_gpu and compute != "GPU offload requested":
                self._set_error("The NVIDIA chat runtime is unavailable. Check the bundled CUDA runtime and supported driver; CPU fallback is disabled.")
                return self.snapshot(selected_model=selected_model)
            host, port = address
            self._gpu_offload_confirmed = False
            self._gpu_offload_event = threading.Event()
            offload_event = self._gpu_offload_event
            self._status = LocalModelRuntimeStatus(
                state="starting", ownership="managed", active_model=selected_model, compute=compute,
            )
            command = [
                sys.executable, "-m", "llama_cpp.server", "--model", str(model_path),
                "--model_alias", selected_model, "--host", host, "--port", str(port),
                "--n_ctx", str(self.context_size),
                # llama-cpp-python's server wrapper defaults this to true,
                # allocating an n_ctx x n_vocab float32 score matrix and
                # committing one vocabulary-sized row for every prompt token.
                # AIFren does not request logprobs, so retaining those rows is
                # unused and can consume several GiB for Qwen's large vocab.
                "--logits_all", "false",
                # Prefer the GGUF's embedded template over a forced generic
                # ChatML wrapper. Qwen's native template honors this kwarg and
                # avoids generating a long hidden reasoning preamble before
                # user-visible dialogue. Templates which do not use the kwarg
                # simply ignore it.
                "--chat_template_kwargs", json.dumps(
                    {"enable_thinking": self._enable_thinking}, separators=(",", ":")
                ),
            ]
            if self._status.compute == "GPU offload requested":
                command.extend(["--n_gpu_layers", "-1"])
            launched = threading.Event()
            self._pending_process_work.add(launched)
        process = None
        try:
            owner_token = str(uuid.uuid4())
            launch_environment = dict(os.environ)
            launch_environment[_OWNERSHIP_TOKEN_ENV] = owner_token
            process_options = {
                "cwd": str(self.application_dir), "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT, "text": True, "bufsize": 1,
                "env": launch_environment,
            }
            if self._process_platform == "nt":
                process_options["creationflags"] = (
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
            else:
                process_options["start_new_session"] = True
            process = self._process_factory(command, **process_options)
            with self._lock:
                accepted = self._owns_operation(operation)
                if accepted:
                    self._process = process
                    self._write_ownership_metadata(
                        process=process, owner_token=owner_token, command=command,
                        endpoint=endpoint, selected_model=selected_model, model_path=model_path,
                    )
            if not accepted:
                self._stop_process(process)
                return self._superseded()
        except Exception:
            with self._lock:
                accepted = self._owns_operation(operation)
                if accepted:
                    if process is not None and self._process is process:
                        self._detach_owned_locked()
                    self._set_error("could not start managed local model")
                    self._log("Managed local model start failed.")
            self._stop_process(process)
            with self._lock:
                return self.snapshot(selected_model=selected_model) if self._owns_operation(operation) else self._superseded()
        finally:
            with self._lock:
                self._pending_process_work.discard(launched)
                launched.set()
        threading.Thread(target=self._drain_output, args=(process,), daemon=True).start()

        deadline = time.monotonic() + self.readiness_timeout_seconds
        while time.monotonic() < deadline:
            with self._lock:
                if not self._owns_operation(operation):
                    return self._superseded()
                self._observe_process_locked()
                if self._process is not process:
                    return self.snapshot(selected_model=selected_model)
            try:
                models = self._probe_models(endpoint, api_key)
            except Exception:
                operation.wait(0.2)
                continue
            if compute == "GPU offload requested":
                offload_event.wait(timeout=2.0)
            with self._lock:
                if not self._owns_operation(operation) or self._process is not process:
                    return self._superseded()
                if selected_model not in models:
                    retired = self._detach_owned_locked()
                    self._set_error("managed server did not advertise the selected model")
                    break
                if require_gpu and not self._gpu_offload_confirmed:
                    retired = self._detach_owned_locked()
                    self._set_error("Full NVIDIA model-layer offload was not confirmed; inference was stopped without CPU fallback.")
                    break
                self._status.state = "ready"
                self._status.ownership = "managed"
                self._status.active_model = selected_model
                self._status.error = ""
                self._status.endpoint_models = models
                if self._status.compute == "GPU offload requested" and not self._gpu_offload_confirmed:
                    self._status.compute = "CPU"
                    self._status.error = "GPU offload was requested but not confirmed"
                    self._log("Managed local model GPU offload was not confirmed; reporting CPU.")
                self._log(f"Managed local model ready ({self._status.compute}).")
                return self.snapshot(selected_model=selected_model)
        else:
            with self._lock:
                if not self._owns_operation(operation) or self._process is not process:
                    return self._superseded()
                retired = self._detach_owned_locked()
                self._set_error("managed local model did not become ready")
        self._stop_process(retired)
        with self._lock:
            return self.snapshot(selected_model=selected_model) if self._owns_operation(operation) else self._superseded()
