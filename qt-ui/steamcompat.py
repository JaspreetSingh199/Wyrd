"""Launch Wyrd-managed MO2 inside Skyrim's Steam Proton compatdata.

This is an experimental mode: it reuses Skyrim's existing AppID prefix but does
not reset, install dependencies into, or otherwise mutate that prefix itself.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from wyrd_mo2 import (inspect_wyrd_installation, inspect_proton_build, _runtime_environment,
                     inspect_mo2_instances, copy_mo2_instance_to_prefix,
                     _steam_root_for_game, _latest_mo2_archive, _download_archive, _extract_mo2,
                     managed_paths)
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone

APP_ID = "489830"


def steam_compat_prefix(steam_root: Path) -> Path:
    return Path(steam_root).expanduser() / "steamapps/compatdata" / APP_ID


def install_mo2_into_skyrim_prefix(proton_path: Path, game_path: Path,
                                      home: Path | None = None, callback=None) -> dict:
    """Install MO2 inside Skyrim's AppID prefix without creating a separate Wyrd prefix.

    Only the MO2 install directory is owned by Wyrd. Skyrim's compatdata prefix
    remains Steam-owned and is never initialized, reset, or marked by Wyrd.
    """
    home = (home or Path.home()).expanduser()
    proton = Path(proton_path).expanduser().resolve()
    game = Path(game_path).expanduser().resolve()
    validation = inspect_proton_build(proton)
    if not validation.get("ok"):
        raise RuntimeError(validation.get("message", "Selected Proton build is invalid."))
    if not game.is_dir():
        raise RuntimeError(f"Skyrim game directory is missing: {game}")
    steam_root = _steam_root_for_game(game, home)
    prefix = steam_compat_prefix(steam_root)
    if prefix.is_symlink() or not prefix.is_dir():
        raise RuntimeError("Skyrim's Steam compatdata prefix is missing or unsafe. Launch Skyrim through Steam once first.")
    try:
        prefix.resolve(strict=True).relative_to(steam_root.resolve(strict=True))
    except (OSError, ValueError, RuntimeError):
        raise RuntimeError("Skyrim compatdata prefix is outside the detected Steam root.")
    wine_drive = prefix / "pfx/drive_c"
    if not wine_drive.is_dir():
        raise RuntimeError("Skyrim's Proton prefix is not initialized. Launch Skyrim through Steam once first.")
    install = wine_drive / "Modding/MO2"
    if install.is_symlink() or install.parent.is_symlink() or (install.parent.parent).is_symlink():
        raise RuntimeError("Refusing to install MO2 through a symlinked path inside Skyrim's prefix.")
    marker = install / ".wyrd-managed.json"
    executable = install / "ModOrganizer.exe"
    if install.exists():
        if executable.is_file() and marker.is_file():
            try:
                ownership = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                ownership = {}
            if ownership.get("managed_by") == "Wyrd" and ownership.get("game_app_id") == APP_ID:
                return {"ok": True, "already_installed": True, "executable": str(executable),
                        "prefix": str(prefix), "install": str(install),
                        "message": "Wyrd-managed MO2 is already installed in Skyrim's Steam compatdata prefix."}
        raise RuntimeError(f"MO2 install target exists but is not a verified Wyrd installation: {install}. It was left untouched.")
    root = managed_paths(home)["root"]
    staging_root = root / "staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    version, archive_name, url = _latest_mo2_archive()
    stage = Path(tempfile.mkdtemp(prefix="mo2-steam-prefix-", dir=staging_root))
    archive = stage / archive_name
    try:
        if callback:
            callback(10, f"Preparing MO2 {version} for Skyrim's prefix…")
        _download_archive(url, archive, callback)
        extracted = _extract_mo2(archive, stage / "extracted", callback)
        if not (extracted / "ModOrganizer.exe").is_file():
            raise RuntimeError("MO2 verification failed: ModOrganizer.exe is missing.")
        extracted_marker = extracted / ".wyrd-managed.json"
        extracted_marker.write_text(json.dumps({
            "managed_by": "Wyrd", "version": version,
            "installed": datetime.now(timezone.utc).isoformat(),
            "proton": str(proton), "steam_root": str(steam_root),
            "game_app_id": APP_ID, "game_path": str(game),
            "prefix": str(prefix), "install": str(install),
            "installation_mode": "skyrim-steam-prefix",
        }, indent=2), encoding="utf-8")
        install.parent.mkdir(parents=True, exist_ok=True)
        if install.exists():
            raise RuntimeError(f"Install target appeared during setup: {install}. Existing files were not changed.")
        os.replace(extracted, install)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    if callback:
        callback(100, "MO2 installed and verified inside Skyrim's Steam compatdata prefix.")
    return {"ok": True, "already_installed": False, "executable": str(executable),
            "version": version, "proton": str(proton), "prefix": str(prefix),
            "install": str(install), "game_path": str(game),
            "message": f"MO2 {version} installed and verified inside Skyrim's Steam compatdata prefix."}


def inspect_skyrim_prefix_mo2_instances(home: Path | None = None) -> dict:
    """Inventory Skyrim-specific MO2 instances in Skyrim's validated AppID prefix."""
    state = inspect_wyrd_installation(home)
    if not state.get("installed"):
        return {"ok": False, "instances": [], "skyrim_instances": [], "message": "Wyrd-managed MO2 is not installed."}
    metadata = state.get("metadata", {})
    steam_root = Path(metadata.get("steam_root", "")).expanduser()
    prefix = steam_compat_prefix(steam_root)
    if prefix.is_symlink() or not prefix.is_dir():
        return {"ok": False, "instances": [], "skyrim_instances": [], "message": "Skyrim's Steam compatdata prefix is missing or unsafe."}
    try:
        prefix.resolve(strict=True).relative_to(steam_root.resolve(strict=True))
    except (OSError, ValueError, RuntimeError):
        return {"ok": False, "instances": [], "skyrim_instances": [], "message": "Skyrim compatdata prefix is outside the Steam root."}
    if not (prefix / "pfx/drive_c/users").is_dir():
        return {"ok": False, "instances": [], "skyrim_instances": [], "message": "Skyrim's Proton prefix is not initialized."}
    return inspect_mo2_instances(prefix, require_managed=False)




