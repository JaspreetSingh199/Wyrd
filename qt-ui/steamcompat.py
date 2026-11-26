"""Configure and launch MO2 inside Skyrim's Steam Proton compatdata.

This integration reuses Skyrim's existing AppID prefix. It never resets the
prefix or modifies saves/game files; narrowly scoped drive and game-registry
discovery metadata may be provisioned for MO2 compatibility.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from skyrim_detection import steam_libraries
from wyrd_mo2 import (
    inspect_proton_build, _runtime_environment, inspect_mo2_instances,
    _steam_root_for_game, managed_paths, _resolve_windows_path,
)

APP_ID = "489830"


def steam_compat_prefix(steam_root: Path) -> Path:
    return Path(steam_root).expanduser() / "steamapps/compatdata" / APP_ID


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


def configure_skyrim_se_registry(prefix_root: Path, game_path: Path,
                                 steam_root: Path, proton_path: Path,
                                 windows_game_path: str | None = None) -> dict:
    """Register the verified Skyrim SE install in this game's existing Proton prefix.

    MO2 and Bethesda tools query this Windows registry key. SteamTinkerLaunch
    documents this registry provisioning as part of its MO2 game discovery.
    Only the Skyrim SE Installed Path value is imported; no instance/config
    directories, Steam manifests, saves, or game files are touched.
    """
    try:
        prefix = _reject_symlinked_path_components(Path(prefix_root), "Steam compatdata prefix")
        game = _reject_symlinked_path_components(Path(game_path), "Skyrim game")
        steam = _reject_symlinked_path_components(Path(steam_root), "Steam client root")
        proton = Path(proton_path).expanduser()
        resolved_prefix = prefix.resolve(strict=True)
        resolved_steam = steam.resolve(strict=True)
        if resolved_prefix != steam_compat_prefix(resolved_steam).resolve(strict=True):
            raise RuntimeError("Registry provisioning refused: prefix is not Skyrim AppID 489830 compatdata.")
        resolved_game = game.resolve(strict=True)
        if not (resolved_game / "SkyrimSE.exe").is_file():
            raise RuntimeError("SkyrimSE.exe is missing from the detected game directory.")
        if proton.is_symlink() or not (proton / "proton").is_file():
            raise RuntimeError("Selected Proton runtime is missing or unsafe.")

        if not windows_game_path:
            raise RuntimeError("A dynamically discovered Proton game path is required; no drive letter is assumed.")
        mapped = _resolve_windows_path(windows_game_path, resolved_prefix)
        if mapped != resolved_game:
            raise RuntimeError("Generated Wine game path does not resolve to the detected Skyrim directory.")
        windows_path = windows_game_path
        registry_path = windows_path.rstrip("\\").replace("\\", "\\\\") + "\\\\"
        reg_text = (
            "Windows Registry Editor Version 5.00\n\n"
            "[HKEY_LOCAL_MACHINE\\SOFTWARE\\WOW6432Node\\Bethesda Softworks\\Skyrim Special Edition]\n"
            f'"Installed Path"="{registry_path}"\n'
        )
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".reg", delete=False) as handle:
            handle.write(reg_text)
            reg_path = Path(handle.name)
        try:
            env = _runtime_environment(proton, resolved_steam, resolved_prefix)
            env.update({"SteamAppId": APP_ID, "SteamGameId": APP_ID,
                        "STEAM_COMPAT_APP_ID": APP_ID,
                        "STEAM_COMPAT_DATA_PATH": str(resolved_prefix)})
            result = subprocess.run(
                [str(proton / "proton"), "run", "regedit", "/S", str(reg_path)],
                cwd=str(resolved_steam), env=env, capture_output=True,
                text=True, timeout=120, check=False,
            )
            if result.returncode != 0:
                details = (result.stdout + "\\n" + result.stderr).strip()
                raise RuntimeError(
                    "Proton regedit failed to import Skyrim's registry path."
                    + (" " + details[-2000:] if details else "")
                )
            query = subprocess.run(
                [str(proton / "proton"), "run", "reg", "query",
                 r"HKLM\SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition",
                 "/v", "Installed Path"],
                cwd=str(resolved_steam), env=env, capture_output=True,
                text=True, timeout=120, check=False,
            )
            if query.returncode != 0:
                details = (query.stdout + "\\n" + query.stderr).strip()
                raise RuntimeError(
                    "Skyrim registry import could not be verified by reading Installed Path."
                    + (" " + details[-2000:] if details else "")
                )
            expected_registry_value = windows_path.rstrip("\\") + "\\"
            if expected_registry_value.casefold() not in query.stdout.casefold():
                raise RuntimeError(
                    "Skyrim registry read-back did not match the detected game path. "
                    f"Expected {windows_path}; registry returned: {query.stdout.strip()[-1000:]}"
                )
        finally:
            reg_path.unlink(missing_ok=True)
        if result.returncode != 0:
            details = (result.stdout + "\\n" + result.stderr).strip()
            raise RuntimeError("Proton regedit failed to import Skyrim's registry path."
                               + (" " + details[-2000:] if details else ""))
        return {"ok": True, "registry_key": r"HKLM\\SOFTWARE\\WOW6432Node\\Bethesda Softworks\\Skyrim Special Edition",
                "value": "Installed Path", "windows_path": windows_path,
                "message": "Registered Skyrim SE's dynamically verified Wine installation path in the existing Steam prefix."}
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        return {"ok": False, "message": str(error)}

def install_mo2_into_skyrim_prefix(proton_path: Path, game_path: Path,
                                      home: Path | None = None, callback=None) -> dict:
    """Download and run the official MO2 Windows installer in Skyrim's existing prefix.

    The installer is interactive so the user can review its options. Wyrd supplies
    the intended C:\\Modding\\MO2 target and never creates or resets a prefix.
    """
    import urllib.request

    home = (home or Path.home()).expanduser()
    proton = Path(proton_path).expanduser().resolve()
    game = Path(game_path).expanduser().resolve()
    validation = inspect_proton_build(proton)
    if not validation.get("ok"):
        raise RuntimeError(validation.get("message", "Selected Proton build is invalid."))
    if not game.is_dir():
        raise RuntimeError(f"Skyrim game directory is missing: {game}")
    steam_root = _steam_root_for_game(game, home)
    prefix = steam_compat_prefix(steam_root)
    if prefix.is_symlink() or not prefix.is_dir():
        raise RuntimeError("Skyrim's Steam compatdata prefix is missing or unsafe. Launch Skyrim through Steam once first.")
    try:
        prefix.resolve(strict=True).relative_to(steam_root.resolve(strict=True))
    except (OSError, ValueError, RuntimeError):
        raise RuntimeError("Skyrim compatdata prefix is outside the detected Steam root.")
    pfx, wine_drive = prefix / "pfx", prefix / "pfx/drive_c"
    if pfx.is_symlink() or wine_drive.is_symlink() or not wine_drive.is_dir():
        raise RuntimeError("Skyrim's Proton prefix is missing, uninitialized or unsafe.")
    install = wine_drive / "Modding/MO2"
    if install.is_symlink() or install.parent.is_symlink() or install.parent.parent.is_symlink():
        raise RuntimeError("Refusing to install MO2 through a symlinked path inside Skyrim's prefix.")
    marker, executable = install / ".wyrd-managed.json", install / "ModOrganizer.exe"
    if install.exists():
        if executable.is_file() and not executable.is_symlink():
            return {"ok": True, "already_installed": True, "executable": str(executable),
                    "prefix": str(prefix), "install": str(install),
                    "message": "An MO2 executable already exists at the intended install location."}
        raise RuntimeError(f"MO2 target already exists and will not be overwritten: {install}")

    version = "2.5.2"
    installer_name = f"Mod.Organizer-{version}.exe"
    url = f"https://github.com/ModOrganizer2/modorganizer/releases/download/v{version}/{installer_name}"
    downloads = managed_paths(home)["root"] / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    installer = downloads / installer_name
    if callback:
        callback(5, f"Downloading official Mod Organizer 2 {version} installer…")
    if not installer.is_file() or installer.is_symlink() or installer.stat().st_size < 50_000_000:
        partial = downloads / (installer_name + ".part")
        partial.unlink(missing_ok=True)
        request = urllib.request.Request(url, headers={"User-Agent": "Wyrd MO2 installer"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
                total = int(response.headers.get("Content-Length", "0") or 0)
                downloaded = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    downloaded += len(chunk)
                    if callback and total:
                        callback(min(35, 5 + int(30 * downloaded / total)),
                                 f"Downloading MO2 installer… {downloaded // (1024 * 1024)} MB")
            if partial.stat().st_size < 50_000_000:
                raise RuntimeError("Downloaded MO2 installer is unexpectedly small.")
            os.replace(partial, installer)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
    if callback:
        callback(40, "Launching official MO2 installer in Skyrim's Proton prefix…")
    env = _runtime_environment(proton, steam_root, prefix)
    env["STEAM_COMPAT_APP_ID"] = APP_ID
    env["SteamAppId"] = APP_ID
    env["SteamGameId"] = APP_ID
    command = [str(proton / "proton"), "run", str(installer),
               r"/DIR=C:\\Modding\\MO2"]
    process = subprocess.Popen(command, cwd=str(steam_root), env=env,
                               stdin=subprocess.DEVNULL, start_new_session=True)
    if callback:
        callback(45, "MO2 installer opened. Complete its wizard; Wyrd will verify the installation when it closes.")
    try:
        return_code = process.wait(timeout=1800)
    except subprocess.TimeoutExpired:
        process.terminate()
        raise RuntimeError("MO2 installer did not finish within 30 minutes.")
    if return_code != 0:
        raise RuntimeError(f"MO2 installer exited with status {return_code}.")
    if not executable.is_file() or executable.is_symlink():
        raise RuntimeError(
            "MO2 installer closed, but ModOrganizer.exe was not found at "
            f"{install}. Check the installer's destination and retry."
        )
    marker.write_text(json.dumps({
        "managed_by": "Wyrd", "version": version,
        "installed": datetime.now(timezone.utc).isoformat(),
        "proton": str(proton), "steam_root": str(steam_root),
        "game_app_id": APP_ID, "game_path": str(game),
        "prefix": str(prefix), "install": str(install),
        "installation_mode": "official-windows-installer",
    }, indent=2), encoding="utf-8")
    if callback:
        callback(100, "Official MO2 installer completed; ModOrganizer.exe verified.")
    return {"ok": True, "already_installed": False, "executable": str(executable),
            "version": version, "proton": str(proton), "prefix": str(prefix),
            "install": str(install), "game_path": str(game),
            "message": f"MO2 {version} installed and verified in Skyrim's existing Steam prefix."}


def inspect_skyrim_prefix_mo2_installation(game_path: Path, home: Path | None = None) -> dict:
    """Safely inspect the Wyrd-owned MO2 directory inside Skyrim's Steam prefix.

    Steam compatdata remains Steam-owned. Only the dedicated MO2 directory may
    carry Wyrd's ownership marker.
    """
    home = (home or Path.home()).expanduser()
    game = Path(game_path).expanduser()
    if not game.is_dir():
        return {"installed": False, "message": "Skyrim game directory is missing."}
    try:
        steam_root = _steam_root_for_game(game, home)
        prefix = steam_compat_prefix(steam_root)
        if prefix.is_symlink() or not prefix.is_dir():
            return {"installed": False, "message": "Skyrim's Steam compatdata prefix is missing or unsafe."}
        resolved_prefix = prefix.resolve(strict=True)
        resolved_steam = steam_root.resolve(strict=True)
        resolved_prefix.relative_to(resolved_steam)
        install = prefix / "pfx/drive_c/Modding/MO2"
        proton_prefix = prefix / "pfx"
        wine_drive = proton_prefix / "drive_c"
        modding_root = wine_drive / "Modding"
        if (proton_prefix.is_symlink() or wine_drive.is_symlink()
                or modding_root.is_symlink()):
            return {"installed": False, "prefix": str(prefix), "install": str(install),
                    "message": "Skyrim's Proton prefix path contains a symlink and was not inspected."}
        if not wine_drive.is_dir():
            return {"installed": False, "prefix": str(prefix), "install": str(install),
                    "message": "Skyrim's Proton prefix is not initialized. Launch Skyrim through Steam once first."}
        if install.is_symlink() or install.parent.is_symlink() or install.parent.parent.is_symlink():
            return {"installed": False, "prefix": str(prefix), "install": str(install),
                    "message": "MO2 install path contains a symlink and was not inspected."}
        marker = install / ".wyrd-managed.json"
        executable = install / "ModOrganizer.exe"
        if marker.is_symlink():
            return {"installed": False, "prefix": str(prefix), "install": str(install),
                    "message": "MO2 ownership marker is a symlink and was not inspected."}
        metadata = {}
        try:
            metadata = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        owned = (
            executable.is_file() and not executable.is_symlink()
            and metadata.get("managed_by") == "Wyrd"
            and metadata.get("game_app_id") == APP_ID
            and metadata.get("installation_mode") in {"skyrim-steam-prefix", "official-windows-installer"}
            and Path(metadata.get("install", "")).expanduser() == install
        )
        return {
            "installed": bool(owned), "executable": str(executable) if owned else None,
            "metadata": metadata if owned else {}, "prefix": str(prefix),
            "install": str(install), "steam_root": str(steam_root),
            "message": "Wyrd-managed MO2 is installed in Skyrim's Steam prefix." if owned
                       else "No verified Wyrd-managed MO2 installation was found in Skyrim's prefix. Existing files were left untouched.",
        }
    except (OSError, ValueError, RuntimeError) as error:
        return {"installed": False, "message": f"Could not safely inspect Skyrim's MO2 prefix: {error}"}


def inspect_skyrim_prefix_mo2_instances(game_path: Path, home: Path | None = None) -> dict:
    """Inventory MO2 instances in Skyrim's validated AppID prefix."""
    state = inspect_skyrim_prefix_mo2_installation(game_path, home)
    if not state.get("installed"):
        return {"ok": False, "instances": [], "skyrim_instances": [],
                "message": state.get("message", "Wyrd-managed MO2 is not installed.")}
    return inspect_mo2_instances(
        Path(state["prefix"]), Path(state["install"]),
        detected_game_path=Path(game_path).expanduser(),
    )



