#!/usr/bin/env python3
"""Jev, the on-Pi router, and the two agreeing (the fast lane), on the router's labelled sets.

A development tool, not part of the service. Jev's answers are cached (the
cache file, default /tmp/jev_cache.json) so thresholds can be tuned without
paying for the same calls again. The key is read from JEV_API_KEY, or from
jev_api.txt at the repository root.

    venv_hailo_rpi5_examples/bin/python scripts/jev_eval.py
    JEV_MODEL=jev-preview MISSES=20 ... scripts/jev_eval.py
    TR=0.9 TJ=0.8 ... scripts/jev_eval.py      # list the fast lane's mistakes at these floors

Results of 2026-10-18 are in evaluation/narada_jev/.
"""
import concurrent.futures as cf, json, os, sys, time
W = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, W); sys.path.insert(0, os.path.join(W, "scripts"))
import router_eval as re_
from basic_pipelines.garuda_auto.decision import JevBackend, routing_questions
from basic_pipelines.garuda_auto.router import RouterBackend

CACHE = os.environ.get("CACHE", "/tmp/jev_cache.json")
MODEL = os.environ.get("JEV_MODEL", "jev-latest")
key = os.environ.get("JEV_API_KEY") or open(os.path.join(W, "jev_api.txt")).read().strip()
jev = JevBackend(key, timeout=20, model=MODEL)
ev = os.path.join(W, "tests", "eval")
sets = {"cases": re_.load(os.path.join(ev, "routing_cases.json")),
        "written": re_.load(os.path.join(ev, "routing_written.json"))[1::2]}
cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}

def ck(c): return MODEL + "|" + json.dumps([c["say"], c["devices"], c["scenes"]])

def jev_one(c):
    k = ck(c)
    if k in cache: return k, cache[k], 0.0
    st = {"utterance": c["say"], "devices": c["devices"], "scenes": c["scenes"]}
    for attempt in range(5):
        t = time.perf_counter()
        try:
            out = jev.decide(st, routing_questions(c["devices"], c["scenes"]))
            return k, {f: {"value": out[f]["value"], "confidence": out[f]["confidence"]} for f in re_.FIELDS}, (time.perf_counter() - t) * 1000
        except Exception as e:
            err = str(e)
            time.sleep(1.5 * (attempt + 1))
    return k, {"error": err}, 0.0

router = RouterBackend(os.path.join(W, "resources/models/narada_router"))
assert router.configured, router.error
lat = []
for name, cases in sets.items():
    todo = [c for c in cases if ck(c) not in cache]
    with cf.ThreadPoolExecutor(int(os.environ.get("WORKERS", "4"))) as ex:
        for k, v, ms in ex.map(jev_one, todo):
            cache[k] = v
            if ms: lat.append(ms)
    json.dump(cache, open(CACHE, "w"))
if lat:
    lat.sort(); print(f"jev latency: median {lat[len(lat)//2]:.0f} ms, p95 {lat[int(.95*len(lat))]:.0f} ms, n={len(lat)}")

results = {}
for name, cases in sets.items():
    rows_r, lat_r, err_r = re_.run(lambda d, s: router, cases)
    rows_j = []
    for c in cases:
        v = cache[ck(c)]
        rows_j.append({"case": c, "error": v["error"]} if "error" in v else
                      {"case": c, "got": {f: v[f]["value"] for f in re_.FIELDS}, "confidence": {f: v[f]["confidence"] for f in re_.FIELDS}})
    re_.report(f"router on {name}", rows_r, lat_r, err_r)
    re_.report(f"jev on {name}", rows_j, [], sum("error" in r for r in rows_j), misses=int(os.environ.get("MISSES", "0")))
    results[name] = list(zip(rows_r, rows_j))

ACT = ("device_control", "scene", "all_off")
def fields_for(intent): return {"device_control": ("device", "action"), "scene": ("scene",), "all_off": ()}[intent]
def fast(r, j, tr, tj):
    if "got" not in r or "got" not in j: return None
    gi = r["got"]["intent"]
    if gi not in ACT or j["got"]["intent"] != gi: return None
    fs = ("intent",) + fields_for(gi)
    if any(r["got"][f] != j["got"][f] for f in fs): return None
    if any(r["confidence"][f] < tr for f in fs) or any(j["confidence"][f] < tj for f in fs): return None
    return {f: r["got"][f] for f in fs}
def right(case, got):
    return all(case[f] is None or case[f] == v for f, v in got.items()) and case["intent"] == got["intent"]
print("\nfast lane (router and Jev agree, both sure): acted / right / WRONG, per set")
for tr in (0.85, 0.9, 0.95):
    for tj in (0.5, 0.7, 0.8, 0.9):
        line = []
        for name, pairs in results.items():
            acted = [(p[0]["case"], g) for p in pairs for g in [fast(p[0], p[1], tr, tj)] if g]
            wrong = [(c, g) for c, g in acted if not right(c, g)]
            eligible = sum(p[0]["case"]["intent"] in ACT for p in pairs)
            line.append(f"{name}: {len(acted)}/{eligible} acted, {len(wrong)} wrong")
        print(f"  router>={tr} jev>={tj}: " + " | ".join(line))
TR, TJ = float(os.environ.get("TR", "0.9")), float(os.environ.get("TJ", "0.8"))
for name, pairs in results.items():
    for p in pairs:
        g = fast(p[0], p[1], TR, TJ)
        if g and not right(p[0]["case"], g):
            c = p[0]["case"]; print(f"   WRONG @({TR},{TJ}) {c['say']!r} [{c['kind']}] acted {g}, is intent={c['intent']} dev={c['device']} act={c['action']}")
