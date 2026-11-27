#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python3 -m venv "${SCRIPT_DIR}/.venv" 2>/dev/null || true
if [[ ! -x "${SCRIPT_DIR}/.venv/bin/python" ]]; then
  echo "Python 3 is required." >&2
  exit 1
fi
# Python dependencies are managed separately from the extracted MO2 application.
bash "${SCRIPT_DIR}/install-requirements.sh"
exec "${SCRIPT_DIR}/.venv/bin/python" "${SCRIPT_DIR}/main.py"
