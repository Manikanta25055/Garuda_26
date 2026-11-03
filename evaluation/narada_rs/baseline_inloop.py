"""E10: the language model in the loop, as the baseline the design avoids.

The same 30 instructions, given to the model as text. At sampled moments of the
scripted day the model is shown the previous and current room state and the
device states, and asked which devices to switch now. Ground truth is what the
reference rule base did at that moment in the replay (run_eval scenario): at a
switching tick the change it made, elsewhere no change.

    DRISHTI_ENV_FILE=/path/.env python3 -m evaluation.narada_rs.baseline_inloop

Reports per-decision latency and tokens (the price of one in-loop decision,
against a 0.5 s tick) and how often the decision matched the reference.
"""
import argparse
import json
import os
import random
import sys
import time

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
load_dotenv(os.environ.get("DRISHTI_ENV_FILE") or os.path.join(ROOT, ".env"))

from basic_pipelines.garuda_auto.llm import NO_THINKING, NimChat, NimUnavailable, parse_models  # noqa: E402
from basic_pipelines.garuda_auto.rule_schema import build_schema                 # noqa: E402
from evaluation.narada_rs.corpus import CORPUS                                    # noqa: E402
from evaluation.narada_rs.reference_rules import as_rule                          # noqa: E402
from evaluation.narada_rs.run_eval import EVAL_REGISTRY, _dist, _replay, _write   # noqa: E402
from evaluation.narada_rs.scenario import descriptors                             # noqa: E402

PROMPT = """You control a lamp and a fan in one room. The owner gave these standing instructions:
%s

Each call shows the room state half a second ago and now, with the devices' current states.
Decide which devices must be switched NOW so the instructions are followed. Act only on what
changed; an instruction that already took effect does not need repeating.
Return JSON: {"actions": [{"device": "lamp"|"fan", "action": "on"|"off"}]}; an empty list
when nothing should switch."""

FORMAT = {"type": "json_schema", "json_schema": {"name": "decision", "schema": {
    "type": "object", "required": ["actions"],
    "properties": {"actions": {"type": "array", "items": {
        "type": "object", "required": ["device", "action"], "additionalProperties": False,
        "properties": {"device": {"type": "string", "enum": ["lamp", "fan"]},
                       "action": {"type": "string", "enum": ["on", "off"]}}}}}}}}


def _hhmm(t):
    return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--edges", type=int, default=40)
    ap.add_argument("--quiet", type=int, default=40)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    chat = NimChat(os.environ.get("NIM_API_KEY", ""),
                   parse_models(os.environ.get("NIM_MODEL", ""), os.environ.get("NIM_FALLBACK_MODELS", "")))
    schema = build_schema(EVAL_REGISTRY)
    day = list(descriptors())
    rules = [dict(as_rule(e["id"], e["utterance"]), cooldown_s=0) for e in CORPUS]
    trace, _, _ = _replay(rules, schema, day)

    edges = [i for i in range(1, len(day)) if trace[i] != trace[i - 1]]
    quiet = [i for i in range(1, len(day)) if trace[i] == trace[i - 1]]
    rng = random.Random(0)
    picks = sorted(rng.sample(edges, min(args.edges, len(edges))) + rng.sample(quiet, args.quiet))
    system = PROMPT % "\n".join(f"- {e['utterance']}" for e in CORPUS)

    rows = []
    for i in picks:
        (t_prev, before), (t, now) = day[i - 1], day[i]
        lamp, fan = trace[i - 1]
        want = sorted([(d, s) for d, s, p in (("lamp", trace[i][0], lamp), ("fan", trace[i][1], fan)) if s != p])
        state = {"time_before": _hhmm(t_prev), "room_before": before,
                 "time_now": _hhmm(t), "room_now": now, "devices": {"lamp": lamp, "fan": fan}}
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(state, default=str)}]
        tokens_before = chat.tokens_used
        t0 = time.perf_counter()
        try:
            message = chat.chat(messages, max_tokens=512, temperature=0.1, timeout=30,
                                extra={**NO_THINKING, "response_format": FORMAT})
            content = message.get("content") or ""
            got = sorted({(a["device"], a["action"]) for a in json.loads(content[content.find("{"):]).get("actions", [])
                          if a.get("action") != {"lamp": lamp, "fan": fan}.get(a.get("device"))})
            error = ""
        except (NimUnavailable, ValueError, AttributeError) as exc:
            got, error = None, f"{type(exc).__name__}: {exc}"[:200]
        elapsed = time.perf_counter() - t0
        rows.append({"tick": i, "time": _hhmm(t), "edge": bool(want), "want": want, "got": got,
                     "correct": got == want, "seconds": elapsed,
                     "tokens": chat.tokens_used - tokens_before, "error": error})
        print(_hhmm(t), "edge" if want else "quiet", want, got, f"{elapsed:.2f}s", error[:60], flush=True)

    answered = [r for r in rows if r["got"] is not None]
    summary = {
        "model_order": chat.models, "decisions": len(rows), "answered": len(answered),
        "correct": sum(r["correct"] for r in rows),
        "correct_at_edges": sum(r["correct"] for r in rows if r["edge"]),
        "edges": sum(r["edge"] for r in rows),
        "spurious_switch_at_quiet": sum(1 for r in answered if not r["edge"] and r["got"]),
        "missed_or_wrong_at_edge": sum(1 for r in rows if r["edge"] and not r["correct"]),
        "decision_seconds": _dist([r["seconds"] for r in answered]),
        "slower_than_tick": sum(1 for r in answered if r["seconds"] > 0.5),
        "tokens_per_decision": _dist([r["tokens"] for r in answered]),
        "ticks_per_day": len(day),
    }
    print(json.dumps(summary, indent=1))
    _write(f"baseline_inloop{args.tag}.json", {"summary": summary, "rows": rows})


if __name__ == "__main__":
    main()
