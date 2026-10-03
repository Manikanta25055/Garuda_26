#!/usr/bin/env python3
"""Sentences for the routing model that a language model wrote, not a template.

A development tool, not part of the service. scripts/router_data.py writes
its sentences from lists a person typed, so a model trained on those alone
has only met one author. This asks NIM for more, one narrow brief at a time
("ways to say you want the fan off without the word fan"), so the label of
every sentence is known from the brief it answers. One sentence in six is
kept back, set in a house and saved as a test the model never trains on.

    python3 scripts/router_cases.py            # needs NIM_API_KEY in .env

Writes datasets/narada_router/written.jsonl (for router_data.py) and
tests/eval/routing_written.json (the test). Read both before trusting them:
a model that writes the questions also gets some of them wrong.
"""
import argparse
import concurrent.futures
import json
import os
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from basic_pipelines.garuda_auto.llm import NO_THINKING, NimChat, parse_models  # noqa: E402
import router_data  # noqa: E402

# kind -> what to call it in a brief, and the words a sentence "without naming it" must not use.
THINGS = {
    "light": ("light", ["light", "lamp", "bulb"]),
    "fan": ("fan", ["fan"]),
    "tv": ("TV", ["tv", "television", "telly"]),
    "ac": ("AC", ["ac", "air conditioner", "aircon", "a/c", "air con"]),
    "geyser": ("geyser (water heater)", ["geyser", "heater"]),
    "pump": ("water pump motor", ["pump", "motor"]),
    "speaker": ("music speaker", ["speaker", "music system"]),
    "heater": ("room heater", ["heater"]),
}
LANGUAGES = {
    "en": "in everyday English",
    "te": "in Telugu written in English letters, mixed with English words the way Telugu speakers text "
          "(for example 'fan on cheyyi', 'light aapu')",
    "hi": "in Hindi written in English letters, mixed with English words the way Hindi speakers text "
          "(for example 'pankha chalu karo', 'light band kar do')",
}
FORM = (" Vary the length, the tone and the wording as much as you can: short and long, polite and blunt, "
        "typed and spoken. Answer with a JSON array of {n} strings and nothing else.")


def briefs():
    """(brief, label) pairs. The label is what every sentence written to the brief is."""
    out = []
    for kind, (thing, banned) in THINGS.items():
        for action in ("on", "off"):
            for lang, how in LANGUAGES.items():
                out.append((f"Write {{n}} different things a person at home might say to their home assistant, {how}, "
                            f"that mean they want the {thing} switched {action}, WITHOUT naming it: never use the "
                            f"words {', '.join(banned)}, and never name any other appliance. They describe how "
                            f"they feel, what is happening or what they are about to do, so that switching the "
                            f"{thing} {action} is the obvious thing to do.",
                            {"intent": "device_control", "kind": "paraphrase", "lang": lang, "banned": banned,
                             "want": {"kind": kind, "action": action}}))
                word = router_data.KINDS[kind]["words"][0]
                out.append((f"Write {{n}} different ways to tell a home assistant, {how}, to switch the {word} "
                            f"{action} right now. Every one must contain the word '{word}', ask for nothing else, "
                            f"and name no room.",
                            {"intent": "device_control", "kind": "mixed-language" if lang != "en" else "plain",
                             "lang": lang, "must": word, "want": {"kind": kind, "action": action}}))
        word = router_data.KINDS[kind]["words"][0]
        for intent, ask in (
                ("state_query", f"ask a home assistant whether the {word} is on or off, or what state it is in"),
                ("explain", f"ask a home assistant WHY the {word} is on, is off, or just switched by itself"),
                ("other", f"tell a home assistant NOT to switch the {word}, to leave it alone, or to cancel "
                          f"switching it (a refusal, never a request to switch it)")):
            out.append((f"Write {{n}} different ways to {ask}, each containing the word '{word}'. A third in English, "
                        f"a third in Telugu written in English letters, a third in Hindi written in English letters.",
                        {"intent": intent, "kind": "negation" if intent == "other" else "plain", "must": word,
                         "want": {"kind": kind, "action": "none"}}))
        for action in ("on", "off"):
            out.append((f"Write {{n}} different ways to ask a home assistant to switch the {word} {action} LATER: "
                        f"after a delay or at a clock time (never right now). Each contains the word '{word}'.",
                        {"intent": "timer", "kind": "plain", "must": word, "want": {"kind": kind, "action": action}}))
            out.append((f"Write {{n}} different standing rules a person might ask a home assistant to set up, where the "
                        f"{word} is switched {action} whenever some condition happens (someone enters, the room is "
                        f"empty, it gets dark, they leave home, the temperature changes...). Each contains the word "
                        f"'{word}' and a condition word such as when, if or whenever.",
                        {"intent": "automation_rule", "kind": "plain", "must": word,
                         "want": {"kind": kind, "action": action}}))
    general = [
        ("questions about science, history, geography, maths or how things work", {}),
        ("requests for help with cooking, health, fitness, travel or money", {}),
        ("small talk, greetings, feelings, jokes and compliments", {}),
        ("things a person tells an assistant about themselves, their family, their habits or their likes "
         "(statements, not requests)", {}),
        ("questions an electronics engineering student might ask about studies, coding, chips or careers", {}),
        ("sentences that mention a lamp, a light, a fan, a TV, an AC or a heater but are NOT a request to switch "
         "anything and NOT a question about whether this home's device is on: buying advice, how they work, "
         "memories, opinions, idioms", {}),
        ("general questions and small talk in Telugu written in English letters", {}),
        ("general questions and small talk in Hindi written in English letters", {}),
    ]
    for topic, _ in general:
        out.append((f"Write {{n}} different things a person might say to a general assistant: {topic}. None of them "
                    f"asks to control anything in a home.",
                    {"intent": "other", "kind": "offtopic"}))
    out.append(("Write {n} different ways to tell a home assistant to switch off EVERYTHING in the house at once "
                "(all devices, the whole house), in English, and some in Telugu or Hindi written in English letters. "
                "Never name a single appliance.",
                {"intent": "all_off", "kind": "plain", "action": "off"}))
    for on in (True, False):
        out.append((f"A home security system has these modes: do not disturb (dnd), night mode, idle mode, emergency "
                    f"mode, privacy mode, email alerts. Write {{n}} different ways to ask for one of these modes to be "
                    f"turned {'ON' if on else 'OFF'}, naming the mode.",
                    {"intent": "mode_change", "kind": "plain", "action": "on" if on else "off"}))
    out.append(("Write {n} different questions a person might ask their home assistant about the house as a whole, "
                "naming no single appliance: who is home, whether anyone is in a room, what is switched on, "
                "the temperature or humidity, whether everything is off.",
                {"intent": "state_query", "kind": "plain"}))
    out.append(("Write {n} different requests to a home assistant that ask for TWO different things in one sentence "
                "(for example two different appliances, or an appliance and a security mode), joined with and, then, "
                "also or a comma. Use common appliances: lamp, fan, TV, AC, heater.",
                {"intent": "other", "kind": "compound", "device": None, "action": None, "scene": None}))
    return out


