#!/usr/bin/env python3
"""Select reviewed Linux package inputs. Never discover data or local settings."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat


SOURCE_MANIFEST = Path(__file__).with_name("package_runtime_files.txt")
FORBIDDEN = {"characters", "logs", "cache", "caches", "seed_data",
             "conversation.json", "conversation_summary.json", "memories.json",
             "config_secret.py", "local_settings.py", "config_private.py"}
# These reviewed Windows wheel resources retain their loader-relative location.
# This is not permission to copy arbitrary hidden directories; normal
# per-file digest, ownership and symlink checks still apply.
REVIEWED_HIDDEN_NATIVE_INPUTS = frozenset({
    "runtime/python/Lib/site-packages/sklearn/.libs/msvcp140.dll",
    "runtime/python/Lib/site-packages/sklearn/.libs/vcomp140.dll",
    "runtime/python/Lib/site-packages/cupy/.data/_depends.json",
    "runtime/python/Lib/site-packages/cupy/.data/_wheel.json",
})


def relative_name(value: str) -> Path:
    if not isinstance(value, str) or "\\" in value or "\x00" in value:
        raise ValueError("invalid_package_path")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(
        part in {".", ".."}
        or (part.startswith(".") and value not in REVIEWED_HIDDEN_NATIVE_INPUTS)
        or part.casefold() in FORBIDDEN
        for part in path.parts
    ) or any(value.casefold().endswith(suffix) for suffix in (".log", ".sqlite3", ".sqlite", ".db", ".wav")):
        raise ValueError("unapproved_package_path")
    return Path(*path.parts)


def owned_file(root: Path, relative: Path) -> Path:
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("package_symlink_rejected")
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("package_input_unavailable")
    return path


def application_inputs() -> tuple[str, ...]:
    return tuple(line for line in SOURCE_MANIFEST.read_text().splitlines()
                 if line and not line.startswith("#"))


def copy_application(source: Path, target: Path) -> None:
    """The production source selector, also used by isolated sentinel tests."""
    source = source.absolute()
    if source.is_symlink() or target.exists():
        raise ValueError("package_root_not_fresh")
    if any(parent.is_symlink() for parent in (*source.parents, *target.absolute().parents)):
        raise ValueError("package_symlink_rejected")
    files = [(relative_name(name), owned_file(source, relative_name(name)))
             for name in application_inputs()]
    target.mkdir(parents=True)
    for relative, path in files:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)


def write_clean_seed(app: Path, *, default_model="") -> None:
    seed = app / "seed_data"
    values = {
        ".aifren_local_settings.json": {
            "model_mode": "local", "local_endpoint": "http://127.0.0.1:8000/v1",
            "local_model": default_model, "local_auto_start": bool(default_model),
            "responsive_speech": True,
        },
        "conversation.json": [],
        "conversation_summary.json": {"summary": "", "summarized_messages": 0},
        "memories.json": [],
        "characters/default/character.json": {
            "name": "Companion", "description": "", "version": "1.0",
            "voice": {"provider": None, "voice_id": None},
            "avatar": {"enabled": True, "model": None, "path": None},
        },
    }
    for name, value in values.items():
        path = seed / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    (seed / "characters/default/personality.md").write_text(
        "You are a friendly AI companion.\n", encoding="utf-8")


def compose_package(source: Path, staging: Path, output: Path, inputs: list[dict], *, platform="linux-x64", default_model="", nvidia_tester=False) -> Path:
    """Copy exact approved runtime/player/model files, never a live tree.

    `inputs` is a separately reviewed list of {path, sha256}. Paths are under a
    clean staging root, with package-relative layout. Licensing/portable native
    runtime validation is a separate release prerequisite, not inferred here.
    """
    if platform not in {"linux-x64", "windows-x64"}:
        raise ValueError("unsupported_package_platform")
    if nvidia_tester and platform != "windows-x64":
        raise ValueError("tester_profile_requires_windows")
    player_files = ({"AIFrenPoc.x86_64", "UnityPlayer.so", "UnityCrashHandler64"} if platform == "linux-x64" else
                    {"AIFrenPoc.exe", "UnityPlayer.dll", "UnityCrashHandler64.exe"})
    if output.exists() or output.is_symlink() or staging.is_symlink():
        raise ValueError("package_root_not_fresh")
    for parent in (*output.absolute().parents, *staging.absolute().parents):
        if parent.is_symlink():
            raise ValueError("package_symlink_rejected")
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= 100000:
        raise ValueError("invalid_package_inputs")
    approved = []
    seen = set()
    runtime_prefixes = ("runtime/python/", "runtime/app/models/", "AIFrenPoc_Data/", "ThirdPartyNotices/")
    if platform == "windows-x64":
        runtime_prefixes += ("MonoBleedingEdge/",)
    for item in inputs:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ValueError("invalid_package_input")
        relative = relative_name(item["path"])
        name = relative.as_posix()
        if name in seen or not (name.startswith(runtime_prefixes)
                               or name in player_files):
            raise ValueError("unapproved_package_destination")
        seen.add(name)
        path = owned_file(staging, relative)
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != item["sha256"]:
            raise ValueError("package_input_digest_mismatch")
        approved.append((relative, path))
    required = ({"AIFrenPoc.x86_64", "runtime/python/bin/python"} if platform == "linux-x64" else
                {"AIFrenPoc.exe", "UnityPlayer.dll", "runtime/python/python.exe",
                 "MonoBleedingEdge/EmbedRuntime/mono-2.0-bdwgc.dll",
                 "MonoBleedingEdge/EmbedRuntime/MonoPosixHelper.dll",
                 "MonoBleedingEdge/etc/mono/config"})
    if not required.issubset(seen):
        raise ValueError("required_package_input_missing")
    if default_model and (Path(default_model).name != default_model or
                          "runtime/app/models/llama/" + default_model not in seen):
        raise ValueError("default_model_not_in_reviewed_inventory")
    copy_application(source, output / "runtime/app")
    for relative, path in approved:
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        target.chmod(0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644)
    write_clean_seed(output / "runtime/app", default_model=default_model)
    if platform == "linux-x64":
        launcher = output / "run-aifren.sh"
        launcher.write_text('''#!/usr/bin/env bash
set -euo pipefail
unset LD_PRELOAD LD_LIBRARY_PATH DYLD_LIBRARY_PATH DYLD_INSERT_LIBRARIES
bundle_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
exec "$bundle_root/runtime/python/bin/python" -I "$bundle_root/runtime/app/scripts/launch_friend.py" --package-root "$bundle_root" "$@"
''', encoding="utf-8")
        launcher.chmod(0o755)
    else:
        (output / "Launch AIFren.cmd").write_text(
            '@echo off\r\n"%~dp0runtime\\python\\python.exe" -I "%~dp0runtime\\app\\scripts\\launch_friend.py" --package-root "%~dp0." '
            + ('--portable ' if nvidia_tester else '') + '%*\r\nif errorlevel 1 pause\r\n', encoding="utf-8")
    (output / "package.json").write_text(json.dumps({
        "format_version": 2, "platform": platform, "resource_root": "runtime/app",
        "default_model": default_model,
        "runtime_profile": "nvidia-stock-v1" if nvidia_tester else "legacy-candidate",
        "writable_data": "explicit --portable uses UserData; otherwise per-user application data",
        "inputs": inputs,
    }, indent=2) + "\n", encoding="utf-8")
    return output


def compose_linux_package(source: Path, staging: Path, output: Path, inputs: list[dict], *, default_model="") -> Path:
    return compose_package(source, staging, output, inputs, platform="linux-x64", default_model=default_model)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--default-model", default="", help="Reviewed included GGUF basename; no developer settings are read.")
    args = parser.parse_args()
    try:
        compose_linux_package(args.source_root, args.staging_root, args.output,
                              json.loads(args.inputs.read_text(encoding="utf-8")), default_model=args.default_model)
    except (OSError, ValueError):
        parser.exit(1, "Package selection failed; check approved inputs and fresh output.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
