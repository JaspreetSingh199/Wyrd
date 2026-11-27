"""Canonical Steam compatibility-prefix identity helpers."""
from __future__ import annotations

from pathlib import Path

APP_ID = "489830"


def steam_compat_prefix(steam_root: Path) -> Path:
    """Return the existing Skyrim Special Edition Steam compatdata directory."""
    return Path(steam_root).expanduser() / "steamapps/compatdata" / APP_ID
