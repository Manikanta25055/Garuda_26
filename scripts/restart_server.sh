#!/bin/bash
# ============================================================
#  Garuda — Restart Server
#  Stops the running instance (if any) then starts a fresh one.
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[Garuda] Restarting server..."

# ── Stop ────────────────────────────────────────────────────
bash "$SCRIPT_DIR/stop_server.sh"

# ── Brief pause so ports are freed ──────────────────────────
sleep 2

# ── Start ───────────────────────────────────────────────────
bash "$SCRIPT_DIR/start_server.sh"
