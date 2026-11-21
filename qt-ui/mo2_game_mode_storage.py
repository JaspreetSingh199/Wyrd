"""Read-only inspection of Wyrd's MO2 instance storage and configuration paths."""
from __future__ import annotations

import configparser
from pathlib import Path

from wyrd_mo2 import inspect_mo2_instances


_PATH_KEYS = ("mod_directory", "profile_directory", "download_directory", "cache_directory")


def inspect_game_mode_storage(prefix_root: Path) -> dict:
    """Inventory existing Wyrd MO2 instances and configured storage paths.

    This intentionally reports configuration values without resolving arbitrary
    paths, following symlinks, creating directories, or changing settings.
    """
    inventory = inspect_mo2_instances(Path(prefix_root))
    if not inventory.get("ok"):
        return {"ok": False, "instances": [], "errors": [],
                "message": inventory.get("message", "MO2 instance inspection failed.")}
    instances = []
    errors = list(inventory.get("errors", []))
    for item in inventory.get("instances", []):
        ini = Path(item["config"])
        parser = configparser.ConfigParser(interpolation=None)
        try:
            with ini.open("r", encoding="utf-8-sig", errors="replace") as handle:
                parser.read_file(handle)
        except (OSError, configparser.Error) as error:
            errors.append(f"{ini}: {error}")
            continue
        configured = {}
        for section_name in ("General", "Settings"):
            if not parser.has_section(section_name):
                continue
            section = parser[section_name]
            for key in _PATH_KEYS:
                value = section.get(key)
                if value and value.strip():
                    configured[key] = value.strip()
        instances.append({
            "name": item.get("name", ini.parent.name),
            "path": item.get("path", str(ini.parent)),
            "config": str(ini),
            "game_name": item.get("game_name", ""),
            "game_path": item.get("game_path", ""),
            "selected_profile": item.get("selected_profile", ""),
            "is_skyrim_se": bool(item.get("is_skyrim_se")),
            "storage_paths": configured,
        })
    return {
        "ok": True,
        "instances": instances,
        "errors": errors,
        "message": f"Read-only scan found {len(instances)} configured MO2 instance(s).",
    }
