"""Read-only verification of Skyrim's Bethesda registry installation path."""
from __future__ import annotations

import re
from pathlib import Path

from mo2_registry import _reject_symlinked_path_components
from mo2_instances import _resolve_windows_path
from steam_prefix import APP_ID, steam_compat_prefix


_REGISTRY_SECTION = (
    "software\\wow6432node\\bethesda softworks\\skyrim special edition"
)


def _read_installed_path(system_registry: Path) -> str | None:
    """Read Skyrim's Installed Path directly from Wine's system.reg.

    This is deliberately filesystem-only. Refresh must never start Wine,
    Proton, regedit, or wineserver as a side effect.
    """
    if system_registry.is_symlink() or not system_registry.is_file():
        return None
    try:
        lines = system_registry.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except OSError:
        return None

    in_target_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and "]" in stripped:
            section = stripped[1:stripped.index("]")]
            normalized = section.replace("\\\\", "\\").casefold()
            in_target_section = normalized == _REGISTRY_SECTION
            continue
        if not in_target_section:
            continue
        match = re.match(r'^"Installed Path"="(.*)"$', stripped)
        if match:
            return match.group(1).replace("\\\\", "\\")
    return None


def configure_skyrim_se_registry(prefix_root: Path, game_path: Path,
                                 steam_root: Path, proton_path: Path,
                                 windows_game_path: str | None = None) -> dict:
    """Verify the existing registry path without modifying the Steam prefix.

    The legacy function name and arguments are retained for callers. Registry
    provisioning is intentionally not performed during refresh: repairing it
    requires a separately initiated operation, never an implicit Wine launch.
    """
    try:
        prefix = _reject_symlinked_path_components(Path(prefix_root), "Steam compatdata prefix")
        game = _reject_symlinked_path_components(Path(game_path), "Skyrim game")
        steam = _reject_symlinked_path_components(Path(steam_root), "Steam client root")
        resolved_prefix = prefix.resolve(strict=True)
        resolved_steam = steam.resolve(strict=True)
        if resolved_prefix != steam_compat_prefix(resolved_steam).resolve(strict=True):
            raise RuntimeError("Registry verification refused: prefix is not Skyrim AppID 489830 compatdata.")
        resolved_game = game.resolve(strict=True)
        if not (resolved_game / "SkyrimSE.exe").is_file():
            raise RuntimeError("SkyrimSE.exe is missing from the detected game directory.")
        if not windows_game_path:
            raise RuntimeError("A dynamically discovered Proton game path is required; no drive letter is assumed.")
        mapped = _resolve_windows_path(windows_game_path, resolved_prefix)
        if mapped != resolved_game:
            raise RuntimeError("Generated Wine game path does not resolve to the detected Skyrim directory.")

        windows_path = windows_game_path.rstrip("\\") + "\\"
        system_registry = resolved_prefix / "pfx/system.reg"
        existing_path = _read_installed_path(system_registry)
        if not existing_path:
            raise RuntimeError(
                "Skyrim's Bethesda Installed Path registry value is missing. "
                "Refresh did not start Proton or modify the Steam prefix."
            )
        if existing_path.casefold() != windows_path.casefold():
            raise RuntimeError(
                "Skyrim's Bethesda Installed Path registry value does not match the detected game. "
                "Refresh did not start Proton or modify the Steam prefix."
            )
        return {
            "ok": True, "already_configured": True,
            "registry_key": r"HKLM\SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition",
            "value": "Installed Path", "windows_path": windows_game_path,
            "message": "Skyrim registry path matches; verified read-only without starting Proton.",
        }
    except (OSError, ValueError, RuntimeError) as error:
        return {"ok": False, "message": str(error)}
