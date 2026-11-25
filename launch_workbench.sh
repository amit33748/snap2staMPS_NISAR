#!/usr/bin/env bash
# ===============================================================================
#    snap2stamps_nisar Cross-Platform Launcher (Linux & macOS) - Independent .venv
# ===============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export PYTHONUTF8=1

if [ ! -x ".venv/bin/python" ]; then
    echo "[INFO] Creating independent virtual environment at $SCRIPT_DIR/.venv ..."
    PY_SYS=""
    for cand in python3.12 python3.11 python3.10 python3; do
        if command -v "$cand" &> /dev/null; then
            PY_SYS="$cand"
            break
        fi
    done
    if [ -z "$PY_SYS" ]; then
        echo "[ERROR] Python 3.10+ was not found on PATH."
        exit 1
    fi
    "$PY_SYS" -m venv .venv
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt
fi

exec .venv/bin/python gui/server.py --port 8501 --open
