"""Wyrd-owned MO2 installation and launch through a discovered Steam Proton build.

All paths are user-local. Existing MO2 installs and unknown prefixes are never
adopted or overwritten automatically.
"""
from __future__ import annotations

import configparser
import json
import os
import re
import shutil
import struct
import subprocess
import time
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Callable

from skyrim_detection import find_steam_roots
from mo2_inspection import find_mo2_installations

APP_ID = "489830"
RELEASE_API = "https://api.github.com/repos/ModOrganizer2/modorganizer/releases/latest"
APP_DIR_NAME = "wyrd"
MO2_DIR_NAME = "mo2"
PREFIX_DIR_NAME = "compatdata/mo2-skyrim"
ProgressCallback = Callable[[int, str], None]


def _data_home(home: Path) -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")).expanduser()


def managed_paths(home: Path | None = None) -> dict[str, Path]:
    home = (home or Path.home()).expanduser()
    root = _data_home(home) / APP_DIR_NAME
    return {
        "root": root,
        "install": root / MO2_DIR_NAME,
        "prefix": root / PREFIX_DIR_NAME,
        "config": Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")).expanduser() / APP_DIR_NAME,
        "settings": Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")).expanduser() / APP_DIR_NAME / "settings.json",
    }


def _host_machine() -> str:
    return os.uname().machine.lower()


def inspect_proton_build(path: Path, host: str | None = None) -> dict:
    """Validate a Proton wrapper and its Wine ELF architecture."""
    path = Path(path).expanduser()
    wrapper = path / "proton"
    wine_candidates = (path / "files/bin/wine", path / "dist/bin/wine")
    wine = next((p for p in wine_candidates if p.is_file()), None)
    if not wrapper.is_file() or not os.access(wrapper, os.X_OK):
        return {"ok": False, "path": str(path), "message": "Proton wrapper is missing or not executable."}
    if wine is None:
        return {"ok": False, "path": str(path), "message": "Proton Wine binary was not found."}
    try:
        header = wine.read_bytes()[:20]
    except OSError as error:
        return {"ok": False, "path": str(path), "message": f"Could not inspect Proton Wine: {error}"}
    if len(header) < 20 or header[:4] != b"\x7fELF":
        return {"ok": False, "path": str(path), "message": "Proton Wine is not a valid ELF binary."}
    endian = "<" if header[5] == 1 else ">" if header[5] == 2 else None
    if endian is None:
        return {"ok": False, "path": str(path), "message": "Proton Wine has invalid ELF byte order."}
    machine_id = struct.unpack(endian + "H", header[18:20])[0]
    arch_names = {3: "x86", 40: "ARM", 62: "x86_64", 183: "AArch64"}
    architecture = arch_names.get(machine_id, f"ELF machine {machine_id}")
    host = (host or _host_machine()).lower()
    expected = {"x86_64": {62}, "amd64": {62}, "aarch64": {183}, "arm64": {183}}
    if machine_id not in expected.get(host, set()):
        return {
            "ok": False, "path": str(path), "wine": str(wine),
            "architecture": architecture, "host_architecture": host,
            "message": f"Proton Wine is {architecture}, but this system is {host}.",
        }
    return {
        "ok": True, "path": str(path.resolve()), "wrapper": str(wrapper.resolve()),
        "wine": str(wine.resolve()), "architecture": architecture,
        "host_architecture": host, "name": path.name,
        "message": f"{path.name} is compatible with {host}.",
    }


def discover_proton_builds(home: Path | None = None) -> list[dict]:
    """Discover built-in and user-installed Proton builds across Steam roots."""
    home = (home or Path.home()).expanduser()
    candidates: list[Path] = []
    for steam_root in find_steam_roots(home):
        candidates.extend((steam_root / "steamapps/common").glob("Proton*"))
        candidates.extend((steam_root / "compatibilitytools.d").iterdir()
                          if (steam_root / "compatibilitytools.d").is_dir() else [])
    extra_roots = [
        home / ".steam/root/compatibilitytools.d",
        home / ".steam/steam/compatibilitytools.d",
        home / ".local/share/Steam/compatibilitytools.d",
        Path("/usr/share/steam/compatibilitytools.d"),
        Path("/usr/lib/steam/compatibilitytools.d"),
    ]
    for root in extra_roots:
        if root.is_dir():
            try:
                candidates.extend(root.iterdir())
            except OSError:
                pass
    found: dict[str, dict] = {}
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        item = inspect_proton_build(candidate)
        if item.get("ok"):
            found[item["path"]] = item
    def sort_key(item: dict):
        name = item["name"].lower()
        # Put Experimental first, then common Proton builds, then GE/custom tools.
        priority = 0 if "experimental" in name else 1 if name.startswith("proton") else 2
        import re
        version = tuple(int(part) for part in re.findall(r"\d+", name)[-3:])
        return (priority, tuple(-v for v in version), name)
    return sorted(found.values(), key=sort_key)


def inspect_wyrd_installation(home: Path | None = None) -> dict:
    paths = managed_paths(home)
    exe = paths["install"] / "ModOrganizer.exe"
    marker = paths["install"] / ".wyrd-managed.json"
    prefix_marker = paths["prefix"] / ".wyrd-managed.json"
    installed = exe.is_file() and marker.is_file()
    metadata: dict = {}
    if installed:
        try:
            metadata = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            installed = False
        else:
            installed = installed and metadata.get("managed_by") == "Wyrd"
    prefix_ready = (paths["prefix"] / "pfx/drive_c").is_dir() and prefix_marker.is_file()
    external = [str(p) for p in find_mo2_installations(Path(home or Path.home()))]
    return {
        "installed": installed,
        "executable": str(exe) if installed else None,
        "metadata": metadata,
        "prefix_ready": prefix_ready,
        "paths": {key: str(value) for key, value in paths.items()},
        "external_installations": external,
        "message": "Wyrd-managed MO2 is installed." if installed else
                   ("An MO2 installation exists elsewhere. Wyrd will not overwrite or adopt it." if external else
                    "MO2 is not installed yet."),
    }