def inspect_external_skyrim_prefix_mo2_installation(game_path: Path, home: Path | None = None) -> dict:
    """Discover an unowned MO2 installation in Skyrim's prefix without mutation."""
    home = (home or Path.home()).expanduser()
    game = Path(game_path).expanduser()
    if not game.is_dir():
        return {"installed": False, "managed": False,
                "message": "Skyrim game directory is missing."}
    try:
        steam_root = _steam_root_for_game(game, home)
        prefix = steam_compat_prefix(steam_root)
        if prefix.is_symlink() or not prefix.is_dir():
            return {"installed": False, "managed": False,
                    "message": "Skyrim's Steam compatdata prefix is missing or unsafe."}
        resolved_steam = steam_root.resolve(strict=True)
        resolved_prefix = prefix.resolve(strict=True)
        resolved_prefix.relative_to(resolved_steam)
        pfx = prefix / "pfx"
        drive = pfx / "drive_c"
        modding = drive / "Modding"
        install = modding / "MO2"
        for component in (pfx, drive, modding, install):
            if component.is_symlink():
                return {"installed": False, "managed": False, "prefix": str(prefix),
                        "install": str(install), "message": "Existing MO2 path contains a symlink; discovery was refused."}
        if not drive.is_dir():
            return {"installed": False, "managed": False, "prefix": str(prefix),
                    "install": str(install), "message": "Skyrim's Proton prefix is not initialized."}
        executable = install / "ModOrganizer.exe"
        marker = install / ".wyrd-managed.json"
        if marker.is_symlink():
            return {"installed": False, "managed": False, "prefix": str(prefix),
                    "install": str(install), "message": "MO2 ownership marker is a symlink; discovery was refused."}
        if marker.exists():
            owned = inspect_skyrim_prefix_mo2_installation(game, home)
            if owned.get("installed"):
                return {**owned, "managed": True}
            return {"installed": False, "managed": False, "prefix": str(prefix),
                    "install": str(install), "message": "An ownership marker exists but cannot be verified; external adoption was refused."}
        if not executable.is_file() or executable.is_symlink():
            # An existing directory still occupies the installer target. Report
            # the path so the UI can block installation without adopting it.
            occupied = install.exists()
            return {"installed": False, "managed": False, "prefix": str(prefix),
                    "install": str(install) if occupied else None,
                    "message": ("An existing MO2 target directory is present but its executable is missing; it was left untouched."
                                if occupied else "No external MO2 executable was found at the conventional Skyrim-prefix location.")}
        return {
            "installed": True, "managed": False, "executable": str(executable),
            "prefix": str(prefix), "install": str(install), "steam_root": str(steam_root),
            "message": "Existing externally managed MO2 installation found. It has not been modified.",
        }
    except (OSError, ValueError, RuntimeError) as error:
        return {"installed": False, "managed": False,
                "message": f"Could not safely inspect existing MO2 installation: {error}"}


