"""Read-only discovery of existing Mod Organizer 2 installations.

This module deliberately does not create directories or modify any installation.
"""
from __future__ import annotations

import os
from pathlib import Path

from skyrim_detection import find_steam_roots

APP_ID = "489830"


def find_mo2_installations(home: Path | None = None) -> list[Path]:
    """Find common MO2 Linux/Proton locations without traversing whole disks."""
    home = home or Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    candidates = [
        home / "Games/ModOrganizer",
        home / "Games/Mod Organizer 2",
        home / ".local/share/Steam/steamapps/compatdata/489830/pfx/drive_c/Modding/MO2",
        home / ".local/share/ModOrganizer",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/compatdata/489830/pfx/drive_c/Modding/MO2",
    ]
    for root in find_steam_roots(home, include_aliases=True):
        candidates.append(root / "steamapps/compatdata/489830/pfx/drive_c/Modding/MO2")
    found: list[Path] = []
    for candidate in candidates:
        executable = candidate / "ModOrganizer.exe"
        if executable.is_file():
            resolved = candidate.resolve()
            if resolved not in found:
                found.append(resolved)
    return found


def inspect_mo2(home: Path | None = None) -> dict:
    installations = find_mo2_installations(home)
    return {
        "found": bool(installations),
        "installations": [str(path) for path in installations],
        "note": "Detection only. Existing MO2 instances and prefixes are not modified.",
    }
