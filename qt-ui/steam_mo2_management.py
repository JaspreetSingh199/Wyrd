"""Wyrd-managed MO2 installation and narrowly scoped removal."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from proton_runtime import (
    _runtime_environment, _steam_root_for_game, inspect_proton_build, managed_paths,
)
from steam_prefix import APP_ID, steam_compat_prefix
from steam_mo2_discovery import inspect_skyrim_prefix_mo2_installation


def install_mo2_into_skyrim_prefix(proton_path: Path, game_path: Path,
                                      home: Path | None = None, callback=None) -> dict:
    """Download and run the official MO2 Windows installer in Skyrim's existing prefix.

    The installer is interactive so the user can review its options. Wyrd supplies
    the intended C:\\Modding\\MO2 target and never creates or resets a prefix.
    """
    import urllib.request

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
    pfx, wine_drive = prefix / "pfx", prefix / "pfx/drive_c"
    if pfx.is_symlink() or wine_drive.is_symlink() or not wine_drive.is_dir():
        raise RuntimeError("Skyrim's Proton prefix is missing, uninitialized or unsafe.")
    install = wine_drive / "Modding/MO2"
    if install.is_symlink() or install.parent.is_symlink() or install.parent.parent.is_symlink():
        raise RuntimeError("Refusing to install MO2 through a symlinked path inside Skyrim's prefix.")
    marker, executable = install / ".wyrd-managed.json", install / "ModOrganizer.exe"
    if install.exists():
        if executable.is_file() and not executable.is_symlink():
            return {"ok": True, "already_installed": True, "executable": str(executable),
                    "prefix": str(prefix), "install": str(install),
                    "message": "An MO2 executable already exists at the intended install location."}
        raise RuntimeError(f"MO2 target already exists and will not be overwritten: {install}")

    version = "2.5.2"
    installer_name = f"Mod.Organizer-{version}.exe"
    url = f"https://github.com/ModOrganizer2/modorganizer/releases/download/v{version}/{installer_name}"
    downloads = managed_paths(home)["root"] / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    installer = downloads / installer_name
    if callback:
        callback(5, f"Downloading official Mod Organizer 2 {version} installer…")
    if not installer.is_file() or installer.is_symlink() or installer.stat().st_size < 50_000_000:
        partial = downloads / (installer_name + ".part")
        partial.unlink(missing_ok=True)
        request = urllib.request.Request(url, headers={"User-Agent": "Wyrd MO2 installer"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
                total = int(response.headers.get("Content-Length", "0") or 0)
                downloaded = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    downloaded += len(chunk)
                    if callback and total:
                        callback(min(35, 5 + int(30 * downloaded / total)),
                                 f"Downloading MO2 installer… {downloaded // (1024 * 1024)} MB")
            if partial.stat().st_size < 50_000_000:
                raise RuntimeError("Downloaded MO2 installer is unexpectedly small.")
            os.replace(partial, installer)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
    if callback:
        callback(40, "Launching official MO2 installer in Skyrim's Proton prefix…")
    env = _runtime_environment(proton, steam_root, prefix)
    env["STEAM_COMPAT_APP_ID"] = APP_ID
    env["SteamAppId"] = APP_ID
    env["SteamGameId"] = APP_ID
    command = [str(proton / "proton"), "run", str(installer),
               r"/DIR=C:\\Modding\\MO2"]
    process = subprocess.Popen(command, cwd=str(steam_root), env=env,
                               stdin=subprocess.DEVNULL, start_new_session=True)
    if callback:
        callback(45, "MO2 installer opened. Complete its wizard; Wyrd will verify the installation when it closes.")
    try:
        return_code = process.wait(timeout=1800)
    except subprocess.TimeoutExpired:
        process.terminate()
        raise RuntimeError("MO2 installer did not finish within 30 minutes.")
    if return_code != 0:
        raise RuntimeError(f"MO2 installer exited with status {return_code}.")
    if not executable.is_file() or executable.is_symlink():
        raise RuntimeError(
            "MO2 installer closed, but ModOrganizer.exe was not found at "
            f"{install}. Check the installer's destination and retry."
        )
    marker.write_text(json.dumps({
        "managed_by": "Wyrd", "version": version,
        "installed": datetime.now(timezone.utc).isoformat(),
        "proton": str(proton), "steam_root": str(steam_root),
        "game_app_id": APP_ID, "game_path": str(game),
        "prefix": str(prefix), "install": str(install),
        "installation_mode": "official-windows-installer",
    }, indent=2), encoding="utf-8")
    if callback:
        callback(100, "Official MO2 installer completed; ModOrganizer.exe verified.")
    return {"ok": True, "already_installed": False, "executable": str(executable),
            "version": version, "proton": str(proton), "prefix": str(prefix),
            "install": str(install), "game_path": str(game),
            "message": f"MO2 {version} installed and verified in Skyrim's existing Steam prefix."}


def reset_wyrd_mo2(home: Path | None = None, game_path: Path | None = None) -> dict:
    """Remove only the verified Wyrd-installed MO2 program directory.

    Global MO2 instances and all user data are deliberately left in place.
    Wyrd has no instance-registration or storage-ownership system.
    """
    import json
    import shutil

    home = (home or Path.home()).expanduser()
    game = Path(game_path).expanduser() if game_path is not None else _game_path_for_wyrd_install(home)
    state = inspect_skyrim_prefix_mo2_installation(game, home)
    if not state.get("installed") or not state.get("managed"):
        return {
            "ok": False,
            "message": state.get("message", "No verified Wyrd-installed MO2 program was found."),
            "removed": [],
        }

    install = Path(state["install"])
    prefix = Path(state["prefix"])
    marker = install / ".wyrd-managed.json"
    executable = install / "ModOrganizer.exe"
    if any(path.is_symlink() for path in (install, prefix, marker, executable)):
        return {"ok": False, "message": "A Wyrd MO2 path is symlinked; removal refused.", "removed": []}
    try:
        resolved_prefix = prefix.resolve(strict=True)
        resolved_install = install.resolve(strict=True)
        resolved_install.relative_to(resolved_prefix)
        metadata = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError, RuntimeError):
        return {
            "ok": False,
            "message": "Wyrd installation ownership or path could not be verified; nothing was removed.",
            "removed": [],
        }
    if (
        not isinstance(metadata, dict)
        or metadata.get("managed_by") != "Wyrd"
        or metadata.get("game_app_id") != APP_ID
        or metadata.get("installation_mode") not in {"skyrim-steam-prefix", "official-windows-installer"}
        or Path(metadata.get("install", "")).expanduser() != install
        or not executable.is_file()
    ):
        return {
            "ok": False,
            "message": "MO2 program directory is not positively identified as Wyrd-installed; nothing was removed.",
            "removed": [],
        }

    # Do not remove global/portable instances or any profiles, mods, downloads,
    # saves, custom storage, or Steam prefix data. The program directory is the
    # only deletion target.
    if install.is_symlink() or not install.is_dir():
        return {"ok": False, "message": "MO2 program directory changed or is unsafe; removal refused.", "removed": []}
    try:
        current = install.resolve(strict=True)
        current.relative_to(resolved_prefix)
    except (OSError, ValueError, RuntimeError):
        return {"ok": False, "message": "MO2 program directory escaped the verified prefix; removal refused.", "removed": []}
    if prefix.is_symlink() or prefix.resolve(strict=True) != resolved_prefix:
        return {"ok": False, "message": "Steam's Skyrim prefix changed during validation; removal refused.", "removed": []}

    try:
        shutil.rmtree(install)
    except OSError as error:
        return {"ok": False, "message": f"MO2 program removal failed: {error}", "removed": []}

    settings_path = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")).expanduser() / "wyrd" / "settings.json"
    try:
        if settings_path.exists() and not settings_path.is_symlink() and settings_path.is_file():
            payload = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload.pop("selected_mo2_instance_id", None)
                temporary = settings_path.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                os.replace(temporary, settings_path)
    except (OSError, ValueError, TypeError):
        # Preferences are not part of the ownership boundary; stale selection
        # data is harmless because discovery is always rebuilt from disk.
        pass

    return {
        "ok": True,
        "removed": [{"path": str(install), "kind": "Wyrd-installed MO2 program"}],
        "message": "The verified Wyrd-installed MO2 program directory was removed. Existing MO2 instances and their profiles, mods, downloads, custom storage, Skyrim's Steam compatdata/489830 prefix, game files and saves were preserved.",
    }

def _game_path_for_wyrd_install(home: Path) -> Path:
    """Recover the recorded Skyrim path from the Wyrd settings file."""
    settings = managed_paths(home)["settings"]
    try:
        payload = json.loads(settings.read_text(encoding="utf-8"))
        game = Path(payload.get("game_path", "")).expanduser()
        return game if game.is_dir() else Path("/__wyrd_missing_game__")
    except (OSError, ValueError, TypeError):
        return Path("/__wyrd_missing_game__")
