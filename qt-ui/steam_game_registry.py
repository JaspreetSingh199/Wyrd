"""Provision and verify Skyrim's Bethesda registry installation path."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from mo2_registry import _reject_symlinked_path_components
from mo2_instances import _resolve_windows_path
from proton_runtime import _runtime_environment
from steam_prefix import APP_ID, steam_compat_prefix


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
