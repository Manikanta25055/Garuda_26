"""Pi 5 PMIC power measurement.

Samples vcgencmd pmic_read_adc at ~1 Hz, sums I*V over every reported rail to
get instantaneous board power (Pi 5 SoC + RAM + connected peripherals incl.
the Hailo-8L AI HAT+ that is powered through the same PMIC rails). Reports
mean / stdev / median / p95 / max in watts.

The Hailo-8L draws via the M.2 HAT+, which is fed from the Pi 5 PMIC; Hailo's
datasheet quotes 2.5 W typical for the chip itself. Whatever the HAT-side
current is, it is included in the per-rail sums below, so the figure is
end-to-end board power, not chip-only.
"""
import argparse, json, re, statistics, subprocess, sys, time
from pathlib import Path

CURRENT_RE = re.compile(r"^\s*(\S+)_A\s+current\(\d+\)=([\d.]+)A")
VOLT_RE    = re.compile(r"^\s*(\S+)_V\s+volt\(\d+\)=([\d.]+)V")


def sample_power_w() -> float:
    out = subprocess.check_output(["vcgencmd", "pmic_read_adc"], text=True)
    cur, vlt = {}, {}
    for line in out.splitlines():
        m = CURRENT_RE.match(line)
        if m:
            cur[m.group(1)] = float(m.group(2)); continue
        m = VOLT_RE.match(line)
        if m:
            vlt[m.group(1)] = float(m.group(2))
    # exclude rails whose voltage is reported but draw zero (DAC/ADC/BATT)
    # exclude EXT5V (PSU input voltage; no matching current channel)
    rails = set(cur) & set(vlt)
    return sum(cur[r] * vlt[r] for r in rails)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=90.0, help="seconds to sample")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between samples")
    ap.add_argument("--label", default="active_concurrent_load",
                    help="phase label written to the output JSON")
    ap.add_argument("--out", default="/home/manikanta/Projects/Garuda_26/evaluation/power_measurement/power_results.json")
    a = ap.parse_args()

    samples = []
    t_end = time.time() + a.duration
    t0 = time.perf_counter()
    while time.time() < t_end:
        try:
            p = sample_power_w()
        except Exception as e:
            print(f"  sample error: {e}", file=sys.stderr); continue
        samples.append(p)
        elapsed = time.perf_counter() - t0
        if len(samples) % 10 == 0:
            print(f"  n={len(samples):3d}  t={elapsed:5.1f}s  P={p:.3f} W  "
                  f"mean={statistics.mean(samples):.3f} W", flush=True)
        time.sleep(max(0.0, a.interval - (time.perf_counter() - t0) % a.interval))

    if not samples:
        raise SystemExit("no samples collected")

    samples_sorted = sorted(samples)
    n = len(samples_sorted)
    res = {
        "label":     a.label,
        "n_samples": n,
        "duration_s": round(time.perf_counter() - t0, 2),
        "mean_w":    round(statistics.mean(samples), 4),
        "stdev_w":   round(statistics.stdev(samples) if n > 1 else 0.0, 4),
        "median_w":  round(statistics.median(samples), 4),
        "p95_w":     round(samples_sorted[int(0.95 * (n - 1))], 4),
        "min_w":     round(min(samples), 4),
        "max_w":     round(max(samples), 4),
        "raw_w":     [round(s, 4) for s in samples],
    }
    print("\n=== PMIC board-power summary ===")
    for k in ("label", "n_samples", "duration_s", "mean_w", "stdev_w",
              "median_w", "p95_w", "min_w", "max_w"):
        print(f"  {k:12s}: {res[k]}")

    out_path = Path(a.out)
    if out_path.exists():
        existing = json.loads(out_path.read_text())
        if isinstance(existing, list):
            existing.append(res); payload = existing
        else:
            payload = [existing, res]
    else:
        payload = [res]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"\nAppended to {out_path}")


if __name__ == "__main__":
    main()
