"""Native host bootstrap used by Wyrd's optional Steam shortcut.

Steam starts this program as a native Linux process. The bootstrap then
requests that the existing Wyrd Qt application opens the saved MO2 instance.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wyrd Steam-native bootstrap")
    parser.add_argument("--steam-bootstrap", action="store_true",
                        help="start Wyrd and open its configured MO2 instance")
    args = parser.parse_args(argv)
    if not args.steam_bootstrap:
        parser.error("--steam-bootstrap is required")
    script_dir = Path(__file__).resolve().parent
    launcher = script_dir / "run.sh"
    if not launcher.is_file():
        print(f"Wyrd launcher is missing: {launcher}", file=sys.stderr)
        return 2
    python = script_dir / ".venv" / "bin" / "python"
    if not python.is_file():
        print("Wyrd virtual environment is missing. Run qt-ui/run.sh once first.", file=sys.stderr)
        return 2
    command = [str(python), str(script_dir / "main.py"), "--steam-bootstrap"]
    try:
        process = subprocess.Popen(command, cwd=script_dir, start_new_session=True)
    except OSError as error:
        print(f"Could not start Wyrd: {error}", file=sys.stderr)
        return 1
    print(f"Started Wyrd bootstrap (PID {process.pid}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
