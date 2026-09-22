#!/usr/bin/env python3
"""Launch one packaged AIFren player with one owned, ready backend."""

from __future__ import annotations

import argparse
import atexit
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import socket
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid


APPLICATION_ROOT = Path(__file__).resolve().parents[1]
if str(APPLICATION_ROOT) not in sys.path:
    sys.path.insert(0, str(APPLICATION_ROOT))

from aifren.runtime.runtime_layout import absolute_path, packaged_user_data_root  # noqa: E402


READY_TIMEOUT_SECONDS = 60.0
OWNERSHIP_DESCRIPTOR = "backend-owner.json"
_phonemizer_data = None
_native_dll_directories = []


def prepare_packaged_runtime():
    """Retain Windows DLL search handles before importing neural providers."""
    if sys.platform == "win32" and not _native_dll_directories:
        site = APPLICATION_ROOT.parents[1] / "runtime/python/Lib/site-packages"
        for directory in (site / "torch/lib", site / "nvidia/cuda_runtime/bin"):
            if directory.is_dir():
                _native_dll_directories.append(os.add_dll_directory(str(directory)))
    prepare_packaged_phonemizer()


def _native_phoneme_path(path):
    """The pinned Windows eSpeak uses narrow stat/fopen, not UTF-8 paths."""
    value = str(path)
    if sys.platform == "win32" and not value.isascii():
        import ctypes
        get_short = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
        get_short.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        get_short.restype = ctypes.c_uint32
        size = get_short(value, None, 0)
        buffer = ctypes.create_unicode_buffer(size) if size else None
        written = get_short(value, buffer, size) if buffer is not None else 0
        if not written or written >= size or not buffer.value.isascii():
            raise RuntimeError("The phonemizer needs an ASCII-compatible system temporary path; a short path is unavailable.")
        value = buffer.value
    if len(os.fsencode(value)) > 128:
        raise RuntimeError("The system temporary directory exceeds the phonemizer's native path limit.")
    return value


def prepare_packaged_phonemizer():
    """Keep eSpeak 1.52's small native path buffer independent of install depth.

    Only packaged, generic phoneme resources are copied to a private process
    temporary directory. No character/reference data, source edits or symlinks.
    The installed application and its saved paths remain in place.
    """
    global _phonemizer_data
    if os.environ.get("AIFREN_ENGLISH_G2P") == "flite":
        return None  # The stock tester contains no eSpeak/phonemizer runtime.
    if _phonemizer_data is not None:
        return _phonemizer_data
    import espeakng_loader

    source = Path(espeakng_loader.get_data_path()).absolute()
    package = APPLICATION_ROOT.parents[1]
    if not source.is_relative_to(package):
        raise RuntimeError("Packaged phonemizer resolved outside this bundle.")
    for parent in (source, *source.parents):
        if parent.is_symlink():
            raise RuntimeError("Packaged phonemizer resources cannot follow links.")
    # Leave space for the native buffer's resource suffixes, measured in UTF-8
    # bytes, not Python characters. The pinned library has a 160-byte buffer.
    # Windows's pinned native library also treats UTF-8 as an ANSI filename.
    # A shallow Unicode installation therefore needs the same bounded alias.
    if len(os.fsencode(source)) <= 128 and (
        sys.platform != "win32" or str(source).isascii()
    ):
        return None
    files = []
    size = 0
    for path in source.rglob("*"):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise RuntimeError("Unsupported packaged phonemizer resource.")
        if stat.S_ISREG(mode):
            size += path.stat().st_size
            files.append(path)
        if len(files) > 8192 or size > 64 * 1024 * 1024:
            raise RuntimeError("Packaged phonemizer resources exceed their bound.")
    if not (source / "phontab").is_file():
        raise RuntimeError("Packaged phonemizer resources are incomplete.")
    temporary = tempfile.TemporaryDirectory(prefix="aifren-phonemes-")
    destination = Path(temporary.name) / "espeak-ng-data"
    try:
        destination.mkdir()
        native_path = _native_phoneme_path(destination)
        for path in files:
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        # Misaki obtains this resource path before constructing its eSpeak
        # wrapper. Do not change the library, inference or installed module.
        espeakng_loader.get_data_path = lambda: native_path
        _phonemizer_data = temporary
        atexit.register(temporary.cleanup)
        return temporary
    except BaseException:
        temporary.cleanup()
        raise


