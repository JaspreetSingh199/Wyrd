"""Explicit Steam client activation and Skyrim context checks.

Importing this module or starting Wyrd never starts Steam. Activation is only
requested from the confirmed modded-game launch action.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path


def activate_steam_client() -> dict:
    """Ensure Steam is ready without restoring an already-running window."""
    # Running Steam's command again activates/focuses its UI on many desktop
    # setups. If the real client and its WebHelper are already present, leave
    # the user's minimized/window state untouched.
    status = steam_client_status()
    if status.get("ok") and status.get("ready"):
        return {
            "ok": True,
            "pid": None,
            "already_running": True,
            "message": "Steam is already ready; preserving its current window state.",
        }

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


def steam_client_status(proc_root: Path = Path("/proc")) -> dict:
    """Report ready only when Steam's main process and matching UI helper exist.

    A short-lived launcher/bootstrap process is not enough: the MO2 Proton
    handoff waits until the actual client has started its Steam WebHelper.
    This function is read-only and never starts Steam.
    """
    try:
        entries = [entry for entry in Path(proc_root).iterdir() if entry.name.isdigit()]
    except OSError as error:
        return {"ok": False, "ready": False, "message": f"Could not inspect Steam processes: {error}"}

    steam_pids: set[int] = set()
    webhelper_owners: set[int] = set()
    for entry in entries:
        try:
            comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip().casefold()
            argv = [
                part.decode("utf-8", errors="replace")
                for part in (entry / "cmdline").read_bytes().split(bytes([0])) if part
            ]
            exe = os.path.basename(os.readlink(entry / "exe")).casefold()
        except OSError:
            continue

        pid = int(entry.name)
        if comm == "steam" or exe == "steam":
            steam_pids.add(pid)
        if comm == "steamwebhelper" or exe == "steamwebhelper":
            owner = None
            for index, arg in enumerate(argv):
                if arg.startswith("-steampid="):
                    try:
                        owner = int(arg.split("=", 1)[1])
                    except ValueError:
                        owner = None
                    break
                if arg == "-steampid" and index + 1 < len(argv):
                    try:
                        owner = int(argv[index + 1])
                    except ValueError:
                        owner = None
                    break
            if owner is not None:
                webhelper_owners.add(owner)

    ready_pids = sorted(steam_pids.intersection(webhelper_owners))
    if ready_pids:
        return {
            "ok": True, "ready": True, "steam_pids": ready_pids,
            "message": "Steam client and its UI helper are running.",
        }
    if steam_pids:
        return {
            "ok": True, "ready": False, "steam_pids": sorted(steam_pids),
            "message": "Steam process is running; waiting for its UI helper.",
        }
    return {"ok": True, "ready": False, "steam_pids": [],
            "message": "Waiting for the Steam client to start."}


def verify_skyrim_launch_context(
    game_path: Path | str,
    steam_root: Path | str,
    prefix_path: Path | str,
) -> dict:
    """Recheck Skyrim's Steam manifest, executable and existing prefix.

    Runs after Steam becomes ready and before MO2 is launched. It never
    creates, repairs, resets or writes to the game, prefix, or user data.
    """
    if not game_path or not steam_root or not prefix_path:
        return {"ok": False, "message": "Skyrim game path, Steam root, or existing AppID 489830 prefix is unavailable."}
    game = Path(game_path).expanduser()
    root = Path(steam_root).expanduser()
    prefix = Path(prefix_path).expanduser()
    if any(path.is_symlink() for path in (game, root, prefix)):
        return {"ok": False, "message": "Skyrim, Steam root, or AppID 489830 prefix became a symlink; launch was refused."}
    try:
        original_game = game
        game = game.resolve(strict=True)
        root = root.resolve(strict=True)
        prefix = prefix.resolve(strict=True)
        expected_prefix = (root / "steamapps/compatdata/489830").resolve(strict=True)
    except (OSError, RuntimeError) as error:
        return {"ok": False, "message": f"Could not re-verify Skyrim's Steam paths: {error}"}

    if game != original_game:
        return {"ok": False, "message": "The detected Skyrim path changed while Steam was starting."}
    if not game.is_dir() or not (game / "SkyrimSE.exe").is_file():
        return {"ok": False, "message": "Skyrim Special Edition is no longer installed at the detected game path."}

    manifest = game.parent.parent / "appmanifest_489830.acf"
    try:
        manifest_text = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"ok": False, "message": "Steam's Skyrim AppID 489830 app manifest is missing or unreadable."}
    app_id = re.search(r'"appid"\s+"(\d+)"', manifest_text)
    install_dir = re.search(r'"installdir"\s+"([^"]+)"', manifest_text)
    if not app_id or app_id.group(1) != "489830":
        return {"ok": False, "message": "The detected Steam library does not identify this game as AppID 489830."}
    if not install_dir or install_dir.group(1).casefold() != game.name.casefold():
        return {"ok": False, "message": "Steam's Skyrim app manifest does not match the detected game directory."}
    if prefix != expected_prefix or not (prefix / "pfx/drive_c/users").is_dir():
        return {"ok": False, "message": "Skyrim's existing AppID 489830 Proton prefix is missing, uninitialized, or mismatched."}

    return {
        "ok": True, "game_path": str(game), "steam_root": str(root),
        "prefix": str(prefix), "manifest": str(manifest),
        "message": "Steam AppID 489830, SkyrimSE.exe and the existing Proton prefix were re-verified.",
    }
