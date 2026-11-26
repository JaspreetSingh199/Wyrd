"""Legacy explicit MO2 instance-copy operation.

Kept separate from discovery and launch. This operation is never invoked by
Wyrd's normal environment scan or launch workflow.
"""
from __future__ import annotations

import configparser
import os
import re
import shutil
from pathlib import Path


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
