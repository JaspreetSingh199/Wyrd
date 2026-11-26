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
    inspect_wyrd_installation, inspect_proton_build, _runtime_environment,
    inspect_mo2_instances, copy_mo2_instance_to_prefix,
    _steam_root_for_game, managed_paths,
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


def configure_steam_library_drive(prefix_root: Path, game_path: Path, steam_root: Path) -> dict:
    """Make S: inside Skyrim's AppID prefix resolve to the detected game library.

    Only the S: dosdevices symlink is changed. The game, Steam manifests,
    saves, and other prefix entries are never modified.
    """
    try:
        prefix = _reject_symlinked_path_components(Path(prefix_root), "Steam compatdata prefix")
        game = _reject_symlinked_path_components(Path(game_path), "Skyrim game")
        steam = _reject_symlinked_path_components(Path(steam_root), "Steam client root")
        resolved_prefix = prefix.resolve(strict=True)
        resolved_steam = steam.resolve(strict=True)
        expected_prefix = steam_compat_prefix(resolved_steam).resolve(strict=True)
        if resolved_prefix != expected_prefix:
            raise RuntimeError("The selected prefix is not this game's Steam AppID 489830 compatdata prefix.")

        resolved_game = game.resolve(strict=True)
        if (not resolved_game.is_dir()
                or resolved_game.parent.name.casefold() != "common"
                or resolved_game.parent.parent.name.casefold() != "steamapps"):
            raise RuntimeError("The detected Skyrim directory is not inside a Steam library's steamapps/common directory.")
        library_steamapps = resolved_game.parent.parent
        library_root = library_steamapps.parent.resolve(strict=True)
        registered_libraries = [item.resolve(strict=True) for item in steam_libraries(resolved_steam)]
        if library_steamapps.resolve(strict=True) not in registered_libraries:
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

        pfx = resolved_prefix / "pfx"
        drive_c = pfx / "drive_c"
        dosdevices = pfx / "dosdevices"
        drive_link = dosdevices / "s:"
        if (pfx.is_symlink() or drive_c.is_symlink() or dosdevices.is_symlink()
                or not drive_c.is_dir() or not dosdevices.is_dir()):
            raise RuntimeError("The Skyrim Proton dosdevices path is missing or unsafe.")
        if drive_link.exists() and not drive_link.is_symlink():
            raise RuntimeError("The S: entry is not a symlink; it was left untouched.")

        current_target = None
        if drive_link.is_symlink():
            try:
                current_target = drive_link.resolve(strict=True)
            except (OSError, RuntimeError):
                current_target = None
        changed = current_target != library_root
        if changed:
            temporary_link = dosdevices / f".wyrd-s-drive-{os.getpid()}"
            if temporary_link.exists() or temporary_link.is_symlink():
                raise RuntimeError("A previous S: mapping transaction artifact exists; manual inspection is required.")
            temporary_link.symlink_to(library_root, target_is_directory=True)
            try:
                os.replace(temporary_link, drive_link)
            finally:
                temporary_link.unlink(missing_ok=True)

        verified_target = drive_link.resolve(strict=True)
        verified_game = (verified_target / "steamapps" / "common" / resolved_game.name).resolve(strict=True)
        if verified_target != library_root or verified_game != resolved_game or not verified_game.is_dir():
            raise RuntimeError("S: mapping verification failed after configuration.")
        return {
            "ok": True, "changed": changed, "drive": "S:",
            "library_root": str(library_root),
            "windows_game_path": "S:\\steamapps\\common\\" + resolved_game.name,
            "resolved_game_path": str(verified_game),
            "message": ("Corrected" if changed else "Verified") + " MO2 S: Steam-library mapping.",
        }
    except (OSError, ValueError, RuntimeError) as error:
        return {
            "ok": False, "changed": False, "drive": "S:",
            "library_root": None, "windows_game_path": None,
            "resolved_game_path": None, "message": str(error),
        }




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


