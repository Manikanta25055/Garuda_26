#!/usr/bin/env python3
"""
Real-world deployment evaluation harness for Project Garuda (IEEE Access P1-4).

Runs for 36 h, driving every engine in Garuda_web.py via its HTTP API, tailing
the permanent log files, and producing reproducible metrics for the paper.

Usage (after `source setup_env.sh`):

    python3 evaluation/eval_36h.py --launch \
        --admin-user <u> --admin-pass <p> \
        --duration-hours 36

If Garuda_web is already running, drop --launch.

Outputs in evaluation/out/<run_id>/ :
    metrics.jsonl   10 s telemetry samples (state, fps, cpu, ram, connectivity)
    engines.jsonl   per-engine probe results
    alerts.jsonl    every DANGER detection + matched "Email alert sent"
    voice.jsonl     voice-log diffs with WER vs ground truth (if provided)
    deadman.jsonl   heartbeat + tamper events
    summary.json    aggregated metrics (written on exit / SIGTERM / 36 h)
    events.log      human-readable run log
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

REPO = Path(__file__).resolve().parent.parent
PIPELINE = REPO / "basic_pipelines" / "Garuda_web.py"
LOG_DIR = REPO / "basic_pipelines" / "system_logs"
SYS_LOG = LOG_DIR / "perm_system_log.txt"
DET_LOG = LOG_DIR / "perm_detection_log.txt"
VOX_LOG = LOG_DIR / "perm_voice_log.txt"

TS_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s*(.*)$")
DANGER_RE = re.compile(r"\[DANGER\]\s+(\S+)\s+conf=([\d.]+)")
WATCH_RE = re.compile(r"\[WATCH\]\s+(\S+)\s+conf=([\d.]+)")
ALERT_EMAIL_RE = re.compile(r"Email alert sent", re.I)
TAMPER_RE = re.compile(r"\[TAMPER\]", re.I)


# ---------------------------------------------------------------------- helpers


def now() -> float:
    return time.time()


def parse_ts(line: str) -> tuple[float, str] | None:
    m = TS_RE.match(line.strip())
    if not m:
        return None
    ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
    return ts, m.group(2)


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(
                cur[j - 1] + 1,
                prev[j] + 1,
                prev[j - 1] + (ca != cb),
            )
        prev = cur
    return prev[-1]


def wer(ref: str, hyp: str) -> float:
    r = ref.lower().split()
    h = hyp.lower().split()
    if not r:
        return 0.0 if not h else 1.0
    return levenshtein(" ".join(r), " ".join(h)) / max(1, len(r))


# ---------------------------------------------------------------------- logging


class JLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, obj: dict[str, Any]) -> None:
        obj.setdefault("t", now())
        with self._lock, self.path.open("a") as f:
            f.write(json.dumps(obj) + "\n")


# ---------------------------------------------------------------------- tailer


class Tailer(threading.Thread):
    """Tail a log file from EOF; call cb(ts, msg) for each new line."""

    def __init__(self, path: Path, cb, stop: threading.Event):
        super().__init__(daemon=True)
        self.path, self.cb, self.stop = path, cb, stop

    def run(self):
        while not self.path.exists() and not self.stop.is_set():
            time.sleep(1)
        if self.stop.is_set():
            return
        with self.path.open("r") as f:
            f.seek(0, os.SEEK_END)
            while not self.stop.is_set():
                line = f.readline()
                if not line:
                    time.sleep(0.5)
                    continue
                parsed = parse_ts(line)
                if parsed:
                    try:
                        self.cb(parsed[0], parsed[1])
                    except Exception:
                        pass


# ---------------------------------------------------------------------- client


class Client:
    def __init__(self, base_url: str, events: JLog):
        self.base = base_url.rstrip("/")
        self.events = events
        self.s = requests.Session()
        self.s.headers.update({"Accept": "application/json"})
        self._creds: tuple[str, str] | None = None

    def _req(self, method: str, path: str, **kw) -> requests.Response | None:
        kw.setdefault("timeout", 8)
        try:
            r = self.s.request(method, self.base + path, **kw)
        except Exception as e:
            self.events.write({"kind": "http_error", "path": path, "err": str(e)})
            return None
        if r.status_code == 401 and path != "/api/login" and self._creds:
            # Session evaporated (server restart). Re-login and retry once.
            u, p = self._creds
            try:
                rl = self.s.post(self.base + "/api/login",
                                 json={"username": u, "password": p}, timeout=8)
                self.events.write({"kind": "reauth", "ok": bool(rl.ok)})
                if rl.ok:
                    r = self.s.request(method, self.base + path, **kw)
            except Exception as e:
                self.events.write({"kind": "reauth_err", "err": str(e)})
        return r

    def login(self, user: str, pw: str) -> bool:
        r = self._req("POST", "/api/login", json={"username": user, "password": pw})
        ok = bool(r and r.status_code == 200)
        self.events.write({"kind": "login", "ok": ok})
        if ok:
            self._creds = (user, pw)
        return ok

    def state(self) -> dict | None:
        r = self._req("GET", "/api/state")
        return r.json() if (r and r.ok) else None

    def heartbeat(self) -> bool:
        r = self._req("GET", "/api/heartbeat")
        return bool(r and r.ok)

    def is_up(self) -> bool:
        """Cheap liveness check that does not depend on auth cookies."""
        try:
            r = self.s.get(self.base + "/api/heartbeat", timeout=3)
            return bool(r.ok)
        except Exception:
            return False

    def email_test(self) -> bool:
        r = self._req("POST", "/api/email/test")
        return bool(r and r.ok)

    def presence_refresh(self) -> dict | None:
        r = self._req("POST", "/api/presence_refresh")
        return r.json() if (r and r.ok) else None

    def devices(self) -> dict | None:
        r = self._req("GET", "/api/devices")
        return r.json() if (r and r.ok) else None

    def chat(self, msg: str) -> tuple[bool, float, str]:
        t0 = now()
        r = self._req("POST", "/api/chat", json={"message": msg}, timeout=30)
        dt = now() - t0
        body = r.text if r else ""
        return bool(r and r.ok), dt, body[:600]

    def snapshot(self) -> bool:
        r = self._req("GET", "/api/snapshot", timeout=10)
        return bool(r and r.ok and r.content)

    def clip_start(self) -> bool:
        r = self._req("POST", "/api/clip/start")
        return bool(r and r.ok)

    def clip_stop(self) -> bool:
        r = self._req("POST", "/api/clip/stop")
        return bool(r and r.ok)

    def events_stats(self) -> dict | None:
        r = self._req("GET", "/api/events/stats")
        return r.json() if (r and r.ok) else None

    def eval_inject_danger(self, token: str, label: str = "Knife",
                           confidence: float = 0.9, email: bool = False) -> dict | None:
        r = self._req("POST", "/api/eval/inject_danger",
                      json={"label": label, "confidence": confidence, "email": email},
                      headers={"X-Eval-Token": token})
        return r.json() if (r and r.ok) else None

    def eval_tag(self, token: str, tag: str, note: str = "") -> dict | None:
        r = self._req("POST", "/api/eval/tag",
                      json={"tag": tag, "note": note},
                      headers={"X-Eval-Token": token})
        return r.json() if (r and r.ok) else None


# ---------------------------------------------------------------------- runner


class Eval:
    def __init__(self, args):
        self.args = args
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        self.out = REPO / "evaluation" / "out" / run_id
        self.out.mkdir(parents=True, exist_ok=True)
        self.stop = threading.Event()
        self.metrics = JLog(self.out / "metrics.jsonl")
        self.engines = JLog(self.out / "engines.jsonl")
        self.alerts = JLog(self.out / "alerts.jsonl")
        self.voice = JLog(self.out / "voice.jsonl")
        self.deadman = JLog(self.out / "deadman.jsonl")
        self.events = JLog(self.out / "events.jsonl")
        self.evlog = (self.out / "events.log").open("a", buffering=1)
        self.client = Client(args.base_url, self.events)
        self.proc: subprocess.Popen | None = None

        # recent DANGER events awaiting email correlation
        self.pending_dangers: deque[dict] = deque()
        self.email_latencies: list[float] = []
        self.alert_count = 0
        self.email_count = 0
        self.watch_count = 0
        self.tamper_events: list[float] = []
        self.voice_lines: list[tuple[float, str]] = []
        self.ground_truth = self._load_truth()
        # suppress deadman false-positive during intentional test window
        self.deadman_test_window: tuple[float, float] | None = None

        # summary counters for periodic probes
        self.probe_counts = {"email": 0, "chat": 0, "presence": 0, "snapshot": 0,
                             "clip": 0, "heartbeat": 0}
        self.probe_fail = dict(self.probe_counts)
        self.chat_latencies: list[float] = []

    # ------------------ helpers
    def log(self, msg: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {msg}"
        print(line, flush=True)
        self.evlog.write(line + "\n")

    def _load_truth(self) -> list[dict]:
        gt = Path(self.args.voice_ground_truth) if self.args.voice_ground_truth else \
            (REPO / "evaluation" / "voice_ground_truth.json")
        if gt.exists():
            try:
                return json.loads(gt.read_text())
            except Exception:
                return []
        return []

    # ------------------ tailer callbacks
    def on_sys(self, ts: float, msg: str) -> None:
        self.events.write({"kind": "sys_log", "ts": ts, "msg": msg})
        if ALERT_EMAIL_RE.search(msg):
            self.email_count += 1
            # correlate with most recent DANGER within 120 s
            while self.pending_dangers and (ts - self.pending_dangers[0]["ts"]) > 120:
                self.pending_dangers.popleft()
            if self.pending_dangers:
                d = self.pending_dangers.popleft()
                lat = ts - d["ts"]
                self.email_latencies.append(lat)
                self.alerts.write({"kind": "email_matched", "danger": d, "email_ts": ts,
                                   "latency_s": lat})
        if TAMPER_RE.search(msg):
            self.tamper_events.append(ts)
            in_test = self.deadman_test_window and \
                self.deadman_test_window[0] <= ts <= self.deadman_test_window[1]
            self.deadman.write({"kind": "tamper", "ts": ts, "expected": bool(in_test)})

    def on_det(self, ts: float, msg: str) -> None:
        md = DANGER_RE.search(msg)
        mw = WATCH_RE.search(msg)
        if md:
            self.alert_count += 1
            d = {"kind": "danger", "ts": ts, "label": md.group(1),
                 "conf": float(md.group(2))}
            self.pending_dangers.append(d)
            self.alerts.write(d)
        elif mw:
            self.watch_count += 1

    def on_vox(self, ts: float, msg: str) -> None:
        self.voice_lines.append((ts, msg))
        self.voice.write({"kind": "voice_log", "ts": ts, "msg": msg})

    # ------------------ periodic workers
    def heartbeat_loop(self) -> None:
        while not self.stop.is_set():
            test = self.deadman_test_window
            skip = test and test[0] <= now() <= test[1]
            if not skip:
                ok = self.client.heartbeat()
                self.probe_counts["heartbeat"] += 1
                if not ok:
                    self.probe_fail["heartbeat"] += 1
            time.sleep(self.args.heartbeat_interval)

    def state_loop(self) -> None:
        while not self.stop.is_set():
            s = self.client.state()
            if s:
                self.metrics.write({
                    "kind": "state",
                    "alert_active": s.get("alert_active"),
                    "camera_blind": s.get("camera_blind"),
                    "connectivity": s.get("connectivity"),
                    "uptime": s.get("uptime"),
                    "detection_log_count": s.get("detection_log_count"),
                    "presence_log_count": s.get("presence_log_count"),
                    "modes": {k: s.get(k) for k in (
                        "MODE_DND", "MODE_EMAIL_OFF", "MODE_IDLE",
                        "MODE_NIGHT", "MODE_EMERGENCY", "MODE_PRIVACY")
                        if k in s},
                    "clip_recording": s.get("clip_recording"),
                })
            time.sleep(self.args.state_interval)

    def inject_loop(self) -> None:
        """Periodically inject synthetic DANGER events for ground-truth latency."""
        token = self.args.eval_token
        interval = self.args.inject_interval
        labels = ["Knife", "scissors", "Hammer"]
        i = 0
        while not self.stop.is_set():
            if self.stop.wait(interval):
                return
            label = labels[i % len(labels)]
            i += 1
            t0 = now()
            resp = self.client.eval_inject_danger(token, label=label, confidence=0.9)
            dt = now() - t0
            self.events.write({"kind": "eval_inject", "label": label,
                               "ok": bool(resp), "rtt_ms": round(dt * 1000, 2),
                               "resp": resp})

    def watchdog_loop(self) -> None:
        """Detect prolonged server downtime and restart via systemctl.

        Polls /api/heartbeat every `watchdog_interval` seconds. If the server
        is unreachable for `watchdog_failures` consecutive polls, restart the
        `gw_service` systemd unit (if set). After `watchdog_max_restarts`
        unsuccessful recovery cycles inside one rolling hour, abort the run.
        """
        if self.args.watchdog_failures <= 0:
            return
        fails = 0
        restarts: deque[float] = deque(maxlen=self.args.watchdog_max_restarts)
        while not self.stop.is_set():
            time.sleep(self.args.watchdog_interval)
            if self.client.is_up():
                if fails:
                    self.log(f"watchdog: server back up after {fails} failures")
                fails = 0
                continue
            fails += 1
            if fails < self.args.watchdog_failures:
                continue
            # threshold tripped
            now_t = now()
            restarts.append(now_t)
            recent = [r for r in restarts if now_t - r < 3600]
            if len(recent) > self.args.watchdog_max_restarts:
                self.log("watchdog: exceeded max restarts in 1 h — aborting run")
                self.events.write({"kind": "watchdog_abort",
                                   "restarts_in_last_hour": len(recent)})
                self.stop.set()
                return
            svc = self.args.gw_service
            self.log(f"watchdog: server down for {fails} polls — "
                     f"restart #{len(recent)} via systemctl {svc}")
            self.events.write({"kind": "watchdog_restart",
                               "service": svc, "attempt": len(recent)})
            try:
                subprocess.run(["sudo", "-n", "systemctl", "restart", svc],
                               check=False, timeout=30)
            except Exception as e:
                self.log(f"watchdog: systemctl restart failed: {e}")
            # give the server up to 120 s to come back
            recovered = False
            for _ in range(60):
                if self.stop.is_set():
                    return
                time.sleep(2)
                if self.client.is_up():
                    recovered = True
                    break
            if recovered:
                self.log("watchdog: server recovered")
                self.events.write({"kind": "watchdog_recovered"})
                fails = 0
            else:
                self.log("watchdog: server did NOT recover after restart")
                fails = 0  # reset so we don't spin-restart; next cycle will re-check

    def probe_loop(self) -> None:
        """Scheduled probes running every N seconds."""
        last = {k: 0.0 for k in ("email", "chat", "presence", "snapshot",
                                 "clip_once", "deadman_once")}
        cadence = {
            "presence": 30 * 60,
            "snapshot": 60 * 60,
            "email": 4 * 60 * 60,
            "chat": 60 * 60,
        }
        start = now()
        while not self.stop.is_set():
            t = now()
            if t - last["presence"] > cadence["presence"]:
                r = self.client.presence_refresh()
                self._record_engine("presence", bool(r), r or {})
                last["presence"] = t
            if t - last["snapshot"] > cadence["snapshot"]:
                ok = self.client.snapshot()
                self._record_engine("snapshot", ok, {})
                last["snapshot"] = t
            if t - last["email"] > cadence["email"]:
                ok = self.client.email_test()
                self._record_engine("email", ok, {})
                last["email"] = t
            if t - last["chat"] > cadence["chat"]:
                ok, dt, body = self.client.chat(
                    "Briefly: what modes are active right now?")
                self.chat_latencies.append(dt)
                self._record_engine("chat", ok, {"latency_s": dt, "body": body})
                last["chat"] = t
            # one-shot clip test at hour 6
            if not last["clip_once"] and (t - start) > 6 * 3600:
                a = self.client.clip_start()
                time.sleep(8)
                b = self.client.clip_stop()
                self._record_engine("clip", a and b, {"start": a, "stop": b})
                last["clip_once"] = t
            # one-shot deadman false-trigger test at hour 12
            if not last["deadman_once"] and (t - start) > 12 * 3600:
                self._run_deadman_test()
                last["deadman_once"] = t
            # intent probes via /api/chat using voice ground truth commands
            time.sleep(10)

    def _record_engine(self, name: str, ok: bool, extra: dict) -> None:
        self.probe_counts[name if name in self.probe_counts else "chat"] = \
            self.probe_counts.get(name, 0) + 1
        if not ok:
            self.probe_fail[name if name in self.probe_fail else "chat"] = \
                self.probe_fail.get(name, 0) + 1
        self.engines.write({"engine": name, "ok": ok, **extra})

    def _run_deadman_test(self) -> None:
        """Stop heartbeat for 4 min; tamper alert should fire after 180 s."""
        self.log("deadman test: suppressing heartbeat for 240 s")
        t0 = now()
        self.deadman_test_window = (t0, t0 + 240)
        self.deadman.write({"kind": "test_begin", "ts": t0})
        # heartbeat loop sees window and skips
        time.sleep(240)
        self.deadman_test_window = None
        self.deadman.write({"kind": "test_end", "ts": now()})
        self.log("deadman test: heartbeat resumed")

    # ------------------ voice evaluation
    def voice_summary(self) -> dict:
        if not self.ground_truth:
            return {"ground_truth": False, "lines": len(self.voice_lines)}
        # pick heard utterances (lines that look like wake-word + command)
        heard = [m for _, m in self.voice_lines
                 if any(k in m.lower() for k in ("heard:", "recognized:", "command:"))]
        matches = []
        for gt in self.ground_truth:
            best = None
            for h in heard:
                dist = wer(gt.get("text", ""), h)
                if best is None or dist < best[1]:
                    best = (h, dist)
            if best:
                matches.append({"ref": gt.get("text"), "hyp": best[0],
                                "wer": best[1], "intent_ok":
                                gt.get("intent", "").lower() in best[0].lower()})
        mean_wer = sum(m["wer"] for m in matches) / max(1, len(matches))
        intent_acc = sum(1 for m in matches if m["intent_ok"]) / max(1, len(matches))
        return {
            "ground_truth": True,
            "samples": len(self.ground_truth),
            "matched": len(matches),
            "mean_wer": mean_wer,
            "intent_accuracy": intent_acc,
        }

    # ------------------ lifecycle
    def launch_pipeline(self) -> None:
        if not self.args.launch:
            return
        env = os.environ.copy()
        cmd = [sys.executable, str(PIPELINE)]
        self.log(f"launching {PIPELINE}")
        self.proc = subprocess.Popen(
            cmd, cwd=str(REPO),
            stdout=(self.out / "garuda.stdout.log").open("w"),
            stderr=subprocess.STDOUT, env=env,
        )
        # wait for /api/state to respond
        for _ in range(60):
            if self.stop.is_set():
                return
            if self.client.state() is not None:
                self.log("pipeline up")
                return
            time.sleep(2)
        self.log("WARN: pipeline did not become ready within 120 s")

    def shutdown(self) -> None:
        self.stop.set()
        summary = self.build_summary()
        (self.out / "summary.json").write_text(json.dumps(summary, indent=2))
        self.log(f"summary written to {self.out / 'summary.json'}")
        if self.proc:
            self.log("terminating garuda process")
            try:
                self.proc.terminate()
                self.proc.wait(timeout=15)
            except Exception:
                self.proc.kill()
        try:
            self.evlog.close()
        except Exception:
            pass

    def build_summary(self) -> dict:
        expected_tamper = len([t for t in self.tamper_events
                               if self.deadman_test_window and False])  # computed below
        real_tamper = 0
        test_tamper = 0
        for t in self.tamper_events:
            window = self.deadman_test_window
            if window and window[0] <= t <= window[1]:
                test_tamper += 1
            else:
                real_tamper += 1
        lat = self.email_latencies
        return {
            "run_id": self.out.name,
            "duration_s": now() - self._t0,
            "alerts": {
                "danger_total": self.alert_count,
                "watch_total": self.watch_count,
                "emails_sent": self.email_count,
                "matched_pairs": len(lat),
                "alert_to_email_latency_s": {
                    "n": len(lat),
                    "mean": sum(lat) / len(lat) if lat else None,
                    "min": min(lat) if lat else None,
                    "max": max(lat) if lat else None,
                },
                "false_alarms": "requires_manual_label "
                                "(inspect alerts.jsonl)",
            },
            "deadman": {
                "tamper_events_total": len(self.tamper_events),
                "tamper_during_test": test_tamper,
                "tamper_outside_test": real_tamper,
                "false_trigger_rate_per_hour":
                    real_tamper / max(1e-9, (now() - self._t0) / 3600),
            },
            "engines_probed": self.probe_counts,
            "engines_failed": self.probe_fail,
            "chat_latency_s": {
                "n": len(self.chat_latencies),
                "mean": sum(self.chat_latencies) / len(self.chat_latencies)
                    if self.chat_latencies else None,
            },
            "voice": self.voice_summary(),
            "out_dir": str(self.out),
        }

    def run(self) -> None:
        self._t0 = now()
        signal.signal(signal.SIGTERM, lambda *_: self.stop.set())
        signal.signal(signal.SIGINT, lambda *_: self.stop.set())

        self.launch_pipeline()

        if self.args.admin_user and self.args.admin_pass:
            self.client.login(self.args.admin_user, self.args.admin_pass)

        # tailers
        for path, cb in [(SYS_LOG, self.on_sys), (DET_LOG, self.on_det),
                         (VOX_LOG, self.on_vox)]:
            Tailer(path, cb, self.stop).start()

        # workers
        threading.Thread(target=self.heartbeat_loop, daemon=True).start()
        threading.Thread(target=self.state_loop, daemon=True).start()
        threading.Thread(target=self.probe_loop, daemon=True).start()
        threading.Thread(target=self.watchdog_loop, daemon=True).start()
        if self.args.eval_token and self.args.inject_interval > 0:
            threading.Thread(target=self.inject_loop, daemon=True).start()

        deadline = self._t0 + self.args.duration_hours * 3600
        self.log(f"evaluation running; deadline in {self.args.duration_hours} h")
        while not self.stop.is_set() and now() < deadline:
            time.sleep(5)

        self.log("finalising")
        self.shutdown()


# ---------------------------------------------------------------------- main


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://127.0.0.1:8080")
    p.add_argument("--duration-hours", type=float, default=24.0)
    p.add_argument("--launch", action="store_true",
                   help="spawn Garuda_web.py in this environment")
    p.add_argument("--admin-user", default=os.environ.get("GARUDA_ADMIN_USER", ""))
    p.add_argument("--admin-pass", default=os.environ.get("GARUDA_ADMIN_PASS", ""))
    p.add_argument("--state-interval", type=int, default=10)
    p.add_argument("--heartbeat-interval", type=int, default=10)
    p.add_argument("--voice-ground-truth", default="")
    # watchdog: detect server crash and restart it via systemd.
    p.add_argument("--gw-service", default="garuda-web",
                   help="systemd unit to restart when server is unreachable")
    p.add_argument("--watchdog-interval", type=int, default=10,
                   help="seconds between watchdog heartbeat polls")
    p.add_argument("--watchdog-failures", type=int, default=18,
                   help="consecutive failed polls before restart (0 to disable)")
    p.add_argument("--watchdog-max-restarts", type=int, default=5,
                   help="abort run if more than N restarts occur in 1 h")
    p.add_argument("--eval-token", default=os.environ.get("GARUDA_EVAL_TOKEN", ""),
                   help="shared token for /api/eval/* endpoints")
    p.add_argument("--inject-interval", type=int, default=0,
                   help="seconds between synthetic DANGER injects (0 to disable)")
    args = p.parse_args()

    if args.launch and not os.environ.get("TAPPAS_WORKSPACE"):
        print("ERROR: source setup_env.sh before --launch (TAPPAS_WORKSPACE unset)",
              file=sys.stderr)
        return 2

    ev = Eval(args)
    try:
        ev.run()
    finally:
        if not ev.stop.is_set():
            ev.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
