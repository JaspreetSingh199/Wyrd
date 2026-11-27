"""Read-only Proton drive mapping discovery and verification."""
from __future__ import annotations

import re
from pathlib import Path

from mo2_registry import _reject_symlinked_path_components
from mo2_instances import _resolve_windows_path
from skyrim_detection import steam_libraries
from steam_prefix import APP_ID, steam_compat_prefix


def discover_wine_game_path(prefix_root: Path, game_path: Path, steam_root: Path) -> dict:
    """Derive the Windows path from the detected Linux game and existing Wine drives.

    No drive letter is assumed and no dosdevices entry is created or changed.
    A discovered filesystem-root mapping is preferred; otherwise the most specific mapping wins.
    """
    try:
        prefix = _reject_symlinked_path_components(Path(prefix_root), "Steam compatdata prefix")
        game = _reject_symlinked_path_components(Path(game_path), "Skyrim game")
        steam = _reject_symlinked_path_components(Path(steam_root), "Steam client root")
        resolved_prefix = prefix.resolve(strict=True)
        resolved_steam = steam.resolve(strict=True)
        if resolved_prefix != steam_compat_prefix(resolved_steam).resolve(strict=True):
            raise RuntimeError("The selected prefix is not this game's Steam AppID 489830 compatdata prefix.")

        resolved_game = game.resolve(strict=True)
        if not resolved_game.is_dir() or not (resolved_game / "SkyrimSE.exe").is_file():
            raise RuntimeError("The detected Skyrim directory is missing SkyrimSE.exe.")
        library_steamapps = resolved_game.parent.parent
        if library_steamapps.name.casefold() != "steamapps":
            raise RuntimeError("The detected Skyrim directory is not inside a Steam library.")
        registered = [item.resolve(strict=True) for item in steam_libraries(resolved_steam)]
        if library_steamapps.resolve(strict=True) not in registered:
            raise RuntimeError("The detected Skyrim directory is not in a library registered by this Steam client.")
        manifest = library_steamapps / f"appmanifest_{APP_ID}.acf"
        if manifest.is_symlink() or not manifest.is_file():
            raise RuntimeError("The detected Steam library has no safe Skyrim AppID manifest.")
        manifest_text = manifest.read_text(encoding="utf-8", errors="replace")
        app_id_match = re.search(r'"appid"\s+"([^"]+)"', manifest_text)
        install_dir_match = re.search(r'"installdir"\s+"([^"]+)"', manifest_text)
        if (not app_id_match or app_id_match.group(1) != APP_ID
                or not install_dir_match or install_dir_match.group(1) != resolved_game.name):
            raise RuntimeError("The Steam AppID manifest does not identify the detected Skyrim directory.")

        dosdevices = resolved_prefix / "pfx/dosdevices"
        if dosdevices.is_symlink() or not dosdevices.is_dir():
            raise RuntimeError("The active Proton dosdevices directory is missing or unsafe.")
        candidates = []
        for entry in dosdevices.iterdir():
            match = re.fullmatch(r"([a-z]):", entry.name.casefold())
            if not match or not entry.is_symlink():
                continue
            try:
                drive_root = entry.resolve(strict=True)
                if not drive_root.is_dir():
                    continue
                relative = resolved_game.relative_to(drive_root)
                windows_path = match.group(1).upper() + ":\\" + str(relative).replace("/", "\\")
                round_trip = _resolve_windows_path(windows_path, resolved_prefix)
                if round_trip != resolved_game:
                    continue
                candidates.append((len(drive_root.parts), match.group(1), windows_path, drive_root))
            except (OSError, ValueError, RuntimeError):
                continue
        if not candidates:
            raise RuntimeError("No existing Proton drive mapping can represent the detected Skyrim directory.")
        # Prefer a discovered mapping to the filesystem root when present.
        # Proton's root mapping can represent any detected Steam library and
        # avoids depending on an old/custom S: library alias. The letter itself
        # is still discovered from dosdevices, never assumed.
        root_mappings = [item for item in candidates if item[3] == Path("/")]
        _specificity, letter, windows_path, drive_root = (
            sorted(root_mappings, key=lambda item: item[1])[0]
            if root_mappings else
            max(candidates, key=lambda item: (item[0], item[1]))
        )
        return {
            "ok": True, "drive": letter.upper() + ":",
            "windows_game_path": windows_path,
            "resolved_game_path": str(resolved_game),
            "drive_root": str(drive_root),
            "message": f"Derived {windows_path} from the detected game path using the existing {letter.upper()}: Proton mapping.",
        }
    except (OSError, ValueError, RuntimeError) as error:
        return {
            "ok": False, "drive": None, "windows_game_path": None,
            "resolved_game_path": None, "message": str(error),
        }



def verify_wine_game_path(proton_path: Path, prefix_root: Path,
                          steam_root: Path, windows_game_path: str) -> dict:
    """Verify Skyrim's Wine path using the existing filesystem mapping only.

    The drive mapping has already been derived from Proton's dosdevices and
    round-trip checked by discover_wine_game_path(). Do not spawn cmd.exe just
    to repeat an existence check: that creates a visible Windows console and
    can interfere with the desktop session.
    """
    try:
        prefix = _reject_symlinked_path_components(Path(prefix_root), "Steam compatdata prefix")
        steam = _reject_symlinked_path_components(Path(steam_root), "Steam client root")
        proton = Path(proton_path).expanduser()
        if proton.is_symlink() or not (proton / "proton").is_file():
            raise RuntimeError("Selected Proton runtime is missing or unsafe.")
        resolved_prefix = prefix.resolve(strict=True)
        resolved_steam = steam.resolve(strict=True)
        expected_prefix = steam_compat_prefix(resolved_steam).resolve(strict=True)
        if resolved_prefix != expected_prefix:
            raise RuntimeError("The supplied prefix is not Skyrim AppID 489830 compatdata.")
        mapped = _resolve_windows_path(windows_game_path, resolved_prefix)
        if mapped is None or not (mapped / "SkyrimSE.exe").is_file():
            raise RuntimeError(
                "Generated Wine path does not resolve to SkyrimSE.exe on the Linux filesystem."
            )
        return {
            "ok": True,
            "windows_executable": windows_game_path.rstrip("\\") + "\\SkyrimSE.exe",
            "resolved_executable": str(mapped / "SkyrimSE.exe"),
            "message": f"Filesystem mapping verified {windows_game_path}\\SkyrimSE.exe; no Windows command shell was launched.",
        }
    except (OSError, ValueError, RuntimeError) as error:
        return {"ok": False, "message": str(error)}
