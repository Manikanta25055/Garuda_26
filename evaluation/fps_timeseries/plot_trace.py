#!/usr/bin/env python3
# Render the FPS-across-modes time-series as Fig. 13 for the IEEE Access paper.
#
# Input: CSV produced by run_trace.py (columns: t_epoch, t_rel, total_frames,
# window, dnd, idle, privacy, emergency, alert_active, primary_proc,
# secondary_proc, secondary_drop) and events.csv.
#
# Output: a PDF figure and a per-mode summary table printed to stdout.

import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

MODE_LABELS = [
    ("baseline", "Baseline"),
    ("idle",     "Idle"),
    ("dnd",      "DND"),
    ("privacy",  "Privacy"),
    ("emergency","Emergency"),
    ("active",   "Active"),
]

MODE_COLORS = {
    "baseline":  "#eeeeee",
    "idle":      "#d9e8f5",
    "dnd":       "#f5d9d9",
    "privacy":   "#e5d9f5",
    "emergency": "#f5e5d9",
    "active":    "#d9f5e1",
}


def read_csv(path):
    rows = []
    with open(path) as f:
        r = csv.DictReader(f)
        for row in r:
            rows.append(row)
    return rows


def derive_instant_fps(rows):
    t_rel = np.array([float(r["t_rel"]) for r in rows])
    frames = np.array([int(r["total_frames"]) for r in rows])
    dt = np.diff(t_rel)
    df = np.diff(frames)
    fps = np.divide(df, dt, out=np.zeros_like(df, dtype=float), where=dt > 0)
    # Report per-sample fps at the interval midpoint.
    t_mid = (t_rel[1:] + t_rel[:-1]) / 2.0
    return t_mid, fps


def assign_window(t, boundaries):
    for name, (t0, t1) in boundaries.items():
        if t0 <= t < t1:
            return name
    return "unknown"


def build_window_boundaries(schedule):
    out = {}
    cursor = 0.0
    for name, dur in schedule:
        out[name] = (cursor, cursor + dur)
        cursor += dur
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", default="trace.csv")
    ap.add_argument("--events", default="events.csv")
    ap.add_argument("--out", default="fig13_fps_timeseries.pdf")
    ap.add_argument("--baseline_sec", type=float, default=60)
    ap.add_argument("--window_sec", type=float, default=120)
    args = ap.parse_args()

    rows = read_csv(args.trace)
    if len(rows) < 2:
        raise SystemExit("trace has <2 samples, nothing to plot")

    schedule = [("baseline", args.baseline_sec)]
    for name, _ in MODE_LABELS[1:]:
        schedule.append((name, args.window_sec))
    boundaries = build_window_boundaries(schedule)

    t_mid, fps = derive_instant_fps(rows)

    # Per-mode statistics (ignore first 2 samples of each window to avoid
    # transition artefacts).
    per_mode = {}
    for name, (t0, t1) in boundaries.items():
        mask = (t_mid >= t0 + 2) & (t_mid < t1 - 1)
        vals = fps[mask]
        if vals.size >= 5:
            per_mode[name] = (vals.mean(), vals.std(ddof=1), vals.min(), vals.max(), vals.size)

    fig, ax = plt.subplots(figsize=(7.0, 3.2))

    # Shade each mode window.
    for name, (t0, t1) in boundaries.items():
        ax.axvspan(t0, t1, color=MODE_COLORS.get(name, "#eeeeee"), alpha=0.6, lw=0)
        label_y = 0.97
        ax.text((t0 + t1) / 2.0, label_y,
                dict(MODE_LABELS).get(name, name),
                transform=ax.get_xaxis_transform(),
                ha="center", va="top", fontsize=8, color="#333333")

    # Plot instantaneous FPS.
    ax.plot(t_mid, fps, color="#1565c0", lw=0.9, label="1-Hz instantaneous FPS")

    # Load events as vertical markers.
    events = read_csv(args.events) if Path(args.events).exists() else []
    t_epoch0 = float(rows[0]["t_epoch"])
    for ev in events:
        try:
            t_rel = float(ev["t_epoch"]) - t_epoch0
        except Exception:
            continue
        if 0 <= t_rel <= t_mid[-1]:
            ax.axvline(t_rel, color="#c62828", lw=0.5, ls=":", alpha=0.6)

    # Overall mean ± stdev horizontal band.
    mu = float(np.mean(fps))
    sd = float(np.std(fps, ddof=1))
    ax.axhline(mu, color="#222222", lw=0.8, ls="--",
               label=f"overall mean {mu:.2f} FPS (sd {sd:.2f})")

    ax.set_xlabel("Time since start (s)")
    ax.set_ylabel("Instantaneous FPS")
    ax.set_xlim(0, t_mid[-1])
    lo = max(0, mu - 6 * max(sd, 1.0))
    hi = mu + 6 * max(sd, 1.0)
    ax.set_ylim(lo, hi)
    ax.grid(True, alpha=0.3, lw=0.4)
    ax.legend(loc="lower right", fontsize=7, framealpha=0.85)
    fig.tight_layout()
    fig.savefig(args.out)
    print(f"[ok] wrote {args.out}")

    print("\nPer-mode summary (mean / sd / min / max / n):")
    for name, _ in MODE_LABELS:
        if name in per_mode:
            m, s, lo_, hi_, n = per_mode[name]
            print(f"  {name:10s}  {m:6.2f}  sd {s:5.3f}  [{lo_:.2f}, {hi_:.2f}]  n={n}")
    print(f"\nOverall: mean {mu:.2f} FPS, sd {sd:.3f}, n={fps.size} samples")


if __name__ == "__main__":
    main()
