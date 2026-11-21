"""Read-only inspection of MO2 instances and portable configuration layouts."""
from __future__ import annotations

import configparser
from pathlib import Path

from wyrd_mo2 import inspect_mo2_instances

_PATH_KEYS = ("mod_directory", "profile_directory", "download_directory", "cache_directory")


def _read_config(ini: Path, name: str, instance_path: Path) -> dict | None:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with ini.open("r", encoding="utf-8-sig", errors="replace") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error):
        return None
    general = parser["General"] if parser.has_section("General") else {}
    settings = parser["Settings"] if parser.has_section("Settings") else {}
    configured = {}
    for section in (general, settings):
        for key in _PATH_KEYS:
            value = section.get(key)
            if value and value.strip():
                configured[key] = value.strip()
    game_name = general.get("gameName", "").strip()
    game_path = general.get("gamePath", "").strip()
    profile = general.get("selected_profile", "").strip()
    searchable = (game_name + " " + game_path).casefold()
    is_skyrim = any(marker in searchable for marker in (
        "skyrim special edition", "skyrimse.exe", "skyrim se"
    ))
    return {
        "name": name,
        "path": str(instance_path),
        "config": str(ini),
        "game_name": game_name,
        "game_path": game_path,
        "selected_profile": profile,
        "is_skyrim_se": is_skyrim,
        "storage_paths": configured,
        "layout": "portable" if ini.parent == instance_path else "instance",
    }


def _portable_configs(prefix_root: Path, install_root: Path | None) -> list[tuple[Path, str, Path]]:
    """Find known portable/root configs; never recurse through arbitrary disks."""
    candidates: list[tuple[Path, str, Path]] = []
    users = prefix_root / "pfx/drive_c/users"
    if users.is_dir():
        for user in users.iterdir():
            if not user.is_dir():
                continue
            for base in (
                user / "AppData/Local/ModOrganizer",
                user / "AppData/Roaming/ModOrganizer",
                user / "Documents/Modding/ModOrganizer",
            ):
                ini = base / "ModOrganizer.ini"
                if ini.is_file():
                    candidates.append((ini, base.name or "MO2 portable", base))
    if install_root and install_root.is_dir() and not install_root.is_symlink():
        ini = install_root / "ModOrganizer.ini"
        if ini.is_file():
            candidates.append((ini, "MO2 portable (install root)", install_root))
    return candidates


def inspect_game_mode_storage(prefix_root: Path, install_root: Path | None = None) -> dict:
    """Inventory managed-prefix instances and portable configs without resolving paths or mutation."""
    inventory = inspect_mo2_instances(Path(prefix_root))
    if not inventory.get("ok"):
        return {"ok": False, "instances": [], "errors": [],
                "message": inventory.get("message", "MO2 instance inspection failed.")}
    instances = []
    errors = list(inventory.get("errors", []))
    seen_configs = set()
    for item in inventory.get("instances", []):
        ini = Path(item["config"])
        seen_configs.add(str(ini.resolve(strict=False)))
        result = _read_config(ini, item.get("name", ini.parent.name), Path(item.get("path", ini.parent)))
        if result is None:
            errors.append(f"{ini}: configuration could not be read")
            continue
        result.update({key: item.get(key, result.get(key)) for key in ("game_name", "game_path", "selected_profile", "is_skyrim_se", "executables")})
        result["layout"] = "instance"
        instances.append(result)
    for ini, name, instance_path in _portable_configs(Path(prefix_root), Path(install_root) if install_root else None):
        identity = str(ini.resolve(strict=False))
        if identity in seen_configs:
            continue
        result = _read_config(ini, name, instance_path)
        if result is None:
            errors.append(f"{ini}: configuration could not be read")
            continue
        instances.append(result)
        seen_configs.add(identity)
    instances.sort(key=lambda item: item["name"].casefold())
    return {
        "ok": True,
        "instances": instances,
        "errors": errors,
        "message": f"Read-only scan found {len(instances)} MO2 configuration(s), including portable layouts.",
    }
