#!/usr/bin/env python3
# FPS-across-modes time-series driver for Project Garuda.
#
# Polls the running Garuda_web server at 1 Hz for (t, total_frames, modes),
# cycles operating modes on a fixed schedule, and injects concurrent load
# (danger triggers, voice chat, evidence clips) so the resulting CSV captures
# the flat-envelope property under realistic stress.
#
# Requires the server to expose /api/eval/fps_probe (gated by GARUDA_EVAL_TOKEN).
# Mode switching also needs an admin session cookie; we log in via /api/login.
#
# Usage:
#   GARUDA_EVAL_TOKEN=... GARUDA_ADMIN_PASS=... python run_trace.py \
#       --base http://localhost:8080 --duration 720 --out trace.csv
#
# The default schedule is 5 modes x 2 min = 10 min (plus a 1-min baseline head).

import argparse
import csv
import os
import sys
import time
import threading
from pathlib import Path

import requests

MODES = ["baseline", "idle", "dnd", "privacy", "emergency", "active"]

# Per-mode window length (seconds). "active" = all flags off, detectors running.
WINDOW_SEC = 120
BASELINE_SEC = 60

LOAD_EVENTS = [
    # (offset_from_start_sec, event_type, payload)
    (30,  "inject", {"label": "Knife",    "confidence": 0.92, "email": False}),
    (90,  "chat",   {"message": "what is the current status"}),
    (150, "inject", {"label": "Hammer",   "confidence": 0.88, "email": False}),
    (210, "inject", {"label": "Scissors", "confidence": 0.91, "email": False}),
    (270, "chat",   {"message": "turn on privacy mode"}),
    (330, "inject", {"label": "Knife",    "confidence": 0.95, "email": False}),
    (390, "chat",   {"message": "how many detections today"}),
    (450, "inject", {"label": "Hammer",   "confidence": 0.87, "email": False}),
    (510, "inject", {"label": "Scissors", "confidence": 0.93, "email": False}),
    (570, "chat",   {"message": "disable privacy mode"}),
    (630, "inject", {"label": "Knife",    "confidence": 0.96, "email": False}),
    (690, "inject", {"label": "Scissors", "confidence": 0.90, "email": False}),
]


def admin_login(base, user, password, token):
    s = requests.Session()
    if token:
        s.headers.update({"X-Eval-Token": token})
    r = s.post(f"{base}/api/login", json={"username": user, "password": password}, timeout=10)
    r.raise_for_status()
    return s


def set_mode(session, base, mode, value):
    r = session.post(f"{base}/api/modes", json={"mode": mode, "value": value}, timeout=10)
    if r.status_code == 429:
        time.sleep(3.0)
        r = session.post(f"{base}/api/modes", json={"mode": mode, "value": value}, timeout=10)
    r.raise_for_status()


def clear_all_modes(session, base):
    for m in ("dnd", "idle", "privacy", "emergency", "email_off", "night"):
        for attempt in range(4):
            try:
                set_mode(session, base, m, False)
                break
            except Exception as e:
                if attempt == 3:
                    print(f"[warn] clear {m}: {e}", file=sys.stderr)
                else:
                    time.sleep(2.0)
        time.sleep(0.3)


def apply_mode_window(session, base, window):
    clear_all_modes(session, base)
    if window in ("baseline", "active"):
        return
    if window == "idle":
        set_mode(session, base, "idle", True)
    elif window == "dnd":
        set_mode(session, base, "dnd", True)
    elif window == "privacy":
        set_mode(session, base, "privacy", True)
    elif window == "emergency":
        set_mode(session, base, "emergency", True)


def inject_danger(base, token, payload):
    try:
        r = requests.post(
            f"{base}/api/eval/inject_danger",
            json=payload,
            headers={"X-Eval-Token": token},
            timeout=10,
        )
        return r.status_code
    except Exception as e:
        return f"err:{e}"


def chat(session, base, payload):
    try:
        r = session.post(f"{base}/api/chat", json=payload, timeout=15)
        return r.status_code
    except Exception as e:
        return f"err:{e}"


