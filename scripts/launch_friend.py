#!/usr/bin/env python3
"""Launch one packaged AIFren player with one owned, ready backend."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import uuid


APPLICATION_ROOT = Path(__file__).resolve().parents[1]
if str(APPLICATION_ROOT) not in sys.path:
    sys.path.insert(0, str(APPLICATION_ROOT))

from runtime_layout import absolute_path, packaged_user_data_root  # noqa: E402


READY_TIMEOUT_SECONDS = 60.0
OWNERSHIP_DESCRIPTOR = "backend-owner.json"


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
            backend=resources / "backend_host.py",
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


def backend_command(
    python: Path,
    layout: FriendPackageLayout,
    data_root: Path,
) -> list[str]:
    """Build an argv list so spaces never pass through shell parsing."""
    return [
        str(python),
        str(layout.backend),
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
    environment = dict(os.environ)
    if owner_token:
        environment["AIFREN_BACKEND_OWNER_TOKEN"] = owner_token
    else:
        environment.pop("AIFREN_BACKEND_OWNER_TOKEN", None)
    return subprocess.run(
        [str(python), str(layout.checker), *arguments],
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
    """Reuse a proven package backend, recognize external AIFren, or start one."""
    classification = _checker(python, layout, "--classify")
    if classification.returncode == 0:
        descriptor = _read_descriptor(data_root, layout)
        if descriptor is not None:
            token = str(descriptor["owner_token"])
            if _checker(python, layout, "--owner-check", owner_token=token).returncode == 0:
                print("Recovered the ready AIFren package backend.")
                return BackendAuthority(owner_token=token, recovered=True)
        print("A compatible external AIFren backend is already ready; it will not be stopped.")
        return BackendAuthority()
    if classification.returncode == 2:
        raise RuntimeError("Port 8765 is owned by a non-AIFren listener; it will not be stopped.")
    if _backend_port_is_open():
        raise RuntimeError(
            "Port 8765 is already listening but could not prove AIFren readiness; "
            "no competing backend was started."
        )

    data_root.mkdir(parents=True, exist_ok=True)
    owner_token = str(uuid.uuid4())
    environment = dict(os.environ)
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
        if _checker(python, layout).returncode == 0:
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
) -> int:
    layout = FriendPackageLayout.from_package_root(package_root)
    layout.validate()
    data = absolute_path(data_root or packaged_user_data_root())
    python = absolute_path(sys.executable)
    with WindowsLaunchMutex(data):
        authority = ensure_backend(python, layout, data)
        try:
            player = subprocess.Popen(
                [str(layout.player), *player_arguments, "-logFile", os.devnull],
                cwd=layout.package_root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return int(player.wait())
        finally:
            stop_backend(python, layout, data, authority)


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch a packaged AIFren friend build.")
    parser.add_argument("--package-root", required=True, type=Path)
    parser.add_argument("--data-root", type=Path)
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
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print("AIFren launch failed; check runtime availability and listener ownership.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
