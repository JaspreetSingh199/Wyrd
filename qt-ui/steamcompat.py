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
    proton_prefix = prefix / "pfx"
    wine_drive = proton_prefix / "drive_c"
    modding_root = wine_drive / "Modding"
    if (proton_prefix.is_symlink() or wine_drive.is_symlink()
            or modding_root.is_symlink()):
        raise RuntimeError("Refusing to install MO2 through a symlinked Proton prefix path.")
    if not wine_drive.is_dir():
        raise RuntimeError("Skyrim's Proton prefix is not initialized. Launch Skyrim through Steam once first.")
    install = wine_drive / "Modding/MO2"
    if install.is_symlink() or install.parent.is_symlink() or install.parent.parent.is_symlink():
        raise RuntimeError("Refusing to install MO2 through a symlinked path inside Skyrim's prefix.")
    marker = install / ".wyrd-managed.json"
    executable = install / "ModOrganizer.exe"
    if install.exists():
        if executable.is_file() and not executable.is_symlink() and marker.is_file() and not marker.is_symlink():
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


def inspect_skyrim_prefix_mo2_installation(game_path: Path, home: Path | None = None) -> dict:
    """Safely inspect the Wyrd-owned MO2 directory inside Skyrim's Steam prefix.

    Steam compatdata remains Steam-owned. Only the dedicated MO2 directory may
    carry Wyrd's ownership marker.
    """
    home = (home or Path.home()).expanduser()
    game = Path(game_path).expanduser()
    if not game.is_dir():
        return {"installed": False, "message": "Skyrim game directory is missing."}
    try:
        steam_root = _steam_root_for_game(game, home)
        prefix = steam_compat_prefix(steam_root)
        if prefix.is_symlink() or not prefix.is_dir():
            return {"installed": False, "message": "Skyrim's Steam compatdata prefix is missing or unsafe."}
        resolved_prefix = prefix.resolve(strict=True)
        resolved_steam = steam_root.resolve(strict=True)
        resolved_prefix.relative_to(resolved_steam)
        install = prefix / "pfx/drive_c/Modding/MO2"
        proton_prefix = prefix / "pfx"
        wine_drive = proton_prefix / "drive_c"
        modding_root = wine_drive / "Modding"
        if (proton_prefix.is_symlink() or wine_drive.is_symlink()
                or modding_root.is_symlink()):
            return {"installed": False, "prefix": str(prefix), "install": str(install),
                    "message": "Skyrim's Proton prefix path contains a symlink and was not inspected."}
        if not wine_drive.is_dir():
            return {"installed": False, "prefix": str(prefix), "install": str(install),
                    "message": "Skyrim's Proton prefix is not initialized. Launch Skyrim through Steam once first."}
        if install.is_symlink() or install.parent.is_symlink() or install.parent.parent.is_symlink():
            return {"installed": False, "prefix": str(prefix), "install": str(install),
                    "message": "MO2 install path contains a symlink and was not inspected."}
        marker = install / ".wyrd-managed.json"
        executable = install / "ModOrganizer.exe"
        if marker.is_symlink():
            return {"installed": False, "prefix": str(prefix), "install": str(install),
                    "message": "MO2 ownership marker is a symlink and was not inspected."}
        metadata = {}
        try:
            metadata = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        owned = (
            executable.is_file() and not executable.is_symlink()
            and metadata.get("managed_by") == "Wyrd"
            and metadata.get("game_app_id") == APP_ID
            and metadata.get("installation_mode") == "skyrim-steam-prefix"
            and Path(metadata.get("install", "")).expanduser() == install
        )
        return {
            "installed": bool(owned), "executable": str(executable) if owned else None,
            "metadata": metadata if owned else {}, "prefix": str(prefix),
            "install": str(install), "steam_root": str(steam_root),
            "message": "Wyrd-managed MO2 is installed in Skyrim's Steam prefix." if owned
                       else "No verified Wyrd-managed MO2 installation was found in Skyrim's prefix. Existing files were left untouched.",
        }
    except (OSError, ValueError, RuntimeError) as error:
        return {"installed": False, "message": f"Could not safely inspect Skyrim's MO2 prefix: {error}"}


