"""Small lifecycle manager for a local, persistent Audio8 HTTP runtime.

This module only speaks the Audio8 HTTP contract.  It owns neither character
data nor voice assets: an optional reference path/transcript comes from local
configuration and is registered once with the resident service.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Callable
from urllib import request
from urllib.parse import urlparse
import uuid


@dataclass
class Audio8RuntimeStatus:
    state: str = "starting"
    model_load_count: int = 0
    voice_condition_count: int = 0
    warmup_count: int = 0
    synthesis_count: int = 0
    startup_seconds: float | None = None
    service_ready_seconds: float | None = None
    voice_condition_seconds: float | None = None
    warmup_seconds: float | None = None
    last_synthesis_seconds: float | None = None
    error: str = ""

    def snapshot(self) -> dict[str, object]:
        return asdict(self)


class Audio8Runtime:
    """Start/reuse one trusted local Audio8 service and one registered voice."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        runtime_root: str = "",
        voice_profile: str = "",
        reference_audio: str = "",
        reference_text: str = "",
        timeout_seconds: float = 45,
        startup_timeout_seconds: float = 75,
        warmup_text: str = "Ready.",
        urlopen: Callable = request.urlopen,
        start_process: Callable = subprocess.Popen,
        stop_process: Callable = subprocess.run,
    ) -> None:
        self.base_url = str(base_url).rstrip("/")
        self.root_url = self.base_url.removesuffix("/v1")
        self.model = str(model)
        self.runtime_root = Path(runtime_root).expanduser() if runtime_root else None
        self.voice_profile = str(voice_profile or "").strip()
        self.reference_audio = Path(reference_audio).expanduser() if reference_audio else None
        self.reference_text = str(reference_text or "").strip()
        self.timeout_seconds = float(timeout_seconds)
        self.startup_timeout_seconds = float(startup_timeout_seconds)
        self.warmup_text = str(warmup_text or "Ready.")
        self._urlopen = urlopen
        self._start_process = start_process
        self._stop_process = stop_process
        self._status = Audio8RuntimeStatus()
        self._lock = threading.Lock()
        self._ready = False
        self._process = None
        self._owns_service = False

    @property
    def status(self) -> dict[str, object]:
        return self._status.snapshot()

    @property
    def failure_reason(self) -> str:
        if self.runtime_root is None:
            return "Audio8 runtime root is not configured"
        if not self.voice_profile and (self.reference_audio is None or not self.reference_text):
            return "Audio8 voice profile is not configured"
        return "Audio8 runtime is unavailable"

    def _http_json(self, url: str, body: dict | None = None, *, timeout: float | None = None) -> dict:
        encoded = None if body is None else json.dumps(body).encode("utf-8")
        req = request.Request(url, data=encoded, headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
        with self._urlopen(req, timeout=timeout or self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def _healthy(self) -> bool:
        try:
            return bool(self._http_json(self.root_url + "/api/health").get("ok"))
        except Exception:
            return False

    def _start_local_service(self) -> None:
        if self.runtime_root is None:
            raise RuntimeError("Audio8 service is unavailable and no local runtime root is configured")
        script = (self.runtime_root / "start_server.sh").resolve()
        if not script.is_file() or script.parent != self.runtime_root.resolve():
            raise RuntimeError("Audio8 runtime root does not contain start_server.sh")
        parsed = urlparse(self.root_url)
        environment = dict(os.environ)
        if parsed.port:
            environment["PORT"] = str(parsed.port)
        # The service is a resident runtime.  ``run`` here used to block on a
        # foreground server and guarantee fallback; the owned process now
        # continues while readiness is probed below.
        self._process = self._start_process(
            [str(script)], cwd=str(self.runtime_root), env=environment,
        )
        self._owns_service = True

    @property
    def owns_service(self) -> bool:
        return self._owns_service

    def shutdown_owned(self) -> bool:
        """Unload only the Audio8 service started by this runtime instance."""
        if not self._owns_service or self.runtime_root is None:
            return False
        stop_script = (self.runtime_root / "stop_server.sh").resolve()
        try:
            if stop_script.is_file() and stop_script.parent == self.runtime_root.resolve():
                self._stop_process(
                    [str(stop_script)], cwd=str(self.runtime_root), timeout=10,
                    check=False, capture_output=True,
                )
        except Exception:
            pass
        finally:
            self._owns_service = False
            self._process = None
            self._ready = False
            self._status.state = "stopped"
        return True

    def _wait_healthy(self) -> bool:
        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            if self._healthy():
                return True
            if self._process is not None and getattr(self._process, "poll", lambda: None)() is not None:
                return False
            time.sleep(0.2)
        return False

    @staticmethod
    def _multipart_reference(audio_path: Path, transcript: str, name: str) -> tuple[bytes, str]:
        boundary = "----aifren-" + uuid.uuid4().hex
        def field(key: str, value: str) -> bytes:
            return f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode()
        payload = bytearray()
        payload.extend(field("text", transcript))
        payload.extend(field("name", name))
        payload.extend(field("overwrite", "false"))
        payload.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"{audio_path.name}\"\r\nContent-Type: audio/wav\r\n\r\n".encode())
        payload.extend(audio_path.read_bytes())
        payload.extend(b"\r\n--" + boundary.encode() + b"--\r\n")
        return bytes(payload), boundary

    def _ensure_voice(self) -> None:
        if self.voice_profile:
            voices = self._http_json(self.root_url + "/api/voices").get("voices", [])
            names = {str(item.get("name", "")) for item in voices if isinstance(item, dict)}
            if self.voice_profile in names:
                return
        if self.reference_audio is None or not self.reference_text:
            if self.voice_profile:
                raise RuntimeError("configured Audio8 voice profile is not registered")
            raise RuntimeError("Audio8 voice profile is not configured")
        if not self.reference_audio.is_file():
            raise RuntimeError("configured Audio8 reference audio is unavailable")
        # A stable local profile name is required so the service itself persists
        # the codec conditioning across client restarts.
        name = self.voice_profile or "aifren_local_voice"
        payload, boundary = self._multipart_reference(self.reference_audio, self.reference_text, name)
        req = request.Request(
            self.root_url + "/api/voices/register", data=payload,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST",
        )
        with self._urlopen(req, timeout=self.startup_timeout_seconds) as response:
            result = json.loads(response.read().decode("utf-8"))
        registered = result.get("voice", {}).get("name") if isinstance(result, dict) else ""
        if not registered:
            raise RuntimeError("Audio8 voice registration returned no profile")
        self.voice_profile = str(registered)
        self._status.voice_condition_count += 1

    def ensure_ready(self) -> bool:
        with self._lock:
            if self._ready:
                return True
            started = time.monotonic()
            try:
                self._status.state = "loading"
                service_started = time.monotonic()
                if not self._healthy():
                    self._start_local_service()
                    if not self._wait_healthy():
                        raise RuntimeError("Audio8 service did not become healthy")
                self._status.service_ready_seconds = time.monotonic() - service_started
                self._status.model_load_count = 1
                self._status.state = "conditioning_voice"
                voice_started = time.monotonic()
                self._ensure_voice()
                self._status.voice_condition_seconds = time.monotonic() - voice_started
                self._status.state = "warming"
                # Audio8 requires a registered voice to synthesize.  If a
                # deployment deliberately has none yet, readiness still means
                # the resident runtime is usable once one is configured.
                if self.voice_profile:
                    warmup_started = time.monotonic()
                    self.request_audio(self.warmup_text, warmup=True)
                    self._status.warmup_seconds = time.monotonic() - warmup_started
                self._status.warmup_count += 1
                self._status.startup_seconds = time.monotonic() - started
                self._status.state = "ready"
                self._ready = True
                return True
            except Exception as error:
                self._status.state = "failed"
                self._status.error = type(error).__name__
                self.shutdown_owned()
                self._status.state = "failed"
                return False

    def request_audio(self, text: str, *, warmup: bool = False) -> bytes:
        if not self._ready and not warmup:
            raise RuntimeError("Audio8 runtime is not ready")
        body = {"model": self.model, "input": str(text), "response_format": "wav"}
        if self.voice_profile:
            body["voice"] = self.voice_profile
        started = time.monotonic()
        encoded = json.dumps(body).encode("utf-8")
        req = request.Request(self.base_url + "/audio/speech", data=encoded, headers={"Content-Type": "application/json"}, method="POST")
        with self._urlopen(req, timeout=self.timeout_seconds) as response:
            payload = response.read()
        if not warmup:
            self._status.synthesis_count += 1
            self._status.last_synthesis_seconds = time.monotonic() - started
        return payload
