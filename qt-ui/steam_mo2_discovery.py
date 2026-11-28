"""Read-only MO2 installation and instance discovery in Skyrim's Steam prefix."""
from __future__ import annotations

import json
from pathlib import Path

from proton_runtime import _steam_root_for_game
from mo2_instances import inspect_mo2_instances
from steam_prefix import APP_ID, steam_compat_prefix


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
            and metadata.get("installation_mode") in {"skyrim-steam-prefix", "official-windows-installer"}
            and Path(metadata.get("install", "")).expanduser() == install
        )
        return {
            "installed": bool(owned), "managed": bool(owned),
            "executable": str(executable) if owned else None,
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
    return inspect_mo2_instances(
        Path(state["prefix"]), Path(state["install"]),
        detected_game_path=Path(game_path).expanduser(),
    )



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
    """Discover MO2 instances independently of the MO2 executable installation.

    The AppID 489830 prefix is the discovery boundary. MO2 instances are
    user data and can outlive, or live separately from, the executable Wyrd
    recognizes at its conventional install path. This function therefore
    validates the Steam prefix first, then inventories known MO2 instance
    roots even when no MO2 installation is registered at that path.
    """
    game = Path(game_path).expanduser()
    if not game.is_dir() or game.is_symlink():
        return {"ok": False, "managed": False, "instances": [],
                "skyrim_instances": [],
                "message": "Skyrim game directory is missing or unsafe; MO2 discovery was not run."}
    try:
        steam_root = _steam_root_for_game(game, (home or Path.home()).expanduser())
        prefix = steam_compat_prefix(steam_root)
        if prefix.is_symlink() or not prefix.is_dir():
            return {"ok": False, "managed": False, "instances": [],
                    "skyrim_instances": [],
                    "message": "Skyrim's Steam compatdata prefix is missing or unsafe; MO2 instances cannot be inspected."}
        prefix.resolve(strict=True).relative_to(steam_root.resolve(strict=True))
        users_root = prefix / "pfx/drive_c/users"
        prefix_components = (
            prefix / "pfx", prefix / "pfx/drive_c", users_root,
        )
        if any(component.is_symlink() for component in prefix_components):
            return {"ok": False, "managed": False, "instances": [],
                    "skyrim_instances": [],
                    "message": "Skyrim's Proton Wine-user path contains a symlink; MO2 discovery was refused."}
        if not users_root.is_dir():
            return {"ok": False, "managed": False, "instances": [],
                    "skyrim_instances": [],
                    "message": "Skyrim's Proton Wine-user directory is missing; MO2 instances cannot be inspected."}
        users_root.resolve(strict=True).relative_to(prefix.resolve(strict=True))
        managed = inspect_skyrim_prefix_mo2_installation(game, home)
        installation = (
            managed if managed.get("installed")
            else inspect_external_skyrim_prefix_mo2_installation(game, home)
        )
        install_root = (
            Path(installation["install"])
            if installation.get("installed") and installation.get("install")
            else None
        )
        # If the installation is not at the conventional path, continue to
        # discover known instances in the validated Wine user tree. Do not
        # follow MO2 storage/base_directory paths as instance-discovery roots.
        inventory = inspect_mo2_instances(
            prefix, install_root, require_managed=False,
            detected_game_path=game,
        )
        inventory["managed"] = bool(installation.get("managed", False))
        inventory["installation"] = installation
        if not installation.get("installed"):
            inventory["installation_message"] = installation.get(
                "message", "No conventional MO2 executable installation was identified."
            )
        if not inventory.get("instances"):
            inventory["message"] = (
                "No MO2 instances were found in the known Skyrim-prefix instance locations. "
                "MO2 executable discovery is independent and is not required for this scan."
            )
        return inventory
    except (OSError, ValueError, RuntimeError) as error:
        return {"ok": False, "managed": False, "instances": [],
                "skyrim_instances": [],
                "message": f"Could not safely inspect Skyrim's MO2 prefix: {error}"}
