"""Configure and launch MO2 inside Skyrim's Steam Proton compatdata.

This integration reuses Skyrim's existing AppID prefix. It never resets the
prefix or modifies saves/game files; narrowly scoped drive and game-registry
discovery metadata may be provisioned for MO2 compatibility.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from skyrim_detection import steam_libraries
from wyrd_mo2 import (
    inspect_proton_build, _runtime_environment, inspect_mo2_instances,
    _steam_root_for_game, managed_paths, _resolve_windows_path,
)

from mo2_registry import (
    _reject_symlinked_path_components, synchronize_mo2_instance_game_paths,
)
from steam_prefix import APP_ID, steam_compat_prefix
from steam_mo2_discovery import (
    inspect_skyrim_prefix_mo2_installation,
    inspect_skyrim_prefix_mo2_instances,
    inspect_external_skyrim_prefix_mo2_installation,
    inspect_any_skyrim_prefix_mo2_instances,
)
from steam_mo2_launch import (
    launch_external_mo2_in_skyrim_prefix,
    launch_mo2_in_skyrim_prefix,
    launch_skyrim_via_mo2_in_skyrim_prefix,
from steam_mo2_management import install_mo2_into_skyrim_prefix, reset_wyrd_mo2
)




def discover_wine_game_path(prefix_root: Path, game_path: Path, steam_root: Path) -> dict:
    """Derive the Windows path from the detected Linux game and existing Wine drives.

    No drive letter is assumed and no dosdevices entry is created or changed.
    A discovered filesystem-root mapping is preferred; otherwise the most specific mapping wins.
    """
    try:
        prefix = _reject_symlinked_path_components(Path(prefix_root), "Steam compatdata prefix")
        game = _reject_symlinked_path_components(Path(game_path), "Skyrim game")
        steam = _reject_symlinked_path_components(Path(steam_root), "Steam client root")
        resolved_prefix = prefix.resolve(strict=True)
        resolved_steam = steam.resolve(strict=True)
        if resolved_prefix != steam_compat_prefix(resolved_steam).resolve(strict=True):
            raise RuntimeError("The selected prefix is not this game's Steam AppID 489830 compatdata prefix.")

        resolved_game = game.resolve(strict=True)
        if not resolved_game.is_dir() or not (resolved_game / "SkyrimSE.exe").is_file():
            raise RuntimeError("The detected Skyrim directory is missing SkyrimSE.exe.")
        library_steamapps = resolved_game.parent.parent
        if library_steamapps.name.casefold() != "steamapps":
            raise RuntimeError("The detected Skyrim directory is not inside a Steam library.")
        registered = [item.resolve(strict=True) for item in steam_libraries(resolved_steam)]
        if library_steamapps.resolve(strict=True) not in registered:
            raise RuntimeError("The detected Skyrim directory is not in a library registered by this Steam client.")
        manifest = library_steamapps / f"appmanifest_{APP_ID}.acf"
        if manifest.is_symlink() or not manifest.is_file():
            raise RuntimeError("The detected Steam library has no safe Skyrim AppID manifest.")
        manifest_text = manifest.read_text(encoding="utf-8", errors="replace")
        app_id_match = re.search(r'"appid"\s+"([^"]+)"', manifest_text)
        install_dir_match = re.search(r'"installdir"\s+"([^"]+)"', manifest_text)
        if (not app_id_match or app_id_match.group(1) != APP_ID
                or not install_dir_match or install_dir_match.group(1) != resolved_game.name):
            raise RuntimeError("The Steam AppID manifest does not identify the detected Skyrim directory.")

        dosdevices = resolved_prefix / "pfx/dosdevices"
        if dosdevices.is_symlink() or not dosdevices.is_dir():
            raise RuntimeError("The active Proton dosdevices directory is missing or unsafe.")
        candidates = []
        for entry in dosdevices.iterdir():
            match = re.fullmatch(r"([a-z]):", entry.name.casefold())
            if not match or not entry.is_symlink():
                continue
            try:
                drive_root = entry.resolve(strict=True)
                if not drive_root.is_dir():
                    continue
                relative = resolved_game.relative_to(drive_root)
                windows_path = match.group(1).upper() + ":\\" + str(relative).replace("/", "\\")
                round_trip = _resolve_windows_path(windows_path, resolved_prefix)
                if round_trip != resolved_game:
                    continue
                candidates.append((len(drive_root.parts), match.group(1), windows_path, drive_root))
            except (OSError, ValueError, RuntimeError):
                continue
        if not candidates:
            raise RuntimeError("No existing Proton drive mapping can represent the detected Skyrim directory.")
        # Prefer a discovered mapping to the filesystem root when present.
        # Proton's root mapping can represent any detected Steam library and
        # avoids depending on an old/custom S: library alias. The letter itself
        # is still discovered from dosdevices, never assumed.
        root_mappings = [item for item in candidates if item[3] == Path("/")]
        _specificity, letter, windows_path, drive_root = (
            sorted(root_mappings, key=lambda item: item[1])[0]
            if root_mappings else
            max(candidates, key=lambda item: (item[0], item[1]))
        )
        return {
            "ok": True, "drive": letter.upper() + ":",
            "windows_game_path": windows_path,
            "resolved_game_path": str(resolved_game),
            "drive_root": str(drive_root),
            "message": f"Derived {windows_path} from the detected game path using the existing {letter.upper()}: Proton mapping.",
        }
    except (OSError, ValueError, RuntimeError) as error:
        return {
            "ok": False, "drive": None, "windows_game_path": None,
            "resolved_game_path": None, "message": str(error),
        }



def verify_wine_game_path(proton_path: Path, prefix_root: Path,
                          steam_root: Path, windows_game_path: str) -> dict:
    """Verify Skyrim's Wine path using the existing filesystem mapping only.

    The drive mapping has already been derived from Proton's dosdevices and
    round-trip checked by discover_wine_game_path(). Do not spawn cmd.exe just
    to repeat an existence check: that creates a visible Windows console and
    can interfere with the desktop session.
    """
    try:
        prefix = _reject_symlinked_path_components(Path(prefix_root), "Steam compatdata prefix")
        steam = _reject_symlinked_path_components(Path(steam_root), "Steam client root")
        proton = Path(proton_path).expanduser()
        if proton.is_symlink() or not (proton / "proton").is_file():
            raise RuntimeError("Selected Proton runtime is missing or unsafe.")
        resolved_prefix = prefix.resolve(strict=True)
        resolved_steam = steam.resolve(strict=True)
        expected_prefix = steam_compat_prefix(resolved_steam).resolve(strict=True)
        if resolved_prefix != expected_prefix:
            raise RuntimeError("The supplied prefix is not Skyrim AppID 489830 compatdata.")
        mapped = _resolve_windows_path(windows_game_path, resolved_prefix)
        if mapped is None or not (mapped / "SkyrimSE.exe").is_file():
            raise RuntimeError(
                "Generated Wine path does not resolve to SkyrimSE.exe on the Linux filesystem."
            )
        return {
            "ok": True,
            "windows_executable": windows_game_path.rstrip("\\") + "\\SkyrimSE.exe",
            "resolved_executable": str(mapped / "SkyrimSE.exe"),
            "message": f"Filesystem mapping verified {windows_game_path}\\SkyrimSE.exe; no Windows command shell was launched.",
        }
    except (OSError, ValueError, RuntimeError) as error:
        return {"ok": False, "message": str(error)}


def configure_skyrim_se_registry(prefix_root: Path, game_path: Path,
                                 steam_root: Path, proton_path: Path,
                                 windows_game_path: str | None = None) -> dict:
    """Register the verified Skyrim SE install in this game's existing Proton prefix.

    MO2 and Bethesda tools query this Windows registry key. SteamTinkerLaunch
    documents this registry provisioning as part of its MO2 game discovery.
    Only the Skyrim SE Installed Path value is imported; no instance/config
    directories, Steam manifests, saves, or game files are touched.
    """
    try:
        prefix = _reject_symlinked_path_components(Path(prefix_root), "Steam compatdata prefix")
        game = _reject_symlinked_path_components(Path(game_path), "Skyrim game")
        steam = _reject_symlinked_path_components(Path(steam_root), "Steam client root")
        proton = Path(proton_path).expanduser()
        resolved_prefix = prefix.resolve(strict=True)
        resolved_steam = steam.resolve(strict=True)
        if resolved_prefix != steam_compat_prefix(resolved_steam).resolve(strict=True):
            raise RuntimeError("Registry provisioning refused: prefix is not Skyrim AppID 489830 compatdata.")
        resolved_game = game.resolve(strict=True)
        if not (resolved_game / "SkyrimSE.exe").is_file():
            raise RuntimeError("SkyrimSE.exe is missing from the detected game directory.")
        if proton.is_symlink() or not (proton / "proton").is_file():
            raise RuntimeError("Selected Proton runtime is missing or unsafe.")

        if not windows_game_path:
            raise RuntimeError("A dynamically discovered Proton game path is required; no drive letter is assumed.")
        mapped = _resolve_windows_path(windows_game_path, resolved_prefix)
        if mapped != resolved_game:
            raise RuntimeError("Generated Wine game path does not resolve to the detected Skyrim directory.")
        windows_path = windows_game_path
        registry_path = windows_path.rstrip("\\").replace("\\", "\\\\") + "\\\\"
        reg_text = (
            "Windows Registry Editor Version 5.00\n\n"
            "[HKEY_LOCAL_MACHINE\\SOFTWARE\\WOW6432Node\\Bethesda Softworks\\Skyrim Special Edition]\n"
            f'"Installed Path"="{registry_path}"\n'
        )
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".reg", delete=False) as handle:
            handle.write(reg_text)
            reg_path = Path(handle.name)
        try:
            env = _runtime_environment(proton, resolved_steam, resolved_prefix)
            env.update({"SteamAppId": APP_ID, "SteamGameId": APP_ID,
                        "STEAM_COMPAT_APP_ID": APP_ID,
                        "STEAM_COMPAT_DATA_PATH": str(resolved_prefix)})
            result = subprocess.run(
                [str(proton / "proton"), "run", "regedit", "/S", str(reg_path)],
                cwd=str(resolved_steam), env=env, capture_output=True,
                text=True, timeout=120, check=False,
            )
            if result.returncode != 0:
                details = (result.stdout + "\\n" + result.stderr).strip()
                raise RuntimeError(
                    "Proton regedit failed to import Skyrim's registry path."
                    + (" " + details[-2000:] if details else "")
                )
            query = subprocess.run(
                [str(proton / "proton"), "run", "reg", "query",
                 r"HKLM\SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition",
                 "/v", "Installed Path"],
                cwd=str(resolved_steam), env=env, capture_output=True,
                text=True, timeout=120, check=False,
            )
            if query.returncode != 0:
                details = (query.stdout + "\\n" + query.stderr).strip()
                raise RuntimeError(
                    "Skyrim registry import could not be verified by reading Installed Path."
                    + (" " + details[-2000:] if details else "")
                )
            expected_registry_value = windows_path.rstrip("\\") + "\\"
            if expected_registry_value.casefold() not in query.stdout.casefold():
                raise RuntimeError(
                    "Skyrim registry read-back did not match the detected game path. "
                    f"Expected {windows_path}; registry returned: {query.stdout.strip()[-1000:]}"
                )
        finally:
            reg_path.unlink(missing_ok=True)
        if result.returncode != 0:
            details = (result.stdout + "\\n" + result.stderr).strip()
            raise RuntimeError("Proton regedit failed to import Skyrim's registry path."
                               + (" " + details[-2000:] if details else ""))
        return {"ok": True, "registry_key": r"HKLM\\SOFTWARE\\WOW6432Node\\Bethesda Softworks\\Skyrim Special Edition",
                "value": "Installed Path", "windows_path": windows_path,
                "message": "Registered Skyrim SE's dynamically verified Wine installation path in the existing Steam prefix."}
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        return {"ok": False, "message": str(error)}
