"""Wyrd MO2 settings and instance operations with compatibility exports.

All paths are user-local. Existing MO2 installs and unknown prefixes are never
adopted or overwritten automatically.
"""
from __future__ import annotations

import configparser
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from mo2_instances import (
    _instance_roots, _same_mo2_game_path, inspect_mo2_instances,
    _resolve_mo2_storage_path, inspect_mo2_storage, _resolve_windows_path,
)
from process_monitor import (
    read_launch_diagnostics, classify_launch_diagnostics,
    detect_game_processes, detect_skyrim_processes,
    _proc_identity, _proc_start_time, _is_descendant_process,
    launch_process_status,
)

from proton_runtime import (
    ProgressCallback, PREFIX_DIR_NAME, managed_paths, inspect_proton_build, discover_proton_builds,
    reset_mo2_compatdata, inspect_wyrd_installation, _steam_root_for_game,
    _runtime_environment, _run_proton, _prefix_ready, _initialize_prefix,
)
from steam_prefix import APP_ID
from audio_compatibility import (
    _validated_wyrd_audio_prefix, _sha256_file, _resolve_xact_dlls,
    inspect_audio_compatibility, repair_audio_compatibility,
)
from mo2_launch import (
    launch_mo2, inspect_skyrim_launch_targets, launch_skyrim_via_mo2,
)


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


def copy_mo2_instance_to_prefix(source_instance: Path, destination_prefix: Path,
                                expected_game: str = "Skyrim Special Edition") -> dict:
    """Copy a self-contained Skyrim MO2 instance; never mutate the source."""
    source = Path(source_instance).expanduser()
    prefix = Path(destination_prefix).expanduser()
    stage = None
    try:
        if source.is_symlink() or not source.is_dir():
            raise ValueError("Source MO2 instance is missing or symlinked.")
        if prefix.is_symlink() or not (prefix / "pfx/drive_c/users").is_dir():
            raise ValueError("Destination is not an initialized, safe Proton prefix.")
        source = source.resolve(strict=True)
        prefix = prefix.resolve(strict=True)
        ini = source / "ModOrganizer.ini"
        if ini.is_symlink() or not ini.is_file():
            raise ValueError("Source does not contain a regular ModOrganizer.ini.")
        parser = configparser.ConfigParser(interpolation=None)
        with ini.open("r", encoding="utf-8-sig", errors="replace") as handle:
            parser.read_file(handle)
        general = parser["General"] if parser.has_section("General") else {}
        settings = parser["Settings"] if parser.has_section("Settings") else {}
        if expected_game.casefold() not in general.get("gameName", "").casefold():
            raise ValueError("Source instance is not explicitly configured for Skyrim Special Edition.")
        selected = general.get("selected_profile", "").strip()
        match = re.fullmatch(r"@ByteArray\((.*)\)", selected, flags=re.DOTALL)
        if match:
            selected = match.group(1).strip()
        base_raw = settings.get("base_directory", "").strip() or str(source)
        base = Path(base_raw.replace("%BASE_DIR%", str(source))).expanduser()
        if not base.is_absolute():
            base = source / base
        base = base.resolve(strict=True)
        if base != source and source not in base.parents:
            raise ValueError("MO2 base directory is external to the instance; explicit storage mapping is required.")

        def storage(key: str, default: str) -> Path:
            raw = settings.get(key, "").strip() or default
            candidate = Path(raw.replace("%BASE_DIR%", str(base))).expanduser()
            if not candidate.is_absolute():
                candidate = base / candidate
            if candidate.is_symlink():
                raise ValueError(f"Configured {key} storage is a symlink.")
            resolved = candidate.resolve(strict=True)
            if resolved != source and source not in resolved.parents:
                raise ValueError(f"Configured {key} storage is outside the source instance.")
            if not resolved.is_dir():
                raise ValueError(f"Configured {key} storage is not a directory.")
            return resolved
        profiles = storage("profiles_directory", "%BASE_DIR%/profiles")
        storage("mod_directory", "%BASE_DIR%/mods")
        storage("download_directory", "%BASE_DIR%/downloads")
        if selected and ((profiles / selected).is_symlink() or not (profiles / selected).is_dir()):
            raise ValueError("Selected MO2 profile is missing or unsafe.")
        parent = prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer"
        if parent.is_symlink():
            raise ValueError("Destination MO2 root is a symlink.")
        parent.mkdir(parents=True, exist_ok=True)
        destination = parent / source.name
        if destination.exists() or destination.is_symlink():
            raise ValueError("Instance name already exists in Skyrim's prefix; nothing was overwritten.")
        stage = parent / (source.name + ".wyrd-staging")
        if stage.exists() or stage.is_symlink():
            raise ValueError("A staging directory already exists; inspect it before retrying.")
        for item in source.rglob("*"):
            if item.is_symlink():
                raise ValueError(f"Source contains symlink {item}; refusing to copy.")
        shutil.copytree(source, stage, symlinks=False)
        staged_ini = stage / "ModOrganizer.ini"
        copied = configparser.ConfigParser(interpolation=None)
        with staged_ini.open("r", encoding="utf-8-sig", errors="replace") as handle:
            copied.read_file(handle)
        if copied.has_section("Settings"):
            copied["Settings"]["base_directory"] = str(stage)
            copied["Settings"]["profiles_directory"] = "%BASE_DIR%/profiles"
            copied["Settings"]["mod_directory"] = "%BASE_DIR%/mods"
            copied["Settings"]["download_directory"] = "%BASE_DIR%/downloads"
            with staged_ini.open("w", encoding="utf-8") as handle:
                copied.write(handle)
        for required in ("profiles", "mods", "downloads"):
            if not (stage / required).is_dir():
                raise ValueError(f"Copy is missing {required}; staged copy rolled back.")
        if selected and not (stage / "profiles" / selected).is_dir():
            raise ValueError("Selected profile was not copied; staged copy rolled back.")
        os.replace(stage, destination)
        stage = None
        return {"ok": True, "status": "copied", "source": str(source),
                "destination": str(destination), "instance": source.name,
                "source_preserved": True,
                "message": f"Copied '{source.name}' into Skyrim's Proton prefix; original source preserved."}
    except (OSError, ValueError, configparser.Error, RuntimeError) as error:
        return {"ok": False, "status": "refused", "message": f"MO2 copy refused: {error}"}
    finally:
        if stage is not None and stage.exists():
            shutil.rmtree(stage, ignore_errors=True)

