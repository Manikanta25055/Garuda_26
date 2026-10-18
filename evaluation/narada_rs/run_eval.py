"""Narada-RS evaluation harness. Run on the Pi from the repository root:

    python3 -m evaluation.narada_rs.run_eval synth   --trials 3
    python3 -m evaluation.narada_rs.run_eval matcher
    python3 -m evaluation.narada_rs.run_eval reflex

Every subcommand writes one JSON file under evaluation/out/narada_rs/. Nothing
here touches the live rule store, the device registry or a GPIO pin: rules go
to a temporary file and the registry is an in-memory lamp and fan.
"""
import argparse
import json
import os
import platform
import random
import statistics
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from basic_pipelines.garuda_auto import nim_client as nim_module          # noqa: E402
from basic_pipelines.garuda_auto.matcher import LocalMatcher, _EMBED_MODEL  # noqa: E402
from basic_pipelines.garuda_auto.rule_engine import RuleEngine           # noqa: E402
from basic_pipelines.garuda_auto.rule_schema import build_schema         # noqa: E402
from basic_pipelines.garuda_auto.rule_store import RuleStore             # noqa: E402
from evaluation.narada_rs.corpus import CORPUS, NEAR_NEIGHBOUR_PAIRS     # noqa: E402
from evaluation.narada_rs.reference_rules import REFERENCE, as_rule      # noqa: E402
from evaluation.narada_rs.scoring import score                           # noqa: E402

OUT_DIR = os.path.join(ROOT, "evaluation", "out", "narada_rs")
EVAL_REGISTRY = SimpleNamespace(devices=[
    {"id": "lamp", "type": "light", "enabled": True},
    {"id": "fan", "type": "fan", "enabled": True},
])
# Values that must never appear in an outbound request. The harness has no
# live descriptor, so the audit is structural: the payload may contain only
# the three keys the client is documented to send.
ALLOWED_PAYLOAD_KEYS = {"schema", "already_known", "instruction"}

_COND = {"type": "object", "additionalProperties": False,
         "required": ["field", "op", "value"],
         "properties": {"field": {"type": "string"},
                        "op": {"type": "string", "enum": ["==", "!=", "<", "<=", ">", ">="]},
                        "value": {"type": ["string", "number"]}}}
_ACT = {"type": "object", "additionalProperties": False, "required": ["device", "action"],
        "properties": {"device": {"type": "string"}, "action": {"type": "string"}}}
# Shape only. Field names, devices and value ranges are still left to the
# on-device validator, so the ablation isolates the effect of syntax.
RULE_FORMAT = {"type": "json_schema", "json_schema": {"name": "rule", "schema": {
    "type": "object",
    "properties": {"source_utterance": {"type": "string"},
                   "when": {"type": "object",
                            "properties": {"all": {"type": "array", "items": _COND},
                                           "any": {"type": "array", "items": _COND}}},
                   "then": {"type": "array", "items": _ACT},
                   "cooldown_s": {"type": "number"},
                   "error": {"type": "string"}}}}}


def _env(name):
    if os.environ.get(name):
        return os.environ[name]
    path = os.path.join(ROOT, ".env")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip().startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _dist(values):
    if not values:
        return {}
    s = sorted(values)

    def pct(p):
        return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]
    return {"n": len(s), "min": s[0], "p50": pct(0.5), "p95": pct(0.95), "p99": pct(0.99),
            "max": s[-1], "mean": statistics.fmean(s)}


