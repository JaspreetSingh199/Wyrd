"""Read-only inspection of MO2 storage configuration.

Storage paths are resolved for display only. This module never creates,
moves, deletes, or traverses arbitrary MO2 storage directories.
"""
from __future__ import annotations

import configparser
import os
import re
from pathlib import Path

from mo2_instances import inspect_mo2_instances


def _resolve_mo2_storage_path(raw_value: str, base_directory: Path,
                              prefix_root: Path, install_root: Path | None = None) -> dict:
    """Resolve one configured MO2 storage path without creating or traversing it."""
    raw = (raw_value or "").strip().strip('"')
    if not raw:
        return {"configured": raw_value or "", "path": "", "status": "unconfigured",
                "resolution": "default"}
    normalized = raw.replace("/", "\\")
    drive = normalized[:2].casefold()
    if drive == "z:":
        candidate = Path("/") / normalized[2:].lstrip("\\").replace("\\", "/")
        resolution = "wine-z-drive"
        boundary = None
    elif drive == "c:":
        # Count all path components from the C: drive root. Any leading parent
        # traversal escapes drive_c, irrespective of the current user's depth.
        relative = normalized[2:].lstrip("\\").replace("\\", "/")
        parts = []
        for part in relative.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                if not parts:
                    return {"configured": raw, "path": "", "status": "outside-prefix",
                            "resolution": "wine-c-drive"}
                parts.pop()
            else:
                parts.append(part)
        root = (Path(prefix_root) / "pfx/drive_c").resolve(strict=False)
        candidate = root.joinpath(*parts)
        resolution = "wine-c-drive"
        boundary = root
    elif re.match(r"^[a-zA-Z]:", normalized):
        return {"configured": raw, "path": "", "status": "unsupported-drive",
                "resolution": "unsupported"}
    else:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = Path(base_directory).expanduser() / candidate
        resolution = "absolute" if Path(raw).is_absolute() else "base-relative"
        boundary = None
    candidate = Path(os.path.normpath(str(candidate)))
    if boundary is not None:
        try:
            candidate.resolve(strict=False).relative_to(boundary)
        except (OSError, ValueError, RuntimeError):
            return {"configured": raw, "path": str(candidate), "status": "outside-prefix",
                    "resolution": resolution}
    if candidate.is_symlink():
        return {"configured": raw, "path": str(candidate), "status": "symlink",
                "resolution": resolution}
    try:
        resolved = candidate.resolve(strict=True) if candidate.exists() else candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return {"configured": raw, "path": str(candidate), "status": "unreadable",
                "resolution": resolution}
    if boundary is not None:
        try:
            resolved.relative_to(boundary)
        except ValueError:
            return {"configured": raw, "path": str(resolved), "status": "outside-prefix",
                    "resolution": resolution}
    exists = resolved.is_dir()
    inside_install = False
    if install_root is not None:
        try:
            resolved.relative_to(Path(install_root).resolve(strict=False))
            inside_install = True
        except ValueError:
            pass
    inside_prefix = False
    try:
        resolved.relative_to(Path(prefix_root).resolve(strict=False))
        inside_prefix = True
    except ValueError:
        pass
    scope = "installation" if inside_install else "prefix" if inside_prefix else "external"
    return {"configured": raw, "path": str(resolved),
            "status": "available" if exists else "missing",
            "resolution": resolution, "ownership_scope": scope}


def inspect_mo2_storage(prefix_root: Path, install_root: Path | None = None,
                        require_managed: bool = True) -> dict:
    """Read-only inventory of configured MO2 storage paths for known instances."""
    inventory = inspect_mo2_instances(prefix_root, install_root, require_managed=require_managed)
    if not inventory.get("ok"):
        return {**inventory, "storage_inventory": []}
    storage_inventory = []
    categories = {
        "mods": ("mod_directory", "%BASE_DIR%/mods"),
        "downloads": ("download_directory", "%BASE_DIR%/downloads"),
        "cache": ("cache_directory", "%BASE_DIR%/webcache"),
        "profiles": ("profiles_directory", "%BASE_DIR%/profiles"),
        "overwrite": ("overwrite_directory", "%BASE_DIR%/overwrite"),
    }
    for instance in inventory.get("instances", []):
        ini = Path(instance["config"])
        parser = configparser.ConfigParser(interpolation=None)
        try:
            with ini.open("r", encoding="utf-8-sig", errors="replace") as handle:
                parser.read_file(handle)
        except (OSError, configparser.Error) as error:
            storage_inventory.append({"instance": instance["name"], "config": str(ini),
                                      "ok": False, "error": str(error), "paths": {}})
            continue
        settings = parser["Settings"] if parser.has_section("Settings") else {}
        instance_path = Path(instance["path"])
        raw_base = settings.get("base_directory", "").strip() or str(instance_path)
        base_text = raw_base.replace("%BASE_DIR%", str(instance_path))
        base_path = Path(base_text).expanduser()
        if not base_path.is_absolute():
            base_path = instance_path / base_path
        paths = {
            "base": _resolve_mo2_storage_path(
                str(base_path), instance_path, Path(prefix_root), install_root
            )
        }
        for category, (key, default) in categories.items():
            raw = settings.get(key, "").strip() or default
            expanded = raw.replace("%BASE_DIR%", str(base_path))
            paths[category] = _resolve_mo2_storage_path(
                expanded, base_path, Path(prefix_root), install_root
            )
        storage_inventory.append({
            "instance": instance["name"], "instance_path": str(instance_path),
            "config": str(ini), "game_name": instance.get("game_name", ""),
            "is_skyrim_se": bool(instance.get("is_skyrim_se")), "paths": paths,
        })
    aliases = {}
    for item in storage_inventory:
        for category, info in item["paths"].items():
            path = info.get("path")
            if info.get("status") == "available" and path:
                key = os.path.normcase(os.path.realpath(path))
                aliases.setdefault(key, []).append(
                    {"instance": item["instance"], "category": category}
                )
    shared = [{"path": path, "references": refs}
              for path, refs in aliases.items() if len(refs) > 1]
    return {
        "ok": True, "managed": inventory.get("managed", require_managed),
        "instances": storage_inventory, "shared_paths": shared,
        "message": f"Inspected storage settings for {len(storage_inventory)} MO2 instance(s). No data was changed.",
    }
