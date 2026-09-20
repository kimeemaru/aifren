#!/usr/bin/env python3
"""Register a reviewed local GPT-SoVITS installation; does not install or train."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

REVISION = "48b1a0169a28582a8984402f82cf438d3bfa6aca"


def register(root, python, output):
    root, python, output = Path(root).resolve(), Path(python).absolute(), Path(output).absolute()
    if output.exists():
        raise ValueError("Use a new installation manifest path; existing registration is preserved.")
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if revision != REVISION or not python.is_file():
        raise ValueError("Use the reviewed GPT-SoVITS revision and its isolated interpreter.")
    tracked = subprocess.check_output(["git", "-C", str(root), "ls-files"], text=True).splitlines()
    code = [name for name in tracked if name.endswith(".py")]
    subprocess.run(["git", "-C", str(root), "diff", "--exit-code", "HEAD", "--", *code],
                   check=True, stdout=subprocess.DEVNULL)
    models = root / "GPT_SoVITS/pretrained_models"
    selected = [root / name for name in code] + [root / "LICENSE"]
    selected.extend(models / name for name in ("s1v3.ckpt", "v2Pro/s2Gv2ProPlus.pth", "sv/pretrained_eres2netv2w24s4ep4.ckpt"))
    for directory in ("chinese-hubert-base", "chinese-roberta-wwm-ext-large"):
        selected.extend(p for p in (models / directory).rglob("*") if p.is_file() and ".cache" not in p.parts)
    inventory = {}
    for path in selected:
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError("A reviewed runtime input is absent or linked outside the installation.")
        with path.open("rb") as stream:
            inventory[path.relative_to(root).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump({"version": 1, "revision": REVISION, "root": str(root), "python": str(python),
                   "device": "cpu", "files": inventory}, stream, indent=2)
    return len(inventory)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        count = register(args.root, args.python, args.output)
        print(f"Registered {count} verified runtime inputs. No reference recordings were imported.")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Registration failed: {error}\n")
