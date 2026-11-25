#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python3 -m venv "${SCRIPT_DIR}/.venv" 2>/dev/null || true
if [[ ! -x "${SCRIPT_DIR}/.venv/bin/python" ]]; then
  echo "Python 3 is required." >&2
  exit 1
fi
# Check every runtime dependency used by Wyrd, not only the Qt UI.
# The MO2 installer downloads the official 7z release and extracts it with py7zr.
if ! "${SCRIPT_DIR}/.venv/bin/python" -c 'import PySide6, py7zr' >/dev/null 2>&1; then
  echo "Installing Wyrd Python requirements (PySide6, py7zr)…"
  "${SCRIPT_DIR}/.venv/bin/python" -m pip install --upgrade -r "${SCRIPT_DIR}/requirements.txt"
fi
exec "${SCRIPT_DIR}/.venv/bin/python" "${SCRIPT_DIR}/main.py"
