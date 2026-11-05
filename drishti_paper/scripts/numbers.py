"""Every number the paper prints, computed from data/. Run before building:

    python3 scripts/numbers.py      (from drishti_paper/)

Writes numbers.tex (one \\newcommand per value) and figures/latency.dat.
Nothing in main.tex is typed by hand: if a value changes, rerun this.
"""
import json
import math
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "data")


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as fh:
        return json.load(fh)


def pct(values, p):
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]


def wilson(k, n, z=1.96):
    """95% Wilson score interval for k successes in n, as percentages."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100 * (c - h), 100 * (c + h)


macros = {}


def put(name, value):
    macros[name] = value


# E1/E2 synthesis -----------------------------------------------------------
def synth(prefix, fname):
    d = load(fname)
    rows = [r for t in d["trials"] for r in t]
    n = len(rows)
    out = {}
    for r in rows:
        out[r["outcome"]] = out.get(r["outcome"], 0) + 1
    correct = out.get("correct", 0)
    lo, hi = wilson(correct, n)
    rt = [r["cloud_round_trip_s"] for r in rows if r["outcome"] != "network_error"]
    put(prefix + "N", n)
    put(prefix + "Correct", correct)
    put(prefix + "CorrectPct", f"{100 * correct / n:.0f}")
    put(prefix + "CorrectCI", f"{lo:.0f}--{hi:.0f}")
    put(prefix + "Wrong", out.get("valid_but_wrong", 0))
    put(prefix + "Rejected", out.get("rejected_by_validator", 0) + out.get("unparseable", 0))
    put(prefix + "Declined", out.get("model_declined", 0))
    put(prefix + "RtMed", f"{pct(rt, .5):.1f}")
    put(prefix + "RtNinetyFive", f"{pct(rt, .95):.1f}")
    put(prefix + "RtMax", f"{max(rt):.1f}")
    put(prefix + "Tokens", f"{sum(r['tokens'] for r in rows) / n:,.0f}")
    put(prefix + "Bytes", f"{sum(r['request_bytes'] for r in rows) / n / 1000:.1f}")
    put(prefix + "KeysOk", "all" if all(r["payload_keys_ok"] for r in rows) else "NOT ALL")
    same = all(tuple(r["outcome"] for r in t) == tuple(r["outcome"] for r in d["trials"][0])
               for t in d["trials"])
    put(prefix + "TrialsSame", "identical" if same else "different")
    # Identical trials are one sample, not three: the interval uses one trial.
    first = d["trials"][0]
    k1 = sum(1 for r in first if r["outcome"] == "correct")
    lo1, hi1 = wilson(k1, len(first))
    put(prefix + "TrialCorrect", k1)
    put(prefix + "TrialN", len(first))
    put(prefix + "TrialCI", f"{lo1:.0f}--{hi1:.0f}")
    return rt


compile_rt = synth("synDep", "synthesis.json")
synth("synFree", "synthesis_free.json")
synth("synThink", "synthesis_think.json")
synth("synUltra", "synthesis_nemotron-3-ultra-550b-a55b.json")

# Production budget before the fix: the partial log of the first run.
lines = [l.split() for l in open(os.path.join(DATA, "synth_budget2048_partial.log"), encoding="utf-8")
         if l[:1].isdigit() and len(l.split()) > 2]
put("oldBudgetN", len(lines))
put("oldBudgetCut", sum(1 for l in lines if "ran out of room" in " ".join(l)))

# E3 evaluator --------------------------------------------------------------
reflex = load("reflex.json")["sizes"]
for size, word in (("8", "Eight"), ("30", "Thirty"), ("64", "SixtyFour")):
    e = reflex[size]["evaluate_us"]
    put(f"eval{word}Med", f"{e['p50']:.0f}")
    put(f"eval{word}NinetyNine", f"{e['p99']:.0f}")
put("evalMaxMs", f"{max(reflex[s]['evaluate_us']['max'] for s in reflex) / 1000:.1f}")

# E4 relay ------------------------------------------------------------------
relay = load("relay_latency.json")
for action, word in (("on", "On"), ("off", "Off")):
    a = relay[action]
    put(f"relay{word}N", a["decide_to_contact_ms"]["n"])
    put(f"relay{word}Med", f"{a['decide_to_contact_ms']['p50']:.2f}")
    put(f"relay{word}NinetyNine", f"{a['decide_to_contact_ms']['p99']:.2f}")
    put(f"relay{word}Max", f"{a['decide_to_contact_ms']['max']:.2f}")
    put(f"relay{word}Gpio", f"{a['decide_to_gpio_ms']['p50']:.2f}")
    put(f"relay{word}Bounce", f"{a['bounce_ms']['p50']:.2f}")
held = sum(1 for r in relay["rows"] if r["action"] == "on" and not r["ok"])
put("relayRequestedOn", sum(1 for r in relay["rows"] if r["action"] == "on"))
put("relayHeld", held)
put("relayEdges", relay["all_edges"])

# E5 replay -----------------------------------------------------------------
scen, off = load("scenario.json"), load("scenario_offline.json")
put("replayTicks", f"{scen['ticks']:,}")
put("replaySameHash", "identical" if scen["event_log_sha256"] == off["event_log_sha256"] else "DIFFERENT")
put("replayRefRules", scen["reference_rules_exercised"])
b = scen["bases"]["synthesis_trial0"]
st, va = b["firing_edges"]["stated"], b["firing_edges"]["vague"]
put("replayStatedMatched", st["matched"])
put("replayStatedFalse", st["false"])
put("replayStatedMissed", st["missed"])
put("replayStatedRules", st["rules"])
put("replayStatedIdentical", st["rules_identical"])
put("replayVagueMatched", va["matched"])
put("replayVagueFalse", va["false"])
put("replayVagueMissed", va["missed"])
put("replayLampAgree", f"{100 * b['lamp_agreement_same_intents']:.1f}")
put("replayFanAgree", f"{100 * b['fan_agreement_same_intents']:.1f}")
put("replayHeld", b["held_by_guard"])

# E6 physical offline -------------------------------------------------------
events = {}
with open(os.path.join(DATA, "e6", "events.log"), encoding="utf-8") as fh:
    for line in fh:
        t, msg = line.split(None, 1)
        events.setdefault(msg.strip(), float(t))
t_off, t_on = events["wifi off"], events["wifi on"]
mon = [json.loads(l) for l in open(os.path.join(DATA, "e6", "monitor.jsonl"), encoding="utf-8")]
net_in = [m for m in mon if m["kind"] == "net" and t_off < m["t"] < t_on]
acts = [json.loads(l) for l in open(os.path.join(DATA, "e6", "actuations_window.jsonl"), encoding="utf-8")]
acts_off = [a for a in acts if t_off < a["ts"] < t_on and a["rule_id"].startswith("e6_") and a["ok"]]
lamp_edges = [m["t"] for m in mon if m["kind"] == "lamp"]
lag = [min(abs(e - a["ts"]) for e in lamp_edges) for a in acts_off if a["device"] == "lamp"]
put("offSeconds", f"{t_on - t_off:.0f}")
put("offChecks", len(net_in))
put("offChecksOnline", sum(m["online"] for m in net_in))
put("offSwitches", len(acts_off))
put("offSwitchesMatched", sum(1 for x in lag if x < 0.25))
put("offLagMax", f"{max(lag) * 1000:.0f}")

# E7 egress -----------------------------------------------------------------
eg = load("egress_audit.json")["summary"]
put("egAgentReq", eg["agent"]["requests"])
put("egAgentCarry", eg["agent"]["carrying_any_live_value"])
put("egAgentKb", f"{eg['agent']['bytes']['p50'] / 1000:.1f}")
put("egCompileReq", eg["compile"]["requests"])
put("egCompileCarry", eg["compile"]["carrying_any_live_value"])
put("egCompileKb", f"{eg['compile']['bytes']['p50'] / 1000:.1f}")
put("egTurns", eg["turns"])
put("egProposals", eg["turns_with_proposal"])
put("egAuthorMed", f"{eg['authoring_seconds']['p50']:.1f}")
put("egAuthorNinetyFive", f"{eg['authoring_seconds']['p95']:.1f}")
turns = load("egress_audit.json")["turns"]
author_s = [t["seconds"] for t in turns]

# E8 conflicts --------------------------------------------------------------
cf = load("conflicts.json")
put("cfPairs", cf["opposed_pairs"])
put("cfReal", cf["really_overlap"])
put("cfFlagged", cf["flagged"])
put("cfPrecision", f"{100 * cf['precision']:.0f}")
put("cfRecall", f"{100 * cf['recall']:.0f}")
put("cfPrompts", cf["incremental"]["prompts"])
put("cfPromptsReal", cf["incremental"]["prompts_real"])

# E9 matcher vs router ------------------------------------------------------
m = load("matcher.json")["backends"]
put("mEmbCorrect", m["embed"]["at_default"]["correct"])
put("mEmbWrong", m["embed"]["at_default"]["false"])
put("mEmbNovel", m["embed"]["at_default"]["novel"]["false_suppressed"])
put("mEmbNovelN", m["embed"]["at_default"]["novel"]["queries"])
put("mEmbMs", f"{m['embed']['production_match_ms_30_rules']['p50'] / 1000:.1f}")
put("mFuzCorrect", m["fuzzy"]["at_default"]["correct"])
put("mFuzWrong", m["fuzzy"]["at_default"]["false"])
r = load("router_eval.json")
for key, word in (("router / routing_cases", "Hand"), ("router / routing_written (test half)", "Held"),
                  ("local / routing_cases", "LitHand"), ("local / routing_written (test half)", "LitHeld")):
    put(f"rt{word}", r[key]["whole"])
    put(f"rt{word}N", r[key]["answered"])
    put(f"rt{word}SureWrong", r[key]["sure_wrong"])
put("rtHandMs", f"{r['router / routing_cases']['latency_ms']['median']:.1f}")
put("rtHeldMs", f"{r['router / routing_written (test half)']['latency_ms']['median']:.1f}")

# E10 in-loop baseline ------------------------------------------------------
bl = load("baseline_inloop.json")["summary"]
put("blN", bl["decisions"])
put("blCorrect", bl["correct"])
put("blEdges", bl["edges"])
put("blEdgeCorrect", bl["correct_at_edges"])
put("blSpurious", bl["spurious_switch_at_quiet"])
put("blMed", f"{bl['decision_seconds']['p50']:.1f}")
put("blNinetyFive", f"{bl['decision_seconds']['p95']:.1f}")
put("blMax", f"{bl['decision_seconds']['max']:.1f}")
put("blSlow", bl["slower_than_tick"])
put("blTokens", f"{bl['tokens_per_decision']['mean']:.0f}")
put("blTokensDay", f"{bl['tokens_per_decision']['mean'] * 2 * 86400 / 1e6:.0f}")
sc = load("scenario_changes.json")
put("blEvents", f"{sc['nonclock_changes']:,}")
put("blEventTokens", f"{bl['tokens_per_decision']['mean'] * sc['nonclock_changes'] / 1e6:.1f}")
rows = load("baseline_inloop.json")["rows"]
loop_s = [x["seconds"] for x in rows if x["got"] is not None]

# Figure: latency ladder (ms), median with p95 and max -----------------------
rel = relay["rows"]
on_rows = [x for x in rel if x.get("contact_ns")]
ladder = [
    ("local", "Rule evaluation, 30 rules", [reflex["30"]["evaluate_us"][k] / 1000 for k in ("p50", "p95", "max")]),
    ("local", "Decision to GPIO write", [pct([x["decide_to_gpio_ms"] for x in on_rows], q) for q in (.5, .95)] + [max(x["decide_to_gpio_ms"] for x in on_rows)]),
    ("local", "Decision to relay contact", [pct([x["decide_to_contact_ms"] for x in on_rows], q) for q in (.5, .95)] + [max(x["decide_to_contact_ms"] for x in on_rows)]),
    ("cloud", "Model decides one switch", [pct(loop_s, .5) * 1000, pct(loop_s, .95) * 1000, max(loop_s) * 1000]),
    ("cloud", "Rule compilation", [pct(compile_rt, .5) * 1000, pct(compile_rt, .95) * 1000, max(compile_rt) * 1000]),
    ("cloud", "Authoring turn (agent)", [pct(author_s, .5) * 1000, pct(author_s, .95) * 1000, max(author_s) * 1000]),
]
os.makedirs(os.path.join(HERE, "figures"), exist_ok=True)
with open(os.path.join(HERE, "figures", "latency.dat"), "w", encoding="utf-8") as fh:
    fh.write("y kind med p95 max\n")
    for y, (kind, _, (med, p95, mx)) in enumerate(reversed(ladder)):
        fh.write(f"{y} {kind} {med:.6g} {p95:.6g} {mx:.6g}\n")
with open(os.path.join(HERE, "figures", "latency_labels.tex"), "w", encoding="utf-8") as fh:
    fh.write("% generated by scripts/numbers.py\n\\def\\latencylabels{"
             + ",".join("{" + name + "}" for _, name, _ in reversed(ladder)) + "}\n")

with open(os.path.join(HERE, "numbers.tex"), "w", encoding="utf-8") as fh:
    fh.write("% Generated by scripts/numbers.py from data/. Do not edit.\n")
    for k, v in macros.items():
        fh.write(f"\\newcommand{{\\{k}}}{{{v}}}\n")
print(f"{len(macros)} macros, {len(ladder)} ladder rows")
