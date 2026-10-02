#!/usr/bin/env python3
"""How well does each decision backend route a sentence, and what does it cost the Pi?

A development tool, not part of the service. It routes two labelled sets
through the literal matcher and through the routing model
(garuda_auto/router.py) and reports, for each: how often the whole decision
was right, by kind of sentence; how often it was sure and wrong; and how long
a decision took. For the model it also reports the memory it holds and what
the CPU and the camera did while it worked.

    python3 scripts/router_eval.py                # both backends, both sets
    python3 scripts/router_eval.py --misses 20    # and the sentences each got wrong
    python3 scripts/router_eval.py --out router.json

The sets: tests/eval/routing_cases.json (50 sentences in one house, written
by hand) and tests/eval/routing_written.json (sentences NIM wrote, each in its
own made-up house; the model was tuned on the even-numbered half, so only the
odd half is scored here). Nothing here changes the house.

For the record, Laya 0.3.23 (github.com/NandhaKishorM/laya, 421 M parameters,
base checkpoints) was measured on the first set on this Pi 5 on 2026-10-02:
14 of 49 right, about 15 s a decision, 5.5 GB resident, and the CPU went from
64 to 85 C and its soft temperature limit in fifteen minutes. It was removed.
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

from basic_pipelines.garuda_auto.decision import LocalBackend, routing_questions  # noqa: E402
from basic_pipelines.garuda_auto.router import RouterBackend  # noqa: E402

FIELDS = ("intent", "device", "action", "scene")
# The confidence at which a decision is called sure (the engine's default threshold).
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


def memory_mb():
    """This process's resident memory."""
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return round(int(line.split()[1]) / 1024)
    return None


def load(path):
    """Cases of a set, each with the house it is said in."""
    data = json.loads(Path(path).read_text())
    return [{"devices": data.get("devices", []), "scenes": data.get("scenes", []), **case}
            for case in data["cases"]]


def run(make_backend, cases):
    rows, latencies, errors = [], [], 0
    for case in cases:
        devices, scenes = case["devices"], case["scenes"]
        state = {"utterance": case["say"], "devices": devices, "scenes": scenes}
        backend = make_backend(devices, scenes)
        started = time.perf_counter()
        try:
            out = backend.decide(state, routing_questions(devices, scenes))
        except Exception as exc:
            errors += 1
            rows.append({"case": case, "error": f"{type(exc).__name__}: {exc}"[:120]})
            continue
        latencies.append((time.perf_counter() - started) * 1000)
        rows.append({"case": case, "got": {f: out[f]["value"] for f in FIELDS},
                     "confidence": {f: out[f]["confidence"] for f in FIELDS}})
    return rows, latencies, errors