def inspect_any_skyrim_prefix_mo2_instances(game_path: Path, home: Path | None = None) -> dict:
    """Discover MO2 instances independently of the MO2 executable installation.

    The AppID 489830 prefix is the discovery boundary. MO2 instances are
    user data and can outlive, or live separately from, the executable Wyrd
    recognizes at its conventional install path. This function therefore
    validates the Steam prefix first, then inventories known MO2 instance
    roots even when no MO2 installation is registered at that path.
    """
    game = Path(game_path).expanduser()
    if not game.is_dir() or game.is_symlink():
        return {"ok": False, "managed": False, "instances": [],
                "skyrim_instances": [],
                "message": "Skyrim game directory is missing or unsafe; MO2 discovery was not run."}
    try:
        steam_root = _steam_root_for_game(game, (home or Path.home()).expanduser())
        prefix = steam_compat_prefix(steam_root)
        if prefix.is_symlink() or not prefix.is_dir():
            return {"ok": False, "managed": False, "instances": [],
                    "skyrim_instances": [],
                    "message": "Skyrim's Steam compatdata prefix is missing or unsafe; MO2 instances cannot be inspected."}
        prefix.resolve(strict=True).relative_to(steam_root.resolve(strict=True))
        users_root = prefix / "pfx/drive_c/users"
        if users_root.is_symlink() or not users_root.is_dir():
            return {"ok": False, "managed": False, "instances": [],
                    "skyrim_instances": [],
                    "message": "Skyrim's Proton Wine-user directory is missing or unsafe; MO2 instances cannot be inspected."}
        managed = inspect_skyrim_prefix_mo2_installation(game, home)
        installation = (
            managed if managed.get("installed")
            else inspect_external_skyrim_prefix_mo2_installation(game, home)
        )
        install_root = (
            Path(installation["install"])
            if installation.get("installed") and installation.get("install")
            else None
        )
        inventory = inspect_mo2_instances(
            prefix, install_root, require_managed=False,
            detected_game_path=game,
        )
        inventory["managed"] = bool(installation.get("managed", False))
        inventory["installation"] = installation
        if not installation.get("installed"):
            inventory["installation_message"] = installation.get(
                "message", "No conventional MO2 executable installation was identified."
            )
        if not inventory.get("instances"):
            inventory["message"] = (
                "No MO2 instances were found in the known Skyrim-prefix instance locations. "
                "MO2 executable discovery is independent and is not required for this scan."
            )
        return inventory
    except (OSError, ValueError, RuntimeError) as error:
        return {"ok": False, "managed": False, "instances": [],
                "skyrim_instances": [],
                "message": f"Could not safely inspect Skyrim's MO2 prefix: {error}"}


