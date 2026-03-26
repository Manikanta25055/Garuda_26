#!/usr/bin/env bash
# Log cleanup script — run via cron: 0 3 * * *
# Rotates large perm_*.txt files and trims old presence_log entries.
set -euo pipefail

LOG_DIR="/home/manikanta/Projects/hailo-rpi5-examples/system_logs"
MAX_SIZE=$((5 * 1024 * 1024))  # 5 MB
KEEP_BACKUPS=3

# Rotate perm_*.txt files exceeding MAX_SIZE
for f in "$LOG_DIR"/perm_*.txt; do
    [ -f "$f" ] || continue
    size=$(stat -c%s "$f" 2>/dev/null || echo 0)
    if [ "$size" -gt "$MAX_SIZE" ]; then
        # Shift existing backups
        for i in $(seq $((KEEP_BACKUPS - 1)) -1 1); do
            [ -f "${f}.$i" ] && mv "${f}.$i" "${f}.$((i + 1))"
        done
        # Remove oldest if over limit
        [ -f "${f}.$((KEEP_BACKUPS + 1))" ] && rm -f "${f}.$((KEEP_BACKUPS + 1))"
        mv "$f" "${f}.1"
        touch "$f"
        echo "[$(date)] Rotated $f (was ${size} bytes)"
    fi
done

# Trim presence_log.json entries older than 7 days
PL="$LOG_DIR/presence_log.json"
if [ -f "$PL" ]; then
    python3 -c "
import json, datetime, sys
cutoff = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
with open('$PL') as f:
    data = json.load(f)
before = len(data)
data = [e for e in data if e.get('ts', '') >= cutoff]
after = len(data)
if after < before:
    with open('$PL', 'w') as f:
        json.dump(data, f)
    print(f'[{datetime.datetime.now().strftime(\"%Y-%m-%d %H:%M:%S\")}] Trimmed presence_log: {before} → {after} entries')
"
fi
