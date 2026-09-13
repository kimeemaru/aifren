#!/usr/bin/env python3
"""Developer-only controls and diagnostics around the Unity shell launcher.

This is not a companion frontend.  It invokes ``aifren_dev_linux.sh`` and
shows its output; that shell script remains the owner of Unity/backend lifecycle.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, scrolledtext


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aifren.runtime.development_flight_recorder import ProcessOutputCapture


def build_launch_arguments(launch_script: Path, action: str, development: bool, *, reset_console: bool = False, reset_ui: bool = False) -> list[str]:
    """Return only arguments accepted by the current shell-launch contract."""
    if action not in {"current", "rebuild"}:
        raise ValueError(f"unsupported AIFren development action: {action}")
    arguments = [str(launch_script), action]
    if development:
        arguments.append("development")
    if reset_console:
        arguments.append("reset-console")
    if reset_ui:
        arguments.append("reset-ui")
    return arguments


class AIFrenDevLauncher(tk.Tk):
    """Thin GUI wrapper around the current Unity development shell launcher."""

    def __init__(self, repository_root: Path) -> None:
        super().__init__()
        self.repository_root = repository_root
        self.launch_script = repository_root / "scripts" / "aifren_dev_linux.sh"
        self.preferences_path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aifren" / "dev-launcher.json"
        self.process: subprocess.Popen[str] | None = None
        self.stop_request_file: Path | None = None
        self.output = ProcessOutputCapture(development=True)
        self.closing = False
        self.launch_buttons: list[tk.Button] = []

        self.title("AIFren Dev Launcher")
        self.geometry("820x600")
        self.minsize(660, 450)
        self.protocol("WM_DELETE_WINDOW", self.close)

        controls = tk.Frame(self, padx=12, pady=12)
        controls.pack(fill=tk.X)
        self.reset_console = tk.BooleanVar(value=False)
        self.reset_ui = tk.BooleanVar(value=False)
        tk.Checkbutton(controls, text="Reset Console unlock", variable=self.reset_console).pack(side=tk.LEFT)
        tk.Checkbutton(controls, text="Reset UI/display settings", variable=self.reset_ui).pack(side=tk.LEFT, padx=(14, 0))

        self.vrma_qa_folder = tk.StringVar(value=self.load_vrma_qa_folder())
        actions = tk.Frame(self, padx=12)
        actions.pack(fill=tk.X)
        self.add_start_button(actions, "Start Development Build", "current", development=True)
        self.add_start_button(actions, "Rebuild Development + Start", "rebuild", development=True)
        qa_button = tk.Menubutton(actions, text="VRMA QA…", relief=tk.RAISED)
        qa_menu = tk.Menu(qa_button, tearoff=False)
        qa_menu.add_command(label="Change QA folder…", command=self.choose_vrma_qa_folder)
        qa_menu.add_command(label="Clear remembered QA folder", command=self.clear_vrma_qa_folder)
        qa_button.configure(menu=qa_menu)
        qa_button.pack(side=tk.LEFT, padx=(8, 0))
        self.stop_button = tk.Button(actions, text="Stop AIFren", command=self.stop, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=(8, 0))

        self.status = tk.StringVar(value="Ready")
        tk.Label(self, textvariable=self.status, anchor=tk.W, padx=12, pady=8).pack(fill=tk.X)
        self.log = scrolledtext.ScrolledText(self, wrap=tk.WORD, state=tk.DISABLED, font=("monospace", 9))
        self.log.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
        self.after(100, self.drain_output)

    def add_start_button(self, parent: tk.Widget, label: str, action: str, *, development: bool) -> None:
        button = tk.Button(parent, text=label, command=lambda: self.start(action, development))
        button.pack(side=tk.LEFT, padx=(8 if self.launch_buttons else 0, 0))
        self.launch_buttons.append(button)

    def append(self, line: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, line[:256])
        # Bound the visible widget independently of the already bounded queue.
        if int(self.log.index("end-1c").split(".")[0]) > 512:
            self.log.delete("1.0", "end-512l")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def start(self, action: str, development: bool = False) -> None:
        if self.process is not None and self.process.poll() is None:
            self.status.set("AIFren is already starting or running.")
            return
        if not self.launch_script.is_file():
            self.status.set("Linux launcher script is missing.")
            return

        arguments = build_launch_arguments(self.launch_script, action, development, reset_console=self.reset_console.get(), reset_ui=self.reset_ui.get())
        # Deliberate recovery actions are one-shot, never inherited by a later start.
        self.reset_console.set(False)
        self.reset_ui.set(False)
        self.append("Starting reviewed launcher action.\n")
        descriptor, stop_request = tempfile.mkstemp(prefix="aifren-dev-stop-", suffix=".request")
        os.close(descriptor)
        self.stop_request_file = Path(stop_request)
        self.stop_request_file.unlink()
        environment = os.environ.copy()
        environment["AIFREN_STOP_REQUEST_FILE"] = str(self.stop_request_file)
        qa_folder = self.vrma_qa_folder.get().strip()
        if development and Path(qa_folder).is_dir():
            environment["AIFREN_VRMA_QA_DIR"] = qa_folder
        else:
            environment.pop("AIFREN_VRMA_QA_DIR", None)
        try:
            self.process = subprocess.Popen(arguments, cwd=self.repository_root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0, start_new_session=True, env=environment)
        except OSError:
            self.stop_request_file.unlink(missing_ok=True)
            self.stop_request_file = None
            self.status.set("Launch failed; check the runtime and retry.")
            return
        self.output = ProcessOutputCapture(development=development,
            directory=self.preferences_path.parent / "output-counters")
        threading.Thread(target=self.output.drain, args=(self.process.stdout,), daemon=True).start()
        for button in self.launch_buttons:
            button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        target = "Development build" if development else "current build"
        self.status.set(f"Building {target}..." if action == "rebuild" else f"Starting {target}...")
        self.after(200, self.poll_process)

    def drain_output(self) -> None:
        for line in self.output.take_records():
            self.append(line)
        if self.winfo_exists():
            self.after(100, self.drain_output)

    def poll_process(self) -> None:
        if self.process is None:
            return
        exit_code = self.process.poll()
        if exit_code is None:
            self.after(200, self.poll_process)
            return
        self.append(f"AIFren launcher exited with code {exit_code}.\n")
        self.process = None
        if self.stop_request_file is not None:
            self.stop_request_file.unlink(missing_ok=True)
            self.stop_request_file = None
        for button in self.launch_buttons:
            button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)
        self.status.set("Ready" if exit_code == 0 else "Launch failed; check runtime and reconnect controls.")
        if self.closing:
            self.destroy()

    def stop(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        self.status.set("Stopping launcher-owned AIFren session...")
        self.stop_button.configure(state=tk.DISABLED)
        if self.stop_request_file is not None:
            self.stop_request_file.touch()
            self.append("Requested a graceful player stop; waiting for launcher-owned cleanup.\n")

    def close(self) -> None:
        if self.process is None or self.process.poll() is not None:
            self.destroy()
            return
        self.closing = True
        self.stop()

    def load_vrma_qa_folder(self) -> str:
        try:
            value = json.loads(self.preferences_path.read_text(encoding="utf-8")).get("vrma_qa_folder", "")
            return str(Path(value).expanduser()) if value and Path(value).expanduser().is_dir() else ""
        except (OSError, ValueError, TypeError):
            return ""

    def save_vrma_qa_folder(self, folder: str) -> None:
        try:
            self.preferences_path.parent.mkdir(parents=True, exist_ok=True)
            self.preferences_path.write_text(json.dumps({"vrma_qa_folder": folder}) + "\n", encoding="utf-8")
        except OSError as error:
            self.status.set(f"Could not save VRMA QA folder: {error}")

    def choose_vrma_qa_folder(self) -> None:
        folder = filedialog.askdirectory(title="Choose local VRMA QA folder", initialdir=self.vrma_qa_folder.get().strip() or str(Path.home()))
        if not folder:
            return
        selected = Path(folder).expanduser()
        if not selected.is_dir():
            self.status.set("Selected VRMA QA folder is unavailable.")
            return
        self.vrma_qa_folder.set(str(selected))
        self.save_vrma_qa_folder(str(selected))

    def clear_vrma_qa_folder(self) -> None:
        self.vrma_qa_folder.set("")
        self.save_vrma_qa_folder("")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--launch", choices=("current", "rebuild"))
    parser.add_argument("--development", action="store_true")
    arguments = parser.parse_args()
    repository_root = Path(__file__).resolve().parent.parent
    launcher = repository_root / "scripts" / "aifren_dev_linux.sh"
    if arguments.smoke_test:
        if not launcher.is_file():
            raise RuntimeError(f"Linux launcher script is missing: {launcher}")
        for action in ("current", "rebuild"):
            for development in (False, True):
                command = [str(launcher), *build_launch_arguments(launcher, action, development)[1:], "--validate-arguments"]
                checked = subprocess.run(command, cwd=repository_root, text=True, capture_output=True)
                if checked.returncode != 0:
                    raise RuntimeError(f"shell launcher rejected {command[1:-1]}: {checked.stderr.strip()}")
        print("AIFren Linux developer launcher is ready; current shell arguments validated.")
        return 0
    app = AIFrenDevLauncher(repository_root)
    if arguments.launch:
        app.after_idle(lambda: app.start(arguments.launch, arguments.development))
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