def launch_external_mo2_in_skyrim_prefix(proton_path: Path, game_path: Path,
                                          home: Path | None = None,
                                          instance_name: str | None = None,
                                          xaudio2_compat: bool = False) -> dict:
    """Launch an existing unowned MO2 installation without adopting or modifying it."""
    home = (home or Path.home()).expanduser()
    proton = Path(proton_path).expanduser()
    game = Path(game_path).expanduser()
    validation = inspect_proton_build(proton)
    if not validation.get("ok"):
        return {"ok": False, "message": validation.get("message", "Selected Proton build is invalid.")}
    state = inspect_external_skyrim_prefix_mo2_installation(game, home)
    if not state.get("installed") or state.get("managed"):
        return {"ok": False, "message": state.get("message", "External MO2 installation is unavailable.")}
    prefix, install = Path(state["prefix"]), Path(state["install"])
    executable = Path(state["executable"])
    if (prefix.is_symlink() or install.is_symlink() or executable.is_symlink()
            or not executable.is_file() or not install.is_dir()):
        return {"ok": False, "message": "External MO2 paths changed or became unsafe after discovery; launch was refused."}
    try:
        executable.resolve(strict=True).relative_to(install.resolve(strict=True))
    except (OSError, ValueError, RuntimeError):
        return {"ok": False, "message": "External MO2 executable resolves outside its installation directory; launch was refused."}
    inventory = inspect_mo2_instances(
        prefix, install, require_managed=False, detected_game_path=game,
    )
    if not inventory.get("ok"):
        return {"ok": False, "message": inventory.get("message", "Could not inspect external MO2 instances.")}
    candidates = inventory.get("skyrim_instances", [])
    if instance_name is None:
        if len(candidates) == 1:
            instance_name = candidates[0]["name"]
        elif candidates:
            return {"ok": False, "needs_instance_selection": True,
                    "instances": [item["name"] for item in candidates],
                    "message": "Choose an MO2 Skyrim instance before launching."}
        else:
            return {"ok": False, "message": "No Skyrim Special Edition MO2 instance was found."}
    if instance_name not in [item["name"] for item in candidates]:
        return {"ok": False, "message": "The selected Skyrim MO2 instance is not present in the external installation."}
    steam_root = Path(state["steam_root"])
    env = _runtime_environment(proton, steam_root, prefix)
    env.update({
        "SteamAppId": APP_ID, "SteamGameId": APP_ID,
        "STEAM_COMPAT_APP_ID": APP_ID,
        "STEAM_COMPAT_DATA_PATH": str(prefix.resolve()),
    })
    if xaudio2_compat:
        current = env.get("WINEDLLOVERRIDES", "")
        overrides = [item for item in current.split(";")
                     if item and not item.lower().startswith("xaudio2_7=")]
        overrides.append("xaudio2_7=n,b")
        env["WINEDLLOVERRIDES"] = ";".join(overrides)
    try:
        process = subprocess.Popen(
            [str(proton / "proton"), "run", str(executable),
             "--instance", instance_name],
            cwd=str(install), env=env, start_new_session=True,
        )
    except OSError as error:
        return {"ok": False, "message": f"Could not launch existing MO2: {error}"}
    return {"ok": True, "pid": process.pid, "managed": False,
            "prefix": str(prefix.resolve()), "instance": instance_name,
            "message": f"Started externally managed MO2 instance '{instance_name}' through Skyrim's Steam prefix. Existing MO2 files were not changed by Wyrd."}


