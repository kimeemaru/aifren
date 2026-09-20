#!/usr/bin/env python3
"""Compose a Windows bundle from the same explicit inventory as Linux.

Only clean, reviewed staging files are accepted. A checkout, a live venv and a
character directory are not package input selectors. This command does not build
or certify a Windows native runtime.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import shutil

_SELECTOR_PATH = Path(__file__).with_name("package_linux.py")
_spec = importlib.util.spec_from_file_location("aifren_package_selector", _SELECTOR_PATH)
_selector = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_selector)


def compose_windows_package(source, staging, output, inputs, *, default_model=""):
    return _selector.compose_package(Path(source), Path(staging), Path(output), inputs, platform="windows-x64", default_model=default_model)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", action="store_true")
    parser.add_argument("--default-model", default="")
    args = parser.parse_args()
    try:
        package = compose_windows_package(args.source_root, args.staging_root, args.output,
                                          json.loads(args.inputs.read_text(encoding="utf-8")), default_model=args.default_model)
        if args.archive:
            archive = package.parent / (package.name + ".zip")
            if archive.exists():
                raise ValueError("Archive already exists; choose a fresh output.")
            shutil.make_archive(str(package), "zip", package.parent, package.name)
    except (OSError, ValueError):
        parser.exit(1, "Windows package selection failed. Check the reviewed inventory and fresh output.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
