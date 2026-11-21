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
    if not data:
        raise ValueError("empty VDF")
    cursor = 0

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

    return parse_object()


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
    for root in find_steam_roots(home):
        userdata = root / "userdata"
        if not userdata.is_dir():
            continue
        for account_dir in userdata.iterdir():
            shortcuts = account_dir / "config" / "shortcuts.vdf"
            if not shortcuts.is_file():
                continue
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
        "message": f"Found {len(results)} Wyrd shortcut record(s)." if not warnings else
                   "One or more Steam shortcut files could not be parsed; no files were modified.",
    }


def inspect_steam_compat_mappings(home: Path | None = None) -> dict:
    """Read only matching Steam CompatToolMapping blocks from config.vdf."""
    home = (home or Path.home()).expanduser()
    matches = []
    warnings = []
    for root in find_steam_roots(home):
        config = root / "config" / "config.vdf"
        if not config.is_file():
            continue
        try:
            # Keep the parser deliberately read-only and narrow: enumerate
            # shortcut appids already discovered in shortcuts.vdf, then report
            # exact numeric keys and nearby mapping names only.
            shortcut_state = inspect_steam_shortcuts(home)
            appids = {
                str(abs(item["appid"]))
                for item in shortcut_state["shortcuts"]
                if item.get("appid") is not None
            }
            text = config.read_text(encoding="utf-8", errors="replace")
            for appid in appids:
                marker = f'"{appid}"'
                start = text.find(marker)
                while start >= 0:
                    excerpt = text[start:text.find("\n\t\t\t}", start) + 5 if text.find("\n\t\t\t}", start) >= 0 else start + 500]
                    if "CompatToolMapping" in text[max(0, start - 5000):start]:
                        matches.append({"steam_root": str(root), "appid": appid, "excerpt": excerpt[:700]})
                        break
                    start = text.find(marker, start + len(marker))
        except OSError as error:
            warnings.append(f"{config}: {error}")
    return {"ok": not warnings, "mappings": matches, "warnings": warnings}


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
