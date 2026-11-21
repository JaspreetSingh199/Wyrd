"""Native host bootstrap used by Wyrd's optional Steam shortcut.

Steam starts this program as a native Linux process. The bootstrap then
requests that the existing Wyrd Qt application opens the saved MO2 instance.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
import json
import os
from datetime import datetime, timezone


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wyrd Steam-native bootstrap")
    parser.add_argument("--steam-bootstrap", action="store_true",
                        help="start Wyrd and open its configured MO2 instance")
    args = parser.parse_args(argv)
    if not args.steam_bootstrap:
        parser.error("--steam-bootstrap is required")
    script_dir = Path(__file__).resolve().parent
    main_script = script_dir / "main.py"
    if not main_script.is_file():
        print(f"Wyrd application is missing: {main_script}", file=sys.stderr)
        return 2
    python = Path(sys.executable)
    command = [str(python), str(main_script), "--steam-bootstrap"]
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")).expanduser()
    log_path = data_home / "wyrd" / "logs" / "steam-bootstrap-environment.json"
    env_keys = (
        "SteamAppId", "SteamGameId", "STEAM_COMPAT_APP_ID",
        "STEAM_COMPAT_CLIENT_INSTALL_PATH", "STEAM_COMPAT_DATA_PATH",
        "STEAM_COMPAT_RUNTIME", "STEAM_RUNTIME", "STEAM_RUNTIME_LIBRARY_PATH",
        "LD_LIBRARY_PATH", "LD_PRELOAD", "XDG_RUNTIME_DIR", "XDG_DATA_DIRS",
        "PULSE_SERVER", "PULSE_RUNTIME_PATH", "PIPEWIRE_REMOTE",
        "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "WAYLAND_DISPLAY",
        "SDL_AUDIODRIVER", "ALSA_CONFIG_PATH", "WINEDLLOVERRIDES",
    )
    snapshot = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "launched_from_steam": bool(os.environ.get("SteamAppId") or os.environ.get("SteamGameId")),
        "environment": {key: os.environ[key] for key in env_keys if key in os.environ},
        "python": str(python),
        "main_script": str(main_script),
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    except OSError as error:
        print(f"Could not write Steam environment diagnostics: {error}", file=sys.stderr)
    try:
        completed = subprocess.run(command, cwd=script_dir, check=False)
    except OSError as error:
        print(f"Could not start Wyrd: {error}", file=sys.stderr)
        return 1
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
