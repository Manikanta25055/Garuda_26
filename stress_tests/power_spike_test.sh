#!/usr/bin/env bash
# Transient Power Spike Test: 50 heavy dd write cycles
# Checks for under-voltage, throttling, and FPS stability
set -euo pipefail

RESULTS="/home/manikanta/Projects/hailo-rpi5-examples/stress_tests/power_spike_results.csv"
SPIKE_FILE="/home/manikanta/stress_tests_spike"
API="http://localhost:8080/api/state"
CYCLES=50

mkdir -p "$(dirname "$SPIKE_FILE")"
echo "=== Power Spike Test ($CYCLES cycles) ===" | tee /dev/stderr
echo "cycle,throttled,temp_c,fps,dmesg_undervolt" > "$RESULTS"

# Record dmesg baseline
dmesg_before=$(sudo dmesg | wc -l)

for i in $(seq 1 $CYCLES); do
    # Heavy write burst
    dd if=/dev/urandom of="$SPIKE_FILE" bs=1M count=100 oflag=direct 2>/dev/null

    # Sample metrics
    throttled=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2 || echo "N/A")
    temp=$(vcgencmd measure_temp 2>/dev/null | grep -oP '[0-9.]+' || echo "N/A")
    fps=$(curl -sf "$API" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('fps', 'N/A'))" 2>/dev/null || echo "N/A")

    # Check for new under-voltage in dmesg
    dmesg_now=$(sudo dmesg | wc -l)
    new_lines=$((dmesg_now - dmesg_before))
    undervolt=0
    if [ "$new_lines" -gt 0 ]; then
        undervolt=$(sudo dmesg | tail -n "$new_lines" | grep -ci "under-voltage\|undervoltage" || true)
    fi

    echo "$i,$throttled,$temp,$fps,$undervolt" >> "$RESULTS"
    printf "Cycle %2d/%d: throttled=%s temp=%sC fps=%s undervolt=%s\n" \
        "$i" "$CYCLES" "$throttled" "$temp" "$fps" "$undervolt" >&2

    rm -f "$SPIKE_FILE"
    sleep 5
done

# Summary
echo -e "\n=== Summary ===" >&2
python3 -c "
import csv
with open('$RESULTS') as f:
    rows = list(csv.DictReader(f))

throttle_events = sum(1 for r in rows if r['throttled'] not in ('0x0', 'N/A'))
undervolt_events = sum(int(r['dmesg_undervolt']) for r in rows if r['dmesg_undervolt'] != 'N/A')
temps = [float(r['temp_c']) for r in rows if r['temp_c'] != 'N/A']
fps_vals = [float(r['fps']) for r in rows if r['fps'] != 'N/A']

print(f'Cycles: {len(rows)}')
print(f'Throttle events: {throttle_events} — {\"PASS\" if throttle_events == 0 else \"FAIL\"}'  )
print(f'Under-voltage events: {undervolt_events} — {\"PASS\" if undervolt_events == 0 else \"WARN\"}'  )
if temps:
    print(f'Temp range: {min(temps):.1f}C - {max(temps):.1f}C')
if fps_vals:
    print(f'FPS range: {min(fps_vals):.1f} - {max(fps_vals):.1f} (avg {sum(fps_vals)/len(fps_vals):.1f})')
" >&2

rm -rf "$(dirname "$SPIKE_FILE")"
echo "Results saved to: $RESULTS" >&2
