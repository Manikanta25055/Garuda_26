#!/bin/bash
# E6: take the Pi's only network (Wi-Fi) away for OFFLINE_S seconds while the
# live service runs, and record what the relays physically do. Runs as its own
# systemd unit, so it finishes and restores Wi-Fi even if the session that
# started it loses its connection:
#
#   sudo systemd-run --unit=drishti-e6 --collect /bin/bash evaluation/narada_rs/e6_offline.sh
#
# Writes $OUT/monitor.jsonl (contact, lamp, net) and $OUT/events.log.
set -u
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$REPO/evaluation/out/narada_rs/e6"
PY=/home/manikanta/Projects/Garuda_26/venv_hailo_rpi5_examples/bin/python
PRE_S=60
OFFLINE_S=600
POST_S=60
mkdir -p "$OUT"
chown manikanta:manikanta "$OUT"
note() { echo "$(date +%s.%N) $*" >> "$OUT/events.log"; }

note "start"
runuser -u manikanta -- env PYTHONPATH="$REPO" "$PY" -m evaluation.narada_rs.offline_monitor \
    --seconds $((PRE_S + OFFLINE_S + POST_S + 20)) --out "$OUT/monitor.jsonl" &
MON=$!
# Whatever happens below, Wi-Fi comes back.
trap 'nmcli radio wifi on; note "wifi on (trap)"' EXIT

sleep "$PRE_S"
nmcli radio wifi off && note "wifi off"
sleep "$OFFLINE_S"
nmcli radio wifi on && note "wifi on"
trap - EXIT
sleep "$POST_S"
wait "$MON"
note "done"