def package_environment(layout, data_root=None):
    """A bundle never inherits development code/data/model overrides."""
    forbidden = {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "HF_HOME", "HF_TOKEN",
                 "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE",
                 "HF_HUB_CACHE", "HF_ASSETS_CACHE", "HF_TOKEN_PATH", "HF_XET_CACHE",
                 "TORCH_HOME", "OPENAI_API_KEY", "GOOGLE_API_KEY", "SD_ENABLE_ASIO"}
    forbidden.update({"LD_LIBRARY_PATH", "LD_PRELOAD", "DYLD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES", "LLAMA_CPP_LIB_PATH"})
    environment = {key: value for key, value in os.environ.items()
                   if key not in forbidden and not key.startswith("AIFREN_")}
    environment.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
                       AIFREN_RESOURCE_ROOT=str(layout.resource_root),
                       HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                       HF_HUB_DISABLE_IMPLICIT_TOKEN="1",
                       AIFREN_KOKORO_DEVICE="cpu")
    manifest = layout.package_root / "package.json"
    profile = json.loads(manifest.read_text(encoding="utf-8")).get("runtime_profile") if manifest.is_file() else None
    if profile == "nvidia-stock-v1":
        environment.update(AIFREN_INFERENCE_DEVICE="cuda", AIFREN_KOKORO_DEVICE="cuda",
                           AIFREN_ENGLISH_G2P="flite", AIFREN_STT_PCM_ONLY="1",
                           AIFREN_TTS_PROVIDER="kokoro")
        site = layout.package_root / "runtime/python/Lib/site-packages"
        # Exact bundled native search locations; no development CUDA toolkit.
        environment["CUDA_PATH"] = str(site / "nvidia/cuda_runtime")
        environment["PATH"] = os.pathsep.join([str(site / "torch/lib"),
            str(site / "nvidia/cuda_runtime/bin"), str(site / "nvidia/cublas/bin"),
            str(site / "nvidia/cudnn/bin"), environment.get("PATH", "")])
    if sys.platform.startswith("linux"):
        runtime = layout.package_root / "runtime/python"
        libraries = [runtime / "lib"]
        for site in ("dist-packages", "site-packages"):
            for vendor in ("cuda_runtime", "cublas", "cudnn", "cuda_nvrtc", "cusparse", "cusolver", "cufft", "curand", "nccl", "nvjitlink", "nvtx", "cufile"):
                path = runtime / "lib/python3.12" / site / "nvidia" / vendor / "lib"
                if path.is_dir(): libraries.append(path)
        environment["LD_LIBRARY_PATH"] = os.pathsep.join(str(path) for path in libraries)
    if data_root is not None:
        environment.update(AIFREN_DATA_ROOT=str(data_root),
                           HF_HOME=str(data_root / "model-cache"),
                           TORCH_HOME=str(data_root / "model-cache"))
        environment["CUPY_CACHE_DIR"] = str(data_root / "compute-cache")
    return environment


@dataclass(frozen=True)
class FriendPackageLayout:
    package_root: Path
    resource_root: Path
    seed_data_root: Path
    player: Path
    backend: Path
    checker: Path

    @classmethod
    def from_package_root(cls, package_root: str | os.PathLike[str]) -> "FriendPackageLayout":
        root = absolute_path(package_root)
        resources = root / "runtime" / "app"
        return cls(
            package_root=root,
            resource_root=resources,
            seed_data_root=resources / "seed_data",
            player=root / ("AIFrenPoc.x86_64" if (root / "AIFrenPoc.x86_64").is_file() else "AIFrenPoc.exe"),
            backend=resources / 'backend_host.py',
            checker=resources / "scripts" / "check_backend_protocol.py",
        )

    def validate(self) -> None:
        for label, path in (
            ("Unity player", self.player),
            ("backend", self.backend),
            ("readiness checker", self.checker),
        ):
            if not path.is_file():
                raise RuntimeError(f"The packaged AIFren {label} is unavailable: {path}")
        if not self.seed_data_root.is_dir():
            raise RuntimeError(f"The packaged AIFren seed data is unavailable: {self.seed_data_root}")
        assembly = self.package_root / "AIFrenPoc_Data/Managed/AIFren.UnityPoc.dll"
        # Older clients ignore the package preference argument and would use
        # host PlayerPrefs. Fail before opening data or launching such a client.
        contents = assembly.read_bytes() if assembly.is_file() and assembly.stat().st_size <= 32 * 1024 * 1024 else b""
        if ("-aifren-preferences-file".encode("utf-16le") not in contents
                or b"get_ManagedDataRoot" not in contents):
            raise RuntimeError("The packaged player lacks isolated persistent preferences. Rebuild the current client before launching this bundle.")