def _steam_root_for_game(game_path: Path, home: Path) -> Path:
    resolved_game = game_path.resolve()
    for root in find_steam_roots(home):
        try:
            resolved_game.relative_to(root.resolve())
            return root
        except ValueError:
            pass
    roots = find_steam_roots(home)
    if roots:
        return roots[0]
    raise RuntimeError("Steam was not detected. Install/launch Steam once, then refresh detection.")


def _runtime_environment(proton: Path, steam_root: Path, prefix_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "STEAM_COMPAT_CLIENT_INSTALL_PATH": str(steam_root),
        "STEAM_COMPAT_DATA_PATH": str(prefix_root),
        "SteamAppId": APP_ID,
        "SteamGameId": APP_ID,
        "STEAM_COMPAT_APP_ID": APP_ID,
        "PROTON_LOG": "0",
    })
    env.setdefault("PROTON_NO_ESYNC", "0")
    return env


def _run_proton(proton: Path, steam_root: Path, prefix_root: Path,
                arguments: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    command = [str(proton / "proton"), "run", *arguments]
    env = _runtime_environment(proton, steam_root, prefix_root)
    try:
        return subprocess.run(command, env=env, cwd=str(proton), check=False,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"Proton command timed out: {' '.join(arguments)}") from error
    except OSError as error:
        raise RuntimeError(f"Could not start Proton: {error}") from error


def _prefix_ready(prefix_root: Path) -> bool:
    return (prefix_root / "pfx/drive_c").is_dir()


def _initialize_prefix(proton: Path, steam_root: Path, prefix_root: Path,
                       game_path: Path, callback: ProgressCallback | None = None) -> None:
    marker = prefix_root / ".wyrd-managed.json"
    if prefix_root.exists() and not marker.is_file():
        raise RuntimeError(
            f"The intended MO2 prefix already exists but is not marked as Wyrd-managed: {prefix_root}. "
            "It was left untouched."
        )
    if not prefix_root.exists():
        prefix_root.mkdir(parents=True, exist_ok=False)
        marker.write_text(json.dumps({
            "managed_by": "Wyrd", "created": datetime.now(timezone.utc).isoformat(),
            "game_app_id": APP_ID, "proton": str(proton.resolve()),
        }, indent=2), encoding="utf-8")
    if callback:
        callback(25, "Initializing Wyrd's dedicated Proton prefix…")
    result = _run_proton(proton, steam_root, prefix_root, ["wineboot", "-u"], timeout=900)
    if result.returncode != 0 or not _prefix_ready(prefix_root):
        details = (result.stdout + "\n" + result.stderr).strip()
        raise RuntimeError("Proton could not initialize the dedicated MO2 prefix." +
                           ("\n" + details[-4000:] if details else ""))
    # Register the detected game path in this MO2-only prefix. Skyrim's real
    # Steam prefix and game files are not changed.
    normalized = "Z:" + str(game_path.resolve()).replace("/", "\\")
    escaped = normalized.replace("\\", "\\\\")
    registry = (
        "Windows Registry Editor Version 5.00\n\n"
        "[HKEY_LOCAL_MACHINE\\Software\\Bethesda Softworks\\Skyrim Special Edition]\n"
        f'"Installed Path"="{escaped}"\n'
    )
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".reg", delete=False) as handle:
        handle.write(registry)
        reg_path = Path(handle.name)
    try:
        if callback:
            callback(35, "Registering the detected Skyrim location in Wyrd's prefix…")
        imported = _run_proton(proton, steam_root, prefix_root,
                               ["regedit", "/S", str(reg_path)], timeout=120)
        if imported.returncode != 0:
            details = (imported.stdout + "\n" + imported.stderr).strip()
            raise RuntimeError("Could not register Skyrim in Wyrd's MO2 prefix." +
                               ("\n" + details[-3000:] if details else ""))
    finally:
        reg_path.unlink(missing_ok=True)


def _latest_mo2_archive() -> tuple[str, str, str]:
    request = urllib.request.Request(RELEASE_API, headers={"User-Agent": "Wyrd-MO2-Installer"})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            release = json.loads(response.read().decode("utf-8"))
    except Exception as error:
        raise RuntimeError(f"Could not retrieve the latest MO2 release: {error}") from error
    excluded = ("pdbs", "src", "uibase", "commits")
    assets = [
        asset for asset in release.get("assets", [])
        if asset.get("name", "").startswith("Mod.Organizer-")
        and asset.get("name", "").endswith(".7z")
        and not any(token in asset.get("name", "").lower() for token in excluded)
        and asset.get("browser_download_url")
    ]
    if len(assets) != 1:
        raise RuntimeError("The latest MO2 release did not expose exactly one standard .7z archive.")
    asset = assets[0]
    return release.get("tag_name", "unknown"), asset["name"], asset["browser_download_url"]


def _download_archive(url: str, destination: Path,
                      callback: ProgressCallback | None = None) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "Wyrd-MO2-Installer"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as out:
            total = int(response.headers.get("Content-Length", "0") or 0)
            downloaded = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)
                if callback:
                    percent = 40 + (int(downloaded * 45 / total) if total else 0)
                    callback(min(percent, 85), f"Downloading MO2… {downloaded // (1024 * 1024)} MiB")
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _validate_archive_members(names: list[str]) -> None:
    for name in names:
        normalized = name.replace("\\", "/")
        path = Path(normalized)
        if path.is_absolute() or PureWindowsPath(normalized).is_absolute() or any(part == ".." for part in path.parts):
            raise RuntimeError(f"Unsafe path in MO2 archive: {name}")


def _extract_mo2(archive: Path, stage: Path, callback: ProgressCallback | None = None) -> Path:
    try:
        import py7zr
    except ImportError as error:
        raise RuntimeError("The 7z extraction component is missing. Re-run Wyrd's GUI launcher to install its dependencies.") from error
    if callback:
        callback(87, "Extracting the official MO2 archive…")
    stage.mkdir(parents=True, exist_ok=True)
    with py7zr.SevenZipFile(archive, mode="r") as bundle:
        _validate_archive_members(bundle.getnames())
        bundle.extractall(path=stage)
    executables = list(stage.rglob("ModOrganizer.exe"))
    if len(executables) != 1:
        raise RuntimeError("The MO2 archive did not contain exactly one ModOrganizer.exe.")
    return executables[0].parent


