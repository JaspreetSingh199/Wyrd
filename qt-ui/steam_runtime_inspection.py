"""Read-only diagnostics for Steam non-Steam shortcut and Proton runtime state.

No Steam configuration is edited. In particular, shortcuts.vdf and config.vdf
are read only: the Steam client is authoritative for these files while running.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path
from typing import Any

from skyrim_detection import find_steam_roots


def _decode_vdf_string(data: bytes, offset: int) -> tuple[str, int]:
    end = data.find(b"\x00", offset)
    if end < 0:
        raise ValueError("unterminated VDF string")
    return data[offset:end].decode("utf-8", errors="replace"), end + 1


def _parse_binary_vdf(data: bytes) -> dict[str, Any]:
    """Parse the small subset of Valve binary VDF used by shortcuts.vdf.

    Supports nested objects, strings, and 32-bit integers. Unknown scalar
    types are rejected rather than skipped, keeping diagnostics fail-closed.
    """
    if not data or data[0] != 0x00:
        raise ValueError("binary VDF must begin with a root object")
    cursor = 1
    root_key, cursor = _decode_vdf_string(data, cursor)
    if root_key != "shortcuts":
        raise ValueError(f"unexpected binary VDF root object: {root_key!r}")

    def parse_object(stop_on_end: bool = False) -> dict[str, Any]:
        nonlocal cursor
        result: dict[str, Any] = {}
        while cursor < len(data):
            kind = data[cursor]
            cursor += 1
            if kind == 0x08:
                if stop_on_end:
                    return result
                raise ValueError("unexpected VDF object terminator")
            key, cursor = _decode_vdf_string(data, cursor)
            if kind == 0x00:
                result[key] = parse_object(stop_on_end=True)
            elif kind == 0x01:
                result[key], cursor = _decode_vdf_string(data, cursor)
            elif kind == 0x02:
                if cursor + 4 > len(data):
                    raise ValueError("truncated VDF integer")
                result[key] = struct.unpack_from("<i", data, cursor)[0]
                cursor += 4
            else:
                raise ValueError(f"unsupported VDF type 0x{kind:02x}")
        if stop_on_end:
            raise ValueError("unterminated VDF object")
        return result

    parsed = parse_object(stop_on_end=True)
    if cursor != len(data):
        raise ValueError("unexpected trailing data after root object")
    return {"shortcuts": parsed}


def _signed_appid(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    unsigned = number & 0xFFFFFFFF
    return unsigned - 0x100000000 if unsigned & 0x80000000 else unsigned


def inspect_steam_shortcuts(home: Path | None = None) -> dict:
    """Read-only inventory of Wyrd shortcut records in detected Steam userdata."""
    home = (home or Path.home()).expanduser()
    results = []
    warnings = []
    checked_files = []
    detected_roots = [str(root) for root in find_steam_roots(home)]
    for root in map(Path, detected_roots):
        userdata = root / "userdata"
        if not userdata.is_dir():
            continue
        try:
            account_dirs = sorted(
                (entry for entry in userdata.iterdir() if entry.is_dir() and entry.name.isdigit()),
                key=lambda entry: entry.name,
            )
        except OSError as error:
            warnings.append(f"{userdata}: {error}")
            continue
        for account_dir in account_dirs:
            shortcuts = account_dir / "config" / "shortcuts.vdf"
            if not shortcuts.is_file():
                continue
            checked_files.append(str(shortcuts))
            try:
                parsed = _parse_binary_vdf(shortcuts.read_bytes())
                entries = parsed.get("shortcuts", {})
                if not isinstance(entries, dict):
                    raise ValueError("shortcuts root is not an object")
                for key, value in entries.items():
                    if not isinstance(value, dict):
                        continue
                    name = str(value.get("appname", value.get("AppName", "")))
                    exe = str(value.get("exe", value.get("Exe", "")))
                    if "wyrd" not in name.casefold() and "wyrd" not in exe.casefold():
                        continue
                    results.append({
                        "steam_root": str(root),
                        "account_id": account_dir.name,
                        "name": name,
                        "exe": exe,
                        "start_dir": value.get("StartDir", value.get("startdir")),
                        "appid": _signed_appid(value.get("appid", value.get("AppId"))),
                        "launch_options": value.get("LaunchOptions", value.get("launchoptions", "")),
                    })
            except (OSError, ValueError) as error:
                warnings.append(f"{shortcuts}: {error}")
    return {
        "ok": not warnings,
        "shortcuts": results,
        "warnings": warnings,
        "detected_steam_roots": detected_roots,
        "checked_shortcut_files": checked_files,
        "message": (
            f"Found {len(results)} Wyrd shortcut record(s) across {len(checked_files)} "
            f"shortcuts.vdf file(s)."
            if not warnings else
            "One or more Steam shortcut files could not be parsed; no files were modified."
        ),
    }


def _unsigned_appid(value: Any) -> int | None:
    try:
        return int(value) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return None


def inspect_steam_compat_mappings(home: Path | None = None) -> dict:
    """Fail closed until Steam text-VDF parsing has complete fixture coverage.

    The shortcut AppID is still reported from binary shortcuts.vdf, but this
    routine deliberately does not guess the CompatToolMapping block.
    """
    shortcuts = inspect_steam_shortcuts(home)
    appids = sorted({
        _unsigned_appid(item.get("appid"))
        for item in shortcuts.get("shortcuts", [])
        if item.get("appid") is not None
    })
    return {
        "ok": not shortcuts.get("warnings"),
        "mappings": [],
        "shortcut_appids": appids,
        "warnings": list(shortcuts.get("warnings", [])),
        "message": (
            "Steam compatibility mappings are not parsed yet; config.vdf was left untouched. "
            f"Detected unsigned shortcut AppID(s): {', '.join(map(str, appids)) or 'none'}."
        ),
    }


def steam_runtime_environment(environment: dict[str, str] | None = None) -> dict:
    """Summarize documented runtime indicators without changing process state."""
    env = os.environ if environment is None else environment
    keys = (
        "SteamAppId", "SteamGameId", "STEAM_COMPAT_APP_ID",
        "STEAM_COMPAT_DATA_PATH", "STEAM_COMPAT_CLIENT_INSTALL_PATH",
        "STEAM_COMPAT_RUNTIME", "STEAM_COMPAT_MOUNTS",
        "STEAM_RUNTIME", "STEAM_RUNTIME_LIBRARY_PATH", "LD_LIBRARY_PATH",
        "LD_PRELOAD", "PULSE_SERVER", "PULSE_RUNTIME_PATH",
        "PIPEWIRE_REMOTE", "XDG_RUNTIME_DIR",
    )
    identity = next((env.get(key) for key in ("SteamAppId", "SteamGameId", "STEAM_COMPAT_APP_ID")
                     if env.get(key, "").isdigit()), None)
    return {
        "steam_identity": identity,
        "steam_runtime_indicators": {
            key: env[key] for key in keys if key in env
        },
        "has_runtime_markers": bool(
            env.get("STEAM_COMPAT_RUNTIME") or env.get("STEAM_RUNTIME_LIBRARY_PATH")
        ),
        "has_pulse_server": bool(env.get("PULSE_SERVER")),
        "has_pipewire_remote": bool(env.get("PIPEWIRE_REMOTE")),
    }
