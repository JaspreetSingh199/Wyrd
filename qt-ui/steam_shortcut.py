"""Reversible desktop launcher integration for a Steam-added Wyrd bootstrap.

This writes a XDG desktop application entry. The user adds that entry to Steam
using its Add a Non-Steam Game UI. This module never edits shortcuts.vdf, Skyrim's
real AppID manifest, compatibility settings, or an existing Steam shortcut.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path

from skyrim_detection import find_steam_roots

SHORTCUT_NAME = "Wyrd - Skyrim through MO2"
DESKTOP_FILE = "wyrd-skyrim-mo2.desktop"
MARKER_NAME = ".wyrd-steam-shortcut.json"


def _desktop_exec_quote(value: str) -> str:
    """Quote one Exec argument using Desktop Entry, not shell, escaping rules."""
    escaped = json.dumps(value, ensure_ascii=False)[1:-1]
    escaped = escaped.replace("$", r"\$").replace("`", r"\`").replace("%", "%%")
    return '"' + escaped + '"'


def _desktop_entry(bootstrap: Path, working_dir: Path) -> str:
    # XDG Desktop Entry Exec syntax is not shell syntax: quote each argument
    # using double quotes and desktop-entry escapes, including for paths with spaces.
    # Use run.sh so Steam uses the same venv-aware native launch path.
    # A system Python may not have PySide6, while run.sh provisions the project venv.
    run_script = working_dir / "run.sh"
    exec_line = " ".join(
        _desktop_exec_quote(value)
        for value in ("bash", str(run_script.resolve()), "--steam-bootstrap")
    )
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={SHORTCUT_NAME}\n"
        f"Exec={exec_line}\n"
        f"Path={working_dir.resolve()}\n"
        "Terminal=false\n"
        "Categories=Game;\n"
        "StartupNotify=true\n"
        "Comment=Start Wyrd and open the configured Skyrim MO2 instance\n"
        "X-Wyrd-Managed=true\n"
    )


def install_steam_shortcut(home: Path | None = None) -> dict:
    """Create Wyrd's dedicated native desktop launcher without clobbering files."""
    home = (home or Path.home()).expanduser()
    roots = find_steam_roots(home)
    if not roots:
        return {"ok": False, "message": "Steam installation was not detected."}
    data_home = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")).expanduser()
    app_dir = data_home / "applications"
    app_dir.mkdir(parents=True, exist_ok=True)
    desktop = app_dir / DESKTOP_FILE
    marker = app_dir / MARKER_NAME
    if desktop.exists() or desktop.is_symlink() or marker.exists():
        if not marker.is_file():
            return {"ok": False, "message": f"Refusing to overwrite an unowned launcher: {desktop}"}
        try:
            record = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"ok": False, "message": "Wyrd shortcut ownership marker is malformed; no changes made."}
        if record.get("managed_by") != "Wyrd" or record.get("desktop_file") != str(desktop):
            return {"ok": False, "message": "Shortcut ownership marker does not match this launcher; no changes made."}
        if not desktop.is_file() or desktop.is_symlink():
            return {"ok": False, "message": "Wyrd ownership marker exists but launcher is missing or unsafe; no changes made."}
        expected_hash = record.get("desktop_sha256")
        if not expected_hash or hashlib.sha256(desktop.read_bytes()).hexdigest() != expected_hash:
            return {"ok": False, "message": "Existing launcher content changed; refusing to treat it as Wyrd-owned."}
        return {"ok": True, "already_installed": True, "desktop_file": str(desktop),
                "message": "Wyrd's Steam launcher is already installed."}
    bootstrap = Path(__file__).resolve().with_name("steam_bootstrap.py")
    content = _desktop_entry(bootstrap, Path(__file__).resolve().parent)
    temporary = desktop.with_suffix(".desktop.tmp")
    created_desktop = False
    try:
        temporary.write_text(content, encoding="utf-8")
        # Exclusive create prevents replacing a file created after the preflight.
        descriptor = os.open(desktop, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        created_desktop = True
        marker_payload = json.dumps({
            "managed_by": "Wyrd", "desktop_file": str(desktop),
            "bootstrap": str(bootstrap), "run_script": str(run_script.resolve()),
            "name": SHORTCUT_NAME,
            "desktop_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }, indent=2)
        marker_fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(marker_fd, "w", encoding="utf-8") as handle:
            handle.write(marker_payload)
        temporary.unlink(missing_ok=True)
    except OSError as error:
        if created_desktop:
            desktop.unlink(missing_ok=True)
        # Remove a marker only if this operation created it. O_EXCL above
        # ensures an existing user-owned marker is never replaced.
        if "marker_fd" in locals():
            marker.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
        return {"ok": False, "message": f"Could not install Wyrd's Steam launcher: {error}"}
    return {"ok": True, "already_installed": False, "desktop_file": str(desktop),
            "message": "Installed Wyrd's native Steam launcher using qt-ui/run.sh. Add it to Steam as a non-Steam game and leave Force Compatibility disabled; existing Steam entries were not changed."}



def inspect_steam_shortcut(home: Path | None = None) -> dict:
    """Read-only status for the Wyrd-owned Steam-import desktop launcher."""
    home = (home or Path.home()).expanduser()
    data_home = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")).expanduser()
    app_dir = data_home / "applications"
    desktop = app_dir / DESKTOP_FILE
    marker = app_dir / MARKER_NAME
    if not desktop.exists() and not marker.exists():
        return {"installed": False, "owned": False, "message": "No Wyrd Steam desktop launcher is installed."}
    if not marker.is_file():
        return {"installed": desktop.is_file(), "owned": False,
                "message": "A launcher file exists without Wyrd ownership metadata; it will not be modified."}
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"installed": desktop.is_file(), "owned": False,
                "message": "Wyrd launcher ownership metadata is unreadable."}
    if record.get("managed_by") != "Wyrd" or record.get("desktop_file") != str(desktop):
        return {"installed": desktop.is_file(), "owned": False,
                "message": "Launcher ownership metadata does not match this Wyrd entry."}
    if not desktop.is_file() or desktop.is_symlink():
        return {"installed": False, "owned": True,
                "message": "Wyrd launcher is missing or its path is unsafe."}
    expected_hash = record.get("desktop_sha256", "")
    actual_hash = hashlib.sha256(desktop.read_bytes()).hexdigest()
    if not expected_hash or actual_hash != expected_hash:
        return {"installed": True, "owned": False,
                "message": "Wyrd launcher content was modified; refusing to manage it."}
    return {"installed": True, "owned": True, "desktop_file": str(desktop),
            "message": "Wyrd native Steam launcher is installed and ownership-verified."}


def remove_steam_shortcut(home: Path | None = None) -> dict:
    """Remove only the exact desktop entry and marker recorded as Wyrd-owned."""
    home = (home or Path.home()).expanduser()
    data_home = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")).expanduser()
    app_dir = data_home / "applications"
    desktop = app_dir / DESKTOP_FILE
    marker = app_dir / MARKER_NAME
    if not marker.is_file():
        return {"ok": False, "message": "No Wyrd ownership marker found; nothing removed."}
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "message": "Ownership marker is unreadable; nothing removed."}
    if record.get("managed_by") != "Wyrd" or record.get("desktop_file") != str(desktop):
        return {"ok": False, "message": "Ownership marker mismatch; nothing removed."}
    if desktop.is_symlink() or (desktop.exists() and not desktop.is_file()):
        return {"ok": False, "message": "Launcher path is not a regular file; nothing removed."}
    if desktop.is_file():
        expected_hash = record.get("desktop_sha256")
        if not expected_hash:
            return {"ok": False, "message": "Ownership marker has no content hash; refusing to remove an unverified launcher."}
        actual_hash = __import__("hashlib").sha256(desktop.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            return {"ok": False, "message": "Launcher content changed since installation; refusing to remove it."}
        desktop.unlink()
    marker.unlink()
    return {"ok": True, "message": "Removed Wyrd's owned desktop launcher. Steam's other entries and game configuration were untouched."}
