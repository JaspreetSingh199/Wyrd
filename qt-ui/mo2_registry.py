"""Narrow MO2 registry and INI path updates for Skyrim in Proton.

Only the General.gamePath field of instances explicitly named Skyrim SE is
updated. Other INI settings and all profile/mod/storage data are preserved.
"""
from __future__ import annotations

import os
import re
from pathlib import Path


def _reject_symlinked_path_components(path: Path, label: str) -> Path:
    """Return an absolute lexical path after rejecting symlinks in every component.

    Do this before resolve(): resolving first would erase evidence that an
    ancestor in the caller-supplied path traversed a symlink.
    """
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise RuntimeError(f"The {label} path must be absolute.")
    if ".." in candidate.parts:
        raise RuntimeError(f"The {label} path contains a parent-directory component.")
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise RuntimeError(
                f"The {label} path contains a symlinked component: {current}"
            )
    return candidate


def synchronize_mo2_instance_game_paths(
    prefix_root: Path, game_path: Path, windows_game_path: str,
) -> dict:
    """Update only Skyrim instances' General.gamePath before MO2 inventory.

    The Windows path is generated from the detected Linux game path and the
    verified Proton drive mapping. Other MO2 settings, profiles and storage
    locations are preserved byte-for-byte.
    """
    try:
        prefix = _reject_symlinked_path_components(Path(prefix_root), "Steam compatdata prefix")
        game = _reject_symlinked_path_components(Path(game_path), "Skyrim game")
        resolved_prefix = prefix.resolve(strict=True)
        resolved_game = game.resolve(strict=True)
        if not (resolved_game / "SkyrimSE.exe").is_file():
            raise RuntimeError("Detected Skyrim directory does not contain SkyrimSE.exe.")
        if not re.match(r"^[A-Za-z]:\\", windows_game_path):
            raise RuntimeError("Generated MO2 game path is not a valid Windows drive path.")

        pfx = resolved_prefix / "pfx"
        users = pfx / "drive_c/users"
        if pfx.is_symlink() or users.is_symlink() or not users.is_dir():
            raise RuntimeError("Skyrim Proton user directory is missing or unsafe.")

        updated = []
        unchanged = []
        skipped = []
        for user in sorted(users.iterdir(), key=lambda item: item.name.casefold()):
            if user.is_symlink() or not user.is_dir():
                continue
            appdata = user / "AppData"
            local = appdata / "Local"
            root = local / "ModOrganizer"
            if any(component.is_symlink() for component in (appdata, local, root)):
                continue
            if not root.is_dir():
                continue
            for instance in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
                if instance.is_symlink() or not instance.is_dir():
                    continue
                ini = instance / "ModOrganizer.ini"
                if ini.is_symlink() or not ini.is_file():
                    continue
                try:
                    original = ini.read_text(encoding="utf-8-sig")
                except (OSError, UnicodeError):
                    skipped.append(str(ini))
                    continue

                general_match = re.search(
                    r"(?ims)^\[General\][ \t]*\r?\n(.*?)(?=^\[|\Z)",
                    original,
                )
                if not general_match:
                    continue
                general = general_match.group(1)
                game_name = re.search(r"(?im)^\s*gameName\s*=\s*(.*?)\s*$", general)
                if not game_name or game_name.group(1).strip().strip('"').casefold() != "skyrim special edition":
                    continue

                game_path_match = re.search(r"(?im)^([ \t]*gamePath[ \t]*=[ \t]*)(.*?)(\r?)$", general)
                if not game_path_match:
                    skipped.append(str(ini))
                    continue
                # QSettings stores this setting as a QByteArray. Keep that
                # representation and escape backslashes exactly as Qt INI does.
                encoded = "@ByteArray(" + windows_game_path.replace("\\", "\\\\") + ")"
                if game_path_match.group(2).strip() == encoded:
                    unchanged.append(str(ini))
                    continue
                replacement = (
                    game_path_match.group(1) + encoded + game_path_match.group(3)
                )
                new_general = (
                    general[:game_path_match.start()]
                    + replacement
                    + general[game_path_match.end():]
                )
                new_text = (
                    original[:general_match.start(1)]
                    + new_general
                    + original[general_match.end(1):]
                )

                temporary = ini.with_name(ini.name + ".wyrd.tmp")
                if temporary.exists() or temporary.is_symlink():
                    raise RuntimeError(f"MO2 update staging file already exists: {temporary}")
                try:
                    with temporary.open("x", encoding="utf-8", newline="") as handle:
                        handle.write(new_text)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary, ini)
                finally:
                    temporary.unlink(missing_ok=True)

                verified = ini.read_text(encoding="utf-8-sig")
                verify_general = re.search(
                    r"(?ims)^\[General\][ \t]*\r?\n(.*?)(?=^\[|\Z)",
                    verified,
                )
                verify_path = re.search(
                    r"(?im)^\s*gamePath\s*=\s*(.*?)\s*$",
                    verify_general.group(1) if verify_general else "",
                )
                if not verify_path or verify_path.group(1).strip() != encoded:
                    raise RuntimeError(f"MO2 gamePath verification failed after updating {ini}.")
                updated.append(str(ini))

        return {
            "ok": not skipped,
            "windows_game_path": windows_game_path,
            "updated": updated,
            "unchanged": unchanged,
            "skipped": skipped,
            "message": (
                f"Updated {len(updated)} Skyrim MO2 instance path(s); "
                f"{len(unchanged)} already matched the detected game."
                if not skipped else
                "One or more Skyrim MO2 instance files could not be safely updated."
            ),
        }
    except (OSError, ValueError, RuntimeError) as error:
        return {
            "ok": False, "updated": [], "unchanged": [], "skipped": [],
            "message": str(error),
        }
