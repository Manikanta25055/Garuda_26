#!/usr/bin/env python3
"""Sentences that are work for the planner, and near misses that are not.

A development tool, not part of the service. The routing model's tenth intent,
"build", marks a request the quick model has no tool for: something to be made
(a shortcut, a routine, a page to look at), steps that depend on each other,
or the upkeep of the house (its devices, people, settings, saved things).
This adds to what scripts/router_cases.py wrote, the same way: NIM answers
narrow briefs, one sentence in six is held back as a test.

    python3 scripts/router_build_cases.py        # needs NIM_API_KEY in .env

Appends to datasets/narada_router/written.jsonl and to the cases of
tests/eval/routing_written.json, and writes tests/eval/routing_build.json: the
sentences in HAND below, typed by a person and never used for training, each
set in a made-up house. English only, as the planner's work is.
"""
import argparse
import concurrent.futures
import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from basic_pipelines.garuda_auto.llm import NO_THINKING, NimChat, parse_models  # noqa: E402
import router_cases  # noqa: E402
import router_data  # noqa: E402

BUILD = {"intent": "build", "kind": "build", "device": None, "action": None, "scene": None}
NOT_BUILD = {"intent": "other", "kind": "near-build", "device": None, "action": None, "scene": None}
ASSISTANT = ("a home assistant that controls lights, fans, a TV, an AC and other appliances, runs scenes, "
             "keeps schedules and has a security camera")

BRIEFS = [
    (f"Write {{n}} different requests a person might make to {ASSISTANT}, asking it to CREATE a reusable "
     "shortcut, routine or one-tap button made of several steps. Each names what the steps do.", BUILD),
    (f"Write {{n}} different requests to {ASSISTANT} for a multi-step job where a later step depends on an "
     "earlier one or on a check: wait and then do something, do something only if a condition holds at that "
     "moment, repeat until something happens, then notify me.", BUILD),
    (f"Write {{n}} different requests to {ASSISTANT} to SHOW something visual it must build: a chart, graph, "
     "table, timeline, dashboard, control panel, widget or report page about the house, its energy use, its "
     "devices or its activity.", BUILD),
    (f"Write {{n}} different requests to {ASSISTANT} to manage the devices it knows: add a new one, rename one, "
     "move one to another room, remove one, disable one, change its wattage.", BUILD),
    (f"Write {{n}} different requests to {ASSISTANT} to manage people and settings: add or remove a person who "
     "can sign in, change a display name, change the detection threshold, alert email addresses, the schedule "
     "of a security mode, the night presence hours, home settings, or which phones are tracked for presence.",
     BUILD),
    (f"Write {{n}} different requests to {ASSISTANT} for upkeep: make or list backups, read or summarise the "
     "system logs, report on system health, send a test email, start or stop recording a camera clip, list "
     "what is on the network.", BUILD),
    (f"Write {{n}} different requests to {ASSISTANT} to CHANGE, PAUSE or DELETE something already saved: a "
     "schedule, a timer, a shortcut, a routine, a scene, an automation, a suggestion.", BUILD),
    (f"Write {{n}} different requests to {ASSISTANT} for an analysis that needs several lookups put together: "
     "compare periods, find the device that costs the most, audit the automations, summarise the day, explain "
     "a pattern and recommend what to do.", BUILD),
    ("Write {n} different things a person might say to a general assistant that use words such as shortcut, "
     "routine, button, chart, graph, dashboard, table, backup, user, password, log, schedule or device, but "
     "are general questions, opinions or small talk and NOT a request to build or manage anything in a home: "
     "how to make a pivot chart in a spreadsheet, a morning routine for fitness, keyboard shortcuts, what a "
     "dashboard camera is.", NOT_BUILD),
    ("Write {n} different short, simple things a person says to a home assistant that are single plain facts "
     "or questions about their saved things and need nothing built: what shortcuts do I have, show my "
     "schedules, what scenes are there, how much energy did we use today, what do you remember about me.",
     {**NOT_BUILD, "kind": "manage"}),
]

