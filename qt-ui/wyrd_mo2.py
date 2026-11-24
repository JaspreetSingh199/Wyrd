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


def _is_path_within(path: Path, root: Path) -> bool:
    """Return whether path resolves beneath root without following a final symlink."""
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
        return True
    except (OSError, ValueError, RuntimeError):
        return False


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


def discover_proton_builds(home: Path | None = None,
                           system_roots: list[Path] | None = None) -> list[dict]:
    """Discover Proton builds across Steam roots and optional system locations.

    ``system_roots=None`` uses the normal host-wide locations. Passing an explicit
    list makes discovery deterministic for isolated tests or callers.
    """
    home = (home or Path.home()).expanduser()
    candidates: list[Path] = []
    for steam_root in find_steam_roots(home):
        candidates.extend((steam_root / "steamapps/common").glob("Proton*"))
        candidates.extend((steam_root / "compatibilitytools.d").iterdir()
                          if (steam_root / "compatibilitytools.d").is_dir() else [])
    extra_roots = system_roots if system_roots is not None else [
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


def reset_mo2_compatdata(home: Path | None = None, confirmed: bool = False) -> dict:
    """Delete only Wyrd's explicitly marked MO2 Proton prefix, never Steam compatdata."""
    paths = managed_paths(home)
    prefix = paths["prefix"]
    marker = prefix / ".wyrd-managed.json"
    if not confirmed:
        return {"ok": False, "message": "Reset requires explicit confirmation.", "removed": False}
    if not prefix.exists() and not prefix.is_symlink():
        return {"ok": True, "message": "Wyrd MO2 compatdata is already absent.", "removed": False}
    if prefix.is_symlink():
        return {"ok": False, "message": "Refusing to reset a symlinked MO2 prefix.", "removed": False}
    if paths["root"].is_symlink():
        return {"ok": False, "message": "Refusing to reset through a symlinked Wyrd data root.", "removed": False}
    try:
        resolved_prefix = prefix.resolve(strict=True)
        resolved_root = paths["root"].resolve(strict=True)
        resolved_prefix.relative_to(resolved_root)
    except (OSError, ValueError, RuntimeError):
        return {"ok": False, "message": "Refusing to reset a prefix outside Wyrd's managed directory.", "removed": False}
    try:
        metadata = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "message": "Wyrd ownership marker is missing or invalid; prefix left untouched.", "removed": False}
    if metadata.get("managed_by") != "Wyrd":
        return {"ok": False, "message": "Prefix is not marked as Wyrd-managed; left untouched.", "removed": False}
    try:
        shutil.rmtree(prefix)
    except OSError as error:
        return {"ok": False, "message": f"Could not remove Wyrd MO2 compatdata: {error}", "removed": False}
    return {"ok": True, "message": "Wyrd MO2 compatdata was removed. MO2 installation files and Skyrim/Steam data were left untouched.", "removed": True}


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
    requested_home = (home or Path.home()).expanduser()
    canonical_home = Path.home().expanduser().resolve()
    home = requested_home.resolve()
    # Explicit home overrides are used by isolated tests; normal GUI calls use Path.home().
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
    # Refuse symlinks in both ownership roots and their target parents.
    for candidate in (paths["root"], paths["install"], prefix,
                      prefix / "pfx", prefix / "pfx/drive_c",
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
    """Return the XACT payload provenance for an explicitly installed, verified cache.

    Wyrd currently does not download or install redistributables automatically.
    A future importer may populate this cache only after validating Microsoft's
    signed package and every extracted file. Keeping this resolver fail-closed
    avoids ever guessing a Proton/Wine DLL as a Microsoft redistributable.
    """
    cache_root = managed_paths(home)["root"] / "cache" / "xact"
    paths = managed_paths(home)
    for candidate in (paths["root"], cache_root.parent, cache_root):
        if candidate.is_symlink():
            return {}
    required = {
        "xact": cache_root / "xactengine3_7.dll",
        "xact_x64": cache_root / "xactengine3_7_x64.dll",
    }
    found = {}
    for key, path in required.items():
        if path.is_symlink() or not path.is_file():
            continue
        try:
            if path.read_bytes()[:2] != b"MZ":
                continue
            found[key] = {"source": str(path.resolve(strict=True)), "sha256": _sha256_file(path)}
        except OSError:
            continue
    return found

def inspect_audio_compatibility(home: Path | None = None) -> dict:
    """Inspect XACT/XAudio DLLs in the verified Wyrd prefix without mutation."""
    try:
        prefix, _ownership = _validated_wyrd_audio_prefix(home)
        windows = prefix / "pfx/drive_c/windows"
        dlls = {
            "xact": ("syswow64", "xactengine3_7.dll"),
            "xact_x64": ("system32", "xactengine3_7.dll"),
            "xaudio2": ("syswow64", "xaudio2_7.dll"),
            "xaudio2_x64": ("system32", "xaudio2_7.dll"),
            "x3daudio": ("syswow64", "x3daudio1_7.dll"),
            "x3daudio_x64": ("system32", "x3daudio1_7.dll"),
            "xapofx": ("syswow64", "xapofx1_5.dll"),
            "xapofx_x64": ("system32", "xapofx1_5.dll"),
        }
        components = {}
        for key, (arch_dir, filename) in dlls.items():
            target = windows / arch_dir / filename
            if target.is_symlink() or target.exists() and not target.is_file():
                raise RuntimeError(f"Unsafe XACT/XAudio target: {target}")
            components[key] = {
                "path": str(target),
                "state": "present" if target.is_file() else "missing",
                "sha256": _sha256_file(target) if target.is_file() else None,
            }
        state = inspect_wyrd_installation(home)
        proton_root = Path(state.get("metadata", {}).get("proton", ""))
        payloads = _resolve_xact_dlls(home)
        runtime_paths = []
        for path in (proton_root / "files/lib64/wine", proton_root / "files/lib/wine",
                     proton_root / "dist/lib64/wine"):
            if path.is_dir():
                runtime_paths.append(str(path))
        return {
            "ok": True, "safe": True, "prefix": str(prefix),
            "components": components,
            "runtime": {
                "proton_library_paths": runtime_paths,
                "faudio_ffmpeg_support": "unknown; needs live runtime verification",
            },
            "repair_recommended": any(
                components[name]["state"] == "missing" for name in ("xact", "xact_x64")
            ),
            "repair_available": set(payloads) == {"xact", "xact_x64"},
            "runtime_verified": False,
            "runtime_note": "File presence is not proof of dialogue audio; test an NPC conversation in Skyrim.",
            "message": "Wyrd prefix ownership verified; XACT and XAudio2 files inspected.",
        }
    except (OSError, RuntimeError, ValueError) as error:
        return {
            "ok": False, "safe": False, "prefix": None, "components": {},
            "repair_recommended": False, "runtime_verified": False,
            "message": str(error),
        }


def repair_audio_compatibility(home: Path | None = None, confirmed: bool = False) -> dict:
    """Install verified cached Microsoft XACT DLLs into Wyrd's prefix only.

    Existing Wine/user files are never replaced; changes are additive, journaled,
    hash-verified and rolled back if post-write verification fails.
    """
    if not confirmed:
        return {"ok": False, "modified": False,
                "message": "Repair requires explicit user confirmation."}
    try:
        prefix, _ownership = _validated_wyrd_audio_prefix(home)
        report = inspect_audio_compatibility(home)
        if not report.get("safe"):
            raise RuntimeError(report.get("message", "Wyrd prefix ownership check failed."))
        sources = _resolve_xact_dlls(home)
        if set(sources) != {"xact", "xact_x64"}:
            raise RuntimeError(
                "No verified Microsoft XACT payload cache is available. "
                "Wyrd did not download or install DLLs and no files were changed."
            )
        windows = prefix / "pfx/drive_c/windows"
        targets = {
            "xact": windows / "syswow64" / "xactengine3_7.dll",
            "xact_x64": windows / "system32" / "xactengine3_7.dll",
        }
        for target in targets.values():
            if target.is_symlink() or not target.parent.is_dir() or target.parent.is_symlink():
                raise RuntimeError(f"Unsafe XACT target path; no files were changed: {target}")
            if target.exists():
                raise RuntimeError(
                    f"XACT target already exists; Wyrd will not overwrite a Wine or user DLL: {target}"
                )
        # Stage and verify both DLLs before changing either target.
        staged_root = prefix / ".audio-repair-staging"
        staged_root.mkdir(mode=0o700, exist_ok=True)
        staged = {}
        for key, target in targets.items():
            source = Path(sources[key]["source"]).resolve(strict=True)
            if not source.is_file() or source.is_symlink():
                raise RuntimeError(f"Unsafe or missing Proton XACT source: {source}")
            stage_file = staged_root / f"{key}.dll"
            shutil.copy2(source, stage_file)
            if stage_file.read_bytes()[:2] != b"MZ":
                raise RuntimeError(f"Extracted XACT payload is not a PE DLL: {source}")
            if _sha256_file(stage_file) != sources[key]["sha256"]:
                raise RuntimeError(f"Staged XACT DLL hash mismatch for {key}.")
            staged[key] = stage_file
        log_path = prefix / "audio-repair.log"
        journal = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "managed_prefix": str(prefix),
            "changes": [],
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
                })
            verification = inspect_audio_compatibility(home)
            if not verification.get("safe") or any(
                verification["components"][key]["sha256"] != sources[key]["sha256"]
                for key in targets
            ):
                raise RuntimeError("Post-repair XACT verification failed.")
        except Exception:
            # Roll back only the exact files installed by this operation.
            for key in reversed(modified):
                target = targets[key]
                if target.is_file() and _sha256_file(target) == sources[key]["sha256"]:
                    target.unlink()
            raise
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(journal, indent=2) + "\n")
        return {
            "ok": True, "modified": bool(modified), "verified": True,
            "runtime_verified": False, "log_path": str(log_path),
            "message": "Microsoft XACT DLLs were installed and file hashes verified in Wyrd's prefix only. Skyrim dialogue audio is NOT runtime-verified.",
        }
    except (OSError, RuntimeError, ValueError) as error:
        return {"ok": False, "modified": False, "verified": False,
                "runtime_verified": False, "message": str(error)}
    finally:
        if "staged_root" in locals():
            shutil.rmtree(staged_root, ignore_errors=True)

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


