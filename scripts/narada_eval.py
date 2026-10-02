#!/usr/bin/env python3
"""Measure Narada against a set of scripted conversations.

A development tool, not part of the service. Each case gets a throwaway house
(three devices in a temp directory, relay pins mocked) and a real model, so
what is measured is Narada's behaviour and nothing in the real house changes.

    python3 scripts/narada_eval.py                    # every case
    python3 scripts/narada_eval.py --group memory     # one group
    python3 scripts/narada_eval.py --case mem-forget -v
    python3 scripts/narada_eval.py --out results.json

The cases and the meaning of their checks are in tests/eval/narada_cases.json.
Exit status is 0 whatever the score: this reports, it does not gate.
"""
import argparse
import collections
import concurrent.futures
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from basic_pipelines import drishti_api  # noqa: E402
from basic_pipelines.garuda_auto import actuation_log, actuators  # noqa: E402
from basic_pipelines.garuda_auto.agent import HomeAgent  # noqa: E402
from basic_pipelines.garuda_auto.decision import DecisionEngine, LocalBackend  # noqa: E402
from basic_pipelines.garuda_auto.home import HomeServices  # noqa: E402
from basic_pipelines.garuda_auto.llm import NimChat, parse_models  # noqa: E402
from basic_pipelines.garuda_auto.runtime import DrishtiRuntime  # noqa: E402
from basic_pipelines.narada_brain import Brain  # noqa: E402

# The relay bank's own no-op mode: the evaluation never opens a GPIO pin, so it
# cannot switch anything real and cases can run side by side.
actuators.GPIO_AVAILABLE = False

DEVICES = [
    {"id": "lamp", "name": "Lamp", "type": "light", "room": "study",
     "transport": {"kind": "relay", "channel": 1}},
    {"id": "fan", "name": "Fan", "type": "fan", "room": "bedroom",
     "transport": {"kind": "relay", "channel": 2}},
    {"id": "tv", "name": "TV", "type": "switch", "room": "hall",
     "transport": {"kind": "relay", "channel": 3}},
]
USERS = {"admin": "mani", "user": "guest"}
ADMIN_ONLY_MODES = {"idle", "email_off"}
# What "I was never told that" sounds like. A recall step marked "knows" fails on
# it, so a lucky word in a reply that admits ignorance does not count as a pass.
IGNORANCE = (r"don.t (have|know)|do not (have|know)|not aware|no (information|record|data)|"
             r"not sure|could you (let me know|remind|tell)|haven.t (told|mentioned)")


class House:
    """One throwaway house and the agent that runs it."""

    def __init__(self, data_dir, chat, extra_devices=()):
        self.data_dir, self.chat, self.extra_devices = data_dir, chat, list(extra_devices)
        self.modes = {"dnd": False, "night": False, "idle": False, "emergency": False,
                      "privacy": True, "email_off": False}
        self.build(first=True)

    def build(self, first=False):
        """(Re)build everything that lives in memory: what a service restart does."""
        if not first:
            self.ctx.relay_bank.close()
        self.ctx = drishti_api.build_context(data_dir=self.data_dir, relay_channels=(1, 2, 3, 4),
                                             channel_to_pin={1: 17, 2: 27, 3: 22, 4: 23})
        if first:
            for device in DEVICES + self.extra_devices:
                ok, why = self.ctx.registry.add(dict(device))
                if not ok:
                    raise RuntimeError(f"the test house could not add {device['id']}: {why}")
        self.ctx.rebuild()
        self.home = HomeServices(self.ctx, DrishtiRuntime(self.ctx), self.data_dir)
        decision = DecisionEngine(LocalBackend(lambda: self.ctx.registry.devices,
                                               lambda: self.home.scenes.scenes))
        self.agent = make_agent(self, decision)

    def seed_routine(self, device, action, hhmm, days=6):
        """Write a routine into the actuation log: switched by hand at about this time
        on each of the last `days` days, the way habits.suggest looks for it."""
        hour, minute = (int(x) for x in hhmm.split(":"))
        today = time.localtime()
        for back in range(1, days + 1):
            stamp = time.mktime((today.tm_year, today.tm_mon, today.tm_mday - back, hour,
                                 minute + (back % 3) * 4, 0, 0, 0, -1))
            actuation_log.record(self.ctx.log_path, device=device, action=action, rule_id=None,
                                 matched=[], ok=True, clock=lambda s=stamp: s, source="manual",
                                 actor=USERS["admin"])

    def answer_offer(self, offer, accepted, user):
        match = next(s for s in self.home.suggestions() if s["id"] == offer["id"])
        if accepted:
            self.home.add_schedule({"device": match["device"], "action": match["action"]},
                                   time_hhmm=match["time"], days=match["days"],
                                   label="From your routine", created_by=user)
        self.home.dismiss_suggestion(offer["id"])
        name = self.ctx.registry.get(match["device"])["name"]
        self.agent.brain.routine_decided(match, name, accepted, by=user)

    def set_mode(self, mode, value, actor):
        if mode not in self.modes:
            raise ValueError(f"unknown mode: {mode!r}")
        if mode in ADMIN_ONLY_MODES and value and actor != USERS["admin"]:
            raise PermissionError("only an admin can turn this mode on: it silences alerts")
        self.modes[mode] = bool(value)
        return f"{mode} {'on' if value else 'off'}"

    def close(self):
        self.ctx.relay_bank.close()


