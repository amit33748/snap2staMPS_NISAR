#!/usr/bin/env bash
# ===============================================================================
#           snap2stamps_nisar Daylight Survey Workbench Launcher (Linux/macOS)
# ===============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$SCRIPT_DIR"

export PYTHONUTF8=1

if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
    PYTHON_CMD="$ROOT_DIR/.venv/bin/python"
else
    echo "[INFO] Independent .venv not found. Bootstrapping $ROOT_DIR/.venv ..."
    PY_SYS=""
    for cand in python3.12 python3.11 python3.10 python3; do
        if command -v "$cand" &> /dev/null; then
            PY_SYS="$cand"
            break
        fi
    done
    if [ -z "$PY_SYS" ]; then
        echo "[ERROR] Neither .venv nor Python 3.10+ was found in PATH!"
        exit 1
    fi
    "$PY_SYS" -m venv "$ROOT_DIR/.venv"
    "$ROOT_DIR/.venv/bin/python" -m pip install --upgrade pip
    "$ROOT_DIR/.venv/bin/python" -m pip install -r "$ROOT_DIR/requirements.txt"
    PYTHON_CMD="$ROOT_DIR/.venv/bin/python"
fi

echo "[INFO] Using Python: $($PYTHON_CMD --version) ($PYTHON_CMD)"
echo "[INFO] Starting Workbench Web App on http://127.0.0.1:8501 ..."
echo ""

exec "$PYTHON_CMD" server.py --port 8501 --open