def backend_command(
    python: Path,
    layout: FriendPackageLayout,
    data_root: Path,
) -> list[str]:
    """Build an argv list so spaces never pass through shell parsing."""
    return [
        str(python),
        "-I",
        "-c",
        "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); from scripts.launch_friend import prepare_packaged_runtime; prepare_packaged_runtime(); runpy.run_module('aifren.backend_host',run_name='__main__')",
        str(layout.resource_root),
        "--resource-root", str(layout.resource_root),
        "--data-root", str(data_root),
        "--seed-data-root", str(layout.seed_data_root),
    ]


def _checker(
    python: Path,
    layout: FriendPackageLayout,
    *arguments: str,
    owner_token: str = "",
) -> subprocess.CompletedProcess[str]:
    environment = package_environment(layout)
    if owner_token:
        environment["AIFREN_BACKEND_OWNER_TOKEN"] = owner_token
    else:
        environment.pop("AIFREN_BACKEND_OWNER_TOKEN", None)
    return subprocess.run(
        [str(python), "-I", str(layout.checker), *arguments],
        cwd=layout.resource_root,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=8,
    )


def _descriptor_path(data_root: Path) -> Path:
    return data_root / "runtime" / OWNERSHIP_DESCRIPTOR


def _read_descriptor(data_root: Path, layout: FriendPackageLayout) -> dict | None:
    try:
        value = json.loads(_descriptor_path(data_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    try:
        uuid.UUID(str(value["owner_token"]))
    except (KeyError, ValueError, TypeError, AttributeError):
        return None
    expected = {
        "resource_root": str(layout.resource_root),
        "data_root": str(data_root),
        "backend": str(layout.backend),
    }
    if any(str(value.get(key) or "") != wanted for key, wanted in expected.items()):
        return None
    return value


def _write_descriptor(
    data_root: Path,
    layout: FriendPackageLayout,
    owner_token: str,
    process_id: int,
) -> None:
    path = _descriptor_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "owner_token": owner_token,
        "pid": int(process_id),
        "resource_root": str(layout.resource_root),
        "data_root": str(data_root),
        "backend": str(layout.backend),
    }
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".backend-owner-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(payload, output, sort_keys=True, separators=(",", ":"))
            output.flush()
            os.fsync(output.fileno())
        try:
            os.chmod(temporary_name, 0o600)
        except OSError:
            pass
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def _remove_matching_descriptor(data_root: Path, owner_token: str) -> None:
    path = _descriptor_path(data_root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(value, dict) and value.get("owner_token") == owner_token:
        path.unlink(missing_ok=True)


@dataclass
class BackendAuthority:
    owner_token: str = ""
    process: subprocess.Popen | None = None
    recovered: bool = False

    @property
    def owned(self) -> bool:
        return bool(self.owner_token)


def _backend_port_is_open() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 8765), timeout=0.5):
            return True
    except OSError:
        return False


def ensure_backend(
    python: Path,
    layout: FriendPackageLayout,
    data_root: Path,
    *,
    ready_timeout: float = READY_TIMEOUT_SECONDS,
) -> BackendAuthority:
    """Reuse a proven package backend or start one; never attach another owner."""
    if _backend_port_is_open():
        descriptor = _read_descriptor(data_root, layout)
        if descriptor is not None:
            token = str(descriptor["owner_token"])
            if _checker(python, layout, "--owner-check", owner_token=token).returncode == 0:
                print("Recovered the ready AIFren package backend.")
                return BackendAuthority(owner_token=token, recovered=True)
        raise RuntimeError(
            "Port 8765 has no matching package owner. Close that application before launching this bundle; "
            "it will not be stopped or reused, and no competing backend was started."
        )

    data_root.mkdir(parents=True, exist_ok=True)
    owner_token = str(uuid.uuid4())
    environment = package_environment(layout, data_root)
    environment["AIFREN_BACKEND_OWNER_TOKEN"] = owner_token
    environment["AIFREN_RESOURCE_ROOT"] = str(layout.resource_root)
    environment["AIFREN_DATA_ROOT"] = str(data_root)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    launch_options: dict[str, object] = {
        "cwd": str(data_root),
        "env": environment,
    }
    if os.name == "nt":
        launch_options["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        launch_options["start_new_session"] = True
    # A release launcher never retains arbitrary child stdout, even for the
    # lifetime after readiness. Backend errors use existing structured transport.
    process = subprocess.Popen(
            backend_command(python, layout, data_root),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **launch_options,
    )
    _write_descriptor(data_root, layout, owner_token, process.pid)

    deadline = time.monotonic() + max(0.1, float(ready_timeout))
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _remove_matching_descriptor(data_root, owner_token)
            raise RuntimeError(
                f"The AIFren backend exited with code {process.returncode}; "
                "use the existing reconnect/console controls."
            )
        if _checker(python, layout, "--owner-check", owner_token=owner_token).returncode == 0:
            print(f"AIFren backend is ready (PID {process.pid}).")
            return BackendAuthority(owner_token=owner_token, process=process)
        time.sleep(0.25)

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    _remove_matching_descriptor(data_root, owner_token)
    raise RuntimeError("The AIFren backend did not reach protocol readiness.")


def stop_backend(
    python: Path,
    layout: FriendPackageLayout,
    data_root: Path,
    authority: BackendAuthority,
) -> None:
    """Stop only a backend whose nonce proves this package's ownership."""
    if not authority.owned:
        return
    ownership_proven = _checker(
        python, layout, "--owner-check", owner_token=authority.owner_token
    ).returncode == 0
    if ownership_proven:
        _checker(python, layout, "--shutdown", owner_token=authority.owner_token)
    process = authority.process
    if process is not None and process.poll() is None:
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=4)
    elif process is None and ownership_proven:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if _checker(
                python, layout, "--owner-check", owner_token=authority.owner_token
            ).returncode != 0:
                break
            time.sleep(0.2)
        else:
            # Retain the nonce descriptor so a later launcher can prove and
            # retry cleanup; never degrade to PID- or port-based termination.
            print("The recovered AIFren backend is still stopping; ownership was retained.")
            return
    _remove_matching_descriptor(data_root, authority.owner_token)