def install_mo2_environment(proton_path: Path, game_path: Path,
                            home: Path | None = None,
                            callback: ProgressCallback | None = None) -> dict:
    """Install the current official MO2 archive and initialize its isolated Proton prefix."""
    home = (home or Path.home()).expanduser()
    proton_path = Path(proton_path).expanduser().resolve()
    game_path = Path(game_path).expanduser().resolve()
    validation = inspect_proton_build(proton_path)
    if not validation.get("ok"):
        raise RuntimeError(validation.get("message", "Selected Proton build is invalid."))
    if not game_path.is_dir():
        raise RuntimeError(f"Detected Skyrim directory is no longer available: {game_path}")
    steam_root = _steam_root_for_game(game_path, home)
    paths = managed_paths(home)
    paths["root"].mkdir(parents=True, exist_ok=True)
    install_dir, prefix_root = paths["install"], paths["prefix"]
    if install_dir.exists():
        state = inspect_wyrd_installation(home)
        if state.get("installed"):
            if not state.get("prefix_ready"):
                if callback:
                    callback(10, "Repairing Wyrd's incomplete Proton prefix…")
                _initialize_prefix(proton_path, steam_root, prefix_root, game_path, callback)
                return {
                    "ok": True, "already_installed": True,
                    "executable": state.get("executable"), "version": state.get("metadata", {}).get("version", "unknown"),
                    "proton": str(proton_path), "prefix": str(prefix_root),
                    "game_path": str(game_path),
                    "message": "The Wyrd Proton prefix was initialized and the existing MO2 installation was verified.",
                }
            return {"ok": True, "already_installed": True, **state}
        raise RuntimeError(f"MO2 install target already exists but is not a verified Wyrd installation: {install_dir}. It was left untouched.")
    # Ownership checks must finish before network access or any extraction begins.
    if prefix_root.exists() and not (prefix_root / ".wyrd-managed.json").is_file():
        raise RuntimeError(
            f"The intended MO2 prefix already exists but is not marked as Wyrd-managed: {prefix_root}. "
            "It was left untouched."
        )
    if callback:
        callback(5, "Checking the official MO2 release…")
    version, archive_name, url = _latest_mo2_archive()
    if callback:
        callback(15, f"Preparing MO2 {version}…")
    _initialize_prefix(proton_path, steam_root, prefix_root, game_path, callback)
    staging_root = paths["root"] / "staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="mo2-", dir=staging_root))
    archive = stage / archive_name
    try:
        _download_archive(url, archive, callback)
        extracted = _extract_mo2(archive, stage / "extracted", callback)
        if callback:
            callback(94, "Verifying the MO2 application files…")
        if not (extracted / "ModOrganizer.exe").is_file():
            raise RuntimeError("MO2 verification failed: ModOrganizer.exe is missing.")
        if extracted != stage / "extracted":
            # The archive sometimes contains a wrapper directory; move its
            # actual application root into the canonical Wyrd location.
            content_root = extracted
        else:
            content_root = extracted
        (content_root / ".wyrd-managed.json").write_text(json.dumps({
            "managed_by": "Wyrd", "version": version,
            "installed": datetime.now(timezone.utc).isoformat(),
            "proton": str(proton_path), "steam_root": str(steam_root),
            "game_app_id": APP_ID, "game_path": str(game_path),
            "prefix": str(prefix_root),
        }, indent=2), encoding="utf-8")
        if install_dir.exists():
            raise RuntimeError(f"Install target appeared during setup: {install_dir}. Existing files were not changed.")
        os.replace(content_root, install_dir)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    if callback:
        callback(100, "MO2 installed and verified. You can launch it now.")
    return {
        "ok": True, "already_installed": False,
        "executable": str(install_dir / "ModOrganizer.exe"),
        "version": version, "proton": str(proton_path),
        "prefix": str(prefix_root), "game_path": str(game_path),
        "message": f"MO2 {version} installed and verified in Wyrd's dedicated Proton environment.",
    }