from pathlib import Path

def copy_wyrd_instance_to_skyrim_prefix(instance_name: str, home: Path | None = None) -> dict:
    """Copy a selected Skyrim instance from Wyrd's prefix into Steam's prefix."""
    state = inspect_wyrd_installation(home)
    if not state.get("installed"):
        return {"ok": False, "status": "refused", "message": "Wyrd-managed MO2 is not installed."}
    paths = state.get("paths", {})
    source_prefix = Path(paths.get("prefix", "")).expanduser()
    if source_prefix.is_symlink() or not source_prefix.is_dir():
        return {"ok": False, "status": "refused", "message": "Wyrd MO2 prefix is missing or unsafe."}
    source_inventory = inspect_mo2_instances(source_prefix, Path(state["executable"]).expanduser().parent)
    if not source_inventory.get("ok"):
        return {"ok": False, "status": "refused", "message": source_inventory.get("message", "Could not inspect Wyrd MO2 instances.")}
    source = next((item for item in source_inventory.get("skyrim_instances", [])
                   if item["name"] == instance_name), None)
    if source is None:
        return {"ok": False, "status": "refused", "message": "Selected Skyrim instance was not found in Wyrd's prefix."}
    steam_root = Path(state.get("metadata", {}).get("steam_root", "")).expanduser()
    return copy_mo2_instance_to_prefix(Path(source["path"]), steam_compat_prefix(steam_root))


def launch_mo2_in_skyrim_prefix(home: Path | None = None, instance_name: str | None = None) -> dict:
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
    inventory = inspect_skyrim_prefix_mo2_instances(home)
    if not inventory.get("ok"):
        return {"ok": False, "message": inventory.get("message", "Could not inspect MO2 instances in Skyrim's prefix.")}
    candidates = inventory.get("skyrim_instances", [])
    if instance_name is None:
        if len(candidates) == 1:
            instance_name = candidates[0]["name"]
        elif candidates:
            return {"ok": False, "needs_instance_selection": True, "instances": [item["name"] for item in candidates], "message": "Choose an MO2 Skyrim instance before launching in Skyrim's prefix."}
        else:
            return {"ok": False, "message": "No Skyrim Special Edition MO2 instance was found in Skyrim's prefix. Create or copy one into this prefix first."}
    if instance_name not in [item["name"] for item in candidates]:
        return {"ok": False, "message": "The selected Skyrim MO2 instance is not verified in Skyrim's prefix."}
    env = _runtime_environment(proton, steam_root, prefix)
    env.update({
        "SteamAppId": APP_ID,
        "SteamGameId": APP_ID,
        "STEAM_COMPAT_APP_ID": APP_ID,
        "STEAM_COMPAT_DATA_PATH": str(prefix.resolve()),
    })
    try:
        process = subprocess.Popen(
            [str(proton / "proton"), "run", str(executable), "--instance", instance_name],
            cwd=str(install), env=env, start_new_session=True,
        )
    except OSError as error:
        return {"ok": False, "message": f"Could not start MO2 in Skyrim's Proton prefix: {error}"}
    return {
        "ok": True, "pid": process.pid, "prefix": str(prefix.resolve()),
        "instance": instance_name,
        "message": f"MO2 wrapper started in Skyrim's Steam compatdata prefix for instance '{instance_name}'. MO2 window/process startup is not yet independently confirmed.",
    }