def report(name, rows, latencies, errors, misses=0):
    scored = [r for r in rows if "got" in r]
    print(f"\n== {name}: {len(scored)} answered, {errors} errors")
    by_field = {}
    for f in FIELDS:
        judged = [r for r in scored if r["case"][f] is not None]
        right = sum(r["got"][f] == r["case"][f] for r in judged)
        by_field[f] = (right, len(judged))
    print("  " + "   ".join(f"{f} {a}/{b}" for f, (a, b) in by_field.items()))
    kinds, wrong = collections.OrderedDict(), []
    for r in scored:
        ok = all(r["case"][f] is None or r["got"][f] == r["case"][f] for f in FIELDS)
        k = kinds.setdefault(r["case"]["kind"], [0, 0])
        k[0] += ok
        k[1] += 1
        if not ok:
            wrong.append(r)
    print("  whole decision right, by kind of sentence:")
    for kind, (ok, total) in sorted(kinds.items()):
        print(f"    {kind:15} {ok:3}/{total}")
    whole = sum(k[0] for k in kinds.values())
    # What matters before anything acts on a decision: when it says it is sure, is it right?
    sure = [r for r in scored if r["confidence"]["intent"] >= THRESHOLD]
    sure_wrong = [r for r in sure if r["got"]["intent"] != r["case"]["intent"]]
    print(f"  whole decision right: {whole}/{len(scored)}")
    print(f"  sure of the intent (>= {THRESHOLD}) on {len(sure)}/{len(scored)}; wrong while sure: {len(sure_wrong)}")
    for r in sure_wrong[:8]:
        print(f"      {r['case']['say']!r}: said {r['got']['intent']} ({r['confidence']['intent']:.2f}), "
              f"is {r['case']['intent']}")
    for r in wrong[:misses]:
        bad = {f: r["got"][f] for f in FIELDS if r["case"][f] is not None and r["got"][f] != r["case"][f]}
        print(f"    wrong: {r['case']['say']!r} [{r['case']['kind']}] {bad}, is { {f: r['case'][f] for f in bad} }")
    if latencies:
        ordered = sorted(latencies)
        p95 = ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]
        print(f"  time per decision: median {statistics.median(ordered):.1f} ms, p95 {p95:.1f} ms, "
              f"slowest {ordered[-1]:.1f} ms")
    return {"fields": by_field, "kinds": kinds, "whole": whole, "answered": len(scored), "errors": errors,
            "sure": len(sure), "sure_wrong": len(sure_wrong),
            "latency_ms": {"median": statistics.median(latencies) if latencies else None,
                           "max": max(latencies) if latencies else None}}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", help="the routing model's folder (default: resources/models/narada_router)")
    ap.add_argument("--misses", type=int, default=0, help="print this many wrong decisions for each run")
    ap.add_argument("--out")
    args = ap.parse_args()
    eval_dir = ROOT / "tests" / "eval"
    sets = {"routing_cases": load(eval_dir / "routing_cases.json")}
    if (eval_dir / "routing_written.json").exists():
        sets["routing_written (test half)"] = load(eval_dir / "routing_written.json")[1::2]
    results = {}

    for name, cases in sets.items():
        local = lambda devices, scenes: LocalBackend(lambda: devices, lambda: scenes)  # noqa: E731
        results[f"local / {name}"] = report(f"literal matcher on {name}", *run(local, cases), misses=args.misses)

    before = {"temp_c": cpu_temp(), "camera_fps": camera_fps(), "load": os.getloadavg()[0], "memory_mb": memory_mb()}
    started = time.perf_counter()
    router = RouterBackend(args.model)
    if not router.configured:
        raise SystemExit(f"the routing model is not loaded: {router.error}")
    print(f"\nrouting model loaded in {time.perf_counter() - started:.2f} s "
          f"({router.model.meta.get('base')}); before it works: {before}")
    for name, cases in sets.items():
        results[f"router / {name}"] = report(f"routing model on {name}", *run(lambda d, s: router, cases),
                                             misses=args.misses)
    # Is it the planner's work? The question the agent asks of the intent
    # (agent.PLANNER_INTENTS, at the engine's threshold), on sentences typed by hand.
    build_path = eval_dir / "routing_build.json"
    if build_path.exists():
        cases = load(build_path)
        rows, _, _ = run(lambda d, s: router, cases)
        sent = lambda r: (r["got"]["intent"] in ("build", "automation_rule")       # noqa: E731
                          and r["confidence"]["intent"] >= THRESHOLD)
        yes = [r for r in rows if r["case"]["build"]]
        no = [r for r in rows if not r["case"]["build"]]
        reached, wrongly = [r for r in yes if sent(r)], [r for r in no if sent(r)]
        print(f"\n== planner routing on routing_build: {len(reached)}/{len(yes)} of the planner's jobs sent "
              f"to it; {len(wrongly)}/{len(no)} others sent there by mistake")
        for r in [r for r in yes if not sent(r)][:10] + wrongly[:10]:
            print(f"      {r['case']['say']!r}: {r['got']['intent']} ({r['confidence']['intent']:.2f})")
        results["planner routing"] = {"jobs": len(yes), "sent": len(reached), "others": len(no),
                                      "sent_by_mistake": len(wrongly)}
    after = {"temp_c": cpu_temp(), "camera_fps": camera_fps(), "load": os.getloadavg()[0], "memory_mb": memory_mb()}
    print(f"\nafter it worked: {after}")
    results["cost"] = {"before": before, "after": after}
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=1, default=str))


if __name__ == "__main__":
    main()
