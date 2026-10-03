#!/usr/bin/env bash
# Log cleanup script — run via cron: 0 3 * * *
# A safety net for the permanent logs: the app rotates them itself when it
# writes (garuda_services/logs.py), with the same size and the same number of
# old files kept, so this only catches a file that grew while the app was down.
#
# It used to rotate at 5 MB keeping 3 old files, overwriting the older ones the
# app keeps (the dashboard, the Logs page and Narada read all of them back),
# and it deleted presence history older than 7 days. Presence history is kept:
# the app caps it at 5000 entries.
set -euo pipefail

# Was a path from the project this one grew out of; nothing was ever cleaned.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$(dirname "$SCRIPT_DIR")/basic_pipelines/system_logs"
MAX_SIZE=$((10 * 1024 * 1024))  # 10 MB: _LOG_MAX_SIZE_BYTES in Garuda_web.py
KEEP_BACKUPS=10                 # _LOG_KEEP_ROTATED in Garuda_web.py

# Rotate perm_*.txt files exceeding MAX_SIZE: .9 → .10 … .1 → .2, file → .1
for f in "$LOG_DIR"/perm_*.txt; do
    [ -f "$f" ] || continue
    size=$(stat -c%s "$f" 2>/dev/null || echo 0)
    if [ "$size" -gt "$MAX_SIZE" ]; then
        rm -f "${f}.${KEEP_BACKUPS}"
        for i in $(seq $((KEEP_BACKUPS - 1)) -1 1); do
            if [ -f "${f}.$i" ]; then mv "${f}.$i" "${f}.$((i + 1))"; fi
        done
        mv "$f" "${f}.1"
        touch "$f"
        echo "[$(date)] Rotated $f (was ${size} bytes)"
    fi
done
