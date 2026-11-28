"""User-selectable Wyrd artwork for MO2 instances and profiles.

Artwork metadata and imported images live in Wyrd's user data directory.
No MO2 instance, profile, mod, or download files are modified.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import uuid
from pathlib import Path

ASSET_DIR = Path(__file__).resolve().parent / "assets"
BUILTIN_ARTWORK = (
    "skyrim_instance_castle.svg",
    "skyrim_instance_mountains.svg",
    "skyrim_instance_forest.svg",
    "skyrim_instance_ruins.svg",
)


def _data_dir() -> Path:
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")).expanduser()
    return data_home / "wyrd" / "ui-artwork"


def _mapping_path() -> Path:
    return _data_dir() / "artwork.json"


def _read_mapping() -> dict:
    try:
        value = json.loads(_mapping_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_mapping(mapping: dict) -> None:
    path = _mapping_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(mapping, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _instance_identity(instance: dict) -> str:
    raw_path = str(instance.get("path") or instance.get("name") or "unknown-instance")
    try:
        return str(Path(raw_path).expanduser().resolve(strict=False))
    except (OSError, RuntimeError):
        return raw_path


def _key(instance: dict, scope: str, profile: str | None = None) -> str:
    identity = _instance_identity(instance)
    if scope == "profile":
        return f"profile:{identity}:{profile or instance.get('selected_profile') or 'default'}"
    return f"instance:{identity}"


def _resolve(value: str | None) -> Path | None:
    if not value:
        return None
    if value.startswith("builtin:"):
        candidate = ASSET_DIR / value.removeprefix("builtin:")
    else:
        candidate = Path(value).expanduser()
    try:
        return candidate if candidate.is_file() and not candidate.is_symlink() else None
    except OSError:
        return None


def get_artwork(instance: dict, scope: str = "instance", profile: str | None = None) -> Path:
    """Return persisted artwork, assigning a stable random built-in on first use."""
    mapping = _read_mapping()
    key = _key(instance, scope, profile)
    selected = _resolve(mapping.get(key))
    if selected is not None:
        return selected
    chosen = random.choice(BUILTIN_ARTWORK)
    mapping[key] = f"builtin:{chosen}"
    try:
        _write_mapping(mapping)
    except OSError:
        pass
    return ASSET_DIR / chosen


def randomize_artwork(instance: dict, scope: str = "instance", profile: str | None = None) -> Path:
    mapping = _read_mapping()
    key = _key(instance, scope, profile)
    current = mapping.get(key, "")
    choices = [name for name in BUILTIN_ARTWORK if f"builtin:{name}" != current]
    chosen = random.choice(choices or list(BUILTIN_ARTWORK))
    mapping[key] = f"builtin:{chosen}"
    _write_mapping(mapping)
    return ASSET_DIR / chosen


def set_custom_artwork(instance: dict, scope: str, source: str | Path, profile: str | None = None) -> Path:
    source_path = Path(source).expanduser()
    if source_path.is_symlink() or not source_path.is_file():
        raise OSError("Choose a regular image file, not a symbolic link.")
    if source_path.stat().st_size > 20 * 1024 * 1024:
        raise OSError("Artwork must be 20 MB or smaller.")
    suffix = source_path.suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        raise OSError("Choose a PNG, JPEG, WebP, or BMP image.")
    directory = _data_dir() / "custom"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{uuid.uuid4().hex}{suffix}"
    shutil.copy2(source_path, destination)
    mapping = _read_mapping()
    mapping[_key(instance, scope, profile)] = str(destination)
    _write_mapping(mapping)
    return destination


def reset_artwork(instance: dict, scope: str, profile: str | None = None) -> Path:
    mapping = _read_mapping()
    mapping.pop(_key(instance, scope, profile), None)
    _write_mapping(mapping)
    return get_artwork(instance, scope, profile)