# Typed by hand; never trained on. (sentence, is it the planner's work?)
HAND = [
    ("make me a button that turns the lamp off and the TV on", True),
    ("create a shortcut for when I leave the house", True),
    ("I want a routine that switches everything off at midnight if nobody is around", True),
    ("build a bedtime routine: fan on, lights off, and night mode on", True),
    ("turn the fan on, wait ten minutes, then turn it off if the room is empty", True),
    ("every hour check whether the heater is on and tell me", True),
    ("show me a chart of this week's energy use", True),
    ("can you draw a graph of how long the TV was on each day", True),
    ("build me a control panel for the bedroom devices", True),
    ("give me a table of everything that happened today", True),
    ("make a small dashboard of the house", True),
    ("add a new device called porch light on channel 4", True),
    ("rename the lamp to reading lamp", True),
    ("remove the old cooler from the device list", True),
    ("add a user for my brother", True),
    ("delete the guest account", True),
    ("change the detection threshold to 0.4", True),
    ("set do not disturb to come on at 10 pm every night and go off at 7", True),
    ("take a backup of everything", True),
    ("what do the logs say about last night", True),
    ("start recording the camera", True),
    ("delete the 6 am schedule", True),
    ("pause my movie night shortcut", True),
    ("change the good night routine so it also locks down security", True),
    ("compare this week's power use with last week's", True),
    ("which device is costing me the most and what should I do about it", True),
    ("put together a morning routine for me", True),
    ("track my new phone so you know when I'm home", True),
    ("go through my automations and tell me which ones never run", True),
    ("make a page with a switch for every light", True),
    ("turn on the lamp", False),
    ("fan off please", False),
    ("switch everything off", False),
    ("run movie night", False),
    ("turn the heater on in 20 minutes", False),
    ("is the TV on", False),
    ("why did the fan turn off", False),
    ("turn on night mode", False),
    ("turn on the lamp and the fan", False),
    ("what's the weather like today", False),
    ("tell me a joke", False),
    ("how do I make a bar chart in Excel", False),
    ("what is a good morning routine for studying", False),
    ("what keyboard shortcuts does VS Code have", False),
    ("I forgot my email password, what should I do", False),
    ("what shortcuts do I have", False),
    ("show my schedules", False),
    ("how much energy did we use today", False),
    ("what do you remember about me", False),
    ("it's getting dark in here", False),
    ("don't turn off the fan", False),
    ("who is at home", False),
    ("remember that I like the fan on at night", False),
    ("thanks, that's all", False),
    ("how does a relay work", False),
    ("good night", False),
    ("is it hot in the bedroom", False),
    ("dashboard cameras, are they worth buying", False),
    ("turn the AC off at 6 am", False),
    ("what scenes do I have", False),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--per-brief", type=int, default=30)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--seed", type=int, default=23)
    ap.add_argument("--hand-only", action="store_true", help="only (re)write tests/eval/routing_build.json")
    args = ap.parse_args()
    rng = random.Random(args.seed)
    eval_dir = ROOT / "tests" / "eval"

    placed = []
    for say, build in HAND:
        case = {"say": say, **(BUILD if build else {"intent": None, "kind": "not-build", "device": None,
                                                    "action": None, "scene": None})}
        row = router_data.place(rng, case)
        row["build"] = build
        placed.append(row)
    (eval_dir / "routing_build.json").write_text(json.dumps({
        "_about": "Is this the planner's work? Typed by hand, never trained on (scripts/router_build_cases.py). "
                  "`build` is the answer; the intent of a sentence that is not the planner's is not scored "
                  "here (null), only that it is not called build.",
        "cases": placed}, ensure_ascii=False, indent=1))
    print(f"{len(placed)} hand-written cases -> tests/eval/routing_build.json")
    if args.hand_only:
        return

    env = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"')
    chat = NimChat(env.get("NIM_API_KEY", os.environ.get("NIM_API_KEY", "")),
                   parse_models(env.get("NIM_MODEL", ""), env.get("NIM_FALLBACK_MODELS", "")), timeout=120)
    if not chat.configured:
        raise SystemExit("NIM_API_KEY is not set")

    def ask(job):
        brief, label, round_no = job
        prompt = brief.format(n=args.per_brief) + router_cases.FORM.format(n=args.per_brief) + " English only."
        if round_no:
            prompt += " Be unusual: avoid the first phrasings that come to mind."
        for attempt in range(4):
            try:
                reply = chat.chat([{"role": "user", "content": prompt}], max_tokens=3000,
                                  temperature=0.9 if round_no else 0.7, timeout=120, extra=NO_THINKING)
                return label, router_cases.parse(reply.get("content", ""))
            except Exception as exc:
                print(f"  failed ({attempt + 1}): {type(exc).__name__}: {str(exc)[:80]}", flush=True)
                import time
                time.sleep(20 * (attempt + 1))
        return label, []

    hand = {router_data._norm(say) for say, _ in HAND}
    written_path = ROOT / "datasets" / "narada_router" / "written.jsonl"
    existing = [json.loads(line) for line in written_path.read_text().splitlines()]
    seen = {router_data._norm(c["say"]) for c in existing} | hand
    jobs = [(brief, label, r) for r in range(args.rounds) for brief, label in BRIEFS]
    cases = []
    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        for label, sentences in pool.map(ask, jobs):
            for sentence in sentences:
                key = router_data._norm(sentence)
                if key not in seen:
                    seen.add(key)
                    cases.append({"say": sentence, **label})
    rng.shuffle(cases)
    held, train = cases[:len(cases) // 6], cases[len(cases) // 6:]
    # Anything this script wrote before is replaced, so running it twice does not double it.
    kept = [c for c in existing if c.get("kind") not in ("build", "near-build")
            and not (c.get("kind") == "manage" and c.get("intent") == "other" and "want" not in c)]
    written_path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in kept + train))
    test_path = eval_dir / "routing_written.json"
    test = json.loads(test_path.read_text())
    test["cases"] = [c for c in test["cases"] if c["kind"] not in ("build", "near-build")]
    # Appended in pairs, so the half the Pi's eval takes ([1::2]) keeps its old cases.
    for case in held:
        row = router_data.place(rng, case)
        if row:
            test["cases"].append(row)
    test_path.write_text(json.dumps(test, ensure_ascii=False, indent=1))
    counts = {}
    for c in cases:
        counts[c["kind"]] = counts.get(c["kind"], 0) + 1
    print(f"{len(cases)} sentences written {counts}: {len(train)} for training, {len(held)} held back")


if __name__ == "__main__":
    main()
