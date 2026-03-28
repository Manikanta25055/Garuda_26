#!/bin/bash
# ============================================================
#  Garuda — Start Server
#  Starts Garuda_web.py in the background, logging to
#  /tmp/garuda_server.log. Safe to run multiple times —
#  won't start a second instance if one is already running.
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PID_FILE="/tmp/garuda_server.pid"
LOG_FILE="/tmp/garuda_server.log"
SERVER_SCRIPT="$PROJECT_DIR/basic_pipelines/Garuda_web.py"

# ── Already running? ────────────────────────────────────────
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "[Garuda] Server is already running (PID $PID)."
        echo "         UI → http://localhost:8080"
        exit 0
    else
        echo "[Garuda] Stale PID file found — cleaning up."
        rm -f "$PID_FILE"
    fi
fi

# ── Load environment ─────────────────────────────────────────
cd "$PROJECT_DIR"
# shellcheck disable=SC1091
source "$PROJECT_DIR/setup_env.sh"

# ── Launch ──────────────────────────────────────────────────
echo "[Garuda] Starting server..."
nohup python3 "$SERVER_SCRIPT" --input rpi \
    >> "$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

# ── Wait briefly and confirm it started ─────────────────────
sleep 3
if kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "[Garuda] Server started successfully (PID $SERVER_PID)."
    echo "         UI  → http://localhost:8080"
    echo "         Log → $LOG_FILE"
else
    echo "[Garuda] ERROR: Server failed to start. Check the log:"
    tail -20 "$LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
fi