def _write(name, payload):
    os.makedirs(OUT_DIR, exist_ok=True)
    payload["meta"] = {
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "host": platform.node(), "machine": platform.machine(),
        "python": platform.python_version(),
    }
    path = os.path.join(OUT_DIR, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    print("wrote", path)


def _temp_store(schema):
    fd, path = tempfile.mkstemp(suffix=".json", prefix="narada_eval_rules_")
    os.close(fd)
    os.unlink(path)
    return RuleStore(path, schema)


# --------------------------------------------------------------------------
# synth: compile the 30 base utterances through the production client
# --------------------------------------------------------------------------

def cmd_synth(args):
    schema = build_schema(EVAL_REGISTRY)
    key, model = _env("NIM_API_KEY"), args.model or _env("NIM_MODEL")
    if not key or not model:
        sys.exit("NIM_API_KEY / NIM_MODEL not configured")

    # Record what the validator was asked and what it said, without changing it.
    seen = {}
    real_validate = nim_module.validate_rule

    def spy(rule, sch):
        ok, reason = real_validate(rule, sch)
        seen["validator"] = {"ok": ok, "reason": reason, "candidate": rule}
        return ok, reason
    nim_module.validate_rule = spy

    trials = []
    for trial in range(args.trials):
        client = nim_module.NimClient(key, model, timeout=args.timeout)
        if args.constrained:
            # Ablation only: the deployed client sends no response_format.
            plain = client.build_request
            client.build_request = lambda u, r, sch: {**plain(u, r, sch), "response_format": RULE_FORMAT}
        store = _temp_store(schema)
        rows = []
        for entry in CORPUS:
            seen.clear()
            body = client.build_request(entry["utterance"], store.rules, schema)
            user_payload = json.loads(body["messages"][1]["content"])
            before = client.tokens_used
            t0 = time.perf_counter()
            rule, reason = client.synthesize(entry["utterance"], store.rules, schema)
            elapsed = time.perf_counter() - t0
            row = {
                "id": entry["id"], "utterance": entry["utterance"],
                "cloud_round_trip_s": elapsed,
                "tokens": client.tokens_used - before,
                "request_bytes": len(json.dumps(body)),
                "payload_keys": sorted(user_payload),
                "payload_keys_ok": set(user_payload) <= ALLOWED_PAYLOAD_KEYS,
                "already_known_sent": len(user_payload.get("already_known", [])),
                "rule": rule, "reason": reason,
            }
            if rule is not None:
                ref = as_rule(entry["id"], entry["utterance"])
                verdict, detail = score(rule, ref, REFERENCE[entry["id"]]["vague"], schema)
                row["outcome"] = "correct" if verdict in ("exact", "threshold") else "valid_but_wrong"
                row["agreement"] = verdict
                row["score_detail"] = detail
                conflict = store.find_conflict(rule)
                row["conflict_with"] = conflict.get("source_utterance") if conflict else None
                # The user confirms every proposal in this protocol, so the rule
                # is stored whether or not a conflict was raised.
                store.add(rule)
            elif "validator" in seen and not seen["validator"]["ok"]:
                row["outcome"] = "rejected_by_validator"
                row["candidate"] = seen["validator"]["candidate"]
            elif reason.startswith("could not reach"):
                row["outcome"] = "network_error"
            elif reason.startswith(("could not parse", "malformed", "the rule service ran out")):
                row["outcome"] = "unparseable"
            else:
                row["outcome"] = "model_declined"
            rows.append(row)
            print(trial, entry["id"], row["outcome"], row.get("agreement", ""),
                  f"{elapsed:.1f}s", row["tokens"], reason[:70], flush=True)
            time.sleep(args.pause)
        try:
            os.unlink(store.path)
        except OSError:
            pass
        trials.append(rows)

    summary = []
    for rows in trials:
        counts = {}
        for r in rows:
            counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
        ok = [r for r in rows if r["outcome"] != "network_error"]
        summary.append({
            "outcomes": counts,
            "exact": sum(1 for r in rows if r.get("agreement") == "exact"),
            "threshold_normalised": sum(1 for r in rows if r.get("agreement") == "threshold"),
            "conflicts_raised": sum(1 for r in rows if r.get("conflict_with")),
            "tokens_total": sum(r["tokens"] for r in rows),
            "cloud_round_trip_s": _dist([r["cloud_round_trip_s"] for r in ok]),
            "payload_keys_all_ok": all(r["payload_keys_ok"] for r in rows),
            "request_bytes": _dist([r["request_bytes"] for r in rows]),
        })
    _write("synthesis_constrained.json" if args.constrained else "synthesis.json",
           {"constrained_decoding": bool(args.constrained), "model": model, "trials": trials, "summary": summary,
            "schema_sent": schema.schema_for_prompt()})


# --------------------------------------------------------------------------
# matcher: paraphrase suppression, false suppression, and a request stream
# --------------------------------------------------------------------------

def _all_utterances():
    items = []
    for entry in CORPUS:
        items.append((entry["id"], "base", entry["utterance"]))
        for p in entry["paraphrases"]:
            items.append((entry["id"], "para", p))
    return items


def _similarity(backend, texts):
    n = len(texts)
    if backend == "fuzzy":
        from rapidfuzz import fuzz
        low = [t.lower() for t in texts]
        return [[fuzz.token_set_ratio(low[i], low[j]) / 100.0 for j in range(n)] for i in range(n)]
    from sentence_transformers import SentenceTransformer, util
    enc = SentenceTransformer(_EMBED_MODEL)
    vec = enc.encode(texts, convert_to_tensor=True)
    return util.cos_sim(vec, vec).tolist()


def _static(sim, items, threshold):
    """Store holds the 30 base utterances; the 90 paraphrases are queried."""
    base = [i for i, it in enumerate(items) if it[1] == "base"]
    correct = false = miss = 0
    for q, it in enumerate(items):
        if it[1] != "para":
            continue
        best = max(base, key=lambda b: sim[q][b])
        if sim[q][best] < threshold:
            miss += 1
        elif items[best][0] == it[0]:
            correct += 1
        else:
            false += 1
    return {"correct": correct, "false": false, "escalated": miss}


def _novel(sim, items, threshold):
    """Leave one intent out: its four utterances are new, so any hit is false."""
    base = [i for i, it in enumerate(items) if it[1] == "base"]
    false = total = 0
    for q, it in enumerate(items):
        others = [b for b in base if items[b][0] != it[0]]
        total += 1
        if max(sim[q][b] for b in others) >= threshold:
            false += 1
    return {"false_suppressed": false, "queries": total}


def _stream(sim, items, threshold, seed):
    """Replay all 120 utterances in random order. A miss compiles in the cloud
    and stores the utterance; compilation is assumed to succeed, so this
    isolates the matcher."""
    order = list(range(len(items)))
    random.Random(seed).shuffle(order)
    stored, cloud, false, curve = [], 0, 0, []
    for q in order:
        best = max(stored, key=lambda b: sim[q][b]) if stored else None
        if best is not None and sim[q][best] >= threshold:
            if items[best][0] != items[q][0]:
                false += 1
        else:
            cloud += 1
            stored.append(q)
        curve.append(cloud)
    return cloud, false, curve


def cmd_matcher(args):
    items = _all_utterances()
    texts = [it[2] for it in items]
    schema = build_schema(EVAL_REGISTRY)
    default_threshold = LocalMatcher.__init__.__defaults__[1]
    out = {"default_threshold": default_threshold, "n_intents": len(CORPUS),
           "n_utterances": len(items), "near_neighbour_pairs": NEAR_NEIGHBOUR_PAIRS,
           "backends": {}}
    for backend in ("fuzzy", "embed"):
        sim = _similarity(backend, texts)
        sweep = []
        for step in range(40, 100, 2):
            th = step / 100.0
            sweep.append({"threshold": th, **_static(sim, items, th),
                          "novel": _novel(sim, items, th)})
        runs = [_stream(sim, items, default_threshold, seed) for seed in range(args.seeds)]
        mean_curve = [statistics.fmean(r[2][i] for r in runs) for i in range(len(items))]

        # Latency and agreement of the production matcher on a 30-rule store.
        store = _temp_store(schema)
        store.rules = [as_rule(e["id"], e["utterance"]) for e in CORPUS]
        matcher = LocalMatcher(store, backend=backend)
        base = [i for i, it in enumerate(items) if it[1] == "base"]
        lat, disagree = [], 0
        probes = [i for i, it in enumerate(items) if it[1] == "para"][::3]
        for q in probes:
            t0 = time.perf_counter()
            hit = matcher.match(texts[q])
            lat.append((time.perf_counter() - t0) * 1000.0)
            best = max(base, key=lambda b: sim[q][b])
            want = items[best][0] if sim[q][best] >= default_threshold else None
            got = hit["id"][4:] if hit else None
            disagree += int(want != got)
        out["backends"][backend] = {
            "backend_in_effect": matcher.backend_name,
            "at_default": {**_static(sim, items, default_threshold),
                           "novel": _novel(sim, items, default_threshold)},
            "sweep": sweep,
            "stream": {
                "seeds": args.seeds,
                "cloud_calls": _dist([r[0] for r in runs]),
                "false_suppressions": _dist([r[1] for r in runs]),
                "mean_cumulative_cloud_calls": mean_curve,
            },
            "production_match_ms_30_rules": _dist(lat),
            "production_vs_matrix_disagreements": disagree,
            "production_probes": len(probes),
        }
        print(backend, json.dumps(out["backends"][backend]["at_default"]),
              "stream cloud calls", out["backends"][backend]["stream"]["cloud_calls"]["mean"],
              "match ms p50", out["backends"][backend]["production_match_ms_30_rules"]["p50"], flush=True)
    _write("matcher.json", out)


def cmd_matchlat(args):
    """Cost of the embedding match if rule vectors were encoded once and kept,
    instead of re-encoding every stored utterance on every query as the
    production matcher does. Reported as a what-if, not as the system."""
    from sentence_transformers import SentenceTransformer, util
    items = _all_utterances()
    enc = SentenceTransformer(_EMBED_MODEL)
    base = [it[2] for it in items if it[1] == "base"]
    probes = [it[2] for it in items if it[1] == "para"]
    cached = enc.encode(base, convert_to_tensor=True)
    enc.encode(probes[0], convert_to_tensor=True)
    lat = []
    for text in probes:
        t0 = time.perf_counter()
        vec = enc.encode(text, convert_to_tensor=True)
        util.cos_sim(vec, cached).max().item()
        lat.append((time.perf_counter() - t0) * 1000.0)
    print("cached-vector match ms", json.dumps({k: round(v, 1) for k, v in _dist(lat).items()}))
    _write("matcher_cached_latency.json", {"rules": len(base), "match_ms": _dist(lat)})


# --------------------------------------------------------------------------
# reflex: cost of one evaluation tick over the rule base
# --------------------------------------------------------------------------

def _random_state(rng):
    occupied = rng.random() < 0.6
    return {
        "occupancy": "occupied" if occupied else "empty",
        "person_count": rng.randint(1, 4) if occupied else 0,
        "occupancy_duration_s": rng.randint(0, 4000),
        "zone": rng.choice(["none", "desk", "door", "center"]) if occupied else "none",
        "posture": rng.choice(["none", "standing", "seated", "walking"]) if occupied else "none",
        "ambient_luma": rng.randint(0, 255),
        "temperature_c": round(rng.uniform(18, 36), 1),
        "humidity_pct": round(rng.uniform(25, 90), 1),
        "hour": rng.randint(0, 23),
        "lamp_state": rng.choice(["on", "off"]),
        "fan_state": rng.choice(["on", "off"]),
    }


def cmd_reflex(args):
    schema = build_schema(EVAL_REGISTRY)
    rng = random.Random(0)
    states = [_random_state(rng) for _ in range(args.ticks)]
    out = {"ticks": args.ticks, "sizes": {}}
    for size in (8, 30, 64):
        store = _temp_store(schema)
        rules = [as_rule(e["id"], e["utterance"]) for e in CORPUS]
        while len(rules) < size:
            extra = dict(rules[len(rules) % 30])
            extra["id"] = f"{extra['id']}_{len(rules)}"
            rules.append(extra)
        store.rules = rules[:size]
        engine = RuleEngine(store)
        for s in states[:500]:
            engine.evaluate(s)
        samples, fired = [], 0
        for s in states:
            t0 = time.perf_counter_ns()
            actions = engine.evaluate(s)
            samples.append((time.perf_counter_ns() - t0) / 1000.0)
            fired += len(actions)
        out["sizes"][str(size)] = {"evaluate_us": _dist(samples), "actions_emitted": fired}
        print(size, "rules", json.dumps({k: round(v, 1) for k, v in out["sizes"][str(size)]["evaluate_us"].items()}), flush=True)
    _write("reflex.json", out)


# --------------------------------------------------------------------------
# scenario: replay a scripted day against learned and reference rule bases
# --------------------------------------------------------------------------

def _replay(rules, schema, day):
    """Run the production engine over the day with device-state feedback.
    Returns (per-tick (lamp, fan) states, switching events)."""
    clock = {"t": 0.0}
    store = _temp_store(schema)
    store.rules = rules
    engine = RuleEngine(store, clock=lambda: clock["t"])
    state = {"lamp": "off", "fan": "off"}
    trace, events = [], []
    for t, desc in day:
        clock["t"] = t
        d = dict(desc, lamp_state=state["lamp"], fan_state=state["fan"])
        for act in engine.evaluate(d):
            if state[act["device"]] != act["action"]:
                state[act["device"]] = act["action"]
                events.append([t, act["device"], act["action"], act["rule_id"]])
        trace.append((state["lamp"], state["fan"]))
    return trace, events


def _edges(rule, day, ref_trace):
    from evaluation.narada_rs.scoring import _fires
    out, prev = [], False
    for (t, desc), (lamp, fan) in zip(day, ref_trace):
        now = _fires(rule, dict(desc, lamp_state=lamp, fan_state=fan))
        if now and not prev:
            out.append(t)
        prev = now
    return out


def cmd_scenario(args):
    import hashlib
    import socket
    from evaluation.narada_rs.scenario import descriptors
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=3).close()
        network = True
    except OSError:
        network = False
    schema = build_schema(EVAL_REGISTRY)
    day = list(descriptors())
    order = [e["id"] for e in CORPUS]
    utter = {e["id"]: e["utterance"] for e in CORPUS}

    def ref_rule(cid):
        return dict(as_rule(cid, utter[cid]), cooldown_s=0)

    ref_trace_all, ref_events_all = _replay([ref_rule(c) for c in order], schema, day)
    ref_edges = {c: _edges(ref_rule(c), day, ref_trace_all) for c in order}
    out = {"network_reachable": network, "ticks": len(day),
           "reference_rules_exercised": sum(1 for c in order if ref_edges[c]),
           "reference_switch_events": len(ref_events_all), "bases": {}}
    digest = hashlib.sha256()
    for source in ("synthesis", "synthesis_constrained"):
        path = os.path.join(OUT_DIR, source + ".json")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for k, rows in enumerate(data["trials"]):
            learned = {r["id"]: dict(r["rule"], id=r["id"]) for r in rows if r["rule"]}
            ids = [c for c in order if c in learned]
            trace, events = _replay([learned[c] for c in ids], schema, day)
            digest.update(json.dumps(events).encode())
            ref_trace, ref_events = _replay([ref_rule(c) for c in ids], schema, day)
            same_lamp = sum(1 for a, b in zip(trace, ref_trace) if a[0] == b[0])
            same_fan = sum(1 for a, b in zip(trace, ref_trace) if a[1] == b[1])
            full_lamp = sum(1 for a, b in zip(trace, ref_trace_all) if a[0] == b[0])
            full_fan = sum(1 for a, b in zip(trace, ref_trace_all) if a[1] == b[1])
            groups = {"stated": [0, 0, 0, 0], "vague": [0, 0, 0, 0]}
            per_rule = {}
            for c in ids:
                got, want = set(_edges(learned[c], day, ref_trace_all)), set(ref_edges[c])
                g = groups["vague" if REFERENCE[c]["vague"] else "stated"]
                g[0] += len(got & want)
                g[1] += len(got - want)
                g[2] += len(want - got)
                g[3] += int(got == want)
                per_rule[c] = {"matched": len(got & want), "false": len(got - want),
                               "missed": len(want - got)}
            out["bases"][f"{source}_trial{k}"] = {
                "rules": len(ids), "switch_events": len(events),
                "reference_switch_events_same_intents": len(ref_events),
                "lamp_agreement_same_intents": same_lamp / len(day),
                "fan_agreement_same_intents": same_fan / len(day),
                "lamp_agreement_full_reference": full_lamp / len(day),
                "fan_agreement_full_reference": full_fan / len(day),
                "firing_edges": {name: {"matched": g[0], "false": g[1], "missed": g[2],
                                        "rules_identical": g[3],
                                        "rules": sum(1 for c in ids if bool(REFERENCE[c]["vague"]) == (name == "vague"))}
                                 for name, g in groups.items()},
                "per_rule": per_rule,
            }
            b = out["bases"][f"{source}_trial{k}"]
            print(source, k, "rules", len(ids), "lamp %.4f fan %.4f" % (b["lamp_agreement_same_intents"], b["fan_agreement_same_intents"]),
                  json.dumps(b["firing_edges"]), flush=True)
    out["event_log_sha256"] = digest.hexdigest()
    print("network_reachable", network, "digest", out["event_log_sha256"][:16])
    _write(f"scenario{args.tag}.json", out)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("synth")
    p.add_argument("--trials", type=int, default=3)
    p.add_argument("--pause", type=float, default=1.0)
    p.add_argument("--timeout", type=float, default=60)
    p.add_argument("--model", default="")
    p.add_argument("--constrained", action="store_true")
    p.set_defaults(fn=cmd_synth)
    p = sub.add_parser("matcher")
    p.add_argument("--seeds", type=int, default=200)
    p.set_defaults(fn=cmd_matcher)
    p = sub.add_parser("matchlat")
    p.set_defaults(fn=cmd_matchlat)
    p = sub.add_parser("scenario")
    p.add_argument("--tag", default="")
    p.set_defaults(fn=cmd_scenario)
    p = sub.add_parser("reflex")
    p.add_argument("--ticks", type=int, default=20000)
    p.set_defaults(fn=cmd_reflex)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