from pathlib import Path

def launch_mo2_in_skyrim_prefix(proton_path: Path, game_path: Path,
                                home: Path | None = None,
                                instance_name: str | None = None,
                                xaudio2_compat: bool = False) -> dict:
    """Launch verified MO2 using Skyrim's Steam AppID prefix and selected Proton."""
    home = (home or Path.home()).expanduser()
    proton = Path(proton_path).expanduser()
    game = Path(game_path).expanduser()
    validation = inspect_proton_build(proton)
    if not validation.get("ok"):
        return {"ok": False, "message": validation.get("message", "Selected Proton build is invalid.")}
    state = inspect_skyrim_prefix_mo2_installation(game, home)
    if not state.get("installed"):
        return {"ok": False, "message": state.get("message", "Wyrd-managed MO2 is not installed.")}
    steam_root = Path(state["steam_root"])
    prefix = Path(state["prefix"])
    install = Path(state["install"])
    executable = Path(state["executable"])
    inventory = inspect_mo2_instances(prefix, install, detected_game_path=game)
    if not inventory.get("ok"):
        return {"ok": False, "message": inventory.get("message", "Could not inspect MO2 instances in Skyrim's prefix.")}
    candidates = inventory.get("skyrim_instances", [])
    if instance_name is None:
        if len(candidates) == 1:
            instance_name = candidates[0]["name"]
        elif candidates:
            return {"ok": False, "needs_instance_selection": True,
                    "instances": [item["name"] for item in candidates],
                    "message": "Choose an MO2 Skyrim instance before launching."}
        else:
            return {"ok": False, "message": "No Skyrim Special Edition MO2 instance was found. Create one in MO2 first."}
    if instance_name not in [item["name"] for item in candidates]:
        return {"ok": False, "message": "The selected Skyrim MO2 instance is not verified in Skyrim's prefix."}
    env = _runtime_environment(proton, steam_root, prefix)
    env.update({
        "SteamAppId": APP_ID, "SteamGameId": APP_ID,
        "STEAM_COMPAT_APP_ID": APP_ID,
        "STEAM_COMPAT_DATA_PATH": str(prefix.resolve()),
    })
    if xaudio2_compat:
        current = env.get("WINEDLLOVERRIDES", "")
        overrides = [item for item in current.split(";")
                     if item and not item.lower().startswith("xaudio2_7=")]
        overrides.append("xaudio2_7=n,b")
        env["WINEDLLOVERRIDES"] = ";".join(overrides)
    try:
        process = subprocess.Popen(
            [str(proton / "proton"), "run", str(executable), "--instance", instance_name],
            cwd=str(install), env=env, start_new_session=True,
        )
    except OSError as error:
        return {"ok": False, "message": f"Could not start MO2 in Skyrim's Proton prefix: {error}"}
    return {
        "ok": True, "pid": process.pid, "prefix": str(prefix.resolve()),
        "instance": instance_name,
        "message": f"Started the Proton wrapper for MO2 instance '{instance_name}' in Skyrim's Steam prefix. MO2 process startup is not yet independently confirmed.",
    }