def inspect_skyrim_prefix_mo2_instances(game_path: Path, home: Path | None = None) -> dict:
    """Inventory MO2 instances in Skyrim's validated AppID prefix."""
    state = inspect_skyrim_prefix_mo2_installation(game_path, home)
    if not state.get("installed"):
        return {"ok": False, "instances": [], "skyrim_instances": [],
                "message": state.get("message", "Wyrd-managed MO2 is not installed.")}
    return inspect_mo2_instances(Path(state["prefix"]), Path(state["install"]))



def inspect_external_skyrim_prefix_mo2_installation(game_path: Path, home: Path | None = None) -> dict:
    """Discover an unowned MO2 installation in Skyrim's prefix without mutation."""
    home = (home or Path.home()).expanduser()
    game = Path(game_path).expanduser()
    if not game.is_dir():
        return {"installed": False, "managed": False,
                "message": "Skyrim game directory is missing."}
    try:
        steam_root = _steam_root_for_game(game, home)
        prefix = steam_compat_prefix(steam_root)
        if prefix.is_symlink() or not prefix.is_dir():
            return {"installed": False, "managed": False,
                    "message": "Skyrim's Steam compatdata prefix is missing or unsafe."}
        resolved_steam = steam_root.resolve(strict=True)
        resolved_prefix = prefix.resolve(strict=True)
        resolved_prefix.relative_to(resolved_steam)
        pfx = prefix / "pfx"
        drive = pfx / "drive_c"
        modding = drive / "Modding"
        install = modding / "MO2"
        for component in (pfx, drive, modding, install):
            if component.is_symlink():
                return {"installed": False, "managed": False, "prefix": str(prefix),
                        "install": str(install), "message": "Existing MO2 path contains a symlink; discovery was refused."}
        if not drive.is_dir():
            return {"installed": False, "managed": False, "prefix": str(prefix),
                    "install": str(install), "message": "Skyrim's Proton prefix is not initialized."}
        executable = install / "ModOrganizer.exe"
        marker = install / ".wyrd-managed.json"
        if marker.is_symlink():
            return {"installed": False, "managed": False, "prefix": str(prefix),
                    "install": str(install), "message": "MO2 ownership marker is a symlink; discovery was refused."}
        if marker.exists():
            owned = inspect_skyrim_prefix_mo2_installation(game, home)
            if owned.get("installed"):
                return {**owned, "managed": True}
            return {"installed": False, "managed": False, "prefix": str(prefix),
                    "install": str(install), "message": "An ownership marker exists but cannot be verified; external adoption was refused."}
        if not executable.is_file() or executable.is_symlink():
            # An existing directory still occupies the installer target. Report
            # the path so the UI can block installation without adopting it.
            occupied = install.exists()
            return {"installed": False, "managed": False, "prefix": str(prefix),
                    "install": str(install) if occupied else None,
                    "message": ("An existing MO2 target directory is present but its executable is missing; it was left untouched."
                                if occupied else "No external MO2 executable was found at the conventional Skyrim-prefix location.")}
        return {
            "installed": True, "managed": False, "executable": str(executable),
            "prefix": str(prefix), "install": str(install), "steam_root": str(steam_root),
            "message": "Existing externally managed MO2 installation found. It has not been modified.",
        }
    except (OSError, ValueError, RuntimeError) as error:
        return {"installed": False, "managed": False,
                "message": f"Could not safely inspect existing MO2 installation: {error}"}


def inspect_any_skyrim_prefix_mo2_instances(game_path: Path, home: Path | None = None) -> dict:
    """Read-only inventory for either Wyrd-managed or external MO2 in Skyrim's prefix."""
    managed = inspect_skyrim_prefix_mo2_installation(game_path, home)
    state = managed if managed.get("installed") else inspect_external_skyrim_prefix_mo2_installation(game_path, home)
    if not state.get("installed"):
        return {"ok": False, "managed": False, "instances": [], "skyrim_instances": [],
                "message": state.get("message", "MO2 installation was not found.")}
    inventory = inspect_mo2_instances(
        Path(state["prefix"]), Path(state["install"]), require_managed=False,
    )
    inventory["managed"] = bool(state.get("managed", managed.get("installed", False)))
    inventory["installation"] = state
    return inventory


