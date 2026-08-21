#!/usr/bin/env python3
"""Safely manage the repository-owned Linux AIFren backend for dev launch."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


PORT = 8765
READY_TIMEOUT_SECONDS = 30


def absolute_path(path: Path) -> Path:
    """Make a path absolute without resolving a virtual-environment symlink."""
    return Path(os.path.abspath(path.expanduser()))


def command_for_pid(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", errors="replace"
        )
    except OSError:
        return ""


def listener_pid() -> int | None:
    result = subprocess.run(
        ["ss", "-H", "-ltnp", f"sport = :{PORT}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"Could not inspect port {PORT}: {result.stderr.strip()}")

    for field in result.stdout.split():
        if "pid=" not in field:
            continue
        value = field.split("pid=", 1)[1].split(",", 1)[0].rstrip(")")
        if value.isdigit():
            return int(value)
    if result.stdout.strip():
        raise RuntimeError(
            f"Port {PORT} is in use but its owner cannot be identified; it will not be stopped."
        )
    return None


def is_expected_backend(pid: int, repository_root: Path) -> bool:
    command = command_for_pid(pid)
    return "backend_host.py" in command and str(repository_root / "backend_host.py") in command


def run_protocol_check(python: Path, checker: Path, *arguments: str) -> int:
    return subprocess.run([str(python), str(checker), *arguments], check=False).returncode


def wait_for_exit(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not Path(f"/proc/{pid}").exists():
            return True
        time.sleep(0.2)
    return not Path(f"/proc/{pid}").exists()


def stop_expected_backend(python: Path, checker: Path, pid: int, repository_root: Path) -> None:
    if not is_expected_backend(pid, repository_root):
        raise RuntimeError(f"Refusing to stop unverified process {pid} on port {PORT}.")

    print(f"Stopping repository-owned AIFren backend (PID {pid}).")
    run_protocol_check(python, checker, "--shutdown")
    if wait_for_exit(pid, 6):
        return
    os.kill(pid, signal.SIGTERM)
    if wait_for_exit(pid, 3):
        return
    os.kill(pid, signal.SIGKILL)
    if not wait_for_exit(pid, 1):
        raise RuntimeError(f"Could not stop AIFren backend PID {pid}.")


def start_backend(python: Path, repository_root: Path, ownership_file: Path) -> None:
    checker = repository_root / "scripts" / "check_backend_protocol.py"
    backend = repository_root / "backend_host.py"
    existing_pid = listener_pid()
    if existing_pid is not None:
        if not is_expected_backend(existing_pid, repository_root):
            raise RuntimeError(
                f"Port {PORT} is owned by an unrelated or unverifiable process (PID {existing_pid}); "
                "it will not be stopped."
            )
        stop_expected_backend(python, checker, existing_pid, repository_root)

    logs = repository_root / "logs"
    logs.mkdir(exist_ok=True)
    log_handle = (logs / "aifren-backend-linux.log").open("a", encoding="utf-8")
    process = subprocess.Popen(
        [str(python), str(backend)],
        cwd=repository_root,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log_handle.close()

    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"AIFren backend exited with code {process.returncode}; see logs/aifren-backend-linux.log."
            )
        if run_protocol_check(python, checker) == 0:
            ownership_file.parent.mkdir(parents=True, exist_ok=True)
            ownership_file.write_text(f"{process.pid}\n", encoding="utf-8")
            print(f"AIFren backend transport v2 is ready (PID {process.pid}).")
            return
        time.sleep(1)

    stop_expected_backend(python, checker, process.pid, repository_root)
    raise RuntimeError("AIFren backend did not reach transport v2 readiness.")


def ensure_backend(python: Path, repository_root: Path, ownership_file: Path) -> None:
    """Reuse a healthy repository backend, or safely start one when absent."""
    existing_pid = listener_pid()
    if existing_pid is not None:
        if not is_expected_backend(existing_pid, repository_root):
            raise RuntimeError(
                f"Port {PORT} is owned by an unrelated or unverifiable process (PID {existing_pid}); "
                "it will not be stopped."
            )
        checker = repository_root / "scripts" / "check_backend_protocol.py"
        if run_protocol_check(python, checker) == 0:
            print(f"AIFren backend transport v2 is already ready (PID {existing_pid}).")
            return

    start_backend(python, repository_root, ownership_file)


def stop_owned_backend(python: Path, repository_root: Path, ownership_file: Path) -> None:
    if not ownership_file.is_file():
        return
    try:
        pid = int(ownership_file.read_text(encoding="utf-8").strip())
    except ValueError:
        ownership_file.unlink(missing_ok=True)
        return
    ownership_file.unlink(missing_ok=True)
    if Path(f"/proc/{pid}").exists():
        stop_expected_backend(python, repository_root / "scripts" / "check_backend_protocol.py", pid, repository_root)


def main() -> int:
    parser = argparse.ArgumentParser()
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--start", action="store_true")
    actions.add_argument("--ensure", action="store_true")
    actions.add_argument("--stop", action="store_true")
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--ownership-file", required=True, type=Path)
    args = parser.parse_args()

    repository_root = args.repository_root.resolve()
    # .venv-aifren/bin/python is commonly a symlink. Resolving it would select
    # the system interpreter and drop the virtual environment's site-packages.
    python = absolute_path(args.python)
    if not python.is_file():
        raise RuntimeError(f"AIFren runtime was not found: {python}")
    if not (repository_root / "backend_host.py").is_file():
        raise RuntimeError(f"AIFren repository was not found: {repository_root}")

    if args.start:
        start_backend(python, repository_root, args.ownership_file)
    elif args.ensure:
        ensure_backend(python, repository_root, args.ownership_file)
    else:
        stop_owned_backend(python, repository_root, args.ownership_file)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"AIFren backend lifecycle error: {error}", file=sys.stderr)
        raise SystemExit(1)
