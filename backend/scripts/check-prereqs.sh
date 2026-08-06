#!/usr/bin/env bash
# Mac/Linux wrapper for check_prereqs.py -- finds a python interpreter first,
# since the Python check itself can't run without one.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "[FAIL] Python not found on PATH -- install Python 3.11+ before running this tool."
    echo "       https://www.python.org/downloads/"
    exit 1
fi

"$PYTHON_BIN" "$SCRIPT_DIR/check_prereqs.py"