def _validated_wyrd_audio_prefix(home: Path | None = None) -> tuple[Path, dict]:
    """Resolve only Wyrd's canonical prefix after validating both ownership records."""
    home = (home or Path.home()).expanduser().resolve()
    paths = managed_paths(home)
    prefix = paths["prefix"]
    expected = (paths["root"] / PREFIX_DIR_NAME).resolve()
    if prefix.resolve(strict=False) != expected:
        raise RuntimeError("The configured Wyrd prefix path is not canonical; refusing audio inspection.")
    install_marker = paths["install"] / ".wyrd-managed.json"
    prefix_marker = prefix / ".wyrd-managed.json"
    exe = paths["install"] / "ModOrganizer.exe"
    if not exe.is_file() or exe.is_symlink() or not install_marker.is_file() or install_marker.is_symlink():
        raise RuntimeError("Wyrd MO2 ownership cannot be established; refusing audio inspection.")
    try:
        install_data = json.loads(install_marker.read_text(encoding="utf-8"))
        prefix_data = json.loads(prefix_marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError("A Wyrd ownership marker is missing or unreadable; refusing audio inspection.") from error
    if install_data.get("managed_by") != "Wyrd" or prefix_data.get("managed_by") != "Wyrd":
        raise RuntimeError("Both MO2 and Proton prefix ownership markers must identify Wyrd.")
    if str(Path(install_data.get("prefix", prefix)).resolve()) != str(expected):
        raise RuntimeError("The MO2 ownership marker does not point to Wyrd's canonical prefix.")
    if install_data.get("game_app_id") not in (None, APP_ID) or prefix_data.get("game_app_id") not in (None, APP_ID):
        raise RuntimeError("The ownership marker does not identify Skyrim Special Edition.")
    if not (prefix / "pfx/drive_c").is_dir():
        raise RuntimeError("Wyrd's Proton prefix is not initialized.")
    # Refuse links at each component below the ownership root. The checks are
    # intentionally repeated before mutation to guard against path substitution.
    for candidate in (prefix, prefix / "pfx", prefix / "pfx/drive_c",
                      prefix / "pfx/drive_c/windows",
                      prefix / "pfx/drive_c/windows/system32",
                      prefix / "pfx/drive_c/windows/syswow64"):
        if candidate.is_symlink():
            raise RuntimeError(f"Symlink detected in managed prefix path: {candidate}")
    return prefix, {"install": install_data, "prefix": prefix_data}


def _sha256_file(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _resolve_xact_dlls(home: Path | None = None) -> dict:
    """Resolve verified Microsoft XACT payloads from a cached DirectX June 2010 installer.

    The redistributable is obtained only from Microsoft's official download
    endpoint and pinned to the SHA-256 used by winetricks. It is extracted
    without executing its installer.
    """
    import hashlib
    import urllib.request

    expected_hash = "053f76dcbb28802e23341b6a787e3b0791c0fa5c8d4d011b1044172dbf89c73b"
    url = (
        "https://download.microsoft.com/download/8/4/A/"
        "84A35BF1-DAFE-4AE8-82AF-AD2AE20B6B14/directx_Jun2010_redist.exe"
    )
    cache_root = managed_paths(home)["root"] / "cache"
    installer = cache_root / "directx_Jun2010_redist.exe"
    if not installer.is_file():
        cache_root.mkdir(parents=True, exist_ok=True)
        temporary = cache_root / (installer.name + ".download")
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Wyrd-Audio-Compatibility"})
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
            if _sha256_file(temporary) != expected_hash:
                raise RuntimeError("Microsoft DirectX June 2010 installer SHA-256 mismatch.")
            os.replace(temporary, installer)
        finally:
            temporary.unlink(missing_ok=True)
    if _sha256_file(installer) != expected_hash:
        raise RuntimeError("Cached DirectX June 2010 installer failed SHA-256 verification.")
    try:
        import cabarchive
    except ImportError as error:
        raise RuntimeError("The cabarchive Python package is required to extract the DirectX installer.") from error
    # The DirectX redistributable is a self-extracting archive. Never execute it.
    extract_root = cache_root / "directx_Jun2010_extracted"
    extract_root.mkdir(parents=True, exist_ok=True)
    cabinets = [p for p in extract_root.rglob("*.cab") if p.is_file() and not p.is_symlink()]
    if not cabinets:
        seven_zip = shutil.which("7z") or shutil.which("7zz")
        if not seven_zip:
            raise RuntimeError("7z/7zz is required to extract the verified DirectX redistributable safely.")
        result = subprocess.run(
            [seven_zip, "x", "-y", f"-o{extract_root}", str(installer)],
            capture_output=True, text=True, timeout=180, check=False,
        )
        if result.returncode not in (0, 1):
            raise RuntimeError("Could not safely extract the verified DirectX redistributable: " +
                               (result.stderr or result.stdout)[-2000:])
        cabinets = [p for p in extract_root.rglob("*.cab") if p.is_file() and not p.is_symlink()]
    if not cabinets:
        raise RuntimeError("No CAB payloads were found in the verified DirectX redistributable.")
    import fnmatch
    result = {}
    patterns = {
        "xact": ("*_xact_*x86*.cab", "xactengine*.dll"),
        "xact_x64": ("*_xact_*x64*.cab", "xactengine*.dll"),
    }
    for key, (cab_pattern, dll_pattern) in patterns.items():
        matches = []
        for cabinet in cabinets:
            if not fnmatch.fnmatch(cabinet.name.lower(), cab_pattern.lower()):
                continue
            try:
                archive = cabarchive.CabArchive(cabinet.read_bytes())
                for member_name, member in archive.items():
                    if fnmatch.fnmatch(Path(member_name).name.lower(), dll_pattern.lower()):
                        matches.append((cabinet, Path(member_name).name, member.buf))
            except Exception:
                continue
        unique = {__import__("hashlib").sha256(item[2]).hexdigest(): item for item in matches}
        if len(unique) == 1:
            cabinet, filename, payload = next(iter(unique.values()))
            digest = __import__("hashlib").sha256(payload).hexdigest()
            destination = cache_root / key / filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists() or _sha256_file(destination) != digest:
                temporary = destination.with_suffix(destination.suffix + ".tmp")
                temporary.write_bytes(payload)
                os.replace(temporary, destination)
            result[key] = {"source": str(destination.resolve()), "sha256": digest,
                           "source_archive": str(cabinet)}
    return result

def inspect_audio_compatibility(home: Path | None = None) -> dict:
    """Read-only XACT inspection scoped to Wyrd's positively owned prefix."""
    try:
        prefix, ownership = _validated_wyrd_audio_prefix(home)
        windows = prefix / "pfx/drive_c/windows"
        locations = {
            "xact": windows / "syswow64" / "xactengine3_7.dll",
            "xact_x64": windows / "system32" / "xactengine3_7.dll",
        }
        components = {}
        for key, target in locations.items():
            if target.is_symlink():
                raise RuntimeError(f"Symlink detected at XACT target: {target}")
            if target.exists() and not target.is_file():
                raise RuntimeError(f"XACT target is not a regular file: {target}")
            components[key] = {
                "path": str(target),
                "state": "present" if target.is_file() else "missing",
                "sha256": _sha256_file(target) if target.is_file() else None,
            }
        overrides = prefix / "pfx/user.reg"
        override_text = ""
        if overrides.is_file() and not overrides.is_symlink():
            try:
                override_text = overrides.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
        for key in components:
            # This is a diagnostic hint only; registry state is not inferred
            # from files being present.
            components[key]["wine_override_recorded"] = (
                f'"{key}"=' in override_text.casefold()
                or f'"{key}"=' in override_text
            )
        missing = [key for key, value in components.items() if value["state"] == "missing"]
        return {
            "ok": True, "safe": True, "prefix": str(prefix),
            "components": components,
            "repair_recommended": bool(missing),
            "runtime_verified": False,
            "runtime_note": "Only an in-game dialogue test can verify Skyrim audio.",
            "message": "Wyrd prefix ownership verified; XACT state inspected.",
        }
    except (OSError, RuntimeError, ValueError) as error:
        return {
            "ok": False, "safe": False, "prefix": None, "components": {},
            "repair_recommended": False, "runtime_verified": False,
            "message": str(error),
        }


def repair_audio_compatibility(home: Path | None = None, confirmed: bool = False) -> dict:
    """Install Wyrd's Proton-provided XACT builtins into Wyrd's prefix only.

    Existing targets are backed up before replacement. No Steam compatdata
    location is read as a target or modified. Caller must obtain explicit
    user confirmation before invoking with confirmed=True.
    """
    if not confirmed:
        return {"ok": False, "modified": False,
                "message": "Repair requires explicit user confirmation."}
    try:
        prefix, _ownership = _validated_wyrd_audio_prefix(home)
        report = inspect_audio_compatibility(home)
        if not report.get("safe"):
            raise RuntimeError(report.get("message", "Wyrd prefix ownership check failed."))
        sources = _resolve_xact_dlls()
        if set(sources) != {"xact", "xact_x64"}:
            raise RuntimeError(
                "Could not uniquely resolve both XACT DLLs from the selected Proton runtime. "
                "No files were changed."
            )
        windows = prefix / "pfx/drive_c/windows"
        targets = {
            "xact": windows / "syswow64" / "xactengine3_7.dll",
            "xact_x64": windows / "system32" / "xactengine3_7.dll",
        }
        for target in targets.values():
            if target.is_symlink() or not target.parent.is_dir() or target.parent.is_symlink():
                raise RuntimeError(f"Unsafe XACT target path; no files were changed: {target}")
            if target.exists() and not target.is_file():
                raise RuntimeError(f"XACT target is not a regular file: {target}")
        # Stage both source DLLs and backups before changing either target.
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_root = prefix / "audio-repair-backups" / timestamp
        staged_root = prefix / ".audio-repair-staging"
        staged_root.mkdir(mode=0o700, exist_ok=True)
        backup_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        staged = {}
        backups = {}
        for key, target in targets.items():
            source = Path(sources[key]["source"]).resolve(strict=True)
            if not source.is_file() or source.is_symlink():
                raise RuntimeError(f"Unsafe or missing Proton XACT source: {source}")
            stage_file = staged_root / f"{key}.dll"
            shutil.copy2(source, stage_file)
            if _sha256_file(stage_file) != sources[key]["sha256"]:
                raise RuntimeError(f"Staged XACT DLL hash mismatch for {key}.")
            staged[key] = stage_file
            if target.is_file():
                backup = backup_root / target.name
                shutil.copy2(target, backup)
                if _sha256_file(backup) != _sha256_file(target):
                    raise RuntimeError(f"Backup verification failed for {target}.")
                backups[key] = str(backup)
        log_path = prefix / "audio-repair.log"
        journal = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "managed_prefix": str(prefix),
            "changes": [],
            "backups": backups,
            "sources": {key: {"path": sources[key]["source"], "sha256": sources[key]["sha256"]}
                        for key in sources},
            "runtime_verified": False,
        }
        modified = []
        try:
            for key, target in targets.items():
                os.replace(staged[key], target)
                modified.append(key)
                journal["changes"].append({
                    "component": key, "target": str(target),
                    "sha256": _sha256_file(target),
                    "backup": backups.get(key),
                })
            verification = inspect_audio_compatibility(home)
            if not verification.get("safe") or any(
                verification["components"][key]["sha256"] != sources[key]["sha256"]
                for key in targets
            ):
                raise RuntimeError("Post-repair XACT verification failed.")
        except Exception:
            # Restore only files whose previous contents were safely backed up.
            # A previously absent target is removed only if it is still the
            # exact file installed by this operation.
            for key in reversed(modified):
                target = targets[key]
                backup = backups.get(key)
                if backup:
                    shutil.copy2(backup, target)
                elif target.is_file() and _sha256_file(target) == sources[key]["sha256"]:
                    target.unlink()
            raise
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(journal, indent=2) + "\\n")
        shutil.rmtree(staged_root, ignore_errors=True)
        return {
            "ok": True, "modified": bool(modified), "verified": True,
            "runtime_verified": False, "log_path": str(log_path),
            "message": "XACT DLLs were installed and file hashes verified in Wyrd's prefix only. Skyrim dialogue audio is NOT runtime-verified.",
        }
    except (OSError, RuntimeError, ValueError) as error:
        return {"ok": False, "modified": False, "verified": False,
                "runtime_verified": False, "message": str(error)}

