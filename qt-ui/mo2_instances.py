"""Read-only MO2 instance discovery, configuration and storage inspection.

This module owns MO2 instance metadata parsing. It never follows arbitrary
storage roots or mutates MO2 instances, profiles, mods or downloads.
"""
from __future__ import annotations

import configparser
import json
import os
import re
from pathlib import Path

from path_utils import is_path_within as _is_path_within

def _instance_roots(prefix_root: Path, install_root: Path | None = None) -> list[Path]:
    """Return known MO2 instance locations inside Wyrd's managed environment.

    MO2 portable mode stores ModOrganizer.ini beside ModOrganizer.exe;
    instance-manager layouts may also keep named instances under instances/.
    """
    users = prefix_root / "pfx/drive_c/users"
    roots: list[Path] = []
    if users.is_dir() and not users.is_symlink():
        try:
            user_dirs = [
                entry for entry in users.iterdir()
                if entry.is_dir() and not entry.is_symlink()
            ]
        except OSError:
            user_dirs = []
        for user_dir in user_dirs:
            candidates = (
                # MO2 global instances are direct children of ModOrganizer.
                user_dir / "AppData/Local/ModOrganizer",
                user_dir / "AppData/Roaming/ModOrganizer",
                user_dir / "Documents/Modding/ModOrganizer",
                # Some layouts place named instances beneath instances/.
                user_dir / "AppData/Local/ModOrganizer/instances",
                user_dir / "AppData/Roaming/ModOrganizer/instances",
                user_dir / "Documents/Modding/ModOrganizer/instances",
            )
            for candidate in candidates:
                if candidate.is_symlink() or not candidate.is_dir():
                    continue
                try:
                    candidate.resolve(strict=True).relative_to(
                        prefix_root.resolve(strict=True)
                    )
                except (OSError, ValueError, RuntimeError):
                    continue
                roots.append(candidate)

    # MO2 exposes its portable instance through InstanceManager::portablePath().
    # Discover it only from verified executable installations supplied by the
    # caller. Also retain the instance-manager "instances/" layout used by
    # existing Wyrd installs. Global instances are always rooted at LocalAppData.
    if install_root is not None:
        install = Path(install_root).expanduser()
        if install.is_dir() and not install.is_symlink():
            try:
                install.resolve(strict=True).relative_to(prefix_root.resolve(strict=True))
                roots.append(install)
            except (OSError, ValueError, RuntimeError):
                pass
            portable_instances = install / "instances"
            if portable_instances.is_dir() and not portable_instances.is_symlink():
                try:
                    portable_instances.resolve(strict=True).relative_to(
                        prefix_root.resolve(strict=True)
                    )
                    roots.append(portable_instances)
                except (OSError, ValueError, RuntimeError):
                    pass
    # Deduplicate overlapping LocalAppData roots and preserve deterministic order.
    unique: dict[str, Path] = {}
    for root in roots:
        unique.setdefault(os.path.normcase(str(root.resolve(strict=True))), root)
    return list(unique.values())


def _same_mo2_game_path(raw_game_path: str, prefix_root: Path,
                         detected_game_path: Path | None = None) -> bool:
    """Recognize Skyrim SE when MO2 gamePath resolves to the detected install.

    MO2 persists Windows paths. Resolve the selected drive through the active
    Proton prefix's dosdevices mapping, then require an exact match with the
    detected Steam game directory. A stale game label is not sufficient.
    """
    raw = (raw_game_path or "").strip().strip('"')
    if not raw:
        return False
    resolved = _resolve_windows_path(raw, prefix_root, detected_game_path)
    if resolved is None:
        return False
    try:
        if resolved.is_file() and resolved.name.casefold() == "skyrimse.exe":
            resolved = resolved.parent
        if not resolved.is_dir() or not (resolved / "SkyrimSE.exe").is_file():
            return False
        if detected_game_path is None:
            return False
        expected = Path(detected_game_path).expanduser().resolve(strict=True)
        actual = resolved.resolve(strict=True)
        return actual == expected
    except (OSError, RuntimeError):
        return False


