#!/usr/bin/env python3
"""Is Laya worth running on this Pi? Measure it against the local matcher.

A development tool, not part of the service. It routes a labelled set of
utterances (tests/eval/routing_cases.json) through a decision backend and
reports how often each answer was right, how sure the backend was when it was
right and wrong, and how long each decision took. With --url it does the same
through a Laya server (`laya-serve`, which speaks Jev's POST /v1/systemone)
and also records what running it costs this machine: its memory, the CPU
temperature, and the camera pipeline's frame rate while it works.

    python3 scripts/laya_experiment.py                               # the local matcher
    python3 scripts/laya_experiment.py --url http://127.0.0.1:8000    # Laya, and the cost
    python3 scripts/laya_experiment.py --url ... --out laya.json

Nothing here changes the house: the backends only label sentences.
"""
import argparse
import collections
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from basic_pipelines.garuda_auto.decision import (LayaBackend, LocalBackend,  # noqa: E402
                                                  routing_questions)

FIELDS = ("intent", "device", "action", "scene")
# The confidence at which the service would act on a decision without the model.
THRESHOLD = 0.85


def cpu_temp():
    try:
        out = subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True, timeout=3).stdout
        return float(out.split("=")[1].split("'")[0])
    except Exception:
        return None


def camera_fps(seconds=3.0):
    """The live service's frame rate, through its evaluation endpoint, or None."""
    try:
        import requests
        unit = subprocess.run(["systemctl", "cat", "garuda-web.service"], capture_output=True,
                              text=True, timeout=5).stdout
        token = next(l.split("=", 2)[2] for l in unit.splitlines()
                     if l.startswith("Environment=GARUDA_EVAL_TOKEN="))

        def probe():
            return requests.get("http://127.0.0.1:8080/api/eval/fps_probe",
                                headers={"X-Eval-Token": token}, timeout=5).json()
        a = probe()
        time.sleep(seconds)
        b = probe()
        return round((b["total_frames"] - a["total_frames"]) / (b["t"] - a["t"]), 1)
    except Exception:
        return None


def server_memory_mb(url):
    """Resident memory of whatever is listening on the URL's port, or None."""
    try:
        port = url.rsplit(":", 1)[1].split("/")[0]
        listing = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True, timeout=5).stdout
        line = next(l for l in listing.splitlines() if f":{port} " in l and "pid=" in l)
        pid = line.split("pid=")[1].split(",")[0]
        rss_kb = subprocess.run(["ps", "-o", "rss=", "-p", pid], capture_output=True, text=True,
                                timeout=5).stdout.strip()
        return round(int(rss_kb) / 1024)
    except Exception:
        return None


def run(backend, data):
    devices, scenes = data["devices"], data["scenes"]
    questions = routing_questions(devices, scenes)
    rows, latencies, errors = [], [], 0
    for case in data["cases"]:
        state = {"utterance": case["say"], "devices": devices, "scenes": scenes}
        started = time.perf_counter()
        try:
            out = backend.decide(state, questions)
        except Exception as exc:
            errors += 1
            rows.append({"case": case, "error": f"{type(exc).__name__}: {exc}"[:120]})
            continue
        latencies.append((time.perf_counter() - started) * 1000)
        rows.append({"case": case, "got": {f: out[f]["value"] for f in FIELDS},
                     "confidence": {f: out[f]["confidence"] for f in FIELDS}})
    return rows, latencies, errors


def report(name, rows, latencies, errors):
    scored = [r for r in rows if "got" in r]
    print(f"\n== {name}: {len(scored)} answered, {errors} errors")
    by_field = {}
    for f in FIELDS:
        judged = [r for r in scored if r["case"][f] is not None]
        right = sum(r["got"][f] == r["case"][f] for r in judged)
        by_field[f] = (right, len(judged))
        print(f"  {f:7} {right:2}/{len(judged)}")
    kinds = collections.OrderedDict()
    for r in scored:
        ok = all(r["case"][f] is None or r["got"][f] == r["case"][f] for f in FIELDS)
        k = kinds.setdefault(r["case"]["kind"], [0, 0])
        k[0] += ok
        k[1] += 1
    print("  whole decision right, by kind of sentence:")
    for kind, (ok, total) in kinds.items():
        print(f"    {kind:15} {ok:2}/{total}")
    whole = sum(k[0] for k in kinds.values())
    # What matters for acting without the model: when it says it is sure, is it right?
    sure = [r for r in scored if r["confidence"]["intent"] >= THRESHOLD]
    sure_wrong = [r for r in sure if r["got"]["intent"] != r["case"]["intent"]]
    print(f"  whole decision right: {whole}/{len(scored)}")
    print(f"  sure of the intent (>= {THRESHOLD}) on {len(sure)}/{len(scored)}; wrong while sure: {len(sure_wrong)}")
    for r in sure_wrong[:8]:
        print(f"      {r['case']['say']!r}: said {r['got']['intent']} ({r['confidence']['intent']:.2f}), "
              f"is {r['case']['intent']}")
    if latencies:
        ordered = sorted(latencies)
        p95 = ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]
        print(f"  time per decision: median {statistics.median(ordered):.0f} ms, p95 {p95:.0f} ms, "
              f"slowest {ordered[-1]:.0f} ms")
    return {"fields": by_field, "kinds": kinds, "whole": whole, "answered": len(scored), "errors": errors,
            "sure": len(sure), "sure_wrong": len(sure_wrong),
            "latency_ms": {"median": statistics.median(latencies) if latencies else None,
                           "max": max(latencies) if latencies else None}}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cases", default=str(ROOT / "tests" / "eval" / "routing_cases.json"))
    ap.add_argument("--url", help="a laya-serve address, e.g. http://127.0.0.1:8000")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--out")
    args = ap.parse_args()
    data = json.loads(Path(args.cases).read_text())
    results = {}

    local = LocalBackend(lambda: data["devices"], lambda: data["scenes"])
    results["local"] = report("local matcher", *run(local, data))

    if args.url:
        # On 2026-10-02 this run took the Pi 5 from 64 to 85 C in fifteen minutes.
        # Watch `vcgencmd measure_temp` and stop the server if it nears 85.
        before = {"temp_c": cpu_temp(), "camera_fps": camera_fps(), "load": os.getloadavg()[0]}
        print(f"\nbefore Laya works: {before}")
        laya = LayaBackend(args.url, timeout=args.timeout)
        started = time.time()
        # The first decision loads the checkpoint: time it apart from the rest.
        first = time.perf_counter()
        try:
            laya.decide({"utterance": "hello", "devices": data["devices"], "scenes": data["scenes"]},
                        routing_questions(data["devices"], data["scenes"]))
            print(f"first decision (includes loading the model): {time.perf_counter() - first:.1f} s")
        except Exception as exc:
            raise SystemExit(f"Laya did not answer at {args.url}: {type(exc).__name__}: {exc}")
        rows, latencies, errors = run(laya, data)
        during = {"temp_c": cpu_temp(), "camera_fps": camera_fps(), "load": os.getloadavg()[0],
                  "server_memory_mb": server_memory_mb(args.url)}
        results["laya"] = report("laya", rows, latencies, errors)
        results["laya"]["cost"] = {"before": before, "after": during, "seconds": round(time.time() - started, 1)}
        print(f"after Laya worked:  {during}")
        for r in rows:
            if "error" in r:
                print("  error:", r["case"]["say"], "->", r["error"])
                break
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=1, default=str))


if __name__ == "__main__":
    main()