def launch_mo2(home: Path | None = None, pick_instance: bool = False) -> dict:
    """Launch MO2 visibly through its configured Proton build."""
    home = (home or Path.home()).expanduser()
    state = inspect_wyrd_installation(home)
    if not state.get("installed"):
        return {"ok": False, "message": "Wyrd-managed MO2 is not installed yet."}
    metadata = state.get("metadata", {})
    proton_path = Path(metadata.get("proton", ""))
    game_path = Path(metadata.get("game_path", ""))
    paths = managed_paths(home)
    if not proton_path.is_dir() or not game_path.is_dir() or not _prefix_ready(paths["prefix"]):
        return {"ok": False, "message": "The saved Proton runtime, Skyrim directory, or MO2 prefix is missing. Refresh detection and repair the setup before launching."}
    validation = inspect_proton_build(proton_path)
    if not validation.get("ok"):
        return {"ok": False, "message": validation.get("message", "Selected Proton build is no longer valid.")}
    executable = paths["install"] / "ModOrganizer.exe"
    arguments = [str(executable)]
    if pick_instance:
        arguments.append("--pick")
    env = _runtime_environment(proton_path, Path(metadata["steam_root"]), paths["prefix"])
    try:
        process = subprocess.Popen(
            [str(proton_path / "proton"), "run", *arguments],
            cwd=str(paths["install"]), env=env, stdin=None, stdout=None, stderr=None,
            start_new_session=True,
        )
    except OSError as error:
        return {"ok": False, "message": f"Could not launch MO2 through Proton: {error}"}
    return {"ok": True, "pid": process.pid,
            "message": "MO2 launched through Wyrd's dedicated Proton prefix."}