def inspect_mo2_instances(prefix_root: Path, install_root: Path | None = None,
                          require_managed: bool = True,
                          detected_game_path: Path | None = None) -> dict:
    """Read-only inventory of MO2 instances in a known Proton prefix.

    It does not follow MO2 custom paths or scan arbitrary disks. By default the
    prefix must be Wyrd-owned. External-prefix mode is for a validated Steam
    AppID prefix and only inspects its known Wine user directories.
    """
    prefix_root = Path(prefix_root).expanduser()
    if require_managed:
        marker = prefix_root / ".wyrd-managed.json"
        if not marker.is_file():
            return {
                "ok": False, "managed": False, "instances": [],
                "message": "The Proton prefix is not marked as Wyrd-managed; it was not inspected.",
            }
        try:
            ownership = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {
                "ok": False, "managed": False, "instances": [],
                "message": "The Wyrd prefix ownership marker is unreadable; it was not inspected.",
            }
        if ownership.get("managed_by") != "Wyrd":
            return {
                "ok": False, "managed": False, "instances": [],
                "message": "The Proton prefix is not marked as Wyrd-managed; it was not inspected.",
            }
    else:
        if prefix_root.is_symlink() or not (prefix_root / "pfx/drive_c/users").is_dir():
            return {
                "ok": False, "managed": False, "instances": [],
                "message": "The external Proton prefix is missing or unsafe; it was not inspected.",
            }

    discovered = []
    errors = []
    scan_roots = _instance_roots(prefix_root, install_root)
    for root in scan_roots:
        try:
            entries = sorted(root.iterdir(), key=lambda item: item.name.casefold())
        except OSError as error:
            errors.append(f"{root}: {error}")
            continue
        # A portable MO2 instance is the install directory itself, not a
        # child directory. Inspect it alongside any named child instances.
        if (root / "ModOrganizer.ini").is_file():
            entries.insert(0, root)
        for directory in entries:
            if not directory.is_dir() or directory.is_symlink():
                continue
            ini = directory / "ModOrganizer.ini"
            if not ini.is_file():
                continue
            parser = configparser.ConfigParser(interpolation=None)
            try:
                with ini.open("r", encoding="utf-8-sig", errors="replace") as handle:
                    parser.read_file(handle)
            except (OSError, configparser.Error) as error:
                errors.append(f"{ini}: {error}")
                continue

            general = parser["General"] if parser.has_section("General") else {}
            settings = parser["Settings"] if parser.has_section("Settings") else {}
            game_path = general.get("gamePath", "").strip()
            # QSettings stores selected_profile as a QByteArray. INI readers
            # may expose Qt's typed representation as @ByteArray(value).
            profile = general.get("selected_profile", "").strip()
            byte_array = re.fullmatch(r"@ByteArray\((.*)\)", profile, flags=re.DOTALL)
            if byte_array:
                profile = byte_array.group(1).strip()

            # MO2 resolves its profile directory from Settings:
            # profiles_directory defaults to %BASE_DIR%/profiles, and
            # base_directory defaults to the instance directory.
            base_value = settings.get("base_directory", "").strip()
            base_value = base_value or str(directory)
            profiles_value = settings.get("profiles_directory", "").strip()
            profiles_value = profiles_value or "%BASE_DIR%/profiles"
            base_value = base_value.replace("%BASE_DIR%", str(directory))
            profiles_value = profiles_value.replace("%BASE_DIR%", base_value)
            profiles_root = Path(profiles_value).expanduser()
            if not profiles_root.is_absolute():
                profiles_root = Path(base_value).expanduser() / profiles_root
            profile_dir = profiles_root / profile if profile else None
            profile_verified = bool(
                profile_dir and profile_dir.is_dir()
                and not profile_dir.is_symlink()
                and (
                    _is_path_within(profile_dir, Path(install_root).expanduser())
                    if install_root is not None else _is_path_within(profile_dir, prefix_root)
                )
            )
            if profile_dir is not None and not profile_verified:
                profile_status = "unverified"
            elif profile:
                profile_status = "verified"
            else:
                profile_status = "not-selected"
            # Report MO2 storage roots for migration preview. Never traverse
            # custom external paths here; they require explicit verification.
            def configured_path(key: str, default: str) -> tuple[str, str]:
                raw = settings.get(key, "").strip() or default
                expanded = raw.replace("%BASE_DIR%", base_value)
                candidate = Path(expanded).expanduser()
                if not candidate.is_absolute():
                    candidate = Path(base_value).expanduser() / candidate
                if candidate.is_symlink():
                    return str(candidate), "symlink"
                if candidate.is_dir():
                    return str(candidate.resolve()), "available"
                return str(candidate), "missing"

            base_path = Path(base_value).expanduser()
            base_status = "symlink" if base_path.is_symlink() else ("available" if base_path.is_dir() else "missing")
            profiles_path, profiles_status = configured_path("profiles_directory", "%BASE_DIR%/profiles")
            mods_path, mods_status = configured_path("mod_directory", "%BASE_DIR%/mods")
            downloads_path, downloads_status = configured_path("download_directory", "%BASE_DIR%/downloads")

            game_name = general.get("gameName", "").strip()

            executables = []
            # MO2 persists this as a QSettings array. Depending on QSettings
            # serialization, entries appear as customExecutables\\1\\title
            # keys in the array section, or as customExecutables\\1 subsections.
            array_size = 0
            if parser.has_section("customExecutables"):
                array_section = parser["customExecutables"]
                try:
                    array_size = int(array_section.get("size", "0"))
                except ValueError:
                    errors.append(f"{ini}: invalid customExecutables array size")
                    array_size = 0
            nested_sections = {}
            for section_name in parser.sections():
                normalized = section_name.replace("\\\\", "/").casefold()
                prefix = "customexecutables/"
                if normalized.startswith(prefix):
                    index = normalized[len(prefix):]
                    if index.isdigit():
                        nested_sections[index] = parser[section_name]
            if array_size <= 0 and nested_sections:
                array_size = max(map(int, nested_sections))
            if array_size < 0 or array_size > 512:
                errors.append(f"{ini}: invalid customExecutables array size {array_size}")
                array_size = 0

            def array_value(index: int, key: str) -> str:
                if parser.has_section("customExecutables"):
                    section = parser["customExecutables"]
                    for candidate_key in (
                        f"{index}\\{key}", f"{index}\\\\{key}", f"{index}/{key}",
                        f"\\{index}\\{key}", f"\\\\{index}\\\\{key}",
                        f"/{index}/{key}",
                    ):
                        value = section.get(candidate_key)
                        if value is not None:
                            return value.strip()
                nested = nested_sections.get(str(index))
                if nested is not None:
                    return nested.get(key, "").strip()
                return ""

            for index in range(1, array_size + 1):
                executable = {
                    "title": array_value(index, "title"),
                    "binary": array_value(index, "binary"),
                    "arguments": array_value(index, "arguments"),
                    "working_directory": array_value(index, "workingDirectory"),
                }
                if executable["title"] or executable["binary"]:
                    executables.append(executable)

            # The configured gamePath is authoritative. Loose matching on
            # profile labels or executable names can classify stale instances
            # as Skyrim even when MO2 points at a different installation.
            game_path_matches = _same_mo2_game_path(
                game_path, prefix_root, detected_game_path
            )
            # MO2's instance manager identifies an instance by its configured
            # gameName. Keep that named instance discoverable even when its
            # stored gamePath is stale (for example, after moving a Steam
            # library). Launch remains gated by verifying each executable
            # against the currently detected Skyrim directory below.
            is_skyrim = (
                game_name.strip().casefold() == "skyrim special edition"
                or game_path_matches
            )
            discovered.append({
                "name": directory.name,
                "path": str(directory),
                "config": str(ini),
                "game_name": game_name,
                "game_path": game_path,
                "selected_profile": profile if profile_verified else "",
                "selected_profile_candidate": profile,
                "selected_profile_path": str(profile_dir) if profile_dir else "",
                "selected_profile_status": profile_status,
                "storage": {
                    "base": {"path": str(base_path), "status": base_status},
                    "profiles": {"path": profiles_path, "status": profiles_status},
                    "mods": {"path": mods_path, "status": mods_status},
                    "downloads": {"path": downloads_path, "status": downloads_status},
                    "cache": configured_path("cache_directory", "%BASE_DIR%/webcache"),
                    "overwrite": configured_path("overwrite_directory", "%BASE_DIR%/overwrite"),
                },
                "is_skyrim_se": is_skyrim,
                "game_path_matches": game_path_matches,
                "executables": executables,
            })

    instances = sorted(discovered, key=lambda item: item["name"].casefold())
    skyrim_instances = [item for item in instances if item["is_skyrim_se"]]
    return {
        "ok": True, "managed": bool(require_managed),
        "instances": instances,
        "skyrim_instances": skyrim_instances,
        "scan_roots": [str(root) for root in scan_roots],
        "errors": errors,
        "message": (
            f"Found {len(instances)} MO2 instance(s), including "
            f"{len(skyrim_instances)} identified Skyrim SE instance(s)."
            if instances else "No MO2 instances were found in the known prefix locations."
        ),
    }