def write_settings(home: Path | None, proton_path: Path, game_path: Path,
                   xaudio2_compat: bool = False) -> None:
    paths = managed_paths(home)
    paths["config"].mkdir(parents=True, exist_ok=True)
    temporary = paths["settings"].with_suffix(".tmp")
    temporary.write_text(json.dumps({
        "proton": str(proton_path.resolve()), "game_path": str(game_path.resolve()),
        "xaudio2_compat": bool(xaudio2_compat),
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
    return result

def _instance_roots(prefix_root: Path, install_root: Path | None = None) -> list[Path]:
    """Return known MO2 instance locations inside Wyrd's managed environment.

    MO2 portable mode stores ModOrganizer.ini beside ModOrganizer.exe;
    instance-manager layouts may also keep named instances under instances/.
    """
    users = prefix_root / "pfx/drive_c/users"
    roots: list[Path] = []
    if users.is_dir():
        try:
            user_dirs = [entry for entry in users.iterdir() if entry.is_dir()]
        except OSError:
            user_dirs = []
        for user_dir in user_dirs:
            candidates = (
                # MO2 global instances are direct children of ModOrganizer.
                user_dir / "AppData/Local/ModOrganizer",
                user_dir / "AppData/Roaming/ModOrganizer",
                user_dir / "Documents/Modding/ModOrganizer",
                # Legacy/alternate nested layouts remain inventory-only.
                user_dir / "AppData/Local/ModOrganizer/instances",
                user_dir / "AppData/Roaming/ModOrganizer/instances",
                user_dir / "Documents/Modding/ModOrganizer/instances",
            )
            roots.extend(path for path in candidates if path.is_dir())

    # Restrict portable discovery to the explicitly supplied Wyrd-owned install
    # root. The root itself may be the active portable instance; instances/
    # is also supported for named portable instances. No arbitrary paths are read.
    if install_root is not None:
        install = Path(install_root).expanduser()
        if install.is_dir():
            roots.append(install)
            portable_instances = install / "instances"
            if portable_instances.is_dir():
                roots.append(portable_instances)
    return roots


def inspect_mo2_instances(prefix_root: Path, install_root: Path | None = None,
                          require_managed: bool = True) -> dict:
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
    for root in _instance_roots(prefix_root, install_root):
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
                "selected_profile": profile if profile_verified else "",
                "selected_profile_candidate": profile,
                "selected_profile_path": str(profile_dir) if profile_dir else "",
                "selected_profile_status": profile_status,
                "storage": {
                    "base": {"path": str(base_path), "status": base_status},
                    "profiles": {"path": profiles_path, "status": profiles_status},
                    "mods": {"path": mods_path, "status": mods_status},
                    "downloads": {"path": downloads_path, "status": downloads_status},
                },
                "is_skyrim_se": is_skyrim,
                "executables": executables,
            })

    instances = sorted(discovered, key=lambda item: item["name"].casefold())
    skyrim_instances = [item for item in instances if item["is_skyrim_se"]]
    return {
        "ok": True, "managed": bool(require_managed),
        "instances": instances,
        "skyrim_instances": skyrim_instances,
        "errors": errors,
        "message": (
            f"Found {len(instances)} MO2 instance(s), including "
            f"{len(skyrim_instances)} identified Skyrim SE instance(s)."
            if instances else "No MO2 instances were found in the known prefix locations."
        ),
    }


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


def inspect_skyrim_launch_targets(prefix_root: Path, game_path: Path, instance_name: str | None = None,
                                 install_root: Path | None = None,
                                 require_managed: bool = True) -> dict:
    """Validate Skyrim/SKSE targets in a managed or explicitly supplied Steam prefix."""
    inventory = inspect_mo2_instances(
        prefix_root, install_root, require_managed=require_managed,
    )
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
        # A detected game binary is not automatically a configured MO2 executable.
        # MO2 run --executable resolves the title from customExecutables.
        targets.extend(candidates)
        for entry in instance.get("executables", []):
            title = entry.get("title", "").strip()
            binary = _resolve_windows_path(entry.get("binary", ""), prefix_root, game_path)
            if not title or not binary or not binary.is_file():
                continue
            name = binary.name.casefold()
            # The configured title is user-editable metadata. Never treat a
            # title containing "SKSE" as proof that the binary is SKSE.
            is_skse = name in {"skse64_loader.exe", "skse_loader.exe"}
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
    diagnostic = None if unique else ("unresolved_game_path" if warnings else "missing_executable_entry")
    message = (
        f"Verified {len(unique)} Skyrim/SKSE launch target(s) from MO2."
        if unique else
        ("No configured Skyrim or SKSE executable entry could be verified. "
         "Check the MO2 instance's game path and executable entries.")
    )
    return {
        "ok": bool(unique),
        "targets": list(unique.values()),
        "warnings": warnings,
        "diagnostic": diagnostic,
        "message": message,
    }


def launch_skyrim_via_mo2(executable_title: str, instance_name: str,
                          home: Path | None = None, log_path: Path | None = None,
                          expected_binary: Path | str | None = None,
                          audio_mode: str = "default",
                          proton_path: Path | None = None,
                          game_path: Path | None = None,
                          prefix_root: Path | None = None,
                          install_root: Path | None = None,
                          require_managed: bool = True) -> dict:
    """Start an MO2 configured-executable handoff with explicit runtime paths.

    Supplying prefix_root/install_root selects the already-initialized Steam
    AppID prefix. The legacy Wyrd prefix remains supported for compatibility.
    """
    home = (home or Path.home()).expanduser()
    title = executable_title.strip()
    instance = instance_name.strip()
    if not title or not instance:
        return {"ok": False, "message": "An MO2 instance and configured executable title are required."}
    if any(char in title + instance for char in ("\x00", "\r", "\n")):
        return {"ok": False, "message": "The selected MO2 instance or executable title is invalid."}

    explicit_prefix = prefix_root is not None or install_root is not None
    if explicit_prefix and (prefix_root is None or install_root is None or proton_path is None or game_path is None):
        return {"ok": False, "message": "Steam-prefix launch requires explicit Proton, game, prefix and MO2 install paths."}
    if explicit_prefix:
        proton = Path(proton_path).expanduser()
        game = Path(game_path).expanduser()
        prefix = Path(prefix_root).expanduser()
        install = Path(install_root).expanduser()
        if prefix.is_symlink() or not prefix.is_dir():
            return {"ok": False, "message": "Skyrim's Steam compatdata prefix is missing or unsafe."}
        try:
            prefix.resolve(strict=True).relative_to(prefix.parent.resolve(strict=True))
        except (OSError, ValueError, RuntimeError):
            return {"ok": False, "message": "The selected compatdata prefix is outside its expected Steam compatdata directory."}
        if install.is_symlink() or not install.is_dir() or not (install / "ModOrganizer.exe").is_file():
            return {"ok": False, "message": "The selected MO2 install directory is missing or unsafe."}
        state_ok = True
        steam_root = prefix.parents[2]
    else:
        state = inspect_wyrd_installation(home)
        if not state.get("installed") or not state.get("prefix_ready"):
            return {"ok": False, "message": "Wyrd-managed MO2 and its dedicated prefix must be ready before launching Skyrim."}
        metadata = state.get("metadata", {})
        proton = Path(metadata.get("proton", ""))
        game = Path(metadata.get("game_path", ""))
        paths = managed_paths(home)
        prefix, install = paths["prefix"], paths["install"]
        steam_root = Path(metadata.get("steam_root", ""))
        if not proton.is_dir() or not game.is_dir():
            return {"ok": False, "message": "The saved Proton runtime or Skyrim directory is missing."}
        state_ok = True

    if not proton.is_dir() or not game.is_dir():
        return {"ok": False, "message": "Selected Proton runtime or Skyrim directory is unavailable."}
    validation = inspect_proton_build(proton)
    if not validation.get("ok"):
        return {"ok": False, "message": validation.get("message", "Selected Proton build is invalid.")}

    prefix_check = inspect_mo2_instances(prefix, install, require_managed=require_managed if explicit_prefix else True)
    instance_match = next(
        (item for item in prefix_check.get("skyrim_instances", []) if item["name"] == instance),
        None,
    ) if prefix_check.get("ok") else None
    if not instance_match:
        return {"ok": False, "message": "The selected Skyrim instance is no longer present in the selected MO2 prefix."}
    target_state = inspect_skyrim_launch_targets(
        prefix, game, instance_name=instance, install_root=install,
        require_managed=require_managed if explicit_prefix else True,
    )
    expected_path = None
    if expected_binary is not None:
        try:
            expected_path = Path(expected_binary).expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            return {"ok": False, "message": "The selected Skyrim executable is no longer available at its verified path."}
    matching_targets = [
        item for item in target_state.get("targets", [])
        if item["title"] == title
        and (expected_path is None or Path(item["binary"]).resolve() == expected_path)
    ]
    if len(matching_targets) != 1:
        return {
            "ok": False,
            "message": (
                "The selected Skyrim executable is no longer verified at the selected path."
                if expected_path is not None else
                "The selected executable title is ambiguous or is no longer verified in this MO2 instance."
            ),
        }
    target_match = matching_targets[0]
    if audio_mode not in {"default", "xaudio2_compat"}:
        return {"ok": False, "message": "Unknown audio compatibility test mode."}
    env = _runtime_environment(proton, steam_root, prefix)
    # Prefix identity alone is not Wyrd attribution: Steam-launched Skyrim
    # shares this AppID prefix. This marker is inherited by the MO2 handoff.
    env["WYRD_LAUNCHED"] = "1"
    if audio_mode == "xaudio2_compat":
        current_overrides = env.get("WINEDLLOVERRIDES", "")
        overrides = [
            item for item in current_overrides.split(";")
            if item and not item.lower().startswith("xaudio2_7=")
        ]
        overrides.append("xaudio2_7=n,b")
        env["WINEDLLOVERRIDES"] = ";".join(overrides)

    command = [
        str(proton / "proton"), "run", str(install / "ModOrganizer.exe"),
        "--instance", instance, "run", "--executable", title,
    ]
    diagnostics = Path(log_path) if log_path else managed_paths(home)["root"] / "logs" / "skyrim-launch.log"
    try:
        diagnostics.parent.mkdir(parents=True, exist_ok=True)
        with diagnostics.open("a", encoding="utf-8") as log:
            log.write("\n=== Wyrd Skyrim launch handoff ===\n")
            log.write(f"timestamp: {datetime.now(timezone.utc).isoformat()}\n")
            log.write(f"instance: {instance}\nexecutable: {title}\nbinary: {target_match['binary']}\naudio_mode: {audio_mode}\n")
            log.write("command: " + json.dumps(command) + "\n")
            log.write("audio_override: " + ("WINEDLLOVERRIDES=" + env.get("WINEDLLOVERRIDES", "") if audio_mode == "xaudio2_compat" else "none; Proton defaults retained") + "\n")
            log.write("stdout/stderr are redirected to this diagnostic log.\n")
            log.flush()
            process = subprocess.Popen(
                command, cwd=str(install), env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            log.write(f"wrapper_pid: {process.pid}\n")
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
        env_readable = True
        env_error = None
        try:
            env_data = (entry / "environ").read_bytes()
        except OSError as error:
            # /proc/<pid>/environ may be restricted by ptrace/Yama or process
            # ownership. Do not misreport an inspection failure as an unset
            # variable.
            env_data = b""
            env_readable = False
            env_error = str(error)
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
        # A failed /proc/<pid>/environ read is unknown, not evidence that a
        # process belongs to Wyrd or that a launch marker is absent. Keep
        # prefix attribution separate because Steam and Wyrd share AppID 489830.
        prefix_attributed = bool(
            env_readable and prefix and (
                any(part.startswith("STEAM_COMPAT_DATA_PATH=") and
                    str(Path(part.split("=", 1)[1]).expanduser().resolve()) == prefix
                    for part in env_parts)
                or any(arg == f"STEAM_COMPAT_DATA_PATH={prefix}" for arg in argv)
            )
        )
        wyrd_marker = env_readable and any(
            part == "WYRD_LAUNCHED=1" for part in env_parts
        )
        attributed = bool(prefix_attributed and wyrd_marker)
        # Only expose a small, non-secret audio-related environment allowlist.
        # This reads the actual Skyrim/SKSE process environment, not the Proton wrapper.
        audio_runtime = {}
        for key in ("WINEDLLOVERRIDES", "PULSE_LATENCY_MSEC"):
            value = next((part.split("=", 1)[1] for part in env_parts
                         if part.startswith(key + "=")), None)
            audio_runtime[key] = value if env_readable else None
        # Read-only observation of DLL mappings in the actual game process.
        # A mapped library is not proof that its audio path works.
        mapped_audio = {}
        maps_readable = True
        maps_error = None
        try:
            maps_text = (entry / "maps").read_text(encoding="utf-8", errors="replace")
            map_paths = set()
            for line in maps_text.splitlines():
                fields = line.split(None, 5)
                if len(fields) == 6 and fields[5].startswith("/"):
                    map_paths.add(fields[5].replace("\\040", " "))
            normalized_maps = {Path(value).name.casefold(): value for value in map_paths}
            for library in (
                "xaudio2_7.dll", "xaudio2_8.dll", "xaudio2_9.dll",
                "xactengine3_7.dll", "x3daudio1_7.dll", "xapofx1_5.dll",
                "faudio.dll",
            ):
                mapped_audio[library] = normalized_maps.get(library)
        except OSError as error:
            maps_readable = False
            maps_error = str(error)
            mapped_audio = {}
        detected.append({
            "pid": int(entry.name), "binary": matches[0], "argv": argv,
            "audio_module_mappings": mapped_audio,
            "audio_module_mappings_readable": maps_readable,
            "audio_module_mappings_error": maps_error,
            "start_time": _proc_start_time(int(entry.name), Path(proc_root)),
            "attributed_to_wyrd": attributed, "prefix_attributed": prefix_attributed,
            "prefix": prefix if prefix_attributed else None,
            "audio_runtime_environment": audio_runtime,
            "audio_runtime_environment_readable": env_readable,
            "audio_runtime_environment_error": env_error,
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
