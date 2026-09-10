#!/usr/bin/env python3
"""Compose a portable Unity friend bundle without including live user data."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


WINDOWS_PLAYER_DIRECTORY = Path("unity/AIFrenUnityPoc/Builds/Windows")
WINDOWS_PLAYER_NAME = "AIFrenPoc.exe"
DEFAULT_CHARACTER_DIRECTORY = Path("characters/default")
RUNTIME_DIRECTORY_NAME = "runtime"

_TOP_LEVEL_RUNTIME_EXCLUDES = {
    ".git", ".venv", ".venv-aifren", ".venv-kokoro", "Builds", "build", "dist",
    "characters", "models", "unity", "logs", "temp", "tmp", "memory_v2",
    "memory_v2_shadow",
}
_EVERYWHERE_EXCLUDES = {
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".env",
    "config_secret.py", "config_local.py", "config_private.py", "local_settings.py",
    ".aifren_local_settings.json", ".aifren_managed_local_runtime.json",
}
_CANONICAL_ROOT_FILES = {
    "conversation.json", "conversation_summary.json", "memories.json", "memories.json.bak",
}


def _runtime_ignore(source_root: Path):
    def ignore(directory: str, names: list[str]) -> set[str]:
        current = Path(directory)
        excluded = set(_EVERYWHERE_EXCLUDES)
        try:
            if current.resolve() == source_root.resolve():
                excluded.update(_TOP_LEVEL_RUNTIME_EXCLUDES)
                excluded.update(_CANONICAL_ROOT_FILES)
        except OSError:
            pass
        excluded.update(name for name in names if name.endswith((".pyc", ".pyo", ".log")))
        excluded.update(name for name in names if name.startswith(".env."))
        return excluded.intersection(names)
    return ignore


def _copy_tree(source: Path, destination: Path, *, ignore=None) -> None:
    if not source.is_dir():
        raise RuntimeError(f"Required package directory is unavailable: {source}")
    shutil.copytree(source, destination, ignore=ignore)


def find_packaged_python(runtime_root: Path) -> Path | None:
    for relative in (Path("python.exe"), Path("Scripts/python.exe")):
        candidate = runtime_root / relative
        if candidate.is_file():
            return candidate
    return None


def validate_python_runtime(runtime_root: Path) -> None:
    python = find_packaged_python(runtime_root)
    if python is None:
        raise RuntimeError(
            "The staged Windows Python runtime must contain python.exe or Scripts\\python.exe."
        )
    required = (
        "websockets", "llama_cpp.server", "kokoro", "faster_whisper",
        "sounddevice", "sentence_transformers",
    )
    program = (
        "import importlib.util, sys\n"
        f"required = {required!r}\n"
        "missing = []\n"
        "for name in required:\n"
        "    try:\n"
        "        present = importlib.util.find_spec(name) is not None\n"
        "    except (ImportError, AttributeError):\n"
        "        present = False\n"
        "    if not present:\n"
        "        missing.append(name)\n"
        "print(','.join(missing))\n"
        "sys.exit(bool(missing))\n"
    )
    result = subprocess.run(
        [str(python), "-c", program], text=True, capture_output=True, check=False
    )
    if result.returncode != 0:
        missing = result.stdout.strip() or "unknown dependencies"
        raise RuntimeError(f"The staged Windows runtime is incomplete: {missing}")


def _replace_allowed(repository_root: Path, output_root: Path) -> bool:
    if output_root == (repository_root / "dist" / "AIFren").resolve():
        return True
    try:
        metadata = json.loads((output_root / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(metadata, dict) and metadata.get("platform") == "windows-x64"


def _write_seed_data(character_source: Path, seed_root: Path) -> None:
    for name in ("character.json", "personality.md"):
        source = character_source / name
        if not source.is_file():
            raise RuntimeError(f"The authorized package character is incomplete: {source}")
    character_target = seed_root / "characters" / "default"
    character_target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(character_source / "character.json", character_target / "character.json")
    shutil.copy2(character_source / "personality.md", character_target / "personality.md")
    values = {
        "conversation.json": [],
        "conversation_summary.json": {"summary": "", "summarized_messages": 0},
        "memories.json": [],
    }
    for name, value in values.items():
        (seed_root / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def compose_windows_package(
    repository_root: Path,
    output_root: Path,
    *,
    python_runtime: Path,
    character_source: Path | None = None,
    replace: bool = False,
    validate_runtime: bool = True,
) -> Path:
    """Compose ``dist/AIFren`` from an already-built Windows Unity player."""
    repository_root = repository_root.resolve()
    output_root = output_root.resolve()
    python_runtime = python_runtime.resolve()
    character_source = (
        character_source.resolve()
        if character_source is not None
        else repository_root / DEFAULT_CHARACTER_DIRECTORY
    )
    player_source = repository_root / WINDOWS_PLAYER_DIRECTORY
    if not (player_source / WINDOWS_PLAYER_NAME).is_file():
        raise RuntimeError(f"The Windows Unity player has not been built: {player_source}")
    if validate_runtime:
        validate_python_runtime(python_runtime)
    elif find_packaged_python(python_runtime) is None:
        raise RuntimeError("The staged Windows Python runtime has no Python executable.")
    if output_root.exists():
        if not replace:
            raise RuntimeError(f"Package output already exists (pass --replace): {output_root}")
        if not _replace_allowed(repository_root, output_root):
            raise RuntimeError(f"Refusing to replace an unverified package directory: {output_root}")
        shutil.rmtree(output_root)
    output_root.parent.mkdir(parents=True, exist_ok=True)

    _copy_tree(player_source, output_root)
    runtime_root = output_root / RUNTIME_DIRECTORY_NAME
    app_root = runtime_root / "app"
    _copy_tree(repository_root, app_root, ignore=_runtime_ignore(repository_root))
    _copy_tree(python_runtime, runtime_root / "python")

    models_source = repository_root / "models"
    models_target = app_root / "models"
    for name in ("kokoro-82m", "llama"):
        source = models_source / name
        if source.is_dir():
            _copy_tree(source, models_target / name)
    if not (models_target / "kokoro-82m").is_dir():
        raise RuntimeError("The packaged Kokoro model resources are unavailable.")
    if not (models_target / "llama").is_dir() or not any(
        (models_target / "llama").rglob("*.gguf")
    ):
        raise RuntimeError("The packaged local LLM model resources are unavailable.")

    _write_seed_data(character_source, app_root / "seed_data")
    shutil.copy2(
        repository_root / "scripts" / "launch_friend_windows.cmd",
        output_root / "Launch AIFren.cmd",
    )
    metadata = {
        "format_version": 1,
        "platform": "windows-x64",
        "player": WINDOWS_PLAYER_NAME,
        "resource_root": "runtime/app",
        "seed_data_root": "runtime/app/seed_data",
        "writable_data": "%LOCALAPPDATA%/AIFren",
    }
    (output_root / "package.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return output_root


def main() -> int:
    parser = argparse.ArgumentParser(description="Compose an AIFren Unity friend package.")
    parser.add_argument("--platform", choices=("windows",), default="windows")
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--python-runtime", required=True, type=Path)
    parser.add_argument("--character-source", type=Path)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--archive", action="store_true")
    options = parser.parse_args()
    repository = options.repository_root.resolve()
    output = options.output.resolve() if options.output else repository / "dist" / "AIFren"
    try:
        package = compose_windows_package(
            repository,
            output,
            python_runtime=options.python_runtime,
            character_source=options.character_source,
            replace=options.replace,
        )
        print(f"Windows friend package: {package}")
        if options.archive:
            archive = shutil.make_archive(
                str(package.parent / "AIFren-windows-x64"), "zip", package.parent, package.name
            )
            print(f"Windows friend archive: {archive}")
        return 0
    except (OSError, RuntimeError) as error:
        print(f"Windows package failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
