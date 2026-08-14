"""Install and resolve the local, offline Kokoro assets used by AIFren."""

from __future__ import annotations

import argparse
from pathlib import Path


REPOSITORY_ID = "hexgrad/Kokoro-82M"
MODEL_FILENAME = "kokoro-v1_0.pth"


def asset_paths(model_dir: Path | str, voice: str) -> tuple[Path, Path, Path]:
    root = Path(model_dir)
    return root / "config.json", root / MODEL_FILENAME, root / "voices" / f"{voice}.pt"


def require_local_assets(model_dir: Path | str, voice: str) -> tuple[Path, Path, Path]:
    paths = asset_paths(model_dir, voice)
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise RuntimeError(
            "Kokoro local assets are missing. Run setup_aifren_runtime.bat while online "
            "to install the selected model and voice into the local models directory. Missing: " +
            ", ".join(missing)
        )
    return paths


def install_assets(model_dir: Path | str, voice: str) -> tuple[Path, Path, Path]:
    """One-time setup download; normal runtime never calls Hugging Face."""
    from huggingface_hub import snapshot_download

    destination = Path(model_dir)
    snapshot_download(
        repo_id=REPOSITORY_ID,
        allow_patterns=["config.json", MODEL_FILENAME, f"voices/{voice}.pt"],
        local_dir=str(destination),
    )
    return require_local_assets(destination, voice)


def main() -> None:
    parser = argparse.ArgumentParser(description="Install or verify local Kokoro assets.")
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--voice", required=True)
    parser.add_argument("--install", action="store_true")
    options = parser.parse_args()
    paths = install_assets(options.model_dir, options.voice) if options.install else require_local_assets(options.model_dir, options.voice)
    print("Kokoro local assets ready:")
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
