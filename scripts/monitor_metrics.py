#!/usr/bin/env python3
"""
Garuda system metrics monitor.
Logs system and Garuda-specific metrics every 10s to a CSV.
Appends to existing file on restart (survives reboots via systemd).

Metrics tracked:
  - CPU % (overall + per-core)
  - RAM % and usage (MB)
  - CPU temperature (C)
  - Disk usage %
  - Network I/O (bytes sent/recv since last sample)
  - Inference FPS (from Garuda API, if running)
  - Garuda uptime
  - Throttle state (vcgencmd on RPi)
  - Process count

Usage:
    python monitor_metrics.py                    # 48h, 10s interval
    python monitor_metrics.py --hours 24         # custom duration
    python monitor_metrics.py --interval 30      # custom interval
    python monitor_metrics.py --garuda-user X --garuda-pass Y  # auth for FPS
"""

import argparse
import csv
import os
import signal
import subprocess
import sys
import time
from datetime import datetime

import psutil

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

GARUDA_URL = "http://127.0.0.1:8080"
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "metrics_log.csv")

FIELDNAMES = [
    "timestamp",
    "cpu_percent", "cpu_core0", "cpu_core1", "cpu_core2", "cpu_core3",
    "ram_percent", "ram_used_mb", "ram_total_mb",
    "temp_c", "throttled",
    "disk_percent", "disk_used_gb", "disk_total_gb",
    "net_sent_kb", "net_recv_kb",
    "inference_fps", "garuda_uptime_s", "garuda_running",
    "process_count", "load_avg_1m", "load_avg_5m", "load_avg_15m",
    "swap_percent",
]

_running = True
_session_cookie = None
_last_net = None


def _signal_handler(sig, frame):
    global _running
    _running = False


def get_cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except Exception:
        return None


def get_throttle_state():
    try:
        out = subprocess.check_output(["vcgencmd", "get_throttled"], timeout=3, text=True)
        val = out.strip().split("=")[-1]
        return val != "0x0"
    except Exception:
        return None


def garuda_login(user, passwd):
    global _session_cookie
    if not HAS_REQUESTS or not user or not passwd:
        return False
    try:
        r = requests.post(f"{GARUDA_URL}/api/login",
                          json={"username": user, "password": passwd}, timeout=5)
        if r.status_code == 200:
            _session_cookie = r.cookies.get("garuda_session")
            return True
    except Exception:
        pass
    return False


def get_garuda_state():
    global _session_cookie
    if not HAS_REQUESTS or not _session_cookie:
        try:
            r = requests.get(f"{GARUDA_URL}/api/heartbeat", timeout=3)
            if r.status_code == 200:
                d = r.json()
                return {"garuda_running": True, "garuda_uptime_s": d.get("uptime"),
                        "inference_fps": None}
        except Exception:
            pass
        return {"garuda_running": False, "garuda_uptime_s": None, "inference_fps": None}
    try:
        r = requests.get(f"{GARUDA_URL}/api/state",
                         cookies={"garuda_session": _session_cookie}, timeout=5)
        if r.status_code == 200:
            d = r.json()
            return {"garuda_running": True,
                    "garuda_uptime_s": d.get("uptime_seconds"),
                    "inference_fps": d.get("inference_fps")}
        if r.status_code == 401:
            _session_cookie = None
    except Exception:
        pass
    return {"garuda_running": False, "garuda_uptime_s": None, "inference_fps": None}


