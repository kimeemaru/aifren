"""One owned resident CPU cloning worker, addressed by private process pipes."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
from queue import Queue, Empty
import subprocess
import threading
import time
import uuid

from aifren.runtime.runtime_layout import resource_path

REVISION = "48b1a0169a28582a8984402f82cf438d3bfa6aca"
MAX_PCM = 32 * 1024 * 1024


class CloneCancelled(RuntimeError):
    pass


class CloneRuntime:
    def __init__(self, installation=None):
        self.installation = Path(installation or resource_path("runtimes/gpt-sovits/runtime.json"))
        self.process = None
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._closed = threading.Event()
        self._responses = Queue(maxsize=2)
        self.instance = uuid.uuid4().hex
        self.state = "not_installed"
        self.load_seconds = None
        self.last_seconds = None

    def available(self):
        return self.installation.is_file()

    def _reader(self, process, responses):
        try:
            while True:
                line = process.stdout.readline(4097)
                if not line or len(line) > 4096:
                    raise ValueError()
                header = json.loads(line)
                length = header["bytes"]
                if (header.get("instance") != self.instance or type(length) is not int
                        or not 0 <= length <= MAX_PCM):
                    raise ValueError()
                pcm = process.stdout.read(length)
                if len(pcm) != length:
                    raise ValueError()
                responses.put((header, pcm), timeout=2)
        except Exception:
            try:
                responses.put_nowait(({"state": "failed"}, b""))
            except Exception:
                pass

    def _receive(self, timeout=120):
        deadline = time.monotonic() + timeout
        try:
            while True:
                if self._closed.is_set():
                    raise CloneCancelled()
                try:
                    response = self._responses.get(timeout=min(.2, max(.001, deadline-time.monotonic())))
                    break
                except Empty:
                    if time.monotonic() >= deadline:
                        raise
        except Empty as error:
            self._stop_owned()
            raise RuntimeError("Voice preparation timed out. Retry or choose Kokoro.") from error
        if response[0].get("state") != "ready":
            self._stop_owned()
            raise RuntimeError("The cloned voice could not be prepared. Check the installation and reference.")
        return response

    def _start(self, cancelled=None):
        if self._closed.is_set() or (cancelled is not None and cancelled.is_set()):
            raise CloneCancelled()
        if self.process is not None and self.process.poll() is None:
            return
        if not self.available():
            raise RuntimeError("GPT-SoVITS is not installed. Register the reviewed runtime before preparing a clone.")
        config = json.loads(self.installation.read_text(encoding="utf-8"))
        if config.get("version") != 1 or config.get("revision") != REVISION or config.get("device") != "cpu":
            raise RuntimeError("Unsupported cloned-voice installation.")
        root = Path(config["root"]).resolve()
        # Registration is an explicit local trust decision. Detect later source
        # or weight substitution before executing upstream code/pickle weights.
        for name, digest in config["files"].items():
            if self._closed.is_set() or (cancelled is not None and cancelled.is_set()):
                raise CloneCancelled()
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError("Invalid voice runtime inventory.")
            path = root / relative
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
                raise RuntimeError("Voice runtime input is unavailable.")
            with path.open("rb") as handle:
                if hashlib.file_digest(handle, "sha256").hexdigest() != digest:
                    raise RuntimeError("Voice runtime changed after registration. Verify it before use.")
        self.state = "preparing"
        self._responses = Queue(maxsize=2)
        environment = {k: v for k, v in os.environ.items()
                       if k not in {"PYTHONPATH", "PYTHONHOME"} and not k.startswith("AIFREN_")}
        environment.update(OMP_NUM_THREADS="4", MKL_NUM_THREADS="4", OPENBLAS_NUM_THREADS="4",
                           PYTHONNOUSERSITE="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        with self._lifecycle_lock:
            if self._closed.is_set() or (cancelled is not None and cancelled.is_set()):
                raise CloneCancelled()
            self.process = subprocess.Popen([config["python"], "-B", str(Path(__file__).with_name("clone_worker.py")),
                                         "--installation", str(self.installation), "--instance", self.instance],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        cwd=root, env=environment)
        threading.Thread(target=self._reader, args=(self.process, self._responses),
                         name="aifren-clone-pipe", daemon=True).start()
        ready, _ = self._receive()
        self.load_seconds = ready.get("seconds")

    def request(self, profile, reference, *, text=None, cancelled=None):
        while not self._lock.acquire(timeout=.05):
            if self._closed.is_set() or (cancelled is not None and cancelled.is_set()):
                raise CloneCancelled()
        try:
            if self._closed.is_set() or (cancelled is not None and cancelled.is_set()):
                raise CloneCancelled()
            self._start(cancelled)
            if cancelled is not None and cancelled.is_set():
                raise CloneCancelled()
            identity = uuid.uuid4().hex
            body = {"id": identity, "op": "prepare" if text is None else "synthesize",
                    "reference": str(reference), "key": profile.conditioning_key,
                    "transcript": profile.transcript, "language": profile.language, "text": text}
            self.process.stdin.write(json.dumps(body).encode() + b"\n")
            self.process.stdin.flush()
            header, pcm = self._receive()
            if header.get("id") != identity:
                self._stop_owned()
                raise RuntimeError("Cloned voice response ownership failed.")
            self.last_seconds = header.get("seconds")
            self.state = "ready"
            if cancelled is not None and cancelled.is_set():
                raise CloneCancelled()
            if text is None:
                return None
            import numpy as np
            return np.frombuffer(pcm, dtype="<f4").copy().reshape(-1, 1), int(header["rate"]), []
        except CloneCancelled:
            raise
        except Exception:
            self.state = "failed"
            raise
        finally:
            self._lock.release()

    def _stop_owned(self):
        with self._lifecycle_lock:
            process, self.process = self.process, None
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            for stream in (process.stdin, process.stdout):
                stream.close()
        self.state = "unavailable"

    def close(self):
        # Called only on owner shutdown, never by port/name or from PCM callbacks.
        self._closed.set()
        self._stop_owned()
