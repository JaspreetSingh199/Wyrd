#!/usr/bin/env bash
# Experimental STL Game Mode-style MO2 launch: run MO2 in Skyrim's Proton prefix.
# This is intentionally opt-in and does not change Steam launch options.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  launch-mo2-game-prefix.sh --proton /path/to/Proton --mo2 /path/to/ModOrganizer.exe --prefix /path/to/steamapps/compatdata/489830 [--steam-root /path/to/Steam]

Runs the supplied MO2 executable using the supplied Proton build and Skyrim's
compatdata prefix. This may initialize/update that prefix. It does not install
MO2, copy files, alter Steam launch options, or delete/reset anything.
EOF
}

PROTON=""
MO2=""
PREFIX=""
STEAM_ROOT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --proton) PROTON="${2:?Missing Proton path}"; shift 2 ;;
    --mo2) MO2="${2:?Missing MO2 path}"; shift 2 ;;
    --prefix) PREFIX="${2:?Missing prefix path}"; shift 2 ;;
    --steam-root) STEAM_ROOT="${2:?Missing Steam root}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$PROTON" && -n "$MO2" && -n "$PREFIX" ]] || { usage >&2; exit 2; }
PROTON="$(realpath "$PROTON")"
MO2="$(realpath "$MO2")"
PREFIX="$(realpath -m "$PREFIX")"
[[ -x "$PROTON" ]] || { echo "Proton wrapper is not executable: $PROTON" >&2; exit 1; }
[[ -f "$MO2" ]] || { echo "MO2 executable not found: $MO2" >&2; exit 1; }
[[ "$PREFIX" == */compatdata/489830 ]] || {
  echo "Refusing: prefix path must end in /compatdata/489830 (Skyrim AppID)." >&2; exit 1;
}
if [[ -e "$PREFIX" && ! -d "$PREFIX/pfx/drive_c" ]]; then
  echo "Prefix exists but does not look initialized: $PREFIX" >&2
  echo "This experiment will not repair or reset it automatically." >&2
  exit 1
fi

if [[ -z "$STEAM_ROOT" ]]; then
  if [[ -d "$HOME/.local/share/Steam" ]]; then STEAM_ROOT="$HOME/.local/share/Steam"
  elif [[ -d "$HOME/.steam/steam" ]]; then STEAM_ROOT="$HOME/.steam/steam"
  else echo "Steam root not found; pass --steam-root." >&2; exit 1
  fi
fi
STEAM_ROOT="$(realpath "$STEAM_ROOT")"
mkdir -p "$PREFIX"
LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/wyrd/steam-native-launch"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/mo2-game-mode-$(date +%Y%m%d-%H%M%S).log"

export STEAM_COMPAT_CLIENT_INSTALL_PATH="$STEAM_ROOT"
export STEAM_COMPAT_DATA_PATH="$PREFIX"
export SteamAppId=489830
export SteamGameId=489830
export STEAM_COMPAT_APP_ID=489830
export PROTON_LOG=1
export PROTON_LOG_DIR="$LOG_DIR"
export WINEPREFIX="$PREFIX/pfx"

{
  echo "Wyrd experimental Game Mode launch"
  echo "timestamp=$(date -Is)"
  echo "proton=$PROTON"
  echo "mo2=$MO2"
  echo "prefix=$PREFIX"
  echo "steam_root=$STEAM_ROOT"
  echo "appid=489830"
  echo
  echo "Launching MO2 in Skyrim's compatdata prefix. Prefix writes may occur."
} | tee "$LOG"

set +e
"$PROTON" run "$MO2" 2>&1 | tee -a "$LOG"
STATUS=${PIPESTATUS[0]}
set -e
echo "proton_exit=$STATUS" | tee -a "$LOG"
exit "$STATUS"