def make_agent(house, decision):
    """The one place that says how Narada is put together for the evaluation."""
    return HomeAgent(house.ctx, house.home, house.chat, decision,
                     brain=Brain(house.data_dir, house.chat, background=False),
                     modes_fn=lambda: dict(house.modes), set_mode_fn=house.set_mode,
                     security_fn=lambda: {"modes": dict(house.modes), "alert_active": False,
                                          "camera": "delivering frames", "owner_present": True})


def _matches(patterns, text):
    return [p for p in patterns if re.search(p, text, re.I | re.S)]


def check(step, reply, actions):
    """Return the list of reasons this step failed (empty when it passed)."""
    problems = []
    if step.get("any") and not _matches(step["any"], reply):
        problems.append(f"reply matched none of {step['any']}")
    hit = _matches(step.get("none", []), reply)
    if hit:
        problems.append(f"reply matched forbidden {hit}")
    if step.get("knows") and re.search(IGNORANCE, reply, re.I):
        problems.append("reply says it does not know")
    if len(reply) < step.get("min_chars", 0):
        problems.append(f"reply shorter than {step['min_chars']} characters")
    if step.get("max_chars") and len(reply) > step["max_chars"]:
        problems.append(f"reply longer than {step['max_chars']} characters ({len(reply)})")
    joined = " | ".join(actions)
    if step.get("action") and not _matches(step["action"], joined):
        problems.append(f"no action matching {step['action']} (actions: {actions or 'none'})")
    if step.get("no_action") and actions:
        problems.append(f"acted when it should not have: {actions}")
    return problems


def run_case(case, chat):
    started, transcript, problems = time.time(), [], []
    with tempfile.TemporaryDirectory(prefix="narada-eval-") as data_dir:
        house = House(data_dir, chat, case.get("devices", ()))
        for routine in case.get("routines", ()):
            house.seed_routine(**routine)
        try:
            for number, step in enumerate(case["steps"], 1):
                role = step.get("role", "admin")
                user = USERS[role]
                if step.get("restart"):
                    house.build()
                elif step.get("new_session"):
                    house.agent.forget(user)
                    house.agent.forget(f"security:{user}")
                # A conversation that already happened, put straight into the record ...
                for said, answered in step.get("seed", ()):
                    house.agent.brain.record(user, [{"role": "user", "content": said},
                                                    {"role": "assistant", "content": answered}])
                # ... and the pass that reads it back once it has gone quiet.
                if step.get("distill"):
                    house.agent.brain.distill(user)
                if not step.get("say"):
                    continue
                try:
                    result = house.agent.handle(step["say"], user=user, role=role,
                                                scope=step.get("scope", "home"),
                                                voice=bool(step.get("voice")))
                except Exception as exc:   # a crash is a failed step, not a failed run
                    result = {"reply": f"[{type(exc).__name__}: {exc}]", "actions": [], "lane": "error"}
                reply, actions = result.get("reply", ""), list(result.get("actions") or [])
                found = check(step, reply, actions)
                if result.get("lane") != "agent":
                    found.append(f"lane was {result.get('lane')!r}, not the model")
                if step.get("offer") and not result.get("offer"):
                    found.append("no routine was offered")
                if step.get("offer") is False and result.get("offer"):
                    found.append(f"offered a routine it should not have: {result['offer']['text']}")
                # Answer the offer the way the Automations page would.
                if step.get("answer") and result.get("offer"):
                    house.answer_offer(result["offer"], step["answer"] == "yes", user)
                transcript.append({"say": step["say"], "reply": reply, "actions": actions,
                                   "problems": found})
                problems += [f"step {number}: {p}" for p in found]
        finally:
            house.close()
    return {"id": case["id"], "group": case["group"], "passed": not problems,
            "problems": problems, "transcript": transcript,
            "seconds": round(time.time() - started, 1)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cases", default=str(ROOT / "tests" / "eval" / "narada_cases.json"))
    ap.add_argument("--group")
    ap.add_argument("--case")
    ap.add_argument("--out")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    cases = json.loads(Path(args.cases).read_text())["cases"]
    if args.group:
        cases = [c for c in cases if c["group"] == args.group]
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
    if not cases:
        raise SystemExit("no cases selected")
    chat = NimChat(os.environ.get("NIM_API_KEY", ""),
                   parse_models(os.environ.get("NIM_MODEL", ""), os.environ.get("NIM_FALLBACK_MODELS", "")))
    if not chat.configured:
        raise SystemExit("NIM_API_KEY is not set: there is no model to evaluate")

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda c: run_case(c, chat), cases))

    groups = collections.OrderedDict()
    for r in results:
        g = groups.setdefault(r["group"], [0, 0])
        g[0] += r["passed"]
        g[1] += 1
        if args.verbose or not r["passed"]:
            print(f"{'PASS' if r['passed'] else 'FAIL'}  {r['id']}  ({r['seconds']} s)")
            for turn in r["transcript"]:
                if args.verbose or turn["problems"]:
                    print(f"      > {turn['say']}\n      < {turn['reply'][:300]!r}  actions={turn['actions']}")
            for p in r["problems"]:
                print(f"      - {p}")
    print()
    for name, (ok, total) in groups.items():
        print(f"{name:12} {ok:2}/{total}")
    passed = sum(r["passed"] for r in results)
    print(f"{'TOTAL':12} {passed:2}/{len(results)}   models: {chat.status().get('models')}")
    if args.out:
        Path(args.out).write_text(json.dumps({"passed": passed, "total": len(results),
                                              "groups": groups, "results": results}, indent=1))


if __name__ == "__main__":
    main()
