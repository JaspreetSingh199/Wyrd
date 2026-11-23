"""Launch Wyrd-managed MO2 inside Skyrim's Steam Proton compatdata.

This is an experimental mode: it reuses Skyrim's existing AppID prefix but does
not reset, install dependencies into, or otherwise mutate that prefix itself.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from wyrd_mo2 import inspect_wyrd_installation, inspect_proton_build, _runtime_environment

APP_ID = "489830"


def steam_compat_prefix(steam_root: Path) -> Path:
    return Path(steam_root).expanduser() / "steamapps/compatdata" / APP_ID


def launch_mo2_in_skyrim_prefix(home: Path | None = None) -> dict:
    """Start MO2 with Proton and STEAM_COMPAT_DATA_PATH set to Skyrim's AppID prefix."""
    state = inspect_wyrd_installation(home)
    if not state.get("installed"):
        return {"ok": False, "message": "Wyrd-managed MO2 is not installed and verified."}
    metadata = state.get("metadata", {})
    proton = Path(metadata.get("proton", "")).expanduser()
    steam_root = Path(metadata.get("steam_root", "")).expanduser()
    game_path = Path(metadata.get("game_path", "")).expanduser()
    install = Path(state.get("executable", "")).expanduser().parent
    if not proton.is_dir() or not game_path.is_dir() or not install.is_dir():
        return {"ok": False, "message": "The saved Proton, Skyrim, or MO2 installation path is unavailable."}
    validation = inspect_proton_build(proton)
    if not validation.get("ok"):
        return {"ok": False, "message": validation.get("message", "Saved Proton build is invalid.")}
    if not steam_root.is_dir():
        return {"ok": False, "message": "Saved Steam root is unavailable."}
    prefix = steam_compat_prefix(steam_root)
    if prefix.is_symlink():
        return {"ok": False, "message": "Refusing to launch through a symlinked Skyrim compatdata prefix."}
    try:
        resolved_prefix = prefix.resolve(strict=True)
        resolved_steam = steam_root.resolve(strict=True)
        resolved_prefix.relative_to(resolved_steam)
    except (OSError, ValueError, RuntimeError):
        return {"ok": False, "message": "Skyrim's Steam compatdata prefix is missing or outside the saved Steam root."}
    if not (prefix / "pfx/drive_c").is_dir():
        return {"ok": False, "message": "Skyrim's Proton prefix is not initialized. Launch Skyrim once through Steam first."}
    executable = install / "ModOrganizer.exe"
    if not executable.is_file() or executable.is_symlink():
        return {"ok": False, "message": "Wyrd's verified MO2 executable is missing or unsafe."}
    env = _runtime_environment(proton, steam_root, prefix)
    env.update({
        "SteamAppId": APP_ID,
        "SteamGameId": APP_ID,
        "STEAM_COMPAT_APP_ID": APP_ID,
        "STEAM_COMPAT_DATA_PATH": str(prefix.resolve()),
    })
    try:
        process = subprocess.Popen(
            [str(proton / "proton"), "run", str(executable)],
            cwd=str(install), env=env, start_new_session=True,
        )
    except OSError as error:
        return {"ok": False, "message": f"Could not start MO2 in Skyrim's Proton prefix: {error}"}
    return {
        "ok": True, "pid": process.pid, "prefix": str(prefix.resolve()),
        "message": "MO2 wrapper started in Skyrim's Steam compatdata prefix. MO2 window/process startup is not yet independently confirmed.",
    }
