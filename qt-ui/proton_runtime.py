"""Proton runtime discovery and Wyrd-owned prefix lifecycle.

Only Wyrd's dedicated MO2 prefix is managed here. Skyrim's Steam compatdata
is not reset, adopted or modified by these operations.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from skyrim_detection import find_steam_roots, steam_libraries
from mo2_inspection import find_mo2_installations

APP_ID = "489830"
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
        name = item["name"].strip().lower()
        import re

        # Prefer Valve's own Proton releases, then GE-Proton, then other
        # compatibility-tool builds (CachyOS, SLR forks, TKG, etc.).
        is_ge = "ge-proton" in name or "proton-ge" in name or name.startswith("ge ")
        official_match = re.match(
            r"^proton(?:\s+\d+(?:\.\d+)*.*|\s*-\s*(?:experimental|hotfix))$",
            name,
        )
        if official_match:
            priority = 0
            # Prefer a stable numbered Valve release, then Experimental, then Hotfix.
            if "experimental" in name:
                subpriority = 1
            elif "hotfix" in name:
                subpriority = 2
            else:
                subpriority = 0
        elif is_ge:
            priority, subpriority = 1, 0
        else:
            priority, subpriority = 2, 0

        version = tuple(int(part) for part in re.findall(r"\d+", name)[-3:])
        return (priority, subpriority, tuple(-v for v in version), name)
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
    """Find the Steam client that owns the library containing this game.

    A game's library may be on a secondary mount, so its directory is not
    necessarily beneath the Steam client installation directory.
    """
    resolved_game = Path(game_path).expanduser().resolve(strict=True)
    for root in find_steam_roots(home):
        try:
            libraries = steam_libraries(root)
        except OSError:
            continue
        for steamapps in libraries:
            try:
                resolved_game.relative_to(steamapps.parent.resolve(strict=True))
                return root.resolve(strict=True)
            except (OSError, ValueError, RuntimeError):
                continue
    raise RuntimeError(
        "The detected game is not inside a library registered by the discovered Steam client. "
        "Wyrd will not guess which compatdata prefix belongs to it."
    )


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
        "[HKEY_LOCAL_MACHINE\\Software\\WOW6432Node\\Bethesda Softworks\\Skyrim Special Edition]\n"
        f'"Install path"="{escaped}"\n'
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
