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


def _same_filesystem_path(left: str | os.PathLike | None,
                         right: str | os.PathLike | None) -> bool:
    """Compare discovered and persisted paths after normalizing aliases."""
    if not left or not right:
        return False
    try:
        return Path(left).expanduser().resolve(strict=False) == Path(right).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return os.path.normpath(os.path.expanduser(str(left))) == os.path.normpath(os.path.expanduser(str(right)))