def launch_external_mo2_in_skyrim_prefix(proton_path: Path, game_path: Path,
                                          home: Path | None = None,
                                          instance_name: str | None = None,
                                          xaudio2_compat: bool = False) -> dict:
    """Launch an existing unowned MO2 installation without adopting or modifying it."""
    home = (home or Path.home()).expanduser()
    proton = Path(proton_path).expanduser()
    game = Path(game_path).expanduser()
    validation = inspect_proton_build(proton)
    if not validation.get("ok"):
        return {"ok": False, "message": validation.get("message", "Selected Proton build is invalid.")}
    state = inspect_external_skyrim_prefix_mo2_installation(game, home)
    if not state.get("installed") or state.get("managed"):
        return {"ok": False, "message": state.get("message", "External MO2 installation is unavailable.")}
    prefix, install = Path(state["prefix"]), Path(state["install"])
    executable = Path(state["executable"])
    if (prefix.is_symlink() or install.is_symlink() or executable.is_symlink()
            or not executable.is_file() or not install.is_dir()):
        return {"ok": False, "message": "External MO2 paths changed or became unsafe after discovery; launch was refused."}
    try:
        executable.resolve(strict=True).relative_to(install.resolve(strict=True))
    except (OSError, ValueError, RuntimeError):
        return {"ok": False, "message": "External MO2 executable resolves outside its installation directory; launch was refused."}
    inventory = inspect_mo2_instances(prefix, install, require_managed=False)
    if not inventory.get("ok"):
        return {"ok": False, "message": inventory.get("message", "Could not inspect external MO2 instances.")}
    candidates = inventory.get("skyrim_instances", [])
    if instance_name is None:
        if len(candidates) == 1:
            instance_name = candidates[0]["name"]
        elif candidates:
            return {"ok": False, "needs_instance_selection": True,
                    "instances": [item["name"] for item in candidates],
                    "message": "Choose an MO2 Skyrim instance before launching."}
        else:
            return {"ok": False, "message": "No Skyrim Special Edition MO2 instance was found."}
    if instance_name not in [item["name"] for item in candidates]:
        return {"ok": False, "message": "The selected Skyrim MO2 instance is not present in the external installation."}
    steam_root = Path(state["steam_root"])
    env = _runtime_environment(proton, steam_root, prefix)
    env.update({
        "SteamAppId": APP_ID, "SteamGameId": APP_ID,
        "STEAM_COMPAT_APP_ID": APP_ID,
        "STEAM_COMPAT_DATA_PATH": str(prefix.resolve()),
    })
    if xaudio2_compat:
        current = env.get("WINEDLLOVERRIDES", "")
        overrides = [item for item in current.split(";")
                     if item and not item.lower().startswith("xaudio2_7=")]
        overrides.append("xaudio2_7=n,b")
        env["WINEDLLOVERRIDES"] = ";".join(overrides)
    try:
        process = subprocess.Popen(
            [str(proton / "proton"), "run", str(executable),
             "--instance", instance_name],
            cwd=str(install), env=env, start_new_session=True,
        )
    except OSError as error:
        return {"ok": False, "message": f"Could not launch existing MO2: {error}"}
    return {"ok": True, "pid": process.pid, "managed": False,
            "prefix": str(prefix.resolve()), "instance": instance_name,
            "message": f"Started externally managed MO2 instance '{instance_name}' through Skyrim's Steam prefix. Existing MO2 files were not changed by Wyrd."}


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


