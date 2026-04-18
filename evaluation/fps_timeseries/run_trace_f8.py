#!/usr/bin/env python3
# F8 variant of run_trace.py — adds a realistic concurrent-load profile:
#   5 SMTP alerts (inject_danger with email=True)
#   3 SSH clip uploads (clip/start -> sleep -> clip/stop triggers exfiltrate_clip)
#   3 voice/chat queries
#   1 sustained MJPEG stream held open for the trace duration (WebRTC-equivalent
#   outbound-streaming load — aiortc peer setup is heavier than needed for this)
#
# Otherwise identical to run_trace.py: 1 Hz probe of /api/eval/fps_probe, mode
# cycle (baseline 60s -> idle 120s -> dnd 120s -> privacy 120s -> emergency 120s
# -> active 120s, total 720s).
import argparse
import csv
import os
import sys
import time
import threading
from pathlib import Path

import requests

MODES = ["baseline", "idle", "dnd", "privacy", "emergency", "active"]
WINDOW_SEC = 120
BASELINE_SEC = 60

# Spacing respects the 60s global EMAIL_COOLDOWN and keeps SMTP events out of
# idle/dnd windows (where send_email_alert short-circuits).  Clip-start/stop
# pairs trigger encryption + paramiko SFTP on stop.
LOAD_EVENTS = [
    (30,  "inject_email", {"label": "Knife",    "confidence": 0.92, "email": True}),
    (90,  "chat",         {"message": "what is the current status"}),
    (150, "clip_start",   {}),
    (165, "clip_stop",    {}),
    (270, "chat",         {"message": "turn on privacy mode"}),
    (300, "clip_start",   {}),
    (315, "clip_stop",    {}),
    (330, "inject_email", {"label": "Hammer",   "confidence": 0.88, "email": True}),
    (420, "inject_email", {"label": "Scissors", "confidence": 0.91, "email": True}),
    (510, "inject_email", {"label": "Knife",    "confidence": 0.95, "email": True}),
    (540, "clip_start",   {}),
    (555, "clip_stop",    {}),
    (600, "inject_email", {"label": "Hammer",   "confidence": 0.87, "email": True}),
    (660, "chat",         {"message": "how many detections today"}),
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
            timeout=20,
        )
        return r.status_code
    except Exception as e:
        return f"err:{e}"


def clip_start(session, base):
    try:
        r = session.post(f"{base}/api/clip/start", timeout=10)
        return r.status_code
    except Exception as e:
        return f"err:{e}"


def clip_stop(session, base):
    try:
        r = session.post(f"{base}/api/clip/stop", timeout=10)
        return r.status_code
    except Exception as e:
        return f"err:{e}"


def chat(session, base, payload):
    try:
        r = session.post(f"{base}/api/chat", json=payload, timeout=20)
        return r.status_code
    except Exception as e:
        return f"err:{e}"


def mjpeg_holder(session, base, duration, stop_evt):
    # Pull MJPEG continuously; this is the WebRTC-equivalent outbound-streaming
    # load for the duration of the trace.
    deadline = time.time() + duration
    try:
        with session.get(f"{base}/stream", stream=True, timeout=(10, None)) as resp:
            if resp.status_code != 200:
                print(f"[mjpeg] non-200: {resp.status_code}", file=sys.stderr)
                return
            bytes_read = 0
            for chunk in resp.iter_content(chunk_size=65536):
                bytes_read += len(chunk) if chunk else 0
                if stop_evt.is_set() or time.time() >= deadline:
                    break
            print(f"[mjpeg] closed after {bytes_read/1e6:.1f} MB", flush=True)
    except Exception as e:
        print(f"[mjpeg] {e}", file=sys.stderr)


def fire_load_events(start_ts, session, base, token, event_log):
    for off, kind, payload in LOAD_EVENTS:
        target = start_ts + off
        wait = target - time.time()
        if wait > 0:
            time.sleep(wait)
        t_fire = time.time()
        if kind == "inject_email":
            code = inject_danger(base, token, payload)
        elif kind == "clip_start":
            code = clip_start(session, base)
        elif kind == "clip_stop":
            code = clip_stop(session, base)
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
    ap.add_argument("--duration", type=int, default=BASELINE_SEC + WINDOW_SEC * (len(MODES) - 1))
    ap.add_argument("--out", default="trace_f8.csv")
    ap.add_argument("--events_out", default="events_f8.csv")
    args = ap.parse_args()

    token = os.environ.get("GARUDA_EVAL_TOKEN", "")
    admin_pass = os.environ.get("GARUDA_ADMIN_PASS", "")
    if not token:
        sys.exit("GARUDA_EVAL_TOKEN not set")
    if not admin_pass:
        sys.exit("GARUDA_ADMIN_PASS not set")

    session = admin_login(args.base, args.user, admin_pass, token)
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
    stop_evt = threading.Event()

    threading.Thread(
        target=fire_load_events,
        args=(start_ts, session, args.base, token, event_log),
        daemon=True,
    ).start()

    # WebRTC-equivalent: a persistent MJPEG pull over the trace duration.
    mjpeg_session = admin_login(args.base, args.user, admin_pass, token)
    threading.Thread(
        target=mjpeg_holder,
        args=(mjpeg_session, args.base, args.duration, stop_evt),
        daemon=True,
    ).start()

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

    stop_evt.set()
    csv_f.close()

    with evt_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_epoch", "kind", "payload", "result"])
        for row in event_log:
            w.writerow(row)

    try:
        clear_all_modes(session, args.base)
        set_mode(session, args.base, "privacy", True)
    except Exception:
        pass

    print(f"[done] wrote {out_path} ({out_path.stat().st_size} bytes) and {evt_path}")


if __name__ == "__main__":
    main()
