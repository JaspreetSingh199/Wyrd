"""Shared filesystem path-safety primitives."""
from __future__ import annotations

from pathlib import Path


def is_path_within(path: Path, root: Path) -> bool:
    """Return whether path resolves beneath root without following a final symlink."""
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
        return True
    except (OSError, ValueError, RuntimeError):
        return False