def launch_mo2_in_skyrim_prefix(proton_path: Path, game_path: Path,
                                home: Path | None = None,
                                instance_name: str | None = None,
                                xaudio2_compat: bool = False) -> dict:
    """Launch verified MO2 using Skyrim's Steam AppID prefix and selected Proton."""
    home = (home or Path.home()).expanduser()
    proton = Path(proton_path).expanduser()
    game = Path(game_path).expanduser()
    validation = inspect_proton_build(proton)
    if not validation.get("ok"):
        return {"ok": False, "message": validation.get("message", "Selected Proton build is invalid.")}
    state = inspect_skyrim_prefix_mo2_installation(game, home)
    if not state.get("installed"):
        return {"ok": False, "message": state.get("message", "Wyrd-managed MO2 is not installed.")}
    steam_root = Path(state["steam_root"])
    prefix = Path(state["prefix"])
    install = Path(state["install"])
    executable = Path(state["executable"])
    inventory = inspect_mo2_instances(prefix, install)
    if not inventory.get("ok"):
        return {"ok": False, "message": inventory.get("message", "Could not inspect MO2 instances in Skyrim's prefix.")}
    candidates = inventory.get("skyrim_instances", [])
    if instance_name is None:
        if len(candidates) == 1:
            instance_name = candidates[0]["name"]
        elif candidates:
            return {"ok": False, "needs_instance_selection": True,
                    "instances": [item["name"] for item in candidates],
                    "message": "Choose an MO2 Skyrim instance before launching."}
        else:
            return {"ok": False, "message": "No Skyrim Special Edition MO2 instance was found. Create one in MO2 first."}
    if instance_name not in [item["name"] for item in candidates]:
        return {"ok": False, "message": "The selected Skyrim MO2 instance is not verified in Skyrim's prefix."}
    env = _runtime_environment(proton, steam_root, prefix)
    env.update({
        "SteamAppId": APP_ID, "SteamGameId": APP_ID,
        "STEAM_COMPAT_APP_ID": APP_ID,
        "STEAM_COMPAT_DATA_PATH": str(prefix.resolve()),
    })
    if xaudio2_compat:
        current = env.get("WINEDLLOVERRIDES", "")
        overrides = [item for item in current.split(";")
                     if item and not item.lower().startswith("xaudio2_7=")]
        overrides.append("xaudio2_7=n,b")
        env["WINEDLLOVERRIDES"] = ";".join(overrides)
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
        "message": f"Started the Proton wrapper for MO2 instance '{instance_name}' in Skyrim's Steam prefix. MO2 process startup is not yet independently confirmed.",
    }