class WindowsLaunchMutex:
    """Per-data-root mutex preventing competing packaged launch sessions."""

    def __init__(self, data_root: Path) -> None:
        self._handle = None
        self._data_root = data_root

    def __enter__(self) -> "WindowsLaunchMutex":
        if os.name != "nt":
            return self
        import ctypes

        digest = hashlib.sha256(str(self._data_root).casefold().encode("utf-8")).hexdigest()[:24]
        name = "Local\\AIFren.Friend." + digest
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
        create_mutex.restype = ctypes.c_void_p
        kernel32.ReleaseMutex.argtypes = (ctypes.c_void_p,)
        kernel32.ReleaseMutex.restype = ctypes.c_bool
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.restype = ctypes.c_bool
        handle = create_mutex(None, False, name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            raise RuntimeError("AIFren is already starting or running for this user data root.")
        self._handle = (kernel32, handle)
        return self

    def __exit__(self, *_args) -> None:
        if self._handle is not None:
            kernel32, handle = self._handle
            kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)
            self._handle = None


def launch_friend(
    package_root: Path,
    *,
    data_root: Path | None = None,
    player_arguments: tuple[str, ...] = (),
    portable: bool = False,
) -> int:
    layout = FriendPackageLayout.from_package_root(package_root)
    layout.validate()
    if data_root is not None and portable:
        raise RuntimeError("Choose either portable mode or an explicit data directory.")
    # Only an explicit CLI selection can override package data, never a stale
    # development AIFREN_DATA_ROOT environment variable.
    environment = package_environment(layout)
    data = absolute_path(data_root or (layout.package_root / "UserData" if portable else
                                      packaged_user_data_root(environment=environment)))
    if any(argument.casefold() in {"-aifren-preferences-file", "-aifren-qa-plan"} for argument in player_arguments):
        raise RuntimeError("Package preferences and QA isolation are owned by the launcher.")
    python = absolute_path(sys.executable)
    with WindowsLaunchMutex(data):
        print("Starting AIFren and its local inference runtime. Please wait…", flush=True)
        authority = ensure_backend(python, layout, data)
        try:
            player = subprocess.Popen(
                [str(layout.player), *player_arguments, "-aifren-preferences-file", str(data / "presentation.json"), "-logFile", os.devnull],
                cwd=layout.package_root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=package_environment(layout, data),
            )
            return int(player.wait())
        finally:
            stop_backend(python, layout, data, authority)


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch a packaged AIFren friend build.")
    parser.add_argument("--package-root", required=True, type=Path)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--portable", action="store_true", help="Keep this bundle's data/preferences in its UserData directory.")
    parser.add_argument("player_arguments", nargs=argparse.REMAINDER)
    options = parser.parse_args()
    arguments = tuple(options.player_arguments)
    if arguments[:1] == ("--",):
        arguments = arguments[1:]
    try:
        return launch_friend(
            options.package_root,
            data_root=options.data_root,
            player_arguments=arguments,
            portable=options.portable,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"AIFren launch failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