def parse(text):
    """The JSON array in a reply, or [] when there is none."""
    match = re.search(r"\[.*\]", text or "", re.S)
    if not match:
        return []
    try:
        items = json.loads(match.group(0))
    except ValueError:
        return []
    return [s.strip() for s in items if isinstance(s, str) and 2 <= len(s.strip()) <= 160]


def keep(sentence, label):
    """Does the sentence obey the brief it was written to?"""
    low = sentence.lower()
    words = set(re.findall(r"[a-z/]+", low))
    if any((b in words) if " " not in b else (b in low) for b in label.get("banned", [])):
        return False
    if label.get("must") and label["must"] not in low:
        return False
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--per-brief", type=int, default=24)
    ap.add_argument("--rounds", type=int, default=2, help="how many times each brief is asked")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()

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
        prompt = brief.format(n=args.per_brief) + FORM.format(n=args.per_brief)
        if round_no:
            prompt += " Be unusual: avoid the first phrasings that come to mind."
        try:
            reply = chat.chat([{"role": "user", "content": prompt}], max_tokens=2500,
                              temperature=0.9 if round_no else 0.7, timeout=120, extra=NO_THINKING)
        except Exception as exc:
            print(f"  failed: {type(exc).__name__}: {str(exc)[:80]}", flush=True)
            return label, []
        return label, parse(reply.get("content", ""))

    jobs = [(brief, label, r) for r in range(args.rounds) for brief, label in briefs()]
    print(f"{len(jobs)} briefs, {args.per_brief} sentences each", flush=True)
    cases, seen, dropped = [], set(), 0
    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        for done, (label, sentences) in enumerate(pool.map(ask, jobs), 1):
            for sentence in sentences:
                key = router_data._norm(sentence)
                if key in seen or not keep(sentence, label):
                    dropped += key not in seen
                    continue
                seen.add(key)
                case = {"say": sentence, **{k: v for k, v in label.items() if k not in ("banned", "must", "lang")}}
                cases.append(case)
            if done % 10 == 0:
                print(f"  {done}/{len(jobs)} briefs, {len(cases)} sentences", flush=True)
    print(f"{len(cases)} sentences kept, {dropped} dropped for breaking their brief")

    rng = random.Random(args.seed)
    rng.shuffle(cases)
    held = cases[:len(cases) // 6]
    train = cases[len(held):]
    out = ROOT / "datasets" / "narada_router"
    out.mkdir(parents=True, exist_ok=True)
    (out / "written.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in train))

    placed = []
    for case in held:
        for _ in range(20):
            row = router_data.place(rng, case)
            if row:
                placed.append(row)
                break
    test = {"_about": "Sentences NIM wrote to narrow briefs (scripts/router_cases.py), each set in a made-up house. "
                      "The routing model never trains on these. Unlike routing_cases.json every case carries its "
                      "own devices and scenes. A field set to null is not scored.",
            "cases": placed}
    (ROOT / "tests" / "eval" / "routing_written.json").write_text(json.dumps(test, ensure_ascii=False, indent=1))
    print(f"{len(train)} for training -> {out / 'written.jsonl'}; {len(placed)} held back as a test")


if __name__ == "__main__":
    main()
