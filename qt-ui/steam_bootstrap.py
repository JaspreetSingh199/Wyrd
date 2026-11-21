"""Native host bootstrap for Wyrd's optional Steam non-Steam shortcut.

This script runs natively under Steam. It records a narrow diagnostic snapshot
of Steam/runtime/audio environment variables and re-enters Wyrd through run.sh,
which provisions and uses the project's Qt virtual environment.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ENVIRONMENT_KEYS = (
    "SteamAppId", "SteamGameId", "STEAM_COMPAT_APP_ID",
    "STEAM_COMPAT_CLIENT_INSTALL_PATH", "STEAM_COMPAT_DATA_PATH",
    "STEAM_COMPAT_RUNTIME", "STEAM_RUNTIME", "STEAM_RUNTIME_LIBRARY_PATH",
    "STEAM_LINUX_RUNTIME", "STEAM_RUNTIME_PREFER_HOST_LIBRARIES",
    "LD_LIBRARY_PATH", "LD_PRELOAD", "XDG_RUNTIME_DIR", "XDG_DATA_DIRS",
    "PULSE_SERVER", "PULSE_RUNTIME_PATH", "PULSE_COOKIE", "PIPEWIRE_REMOTE",
    "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "WAYLAND_DISPLAY",
    "SDL_AUDIODRIVER", "ALSA_CONFIG_PATH", "WINEDLLOVERRIDES",
)


def environment_snapshot(environment: dict[str, str] | None = None) -> dict:
    """Return only an explicit allow-list; never serialize the full environment."""
    env = os.environ if environment is None else environment
    identity = next(
        (env.get(key, "").strip() for key in ("SteamAppId", "SteamGameId", "STEAM_COMPAT_APP_ID")
         if env.get(key, "").strip().isdigit()),
        None,
    )
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "launched_from_steam": identity is not None,
        "steam_shortcut_app_id": identity,
        "xdg_data_home": env.get("XDG_DATA_HOME") or str(Path.home() / ".local/share"),
        "snapshot_path": str(
            Path(env.get("XDG_DATA_HOME", Path.home() / ".local/share")).expanduser()
            / "wyrd" / "logs" / "steam-bootstrap-environment.json"
        ),
        "environment": {key: env[key] for key in ENVIRONMENT_KEYS if key in env},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wyrd Steam-native bootstrap")
    parser.add_argument("--steam-bootstrap", action="store_true",
                        help="start Wyrd and open its configured MO2 instance")
    args = parser.parse_args(argv)
    if not args.steam_bootstrap:
        parser.error("--steam-bootstrap is required")

    script_dir = Path(__file__).resolve().parent
    run_script = script_dir / "run.sh"
    if not run_script.is_file():
        print(f"Wyrd launcher is missing: {run_script}", file=sys.stderr)
        return 2

    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")).expanduser()
    log_path = data_home / "wyrd" / "logs" / "steam-bootstrap-environment.json"
    snapshot = environment_snapshot()
    snapshot.update({"run_script": str(run_script), "bootstrap": str(Path(__file__).resolve())})
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    except OSError as error:
        print(f"Could not write Steam environment diagnostics: {error}", file=sys.stderr)

    command = ["bash", str(run_script), "--steam-bootstrap"]
    try:
        completed = subprocess.run(command, cwd=script_dir, env=os.environ.copy(), check=False)
    except OSError as error:
        print(f"Could not start Wyrd: {error}", file=sys.stderr)
        return 1
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
