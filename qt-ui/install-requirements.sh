#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${SCRIPT_DIR}/.venv/bin/python"
if [[ ! -x "${PYTHON}" ]]; then
  echo "Wyrd's virtual environment is missing. Run qt-ui/run.sh first." >&2
  exit 1
fi
if ! "${PYTHON}" -c 'import PySide6, py7zr' >/dev/null 2>&1; then
  echo "Installing Wyrd's Python requirements separately from the MO2 application files…"
  "${PYTHON}" -m pip install --upgrade -r "${SCRIPT_DIR}/requirements.txt"
else
  echo "Wyrd's Python requirements are already installed."
fi
