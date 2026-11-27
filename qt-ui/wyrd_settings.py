"""Persistence for Wyrd's user-local GUI preferences.

This module owns only Wyrd settings. It does not inspect or modify MO2,
Skyrim, Proton prefixes, or Steam data.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from proton_runtime import managed_paths


def write_settings(home: Path | None, proton_path: Path, game_path: Path,
                   xaudio2_compat: bool = False) -> None:
    paths = managed_paths(home)
    paths["config"].mkdir(parents=True, exist_ok=True)
    temporary = paths["settings"].with_suffix(".tmp")
    temporary.write_text(json.dumps({
        "proton": str(proton_path.resolve()), "game_path": str(game_path.resolve()),
        "xaudio2_compat": bool(xaudio2_compat),
        "selected_mo2_instance_id": read_settings(home).get("selected_mo2_instance_id", ""),
        "first_run_completed": read_settings(home).get("first_run_completed", False),
        "updated": datetime.now(timezone.utc).isoformat(),
    }, indent=2), encoding="utf-8")
    os.replace(temporary, paths["settings"])

def read_settings(home: Path | None = None) -> dict:
    """Read persisted GUI selections without making or changing any files."""
    settings = managed_paths(home)["settings"]
    try:
        payload = json.loads(settings.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    result = {}
    for key in ("proton", "game_path"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value
    result["xaudio2_compat"] = bool(payload.get("xaudio2_compat", False))
    result["first_run_completed"] = bool(payload.get("first_run_completed", False))
    selected_instance = payload.get("selected_mo2_instance_id")
    if isinstance(selected_instance, str) and selected_instance.strip():
        result["selected_mo2_instance_id"] = selected_instance.strip()
    return result

def reset_wyrd_settings(home: Path | None = None) -> dict:
    """Clear only Wyrd's GUI preferences; preserve MO2, registry, game and Steam data."""
    paths = managed_paths(home)
    config = paths["config"]
    settings = paths["settings"]
    if config.is_symlink() or settings.is_symlink():
        return {"ok": False, "message": "Wyrd settings path is symlinked; nothing was changed."}
    try:
        config_resolved = config.resolve(strict=True)
        settings.parent.resolve(strict=True)
        if settings.parent.resolve(strict=True) != config_resolved:
            return {"ok": False, "message": "Wyrd settings path is outside its configuration folder."}
        if settings.exists():
            if not settings.is_file():
                return {"ok": False, "message": "Wyrd settings is not a regular file; nothing was changed."}
            settings.unlink()
        return {
            "ok": True,
            "message": "Wyrd preferences cleared. MO2 files, registrations, Skyrim and Steam data were left untouched.",
        }
    except FileNotFoundError:
        return {"ok": True, "message": "Wyrd preferences were already clear."}
    except OSError as error:
        return {"ok": False, "message": f"Could not clear Wyrd preferences: {error}"}


def mark_first_run_completed(home: Path | None = None) -> None:
    """Persist that the first-launch choices have been presented."""
    paths = managed_paths(home)
    paths["config"].mkdir(parents=True, exist_ok=True)
    payload = {}
    try:
        current = json.loads(paths["settings"].read_text(encoding="utf-8"))
        if isinstance(current, dict):
            payload = current
    except (FileNotFoundError, OSError, ValueError):
        pass
    payload["first_run_completed"] = True
    payload["updated"] = datetime.now(timezone.utc).isoformat()
    temporary = paths["settings"].with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temporary, paths["settings"])
