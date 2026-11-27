"""Explicit Steam client activation for user-requested game launches.

Importing this module or starting Wyrd never starts Steam. The helper is only
called from the confirmed Launch Modded Skyrim action.
"""
from __future__ import annotations

import shutil
import subprocess


def activate_steam_client() -> dict:
    """Ask the Steam client to open without asking it to launch a game."""
    steam = shutil.which("steam")
    if not steam:
        return {
            "ok": False,
            "message": "The Steam command was not found on PATH; Steam could not be opened.",
        }
    try:
        process = subprocess.Popen(
            [steam],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as error:
        return {"ok": False, "message": f"Could not open the Steam client: {error}"}
    return {
        "ok": True,
        "pid": process.pid,
        "message": "Steam client activation requested (no game AppID was supplied).",
    }