def collect_sample():
    global _last_net

    cpu_overall = psutil.cpu_percent(interval=1)
    cores = psutil.cpu_percent(percpu=True, interval=None)
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage("/")
    load = os.getloadavg()
    temp = get_cpu_temp()
    throttled = get_throttle_state()

    net = psutil.net_io_counters()
    if _last_net:
        sent_kb = round((net.bytes_sent - _last_net.bytes_sent) / 1024, 1)
        recv_kb = round((net.bytes_recv - _last_net.bytes_recv) / 1024, 1)
    else:
        sent_kb = 0
        recv_kb = 0
    _last_net = net

    garuda = get_garuda_state()

    core_vals = [round(c, 1) for c in (cores + [0, 0, 0, 0])[:4]]

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cpu_percent": cpu_overall,
        "cpu_core0": core_vals[0],
        "cpu_core1": core_vals[1],
        "cpu_core2": core_vals[2],
        "cpu_core3": core_vals[3],
        "ram_percent": round(mem.percent, 1),
        "ram_used_mb": round(mem.used / (1024 ** 2), 1),
        "ram_total_mb": round(mem.total / (1024 ** 2), 1),
        "temp_c": temp,
        "throttled": throttled,
        "disk_percent": round(disk.percent, 1),
        "disk_used_gb": round(disk.used / (1024 ** 3), 1),
        "disk_total_gb": round(disk.total / (1024 ** 3), 1),
        "net_sent_kb": sent_kb,
        "net_recv_kb": recv_kb,
        "inference_fps": garuda.get("inference_fps"),
        "garuda_uptime_s": garuda.get("garuda_uptime_s"),
        "garuda_running": garuda.get("garuda_running"),
        "process_count": len(psutil.pids()),
        "load_avg_1m": round(load[0], 2),
        "load_avg_5m": round(load[1], 2),
        "load_avg_15m": round(load[2], 2),
        "swap_percent": round(swap.percent, 1),
    }


def main():
    parser = argparse.ArgumentParser(description="Garuda system metrics monitor")
    parser.add_argument("--hours", type=float, default=48)
    parser.add_argument("--interval", type=int, default=10)
    parser.add_argument("--output", type=str, default=OUTPUT_FILE)
    parser.add_argument("--garuda-user", type=str, default=os.environ.get("GARUDA_MON_USER", ""))
    parser.add_argument("--garuda-pass", type=str, default=os.environ.get("GARUDA_MON_PASS", ""))
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    if args.garuda_user and args.garuda_pass:
        garuda_login(args.garuda_user, args.garuda_pass)

    duration_s = args.hours * 3600
    start = time.monotonic()
    file_exists = os.path.exists(args.output) and os.path.getsize(args.output) > 0

    with open(args.output, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()

        print(f"Monitoring: {args.hours}h @ {args.interval}s → {args.output}")
        if _session_cookie:
            print("Garuda auth: OK (FPS tracking enabled)")
        else:
            print("Garuda auth: none (FPS from heartbeat only — pass --garuda-user/--garuda-pass for full stats)")

        sample_count = 0
        relogin_interval = 3600
        last_relogin = time.monotonic()

        while _running and (time.monotonic() - start) < duration_s:
            sample = collect_sample()
            writer.writerow(sample)
            f.flush()
            sample_count += 1

            if (args.garuda_user and args.garuda_pass and
                    not _session_cookie and
                    time.monotonic() - last_relogin > relogin_interval):
                garuda_login(args.garuda_user, args.garuda_pass)
                last_relogin = time.monotonic()

            elapsed_h = (time.monotonic() - start) / 3600
            remaining_h = args.hours - elapsed_h
            sys.stdout.write(
                f"\r[{sample['timestamp']}] CPU={sample['cpu_percent']}% "
                f"RAM={sample['ram_percent']}% Temp={sample['temp_c']}C "
                f"FPS={sample['inference_fps']} "
                f"({remaining_h:.1f}h left, {sample_count} samples)")
            sys.stdout.flush()

            sleep_until = start + sample_count * args.interval
            wait = sleep_until - time.monotonic()
            if wait > 0:
                time.sleep(wait)

    elapsed = (time.monotonic() - start) / 3600
    print(f"\nDone. {sample_count} samples over {elapsed:.1f}h → {args.output}")


if __name__ == "__main__":
    main()
