"""Steam-prefix path helpers used by the Qt interface."""
from __future__ import annotations

import os
from pathlib import Path

from proton_runtime import _steam_root_for_game
from steam_prefix import steam_compat_prefix


def _steam_prefix_for_game_path(game_path: str | Path | None) -> Path | None:
    """Resolve AppID 489830 under the Steam client that owns this game library."""
    if not game_path:
        return None
    try:
        steam_root = _steam_root_for_game(Path(game_path), Path.home())
        return steam_compat_prefix(steam_root)
    except (OSError, RuntimeError, ValueError):
        return None


def _same_filesystem_path(left: str | os.PathLike | None,
                         right: str | os.PathLike | None) -> bool:
    """Compare discovered and persisted paths after normalizing aliases."""
    if not left or not right:
        return False
    try:
        return Path(left).expanduser().resolve(strict=False) == Path(right).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return os.path.normpath(os.path.expanduser(str(left))) == os.path.normpath(os.path.expanduser(str(right)))


def initial_mo2_dialog_paths(mo2_state: dict, saved_settings: dict) -> tuple[str, str, str]:
    """Resolve initial dialog paths without putting discovery logic in page construction."""
    from skyrim_detection import detect_skyrim

    detected = detect_skyrim()
    game_path = detected.get("game_dir", "") if detected.get("found") else ""
    prefix = _steam_prefix_for_game_path(game_path) if game_path else None
    install_path = mo2_state.get("install") or (
        str(prefix / "pfx/drive_c/Modding/MO2") if prefix else ""
    )
    return str(prefix) if prefix else "", str(install_path), str(game_path)