def write_settings(home: Path | None, proton_path: Path, game_path: Path) -> None:
    paths = managed_paths(home)
    paths["config"].mkdir(parents=True, exist_ok=True)
    temporary = paths["settings"].with_suffix(".tmp")
    temporary.write_text(json.dumps({
        "proton": str(proton_path.resolve()), "game_path": str(game_path.resolve()),
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
    return result

def _instance_roots(prefix_root: Path) -> list[Path]:
    """Return known MO2 instance roots inside this managed Proton prefix."""
    users = prefix_root / "pfx/drive_c/users"
    roots: list[Path] = []
    if not users.is_dir():
        return roots
    try:
        user_dirs = [entry for entry in users.iterdir() if entry.is_dir()]
    except OSError:
        return roots
    for user_dir in user_dirs:
        candidates = (
            user_dir / "AppData/Local/ModOrganizer/instances",
            user_dir / "AppData/Roaming/ModOrganizer/instances",
            user_dir / "Documents/Modding/ModOrganizer/instances",
        )
        roots.extend(path for path in candidates if path.is_dir())
    return roots


def inspect_mo2_instances(prefix_root: Path) -> dict:
    """Read-only inventory of MO2 instances stored in Wyrd's managed prefix.

    It deliberately does not follow MO2 custom paths or scan arbitrary disks.
    A directory is reported only when it contains a parseable ModOrganizer.ini.
    """
    prefix_root = Path(prefix_root).expanduser()
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

    discovered = []
    errors = []
    for root in _instance_roots(prefix_root):
        try:
            entries = sorted(root.iterdir(), key=lambda item: item.name.casefold())
        except OSError as error:
            errors.append(f"{root}: {error}")
            continue
        for directory in entries:
            if not directory.is_dir():
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
            game_path = general.get("gamePath", "").strip()
            profile = general.get("selected_profile", "").strip()
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
                        f"{index}\\\\{key}", f"{index}/{key}",
                        f"\\\\{index}\\\\{key}", f"/{index}/{key}",
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

            skyrim_markers = ("skyrim special edition", "skyrimse.exe", "skyrim se")
            searchable = " ".join([game_name, game_path] + [
                value for entry in executables for value in entry.values()
            ]).casefold()
            is_skyrim = any(marker in searchable for marker in skyrim_markers)
            discovered.append({
                "name": directory.name,
                "path": str(directory),
                "config": str(ini),
                "game_name": game_name,
                "game_path": game_path,
                "selected_profile": profile,
                "is_skyrim_se": is_skyrim,
                "executables": executables,
            })

    instances = sorted(discovered, key=lambda item: item["name"].casefold())
    skyrim_instances = [item for item in instances if item["is_skyrim_se"]]
    return {
        "ok": True, "managed": True,
        "instances": instances,
        "skyrim_instances": skyrim_instances,
        "errors": errors,
        "message": (
            f"Found {len(instances)} MO2 instance(s), including "
            f"{len(skyrim_instances)} identified Skyrim SE instance(s)."
            if instances else "No MO2 instances were found in the known prefix locations."
        ),
    }

def _resolve_windows_path(value: str, prefix_root: Path, game_path: Path) -> Path | None:
    """Resolve Wine DOS paths conservatively beneath the supplied known root."""
    raw = value.strip().strip('"')
    if not raw:
        return None
    normalized = raw.replace("/", "\\")
    drive = normalized[:2].casefold()
    if drive == "z:":
        relative = normalized[2:].lstrip("\\")
        candidate = Path("/") / relative.replace("\\", "/")
        allowed_root = game_path
    elif drive == "c:":
        relative = normalized[2:].lstrip("\\")
        allowed_root = prefix_root / "pfx/drive_c"
        candidate = allowed_root / relative.replace("\\", "/")
    else:
        return None
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(allowed_root.resolve(strict=True))
    except (OSError, ValueError):
        return None
    return resolved


def inspect_skyrim_launch_targets(prefix_root: Path, game_path: Path, instance_name: str | None = None) -> dict:
    """Validate Skyrim/SKSE executable targets from the first detected Skyrim instance."""
    inventory = inspect_mo2_instances(prefix_root)
    if not inventory.get("ok"):
        return {"ok": False, "targets": [], "message": inventory.get("message", "MO2 inspection failed.")}
    if not inventory.get("skyrim_instances"):
        return {"ok": False, "targets": [], "message": "No Skyrim Special Edition MO2 instance was identified."}

    targets = []
    warnings = []
    selected_instances = inventory["skyrim_instances"]
    if instance_name is not None:
        selected_instances = [item for item in selected_instances if item["name"] == instance_name]
        if not selected_instances:
            return {"ok": False, "targets": [], "message": "The selected Skyrim MO2 instance was not found."}
    for instance in selected_instances:
        candidates = []
        # MO2 gamePath may be stored as either the game directory or
        # SkyrimSE.exe itself. The registered path is authoritative only
        # after it resolves beneath the detected Steam game directory.
        raw_game_path = instance.get("game_path", "").strip()
        game_root = Path(game_path).resolve()
        game_binary = (game_root / "SkyrimSE.exe").resolve()
        try:
            game_binary.relative_to(game_root)
        except ValueError:
            game_binary = None
        if raw_game_path:
            game_location = _resolve_windows_path(raw_game_path, prefix_root, game_root)
            if game_location:
                if game_location.is_dir():
                    candidate_binary = game_location / "SkyrimSE.exe"
                elif game_location.is_file() and game_location.name.casefold() == "skyrimse.exe":
                    candidate_binary = game_location
                else:
                    candidate_binary = None
                if candidate_binary and candidate_binary.is_file():
                    game_binary = candidate_binary.resolve()
                    try:
                        game_binary.relative_to(game_root)
                    except ValueError:
                        game_binary = None
            elif game_binary is None:
                warnings.append(f"{instance['name']}: MO2 gamePath could not be resolved safely.")
        if game_binary and game_binary.is_file() and game_binary.name.casefold() == "skyrimse.exe":
            candidates.append({
                "title": "Skyrim",
                "binary": str(game_binary),
                "arguments": "",
                "working_directory": str(game_binary.parent),
                "source": "MO2 gamePath" if raw_game_path else "detected Skyrim installation",
                "verified": True,
            })

        targets.extend(candidates)
        for entry in instance.get("executables", []):
            title = entry.get("title", "").strip()
            binary = _resolve_windows_path(entry.get("binary", ""), prefix_root, game_path)
            if not title or not binary or not binary.is_file():
                continue
            name = binary.name.casefold()
            is_skse = name in {"skse64_loader.exe", "skse_loader.exe"} or "skse" in title.casefold()
            is_skyrim = name == "skyrimse.exe"
            if not (is_skse or is_skyrim):
                continue
            targets.append({
                "title": title,
                "binary": str(binary),
                "arguments": entry.get("arguments", ""),
                "working_directory": entry.get("working_directory") or str(binary.parent),
                "source": "MO2 custom executable",
                "verified": True,
            })

    unique = {}
    for target in targets:
        key = (target["title"].casefold(), target["binary"].casefold())
        unique[key] = target
    return {
        "ok": bool(unique),
        "targets": list(unique.values()),
        "warnings": warnings,
        "message": (
            f"Verified {len(unique)} Skyrim/SKSE launch target(s) from MO2."
            if unique else "No configured SkyrimSE.exe or SKSE executable could be verified. "
                          "Check the MO2 instance's game path and executable entries."
        ),
    }


def launch_skyrim_via_mo2(executable_title: str, instance_name: str,
                          home: Path | None = None, log_path: Path | None = None) -> dict:
    """Start MO2's configured-executable handoff and retain a diagnostic log.

    A successful return means only that the Proton wrapper process was spawned.
    It does not assert that MO2 accepted the executable or that Skyrim started.
    """
    home = (home or Path.home()).expanduser()
    title = executable_title.strip()
    instance = instance_name.strip()
    if not title or not instance:
        return {"ok": False, "message": "An MO2 instance and configured executable title are required."}
    if any(char in title + instance for char in ("\\x00", "\\r", "\\n")):
        return {"ok": False, "message": "The selected MO2 instance or executable title is invalid."}
    state = inspect_wyrd_installation(home)
    if not state.get("installed") or not state.get("prefix_ready"):
        return {"ok": False, "message": "Wyrd-managed MO2 and its dedicated prefix must be ready before launching Skyrim."}
    metadata = state.get("metadata", {})
    proton_path = Path(metadata.get("proton", ""))
    game_path = Path(metadata.get("game_path", ""))
    paths = managed_paths(home)
    if not proton_path.is_dir() or not game_path.is_dir():
        return {"ok": False, "message": "The saved Proton runtime or Skyrim directory is missing."}
    validation = inspect_proton_build(proton_path)
    if not validation.get("ok"):
        return {"ok": False, "message": validation.get("message", "Selected Proton build is invalid.")}
    prefix_check = inspect_mo2_instances(paths["prefix"])
    instance_match = next(
        (item for item in prefix_check.get("skyrim_instances", [])
         if item["name"] == instance), None
    ) if prefix_check.get("ok") else None
    if not instance_match:
        return {"ok": False, "message": "The selected Skyrim instance is no longer present in Wyrd's managed prefix."}
    target_match = next(
        (item for item in inspect_skyrim_launch_targets(
            paths["prefix"], game_path, instance_name=instance
        ).get("targets", []) if item["title"] == title), None
    )
    if not target_match:
        return {"ok": False, "message": "The selected Skyrim executable is no longer verified in this MO2 instance."}
    env = _runtime_environment(proton_path, Path(metadata["steam_root"]), paths["prefix"])
    # MO2 parses global options before the command name. Keep --instance
    # ahead of ModOrganizer.exe's "run" subcommand so it selects the intended
    # game instance rather than becoming an unparsed subcommand argument.
    command = [
        str(proton_path / "proton"), "run",
        str(paths["install"] / "ModOrganizer.exe"),
        "--instance", instance,
        "run", "--executable", title,
    ]
    diagnostics = Path(log_path) if log_path else paths["root"] / "logs" / "skyrim-launch.log"
    try:
        diagnostics.parent.mkdir(parents=True, exist_ok=True)
        with diagnostics.open("a", encoding="utf-8") as log:
            log.write("\\n=== Wyrd Skyrim launch handoff ===\\n")
            log.write(f"timestamp: {datetime.now(timezone.utc).isoformat()}\\n")
            log.write(f"instance: {instance}\\nexecutable: {title}\\npid: pending\\n")
            log.write("command: " + json.dumps(command) + "\\n")
            log.write("stdout/stderr are redirected to this diagnostic log.\\n")
            log.flush()
            process = subprocess.Popen(
                command, cwd=str(paths["install"]), env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            log.write(f"wrapper_pid: {process.pid}\\n")
            log.flush()
    except OSError as error:
        return {"ok": False, "message": f"Could not start MO2 executable handoff: {error}"}
    return {
        "ok": True, "pid": process.pid, "command": command,
        "log_path": str(diagnostics),
        "message": (
            f"Started the Proton wrapper for MO2 executable '{title}' "
            f"in instance '{instance}'. This does not yet confirm that MO2 or Skyrim started. "
            f"Diagnostics: {diagnostics}"
        ),
    }


def read_launch_diagnostics(log_path: Path, max_bytes: int = 65536) -> dict:
    """Read the tail of Wyrd's launch diagnostics without changing the log."""
    path = Path(log_path)
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            content = handle.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return {"ok": False, "message": "No launch diagnostic log exists yet.", "content": ""}
    except OSError as error:
        return {"ok": False, "message": f"Could not read launch diagnostics: {error}", "content": ""}
    return {"ok": True, "path": str(path), "content": content, "truncated": size > max_bytes}



def classify_launch_diagnostics(content: str) -> list[dict]:
    """Classify common MO2/Proton launch-log symptoms into actionable hints."""
    text = content.lower()
    rules = [
        ("missing_executable", ("could not find executable", "executable not found", "file not found", "no such file or directory"),
         "MO2 could not resolve the selected executable. Re-check the Skyrim install path and the executable selected in MO2."),
        ("missing_proton", ("proton not found", "compatibility tool not found", "wine: command not found"),
         "The selected Proton/Wine runtime appears unavailable. Re-detect Proton builds and select an installed runtime."),
        ("prefix_permission", ("permission denied", "access denied", "read-only file system"),
         "A launch path or managed prefix is inaccessible. Check ownership and filesystem permissions; Wyrd will not take ownership or overwrite it."),
        ("graphics_initialization", ("vulkan", "dxvk", "d3d11", "failed to create device", "gpu"),
         "Graphics/runtime initialization may have failed. Review Vulkan drivers and Proton runtime logs before changing game files."),
        ("mo2_usvfs", ("usvfs", "virtual filesystem", "hook failed", "failed to inject"),
         "MO2/USVFS initialization may have failed. Confirm MO2 is launching the game through its configured instance; do not bypass MO2."),
        ("wine_crash", ("wine: fatal", "unhandled exception", "segmentation fault", "crash"),
         "The Wine/Proton process appears to have reported a crash. Inspect the surrounding log context and runtime version."),
    ]
    findings = []
    for code, needles, hint in rules:
        if any(needle in text for needle in needles):
            findings.append({"code": code, "hint": hint})
    return findings


def detect_skyrim_processes(prefix_root: Path | None = None,
                           proc_root: Path = Path("/proc")) -> dict:
    """Read /proc for Skyrim/SKSE and attribute processes to Wyrd when possible."""
    expected = {"skyrimse.exe", "skyrimse", "skse64_loader.exe", "skse64.exe"}
    prefix = str(Path(prefix_root).expanduser().resolve()) if prefix_root else None
    detected = []
    try:
        entries = list(Path(proc_root).iterdir())
    except OSError as error:
        return {"ok": False, "processes": [], "message": f"Could not inspect process table: {error}"}
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            argv_data = (entry / "cmdline").read_bytes()
        except OSError:
            argv_data = b""
        try:
            comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip().lower()
        except OSError:
            comm = ""
        try:
            exe_path = os.readlink(entry / "exe")
        except OSError:
            exe_path = ""
        try:
            env_data = (entry / "environ").read_bytes()
        except OSError:
            env_data = b""
        argv = [part.decode("utf-8", errors="replace") for part in
                argv_data.split(bytes([0])) if part]
        if not argv and not comm and not exe_path:
            continue
        normalize = lambda value: value.replace(chr(92), "/")
        names = {Path(normalize(arg)).name.lower() for arg in argv}
        names.add(Path(normalize(exe_path)).name.lower())
        names.add(comm)
        matches = sorted(names.intersection(expected))
        if not matches:
            continue
        env_parts = [part.decode("utf-8", errors="replace") for part in env_data.split(bytes([0])) if part]
        attributed = bool(prefix and (any(prefix in part for part in env_parts) or prefix in " ".join(argv)))
        detected.append({
            "pid": int(entry.name), "binary": matches[0], "argv": argv,
            "start_time": _proc_start_time(int(entry.name), Path(proc_root)),
            "attributed_to_wyrd": attributed, "prefix": prefix if attributed else None,
        })
    detected.sort(key=lambda item: item["pid"])
    associated = [item for item in detected if item["attributed_to_wyrd"]]
    message = (f"Detected {len(associated)} Skyrim/SKSE process(es) associated with Wyrd's managed prefix."
               if associated else
               "Skyrim/SKSE process(es) detected, but none could be attributed to Wyrd's managed prefix."
               if detected else "No Skyrim/SKSE process was detected.")
    return {"ok": True, "processes": detected, "wyrd_processes": associated, "message": message}

def _proc_identity(pid: int, proc_root: Path = Path("/proc")) -> dict:
    """Return stable Linux process identity fields from /proc/<pid>/stat."""
    try:
        stat = (Path(proc_root) / str(pid) / "stat").read_text(encoding="utf-8")
        # comm is parenthesized and may itself contain spaces or ')' characters.
        fields = stat[stat.rfind(")") + 2:].split()
        return {"ppid": int(fields[1]), "start_time": int(fields[19])}
    except (OSError, ValueError, IndexError):
        return {"ppid": None, "start_time": None}


def _proc_start_time(pid: int, proc_root: Path = Path("/proc")) -> int | None:
    """Return Linux process start time (clock ticks since boot), if readable."""
    return _proc_identity(pid, proc_root)["start_time"]


def _is_descendant_process(pid: int, ancestor_pid: int,
                           proc_root: Path = Path("/proc"),
                           max_depth: int = 64) -> bool:
    """Return whether pid currently descends from ancestor_pid using procfs PPIDs."""
    current = pid
    seen: set[int] = set()
    for _ in range(max_depth):
        if current == ancestor_pid:
            return True
        if current <= 1 or current in seen:
            return False
        seen.add(current)
        identity = _proc_identity(current, proc_root)
        parent = identity["ppid"]
        if parent is None:
            return False
        current = parent
    return False

def launch_process_status(pid: int, start_time: int | None = None,
                          proc_root: Path = Path("/proc")) -> dict:
    """Report wrapper state and optionally verify its original process identity."""
    if not isinstance(pid, int) or pid <= 0:
        return {"state": "invalid", "message": "Invalid launch process ID."}
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return {"state": "exited", "message": "The Proton wrapper process has exited."}
    except PermissionError:
        return {"state": "unknown", "message": "The Proton wrapper exists but its status cannot be read."}
    except OSError as error:
        return {"state": "unknown", "message": f"Could not inspect Proton wrapper process: {error}"}
    if start_time is not None:
        actual_start = _proc_start_time(pid, proc_root)
        if actual_start is None:
            return {"state": "unknown", "message": "The Proton wrapper exists, but its process identity cannot be verified from /proc."}
        if actual_start != start_time:
            return {"state": "exited", "message": "The original Proton wrapper exited; its PID has since been reused by another process."}
    return {
        "state": "running",
        "message": "The Proton wrapper process is still running. Skyrim startup is not independently confirmed.",
    }
