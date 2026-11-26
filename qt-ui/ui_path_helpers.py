"""Steam-prefix path helpers used by the Qt interface."""
from __future__ import annotations

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


class MainWindow(QMainWindow):
