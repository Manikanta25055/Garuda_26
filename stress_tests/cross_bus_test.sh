#!/usr/bin/env bash
# Cross-Bus Stress Test: YOLO inference + heavy SSD I/O simultaneously
# Tests if concurrent disk I/O disrupts Hailo-8L inference via USB
set -euo pipefail

RESULTS="/home/manikanta/Projects/hailo-rpi5-examples/stress_tests/cross_bus_results.txt"
FIO_DIR="/home/manikanta/stress_tests_fio"
API="http://localhost:8080/api/state"
DURATION=120  # seconds

mkdir -p "$FIO_DIR"
echo "=== Cross-Bus Stress Test ===" | tee "$RESULTS"
echo "Started: $(date)" | tee -a "$RESULTS"

# --- Baseline FPS ---
echo -e "\n--- Baseline FPS (10 samples, 20s) ---" | tee -a "$RESULTS"
baseline_fps=()
for i in $(seq 1 10); do
    fps=$(curl -sf "$API" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('fps', 'N/A'))" 2>/dev/null || echo "N/A")
    echo "  Sample $i: $fps FPS" | tee -a "$RESULTS"
    baseline_fps+=("$fps")
    sleep 2
done

# Calculate baseline average
avg_baseline=$(python3 -c "
vals = [float(x) for x in '${baseline_fps[*]}'.split() if x != 'N/A']
print(f'{sum(vals)/len(vals):.1f}' if vals else 'N/A')
")
echo "Baseline average: $avg_baseline FPS" | tee -a "$RESULTS"

# --- Record dmesg baseline ---
dmesg_before=$(sudo dmesg | wc -l)

# --- Start fio ---
echo -e "\n--- Starting fio (${DURATION}s) ---" | tee -a "$RESULTS"
fio --name=crossbus --rw=randrw --bs=4k --size=512M --numjobs=4 \
    --runtime=$DURATION --time_based --directory="$FIO_DIR" \
    --output="$FIO_DIR/fio_output.txt" &
FIO_PID=$!

# --- Sample during stress ---
echo -e "\n--- Stress Samples (every 5s) ---" | tee -a "$RESULTS"
printf "%-8s %-10s %-10s\n" "Time" "FPS" "Temp(C)" | tee -a "$RESULTS"
stress_fps=()
elapsed=0
while kill -0 $FIO_PID 2>/dev/null && [ $elapsed -lt $DURATION ]; do
    fps=$(curl -sf "$API" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('fps', 'N/A'))" 2>/dev/null || echo "N/A")
    temp=$(vcgencmd measure_temp 2>/dev/null | grep -oP '[0-9.]+' || echo "N/A")
    printf "%-8s %-10s %-10s\n" "${elapsed}s" "$fps" "$temp" | tee -a "$RESULTS"
    stress_fps+=("$fps")
    sleep 5
    elapsed=$((elapsed + 5))
done

wait $FIO_PID 2>/dev/null || true

# --- Post-stress checks ---
echo -e "\n--- Post-Stress Checks ---" | tee -a "$RESULTS"

# Check Hailo device
if [ -e /dev/hailo0 ]; then
    echo "PASS: /dev/hailo0 present" | tee -a "$RESULTS"
else
    echo "FAIL: /dev/hailo0 MISSING" | tee -a "$RESULTS"
fi

# Check dmesg for USB errors
dmesg_after=$(sudo dmesg | wc -l)
new_lines=$((dmesg_after - dmesg_before))
usb_errors=$(sudo dmesg | tail -n "$new_lines" | grep -ciE "usb.*error|disconnect|reset" || true)
if [ "$usb_errors" -eq 0 ]; then
    echo "PASS: No USB errors in dmesg" | tee -a "$RESULTS"
else
    echo "WARN: $usb_errors USB-related dmesg entries" | tee -a "$RESULTS"
    sudo dmesg | tail -n "$new_lines" | grep -iE "usb.*error|disconnect|reset" | tee -a "$RESULTS"
fi

# Calculate stress average and drop
avg_stress=$(python3 -c "
vals = [float(x) for x in '${stress_fps[*]}'.split() if x != 'N/A']
print(f'{sum(vals)/len(vals):.1f}' if vals else 'N/A')
")
echo "Stress average: $avg_stress FPS" | tee -a "$RESULTS"

python3 -c "
base = '$avg_baseline'
stress = '$avg_stress'
if base != 'N/A' and stress != 'N/A':
    drop = (1 - float(stress)/float(base)) * 100
    status = 'PASS' if drop <= 15 else 'FAIL'
    print(f'FPS drop: {drop:.1f}% — {status}')
else:
    print('Could not calculate FPS drop (N/A values)')
" | tee -a "$RESULTS"

# Cleanup
rm -rf "$FIO_DIR"
echo -e "\nCompleted: $(date)" | tee -a "$RESULTS"