def launch_skyrim_via_mo2_in_skyrim_prefix(
    proton_path: Path, game_path: Path, instance_name: str,
    executable_title: str, expected_binary: Path | str,
    *, prefix_path: Path | None = None, install_root: Path | None = None,
    steam_root_path: Path | None = None, xaudio2_compat: bool = False,
    home: Path | None = None,
) -> dict:
    """Launch a verified MO2 Skyrim target using the established Steam-prefix context.

    This is the single Skyrim launch adapter for registered environments. It
    deliberately builds the same Steam AppID environment used by
    launch_mo2_in_skyrim_prefix(), including SteamAppId, SteamGameId,
    STEAM_COMPAT_APP_ID and STEAM_COMPAT_DATA_PATH. It never creates or resets
    the Steam prefix.
    """
    from wyrd_mo2 import inspect_skyrim_launch_targets
    import uuid

    home = (home or Path.home()).expanduser()
    game = Path(game_path).expanduser()
    proton = Path(proton_path).expanduser()
    if game.is_symlink() or not game.is_dir():
        return {"ok": False, "message": "Skyrim game directory is missing or unsafe."}
    if proton.is_symlink() or not proton.is_dir():
        return {"ok": False, "message": "Selected Proton runtime is missing or unsafe."}
    validation = inspect_proton_build(proton)
    if not validation.get("ok"):
        return {"ok": False, "message": validation.get("message", "Selected Proton build is invalid.")}

    try:
        steam_root = Path(steam_root_path).expanduser() if steam_root_path else _steam_root_for_game(game, home)
        expected_prefix = steam_compat_prefix(steam_root)
        prefix = Path(prefix_path).expanduser() if prefix_path else expected_prefix
        if prefix.is_symlink() or not prefix.is_dir():
            return {"ok": False, "message": "Skyrim's existing Steam compatdata prefix is missing or unsafe."}
        if prefix.resolve(strict=True) != expected_prefix.resolve(strict=True):
            return {"ok": False, "message": "Registered prefix does not match Skyrim's existing Steam AppID 489830 prefix."}
        if not (prefix / "pfx/drive_c/users").is_dir():
            return {"ok": False, "message": "Skyrim's Steam Proton prefix is not initialized."}
        install = Path(install_root).expanduser() if install_root else prefix / "pfx/drive_c/Modding/MO2"
        executable = install / "ModOrganizer.exe"
        if install.is_symlink() or not install.is_dir() or executable.is_symlink() or not executable.is_file():
            return {"ok": False, "message": "Registered MO2 installation is missing or unsafe."}
        executable.resolve(strict=True).relative_to(install.resolve(strict=True))
        prefix.resolve(strict=True).relative_to(steam_root.resolve(strict=True))
    except (OSError, ValueError, RuntimeError) as error:
        return {"ok": False, "message": f"Steam compatibility paths could not be verified: {error}"}

    targets = inspect_skyrim_launch_targets(
        prefix, game, instance_name=instance_name, install_root=install,
        require_managed=False,
    )
    if not targets.get("ok"):
        return {"ok": False, "message": targets.get("message", "Could not inspect the selected MO2 instance.")}
    wanted = Path(expected_binary).expanduser()
    matches = [
        item for item in targets.get("targets", [])
        if item.get("title") == executable_title
        and Path(item.get("binary", "")).resolve() == wanted.resolve()
    ]
    if len(matches) != 1 or wanted.is_symlink() or not wanted.is_file():
        return {"ok": False, "message": "Selected Skyrim executable is no longer uniquely verified in this MO2 instance."}

    env = _runtime_environment(proton, steam_root, prefix)
    env.update({
        "SteamAppId": APP_ID, "SteamGameId": APP_ID,
        "STEAM_COMPAT_APP_ID": APP_ID,
        "STEAM_COMPAT_DATA_PATH": str(prefix.resolve()),
    })
    if xaudio2_compat:
        current = env.get("WINEDLLOVERRIDES", "")
        overrides = [item for item in current.split(";")
                     if item and not item.lower().startswith("xaudio2_7=")]
        overrides.append("xaudio2_7=n,b")
        env["WINEDLLOVERRIDES"] = ";".join(overrides)

    launch_token = uuid.uuid4().hex
    env["WYRD_LAUNCHED"] = "1"
    env["WYRD_LAUNCH_ID"] = launch_token
    command = [
        str(proton / "proton"), "run", str(executable),
        "--instance", instance_name, "run", "--executable", executable_title,
    ]
    diagnostics = managed_paths(home)["root"] / "logs" / "skyrim-launch.log"
    try:
        diagnostics.parent.mkdir(parents=True, exist_ok=True)
        with diagnostics.open("a", encoding="utf-8") as log:
            log.write("\n=== Wyrd SteamCompat Skyrim launch ===\n")
            log.write(f"instance: {instance_name}\nexecutable: {executable_title}\n")
            log.write(f"binary: {wanted}\naudio_mode: {'xaudio2_compat' if xaudio2_compat else 'default'}\n")
            log.write("command: " + json.dumps(command) + "\n")
            log.write("SteamAppId: 489830\n")
            log.flush()
            process = subprocess.Popen(
                command, cwd=str(install), env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            log.write(f"wrapper_pid: {process.pid}\n")
            log.flush()
    except OSError as error:
        return {"ok": False, "message": f"Could not start MO2 through SteamCompat: {error}"}
    return {
        "ok": True, "pid": process.pid, "prefix": str(prefix.resolve()),
        "instance": instance_name, "launch_token": launch_token,
        "log_path": str(diagnostics),
        "message": f"Started '{executable_title}' through MO2 instance '{instance_name}' using Skyrim's SteamCompat environment.",
    }


def reset_wyrd_mo2(home: Path | None = None, game_path: Path | None = None) -> dict:
    """Remove only the verified Wyrd-installed MO2 program directory.

    Global MO2 instances and all user data are deliberately left in place.
    Wyrd has no instance-registration or storage-ownership system.
    """
    import json
    import shutil

    home = (home or Path.home()).expanduser()
    game = Path(game_path).expanduser() if game_path is not None else _game_path_for_wyrd_install(home)
    state = inspect_skyrim_prefix_mo2_installation(game, home)
    if not state.get("installed") or not state.get("managed"):
        return {
            "ok": False,
            "message": state.get("message", "No verified Wyrd-installed MO2 program was found."),
            "removed": [],
        }

    install = Path(state["install"])
    prefix = Path(state["prefix"])
    marker = install / ".wyrd-managed.json"
    executable = install / "ModOrganizer.exe"
    if any(path.is_symlink() for path in (install, prefix, marker, executable)):
        return {"ok": False, "message": "A Wyrd MO2 path is symlinked; removal refused.", "removed": []}
    try:
        resolved_prefix = prefix.resolve(strict=True)
        resolved_install = install.resolve(strict=True)
        resolved_install.relative_to(resolved_prefix)
        metadata = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError, RuntimeError):
        return {
            "ok": False,
            "message": "Wyrd installation ownership or path could not be verified; nothing was removed.",
            "removed": [],
        }
    if (
        not isinstance(metadata, dict)
        or metadata.get("managed_by") != "Wyrd"
        or metadata.get("game_app_id") != APP_ID
        or metadata.get("installation_mode") not in {"skyrim-steam-prefix", "official-windows-installer"}
        or Path(metadata.get("install", "")).expanduser() != install
        or not executable.is_file()
    ):
        return {
            "ok": False,
            "message": "MO2 program directory is not positively identified as Wyrd-installed; nothing was removed.",
            "removed": [],
        }

    # Do not remove global/portable instances or any profiles, mods, downloads,
    # saves, custom storage, or Steam prefix data. The program directory is the
    # only deletion target.
    if install.is_symlink() or not install.is_dir():
        return {"ok": False, "message": "MO2 program directory changed or is unsafe; removal refused.", "removed": []}
    try:
        current = install.resolve(strict=True)
        current.relative_to(resolved_prefix)
    except (OSError, ValueError, RuntimeError):
        return {"ok": False, "message": "MO2 program directory escaped the verified prefix; removal refused.", "removed": []}
    if prefix.is_symlink() or prefix.resolve(strict=True) != resolved_prefix:
        return {"ok": False, "message": "Steam's Skyrim prefix changed during validation; removal refused.", "removed": []}

    try:
        shutil.rmtree(install)
    except OSError as error:
        return {"ok": False, "message": f"MO2 program removal failed: {error}", "removed": []}

    settings_path = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")).expanduser() / "wyrd" / "settings.json"
    try:
        if settings_path.exists() and not settings_path.is_symlink() and settings_path.is_file():
            payload = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload.pop("selected_mo2_instance_id", None)
                temporary = settings_path.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                os.replace(temporary, settings_path)
    except (OSError, ValueError, TypeError):
        # Preferences are not part of the ownership boundary; stale selection
        # data is harmless because discovery is always rebuilt from disk.
        pass

    return {
        "ok": True,
        "removed": [{"path": str(install), "kind": "Wyrd-installed MO2 program"}],
        "message": "The verified Wyrd-installed MO2 program directory was removed. Existing MO2 instances and their profiles, mods, downloads, custom storage, Skyrim's Steam compatdata/489830 prefix, game files and saves were preserved.",
    }

def _game_path_for_wyrd_install(home: Path) -> Path:
    """Recover the recorded Skyrim path from the Wyrd settings file."""
    settings = managed_paths(home)["settings"]
    try:
        payload = json.loads(settings.read_text(encoding="utf-8"))
        game = Path(payload.get("game_path", "")).expanduser()
        return game if game.is_dir() else Path("/__wyrd_missing_game__")
    except (OSError, ValueError, TypeError):
        return Path("/__wyrd_missing_game__")