def _resolve_mo2_storage_path(raw_value: str, base_directory: Path,
                              prefix_root: Path, install_root: Path | None = None) -> dict:
    """Compatibility facade for the read-only storage path resolver."""
    from mo2_storage import _resolve_mo2_storage_path as resolve

    return resolve(raw_value, base_directory, prefix_root, install_root)


def inspect_mo2_storage(prefix_root: Path, install_root: Path | None = None,
                        require_managed: bool = True) -> dict:
    """Compatibility facade for read-only MO2 storage inspection."""
    from mo2_storage import inspect_mo2_storage as inspect

    return inspect(prefix_root, install_root, require_managed)


def _resolve_windows_path(value: str, prefix_root: Path,
                          game_path: Path | None = None) -> Path | None:
    """Resolve any Windows drive through the active Proton prefix's mappings.

    Drive letters are never assumed: each mapping is read from pfx/dosdevices
    and the resolved target is treated as that drive's root.
    """
    raw = value.strip().strip('"')
    if len(raw) < 2 or raw[1] != ":" or not raw[0].isalpha():
        return None
    drive_letter = raw[0].casefold()
    separator = chr(92)
    relative = raw[2:].lstrip(separator + "/")
    dosdevices = Path(prefix_root).expanduser() / "pfx/dosdevices"
    drive_link = dosdevices / f"{drive_letter}:"
    try:
        if dosdevices.is_symlink() or not drive_link.is_symlink():
            return None
        drive_root = drive_link.resolve(strict=True)
        if not drive_root.is_dir():
            return None
        parts = [
            part for part in relative.replace("/", separator).split(separator)
            if part not in ("", ".")
        ]
        candidate = drive_root.joinpath(*parts)
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(drive_root)
        return resolved
    except (OSError, ValueError, RuntimeError):
        return None
