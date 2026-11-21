#!/usr/bin/env python3
"""Read-only Steam/Proton launch diagnostics for Wyrd's isolated experiment.

Only a narrow allowlist of environment variables is recorded; the full process
environment is never dumped because it may contain secrets.
"""
from __future__ import annotations

import json
from pathlib import Path
import time

PROCESS_HINTS = ("skyrim", "modorganizer", "proton", "wine")
ENV_ALLOWLIST = (
    "SteamAppId", "SteamGameId", "STEAM_COMPAT_APP_ID",
    "STEAM_COMPAT_DATA_PATH", "STEAM_COMPAT_CLIENT_INSTALL_PATH",
    "STEAM_RUNTIME", "STEAM_RUNTIME_LIBRARY_PATH", "PRESSURE_VESSEL_FILESYSTEMS",
    "PRESSURE_VESSEL_RUNTIME", "PROTON_LOG", "PROTON_NO_ESYNC", "PROTON_NO_FSYNC",
    "WINEDLLOVERRIDES", "WINEPREFIX", "DISPLAY", "WAYLAND_DISPLAY",
    "XDG_SESSION_TYPE", "SDL_AUDIODRIVER", "PULSE_SERVER",
)


def read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except (OSError, PermissionError):
        return ""


def process_info(pid: int) -> dict | None:
    root = Path("/proc") / str(pid)
    cmdline = read_text(root / "cmdline").replace("\0", " ").strip()
    if not cmdline:
        return None
    comm = read_text(root / "comm").strip()
    try:
        stat = read_text(root / "stat")
        # comm is parenthesized and can itself contain spaces; split after its final ')'.
        stat_tail = stat[stat.rfind(")") + 1:].split()
        ppid = int(stat_tail[1])  # state is index 0, parent PID is index 1
    except (ValueError, IndexError):
        ppid = None
    env_raw = read_text(root / "environ")
    env = {}
    for entry in env_raw.split("\0"):
        if "=" not in entry:
            continue
        key, value = entry.split("=", 1)
        if key in ENV_ALLOWLIST:
            env[key] = value
    return {"pid": pid, "ppid": ppid, "comm": comm, "cmdline": cmdline, "environment": env}


def main() -> None:
    proc = Path("/proc")
    candidates = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        info = process_info(int(entry.name))
        if info and any(hint in (info["comm"] + " " + info["cmdline"]).lower()
                        for hint in PROCESS_HINTS):
            candidates.append(info)

    for item in candidates:
        chain = []
        ppid = item["ppid"]
        visited = set()
        while ppid and ppid not in visited and len(chain) < 12:
            visited.add(ppid)
            parent = process_info(ppid)
            if not parent:
                break
            chain.append({"pid": parent["pid"], "ppid": parent["ppid"],
                        "comm": parent["comm"], "cmdline": parent["cmdline"]})
            ppid = parent["ppid"]
        item["ancestry"] = chain

    report = {
        "captured_at_epoch": time.time(),
        "capture_mode": "read-only; environment allowlist only",
        "processes": sorted(candidates, key=lambda x: x["pid"]),
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
