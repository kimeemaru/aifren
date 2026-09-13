"""Resolve immutable package resources separately from mutable AIFren data."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Mapping


APPLICATION_DIRECTORY_NAME = "AIFren"
DEVELOPMENT_STAGED_DATA_ROOT_ENV = "AIFREN_DEVELOPMENT_STAGED_DATA_ROOT"
DEVELOPMENT_STAGED_CHARACTER_ID_ENV = "AIFREN_DEVELOPMENT_STAGED_CHARACTER_ID"


def source_root() -> Path:
    """Return the application checkout/package root, independently of CWD/data."""
    return Path(__file__).resolve().parents[2]


def absolute_path(path: str | os.PathLike[str]) -> Path:
    """Return an absolute path without requiring the target to exist."""
    return Path(os.path.abspath(os.fspath(Path(path).expanduser())))


def windows_user_data_root(environment: Mapping[str, str] | None = None) -> Path:
    """Return the normal per-user writable root for a packaged Windows build."""
    values = os.environ if environment is None else environment
    base = str(values.get("LOCALAPPDATA") or "").strip()
    if not base:
        raise RuntimeError("LOCALAPPDATA is unavailable; AIFren cannot resolve writable user data.")
    return Path(base).expanduser() / APPLICATION_DIRECTORY_NAME


def packaged_user_data_root(
    *,
    platform: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Resolve packaged mutable data without consulting the current directory."""
    values = os.environ if environment is None else environment
    platform = sys.platform if platform is None else platform
    override = str(values.get("AIFREN_DATA_ROOT") or "").strip()
    if override:
        return Path(override).expanduser() if platform == "win32" else absolute_path(override)
    if platform == "win32":
        return windows_user_data_root(values)
    xdg = str(values.get("XDG_DATA_HOME") or "").strip()
    base = absolute_path(xdg) if xdg else absolute_path(Path.home() / ".local" / "share")
    return base / "aifren"


def resolve_runtime_roots(
    default_resource_root: str | os.PathLike[str],
    *,
    resource_root: str | os.PathLike[str] | None = None,
    data_root: str | os.PathLike[str] | None = None,
    seed_data_root: str | os.PathLike[str] | None = None,
) -> tuple[Path, Path, Path | None]:
    """Resolve explicit roots; legacy/direct runs keep resource and data together."""
    resources = absolute_path(
        resource_root or os.environ.get("AIFREN_RESOURCE_ROOT") or default_resource_root
    )
    staged_data = str(os.environ.get(DEVELOPMENT_STAGED_DATA_ROOT_ENV) or "").strip()
    if staged_data:
        development_enabled = str(
            os.environ.get("AIFREN_ENABLE_DEVELOPMENT_QA") or ""
        ).strip().casefold() in {"1", "true", "yes", "on"}
        if not development_enabled:
            raise RuntimeError(
                "A staged AIFren data root requires the Development QA gate."
            )
        if data_root is not None and absolute_path(data_root) != absolute_path(staged_data):
            raise RuntimeError(
                "Explicit data root disagrees with the Development staged data root."
            )
        data = absolute_path(staged_data)
    else:
        data = absolute_path(data_root or os.environ.get("AIFREN_DATA_ROOT") or resources)
    seed_value = seed_data_root or os.environ.get("AIFREN_SEED_DATA_ROOT")
    seed = absolute_path(seed_value) if seed_value else None
    return resources, data, seed


def _copy_new_file(source: Path, destination: Path) -> None:
    """Publish one seed file without ever replacing an existing durable record."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".aifren-seed-", suffix=".tmp", dir=destination.parent
        )
    except OSError:
        raise
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as input_file, os.fdopen(descriptor, "wb") as output_file:
            shutil.copyfileobj(input_file, output_file)
            output_file.flush()
            os.fsync(output_file.fileno())
        try:
            # Link publication is atomic and fails if another launcher already
            # created the canonical target. It therefore never overwrites data.
            os.link(temporary, destination)
        except FileExistsError:
            pass
        except OSError:
            # Windows filesystems may reject hard links. Exclusive creation
            # preserves the same no-overwrite ownership rule.
            try:
                with destination.open("xb") as output_file, temporary.open("rb") as input_file:
                    shutil.copyfileobj(input_file, output_file)
                    output_file.flush()
                    os.fsync(output_file.fileno())
            except FileExistsError:
                pass
    finally:
        temporary.unlink(missing_ok=True)


def initialize_data_root(data_root: Path, seed_data_root: Path | None = None) -> Path:
    """Create a writable root and copy only absent package seed records into it."""
    data_root = absolute_path(data_root)
    data_root.mkdir(parents=True, exist_ok=True)
    if seed_data_root is None or (data_root / "characters" / "registry.json").exists():
        # Seed only a first installation. A persisted registry (including an
        # intentionally empty one) owns its timelines; missing reset/deleted
        # files must never be revived from package seeds.
        return data_root
    seed_data_root = absolute_path(seed_data_root)
    if not seed_data_root.is_dir():
        raise RuntimeError(f"AIFren seed data is unavailable: {seed_data_root}")
    for source in sorted(seed_data_root.rglob("*")):
        relative = source.relative_to(seed_data_root)
        destination = data_root / relative
        if source.is_symlink():
            raise RuntimeError(f"AIFren seed data cannot contain symlinks: {relative}")
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif source.is_file() and not destination.exists():
            _copy_new_file(source, destination)
    return data_root


def resource_path(relative_path: str | os.PathLike[str]) -> Path:
    """Resolve an immutable backend asset from the configured resource root."""
    root = absolute_path(os.environ.get("AIFREN_RESOURCE_ROOT") or source_root())
    path = Path(relative_path)
    return absolute_path(path if path.is_absolute() else root / path)
