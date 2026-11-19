"""Read-only discovery of existing Mod Organizer 2 installations.

This module deliberately does not create directories or modify any installation.
"""
from __future__ import annotations

import os
from pathlib import Path

APP_ID = "489830"
STEAM_CANDIDATES = (
    ".steam/steam",
    ".local/share/Steam",
    ".steam/root",
    ".var/app/com.valvesoftware.Steam/.local/share/Steam",
)


def find_steam_roots(home: Path | None = None) -> list[Path]:
    """Return both lexical and canonical Steam roots; do not lose install-layout identity."""
    home = home or Path.home()
    roots: list[Path] = []
    for relative in STEAM_CANDIDATES:
        path = home / relative
        if (path / "steamapps").is_dir():
            for candidate in (path, path.resolve()):
                if candidate not in roots:
                    roots.append(candidate)
    return roots


def find_mo2_installations(home: Path | None = None) -> list[Path]:
    """Find common MO2 Linux/Proton locations without traversing whole disks."""
    home = home or Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    candidates = [
        config / "steamtinkerlaunch/mo2/compatdata/pfx/drive_c/Modding/MO2",
        config / "stl/mo2/compatdata/pfx/drive_c/Modding/MO2",
        home / "Games/ModOrganizer",
        home / "Games/Mod Organizer 2",
        home / ".local/share/Steam/steamapps/compatdata/489830/pfx/drive_c/Modding/MO2",
        home / ".local/share/ModOrganizer",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/compatdata/489830/pfx/drive_c/Modding/MO2",
    ]
    for root in find_steam_roots(home):
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