def fire_load_events(start_ts, session, base, token, event_log):
    for off, kind, payload in LOAD_EVENTS:
        target = start_ts + off
        wait = target - time.time()
        if wait > 0:
            time.sleep(wait)
        t_fire = time.time()
        if kind == "inject":
            code = inject_danger(base, token, payload)
        elif kind == "chat":
            code = chat(session, base, payload)
        else:
            code = "skip"
        event_log.append((t_fire, kind, str(payload), str(code)))
        print(f"[load] t+{off:4d}s {kind} -> {code}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--duration", type=int, default=BASELINE_SEC + WINDOW_SEC * (len(MODES) - 1),
                    help="total run time in seconds; default = baseline + window*5 modes")
    ap.add_argument("--out", default="trace.csv")
    ap.add_argument("--events_out", default="events.csv")
    args = ap.parse_args()

    token = os.environ.get("GARUDA_EVAL_TOKEN", "")
    admin_pass = os.environ.get("GARUDA_ADMIN_PASS", "")
    if not token:
        sys.exit("GARUDA_EVAL_TOKEN not set")
    if not admin_pass:
        sys.exit("GARUDA_ADMIN_PASS not set")

    session = admin_login(args.base, args.user, admin_pass, token)

    # Clear modes before starting for a clean baseline.
    clear_all_modes(session, args.base)

    out_path = Path(args.out)
    evt_path = Path(args.events_out)
    csv_f = out_path.open("w", newline="")
    writer = csv.writer(csv_f)
    writer.writerow(["t_epoch", "t_rel", "total_frames", "window",
                     "dnd", "idle", "privacy", "emergency",
                     "alert_active", "primary_proc", "secondary_proc",
                     "secondary_drop"])

    start_ts = time.time()
    event_log = []

    # Background load thread.
    threading.Thread(
        target=fire_load_events,
        args=(start_ts, session, args.base, token, event_log),
        daemon=True,
    ).start()

    # Mode scheduler thread.
    def schedule_modes():
        boundaries = [("baseline", BASELINE_SEC)]
        for m in MODES[1:]:
            boundaries.append((m, WINDOW_SEC))
        t_cursor = 0
        for name, dur in boundaries:
            tgt = start_ts + t_cursor
            wait = tgt - time.time()
            if wait > 0:
                time.sleep(wait)
            apply_mode_window(session, args.base, name)
            print(f"[mode] t+{t_cursor:4d}s window={name} dur={dur}", flush=True)
            t_cursor += dur

    threading.Thread(target=schedule_modes, daemon=True).start()

    # 1 Hz sampling loop.
    next_tick = start_ts
    while True:
        now = time.time()
        t_rel = now - start_ts
        if t_rel >= args.duration:
            break
        try:
            r = requests.get(
                f"{args.base}/api/eval/fps_probe",
                headers={"X-Eval-Token": token},
                timeout=5,
            )
            r.raise_for_status()
            d = r.json()
            modes = d.get("modes", {})
            cas = d.get("cascade", {})
            # Derive current window label from modes (scheduler source of truth
            # is separate; we record the flag state as observed).
            if modes.get("emergency"):
                win = "emergency"
            elif modes.get("dnd"):
                win = "dnd"
            elif modes.get("idle"):
                win = "idle"
            elif modes.get("privacy"):
                win = "privacy"
            else:
                win = "active_or_baseline"
            writer.writerow([
                f"{d['t']:.3f}",
                f"{t_rel:.3f}",
                d["total_frames"],
                win,
                int(bool(modes.get("dnd"))),
                int(bool(modes.get("idle"))),
                int(bool(modes.get("privacy"))),
                int(bool(modes.get("emergency"))),
                int(bool(d.get("alert_active"))),
                cas.get("primary_processed", 0),
                cas.get("secondary_processed", 0),
                cas.get("secondary_dropped", 0),
            ])
            csv_f.flush()
        except Exception as e:
            print(f"[probe err] {e}", file=sys.stderr)
        next_tick += 1.0
        delay = next_tick - time.time()
        if delay > 0:
            time.sleep(delay)
        else:
            next_tick = time.time()

    csv_f.close()

    with evt_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_epoch", "kind", "payload", "result"])
        for row in event_log:
            w.writerow(row)

    # Final cleanup: put modes back to conservative default.
    try:
        clear_all_modes(session, args.base)
        set_mode(session, args.base, "privacy", True)
    except Exception:
        pass

    print(f"[done] wrote {out_path} ({out_path.stat().st_size} bytes) and {evt_path}")


if __name__ == "__main__":
    main()