def reset_wyrd_mo2(home: Path | None = None) -> dict:
    """Remove only the verified Wyrd MO2 install and its registered global instances.

    Skyrim's Steam compatdata prefix is deliberately never removed. Global
    instance directories are eligible only when registered to this verified
    Wyrd installation and located in its Proton user's standard MO2 root.
    Custom storage outside each instance directory is preserved.
    """
    import json
    import shutil

    home = (home or Path.home()).expanduser()
    state = inspect_skyrim_prefix_mo2_installation(
        _game_path_for_wyrd_install(home), home
    )
    if not state.get("installed"):
        return {"ok": False, "message": state.get("message", "No verified Wyrd MO2 installation found."), "removed": []}

    install = Path(state["install"])
    prefix = Path(state["prefix"])
    marker = install / ".wyrd-managed.json"
    executable = install / "ModOrganizer.exe"
    if any(path.is_symlink() for path in (install, prefix, marker, executable)):
        return {"ok": False, "message": "A Wyrd MO2 path is symlinked; reset refused.", "removed": []}
    try:
        resolved_prefix = prefix.resolve(strict=True)
        resolved_install = install.resolve(strict=True)
        resolved_install.relative_to(resolved_prefix)
        metadata = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError, RuntimeError):
        return {"ok": False, "message": "Wyrd installation ownership or path could not be verified; nothing was removed.", "removed": []}
    if (
        metadata.get("managed_by") != "Wyrd"
        or metadata.get("installation_mode") != "skyrim-steam-prefix"
        or Path(metadata.get("install", "")).expanduser() != install
        or not executable.is_file()
    ):
        return {"ok": False, "message": "MO2 installation is not positively identified as Wyrd-owned; nothing was removed.", "removed": []}

    # Resolve only registry entries that explicitly reference this Wyrd-owned executable.
    config_dir = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")).expanduser() / "wyrd"
    registry_path = config_dir / "mo2-registry.json"
    settings_path = config_dir / "settings.json"
    owned_instance_dirs: list[Path] = []
    registry_payload = None
    if registry_path.exists() or registry_path.is_symlink():
        if registry_path.is_symlink() or not registry_path.is_file():
            return {"ok": False, "message": "Wyrd registry path is unsafe; reset refused.", "removed": []}
        try:
            from wyrd_mo2_registry import load_registry
            registry_payload = load_registry(registry_path)
        except Exception as error:
            return {"ok": False, "message": f"Wyrd registry could not be validated; reset refused: {error}", "removed": []}
        owned_install_ids = {
            item["id"] for item in registry_payload["installations"]
            if item.get("owner") == "wyrd"
            and Path(item.get("executable", "")).expanduser() == executable
        }
        users_root = prefix / "pfx/drive_c/users"
        if users_root.is_symlink() or not users_root.is_dir():
            return {"ok": False, "message": "Proton user root is missing or unsafe; reset refused.", "removed": []}
        # The instance manager stores global instances under the Proton user's
        # AppData/Local/ModOrganizer directory. Do not follow user-name symlinks.
        user_roots = [p for p in users_root.iterdir() if p.is_dir() and not p.is_symlink()]
        global_roots = [p / "AppData/Local/ModOrganizer" for p in user_roots]
        for record in registry_payload["instances"]:
            if record.get("installation_id") not in owned_install_ids:
                continue
            instance_dir = Path(record.get("instance_path", "")).expanduser()
            config_file = Path(record.get("config", "")).expanduser()
            if instance_dir.is_symlink() or not instance_dir.is_dir() or config_file.is_symlink() or not config_file.is_file():
                return {"ok": False, "message": f"Registered instance is missing or unsafe: {instance_dir}. Reset refused before deleting anything.", "removed": []}
            valid_root = False
            for root in global_roots:
                try:
                    if root.is_symlink():
                        continue
                    resolved_root = root.resolve(strict=False)
                    resolved_root.relative_to(resolved_prefix)
                    instance_dir.resolve(strict=True).relative_to(resolved_root)
                    config_file.resolve(strict=True).relative_to(instance_dir.resolve(strict=True))
                    valid_root = config_file.name.casefold() == "modorganizer.ini"
                    if valid_root:
                        break
                except (OSError, ValueError, RuntimeError):
                    continue
            if valid_root:
                owned_instance_dirs.append(instance_dir)
            elif record.get("instance_type") == "portable":
                # Portable data is inside the exact marked installation and is
                # removed with it; do not treat arbitrary registered paths as owned.
                try:
                    instance_dir.resolve(strict=True).relative_to(resolved_install)
                except (OSError, ValueError, RuntimeError):
                    return {"ok": False, "message": f"Registered instance is outside Wyrd's verified install: {instance_dir}. Reset refused.", "removed": []}

    # Validate every deletion target before the first destructive operation.
    targets = []
    for directory in owned_instance_dirs:
        targets.append((directory, "global instance"))
    targets.append((install, "Wyrd MO2 installation"))
    for target, _kind in targets:
        if target.is_symlink() or not target.is_dir():
            return {"ok": False, "message": f"Reset target changed or is unsafe: {target}", "removed": []}
        try:
            target.resolve(strict=True).relative_to(resolved_prefix)
        except (OSError, ValueError, RuntimeError):
            return {"ok": False, "message": f"Reset target escaped Skyrim's prefix boundary: {target}", "removed": []}

    removed = []
    try:
        for target, kind in targets:
            shutil.rmtree(target)
            removed.append({"path": str(target), "kind": kind})
    except OSError as error:
        return {"ok": False, "message": f"Reset stopped after a partial removal: {error}", "removed": removed}

    # Remove only the records for this installation; preserve all external and
    # unrelated Wyrd installations in the registry.
    if registry_payload is not None:
        registry_payload["instances"] = [
            item for item in registry_payload["instances"]
            if item.get("installation_id") not in owned_install_ids
        ]
        registry_payload["installations"] = [
            item for item in registry_payload["installations"]
            if item.get("id") not in owned_install_ids
        ]
        used_games = {item.get("game_id") for item in registry_payload["instances"]}
        used_runtimes = {item.get("runtime_id") for item in registry_payload["instances"]}
        registry_payload["games"] = [item for item in registry_payload["games"] if item.get("id") in used_games]
        registry_payload["runtimes"] = [item for item in registry_payload["runtimes"] if item.get("id") in used_runtimes]
        try:
            from wyrd_mo2_registry import save_registry
            save_registry(registry_path, registry_payload)
        except Exception as error:
            return {"ok": False, "message": f"MO2 files were removed but registry cleanup failed: {error}", "removed": removed}

    if settings_path.exists() and not settings_path.is_symlink() and settings_path.is_file():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(settings, dict):
                settings.pop("selected_mo2_instance_id", None)
                temporary = settings_path.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
                os.replace(temporary, settings_path)
        except (OSError, ValueError):
            return {"ok": False, "message": "MO2 files were removed, but saved selection could not be cleared.", "removed": removed}

    return {
        "ok": True,
        "removed": removed,
        "message": "Wyrd-managed MO2 installation and registered global instances were removed. Skyrim's Steam compatdata prefix, game files, saves, external installations, and custom storage outside instance folders were preserved.",
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