def configure_skyrim_se_registry(prefix_root: Path, game_path: Path,
                                 steam_root: Path, proton_path: Path) -> dict:
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
        drive = resolved_prefix / "pfx/dosdevices/s:"
        if drive.is_symlink() is False or drive.resolve(strict=True) != resolved_game.parent.parent.parent.resolve(strict=True):
            raise RuntimeError("Verified S: Steam-library mapping is missing or does not point to Skyrim's library.")
        if proton.is_symlink() or not (proton / "proton").is_file():
            raise RuntimeError("Selected Proton runtime is missing or unsafe.")

        library_root = resolved_game.parent.parent.parent
        windows_path = "S:" + str(resolved_game.relative_to(library_root)).replace("/", "\\")
        registry_path = windows_path.replace("\\", "\\\\")
        reg_text = (
            "Windows Registry Editor Version 5.00\n\n"
            "[HKEY_LOCAL_MACHINE\\SOFTWARE\\WOW6432Node\\Bethesda Softworks\\Skyrim Special Edition]\n"
            f'"Installed Path"="{registry_path}\\\n'
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
        finally:
            reg_path.unlink(missing_ok=True)
        if result.returncode != 0:
            details = (result.stdout + "\\n" + result.stderr).strip()
            raise RuntimeError("Proton regedit failed to import Skyrim's registry path."
                               + (" " + details[-2000:] if details else ""))
        return {"ok": True, "registry_key": r"HKLM\\SOFTWARE\\WOW6432Node\\Bethesda Softworks\\Skyrim Special Edition",
                "value": "Installed Path", "windows_path": windows_path,
                "message": "Registered Skyrim SE's verified S: installation path in the existing Steam prefix."}
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
    """Read-only inventory for either Wyrd-managed or external MO2 in Skyrim's prefix."""
    managed = inspect_skyrim_prefix_mo2_installation(game_path, home)
    state = managed if managed.get("installed") else inspect_external_skyrim_prefix_mo2_installation(game_path, home)
    if not state.get("installed"):
        return {"ok": False, "managed": False, "instances": [], "skyrim_instances": [],
                "message": state.get("message", "MO2 installation was not found.")}
    inventory = inspect_mo2_instances(
        Path(state["prefix"]), Path(state["install"]), require_managed=False,
        detected_game_path=Path(game_path).expanduser(),
    )
    inventory["managed"] = bool(state.get("managed", managed.get("installed", False)))
    inventory["installation"] = state
    return inventory


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

def copy_wyrd_instance_to_skyrim_prefix(instance_name: str, home: Path | None = None) -> dict:
    """Copy a selected Skyrim instance from Wyrd's prefix into Steam's prefix."""
    state = inspect_wyrd_installation(home)
    if not state.get("installed"):
        return {"ok": False, "status": "refused", "message": "Wyrd-managed MO2 is not installed."}
    paths = state.get("paths", {})
    source_prefix = Path(paths.get("prefix", "")).expanduser()
    if source_prefix.is_symlink() or not source_prefix.is_dir():
        return {"ok": False, "status": "refused", "message": "Wyrd MO2 prefix is missing or unsafe."}
    source_inventory = inspect_mo2_instances(
        source_prefix, Path(state["executable"]).expanduser().parent,
        detected_game_path=Path(state.get("metadata", {}).get("game_path", "")),
    )
    if not source_inventory.get("ok"):
        return {"ok": False, "status": "refused", "message": source_inventory.get("message", "Could not inspect Wyrd MO2 instances.")}
    source = next((item for item in source_inventory.get("skyrim_instances", [])
                   if item["name"] == instance_name), None)
    if source is None:
        return {"ok": False, "status": "refused", "message": "Selected Skyrim instance was not found in Wyrd's prefix."}
    steam_root = Path(state.get("metadata", {}).get("steam_root", "")).expanduser()
    return copy_mo2_instance_to_prefix(Path(source["path"]), steam_compat_prefix(steam_root))


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
    """Remove verified Wyrd MO2 files while preserving Steam's Skyrim prefix."""
    import json
    import shutil
    from wyrd_mo2_registry import empty_registry, load_registry, save_registry

    home = (home or Path.home()).expanduser()
    game = Path(game_path).expanduser() if game_path is not None else _game_path_for_wyrd_install(home)
    state = inspect_skyrim_prefix_mo2_installation(game, home)
    if not state.get("installed"):
        return {"ok": False, "message": state.get("message", "No verified Wyrd MO2 installation found."), "removed": []}

    install = Path(state["install"])
    prefix = Path(state["prefix"])
    marker = install / ".wyrd-managed.json"
    executable = install / "ModOrganizer.exe"
    if any(path.is_symlink() for path in (install, prefix, marker, executable)):
        return {"ok": False, "message": "A Wyrd MO2 path is symlinked; reset refused.", "removed": []}
    try:
        resolved_prefix = prefix.resolve(strict=True)
        resolved_install = install.resolve(strict=True)
        resolved_install.relative_to(resolved_prefix)
        metadata = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError, RuntimeError):
        return {"ok": False, "message": "Wyrd installation ownership or path could not be verified; nothing was removed.", "removed": []}
    if (
        metadata.get("managed_by") != "Wyrd"
        or metadata.get("game_app_id") != APP_ID
        or metadata.get("installation_mode") not in {"skyrim-steam-prefix", "official-windows-installer"}
        or Path(metadata.get("install", "")).expanduser() != install
        or not executable.is_file()
    ):
        return {"ok": False, "message": "MO2 installation is not positively identified as Wyrd-owned; nothing was removed.", "removed": []}

    config_dir = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")).expanduser() / "wyrd"
    registry_path = config_dir / "mo2-registry.json"
    settings_path = config_dir / "settings.json"
    registry_payload = empty_registry()
    owned_install_ids: set[str] = set()
    owned_instance_dirs: list[Path] = []

    # Metadata is read-only and fully validated before any deletion is attempted.
    if registry_path.exists() or registry_path.is_symlink():
        if registry_path.is_symlink() or not registry_path.is_file():
            return {"ok": False, "message": "Wyrd registry path is unsafe; reset refused.", "removed": []}
        try:
            registry_payload = load_registry(registry_path)
        except Exception as error:
            return {"ok": False, "message": f"Wyrd registry could not be validated; reset refused: {error}", "removed": []}
        try:
            owned_install_ids = {
                item["id"] for item in registry_payload["installations"]
                if item.get("owner") == "wyrd"
                and Path(item.get("executable", "")).expanduser().resolve(strict=True) == executable.resolve(strict=True)
            }
        except (OSError, ValueError, RuntimeError, KeyError):
            return {"ok": False, "message": "Wyrd installation registration could not be resolved safely; reset refused.", "removed": []}

        users_root = prefix / "pfx/drive_c/users"
        if users_root.is_symlink() or not users_root.is_dir():
            return {"ok": False, "message": "Proton user root is missing or unsafe; reset refused.", "removed": []}
        user_roots = [p for p in users_root.iterdir() if p.is_dir() and not p.is_symlink()]
        for record in registry_payload["instances"]:
            if record.get("installation_id") not in owned_install_ids:
                continue
            instance_dir = Path(record.get("instance_path", "")).expanduser()
            config_file = Path(record.get("config", "")).expanduser()
            if instance_dir.is_symlink() or not instance_dir.is_dir() or config_file.is_symlink() or not config_file.is_file():
                return {"ok": False, "message": f"Registered instance is missing or unsafe: {instance_dir}. Reset refused before deleting anything.", "removed": []}

            valid_global = False
            for user_root in user_roots:
                components = [
                    user_root / "AppData",
                    user_root / "AppData/Local",
                    user_root / "AppData/Local/ModOrganizer",
                    instance_dir,
                    config_file,
                ]
                # Reject symlinks in every component, including components that
                # do not yet exist but could redirect traversal through a link.
                if any(component.is_symlink() for component in components):
                    continue
                root = user_root / "AppData/Local/ModOrganizer"
                try:
                    resolved_user = user_root.resolve(strict=True)
                    resolved_user.relative_to(resolved_prefix)
                    resolved_root = root.resolve(strict=True)
                    resolved_root.relative_to(resolved_user)
                    resolved_instance = instance_dir.resolve(strict=True)
                    resolved_instance.relative_to(resolved_root)
                    resolved_config = config_file.resolve(strict=True)
                    resolved_config.relative_to(resolved_instance)
                    valid_global = (
                        config_file.name.casefold() == "modorganizer.ini"
                        and resolved_config.is_file()
                    )
                    if valid_global:
                        break
                except (OSError, ValueError, RuntimeError):
                    continue
            if valid_global:
                owned_instance_dirs.append(instance_dir)
            elif record.get("instance_type") == "portable":
                try:
                    instance_dir.resolve(strict=True).relative_to(resolved_install)
                except (OSError, ValueError, RuntimeError):
                    return {"ok": False, "message": f"Registered portable instance is outside Wyrd's verified install: {instance_dir}. Reset refused.", "removed": []}
            else:
                return {"ok": False, "message": f"Registered global instance is outside the verified Wyrd instance root: {instance_dir}. Reset refused.", "removed": []}

    settings_payload = None
    if settings_path.exists() or settings_path.is_symlink():
        if settings_path.is_symlink() or not settings_path.is_file():
            return {"ok": False, "message": "Wyrd settings path is unsafe; reset refused.", "removed": []}
        try:
            settings_payload = json.loads(settings_path.read_text(encoding="utf-8"))
            if not isinstance(settings_payload, dict):
                return {"ok": False, "message": "Wyrd settings file is not a JSON object; reset refused.", "removed": []}
        except FileNotFoundError:
            # Settings are optional; a clean installation may not have written
            # them yet. Unsafe or malformed existing settings still fail closed.
            settings_payload = None
        except (OSError, ValueError):
            return {"ok": False, "message": "Wyrd settings could not be read; reset refused.", "removed": []}

    # Reject duplicate and overlapping targets. A nested instance directory or an
    # instance that overlaps the Wyrd install could otherwise cause order-dependent
    # deletion and accidentally broaden the ownership boundary.
    targets = [(directory, "registered global instance") for directory in owned_instance_dirs]
    targets.append((install, "Wyrd MO2 installation"))
    resolved_targets = []
    for target, _kind in targets:
        if target.is_symlink() or not target.is_dir():
            return {"ok": False, "message": f"Reset target changed or is unsafe: {target}", "removed": []}
        try:
            resolved = target.resolve(strict=True)
            resolved.relative_to(resolved_prefix)
        except (OSError, ValueError, RuntimeError):
            return {"ok": False, "message": f"Reset target escaped the verified Skyrim prefix boundary: {target}", "removed": []}
        for previous in resolved_targets:
            if resolved == previous or resolved in previous.parents or previous in resolved.parents:
                return {"ok": False, "message": f"Reset targets overlap or are duplicated: {target}", "removed": []}
        resolved_targets.append(resolved)

    # Revalidate the complete target set immediately before deletion to catch
    # path replacement between inventory validation and the first rmtree call.
    for target, _kind in targets:
        if target.is_symlink() or not target.is_dir():
            return {"ok": False, "message": f"Reset target changed or is unsafe immediately before deletion: {target}", "removed": []}
        try:
            current = target.resolve(strict=True)
            current.relative_to(resolved_prefix)
        except (OSError, ValueError, RuntimeError):
            return {"ok": False, "message": f"Reset target changed or escaped the verified prefix: {target}", "removed": []}
    if prefix.is_symlink() or prefix.resolve(strict=True) != resolved_prefix:
        return {"ok": False, "message": "Steam's Skyrim prefix changed during reset validation; reset refused.", "removed": []}

    removed = []
    try:
        for target, kind in targets:
            shutil.rmtree(target)
            removed.append({"path": str(target), "kind": kind})
    except OSError as error:
        return {"ok": False, "message": f"Reset stopped after a partial removal: {error}", "removed": removed}

    # Remove only registry records for this verified Wyrd installation.
    try:
        if registry_path.exists():
            registry_payload["instances"] = [
                item for item in registry_payload["instances"]
                if item.get("installation_id") not in owned_install_ids
            ]
            registry_payload["installations"] = [
                item for item in registry_payload["installations"]
                if item.get("id") not in owned_install_ids
            ]
            used_games = {item.get("game_id") for item in registry_payload["instances"]}
            used_runtimes = {item.get("runtime_id") for item in registry_payload["instances"]}
            registry_payload["games"] = [item for item in registry_payload["games"] if item.get("id") in used_games]
            registry_payload["runtimes"] = [item for item in registry_payload["runtimes"] if item.get("id") in used_runtimes]
            save_registry(registry_path, registry_payload)
        if settings_payload is not None:
            settings_payload.pop("selected_mo2_instance_id", None)
            temporary = settings_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(settings_payload, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, settings_path)
    except Exception as error:
        return {"ok": False, "message": f"MO2 files were removed but Wyrd metadata cleanup failed: {error}", "removed": removed}

    return {
        "ok": True,
        "removed": removed,
        "message": "Wyrd-managed MO2 installation and registered global instances were removed. Skyrim's Steam compatdata/489830 prefix, game files, saves, external MO2 installations, and custom storage outside verified instance folders were preserved.",
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
