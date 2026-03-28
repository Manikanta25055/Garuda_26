#!/bin/bash
# ============================================================
#  Garuda — Stop Server
#  Gracefully stops the running Garuda server.
# ============================================================

set -euo pipefail

PID_FILE="/tmp/garuda_server.pid"

# ── Check PID file ───────────────────────────────────────────
if [ ! -f "$PID_FILE" ]; then
    echo "[Garuda] No PID file found — server may not be running."
    # Kill any stray processes just in case
    STRAY=$(pgrep -f "Garuda_web.py" 2>/dev/null || true)
    if [ -n "$STRAY" ]; then
        echo "[Garuda] Found stray process(es): $STRAY — killing."
        kill $STRAY 2>/dev/null || true
        sleep 1
        echo "[Garuda] Done."
    fi
    exit 0
fi

PID=$(cat "$PID_FILE")

# ── Process alive? ───────────────────────────────────────────
if ! kill -0 "$PID" 2>/dev/null; then
    echo "[Garuda] Process $PID is not running (already stopped)."
    rm -f "$PID_FILE"
    exit 0
fi

# ── Graceful SIGTERM ─────────────────────────────────────────
echo "[Garuda] Stopping server (PID $PID)..."
kill -TERM "$PID" 2>/dev/null || true

# ── Wait up to 8 seconds for clean shutdown ──────────────────
for i in $(seq 1 8); do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "[Garuda] Server stopped cleanly."
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 1
done

# ── Force kill if still alive ────────────────────────────────
echo "[Garuda] Server did not stop cleanly — force killing..."
kill -KILL "$PID" 2>/dev/null || true
sleep 1
rm -f "$PID_FILE"
echo "[Garuda] Server force-stopped."
