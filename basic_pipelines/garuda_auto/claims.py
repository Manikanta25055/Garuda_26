"""What a reply says about the house, checked against the house.

A model can write "The lamp is on in the study" without having called a tool,
and the person believes it. Measured 2026-10-17: nemotron-3.5-super-vl did so
3 times in about 84 device commands ("don't turn on the TV, just the lamp":
no call, "The lamp is now on"); nemotron-3-super did not. So the agent reads
its own reply back: a sentence that names a device and states it on or off
must match that device's state once the turn's tools have run.

Deliberately narrow. Only plain English statements of a present state are
read; offers ("want me to turn it on?"), questions, plans ("will turn off at
7") and anything not in English are left alone, so a miss here is a claim
not caught, never a true reply flagged.
"""
import re

# A clause states a state with one of these, the state word in group "state".
_STATE = (r"(?:(?:is|are|'s)\s+(?:now\s+|already\s+|still\s+|currently\s+)?"
          r"|(?:remains?|stays?|kept|left)\s+"
          r"|(?:turned|switched|put|flipped)\s+(?:it\s+|them\s+|both\s+)?"
          r"|now\s+)"
          r"(?P<state>on|off)\b"
          # "on a timer", "off at 7", "off in 10 minutes": a plan, not a state.
          r"(?!\s+(?:(?:a|an|at|for|until|after|by|from|schedule|timer)\b|in\s+\d))")
_STATE_RE = re.compile(_STATE, re.I)
# Words that make a clause an offer, a question, a plan or a denial.
_NOT_A_CLAIM = re.compile(
    r"\b(would you|do you|shall i|should i|can i|could i|want me|like me|if you|let me know|"
    r"i can|i could|i will|i.ll|will be|going to|not|n.t|never|no longer|didn.t|couldn.t|"
    r"unable|failed|try|tried|wait)\b|\?"
    # A time puts the clause in the past or the future: "turned on at 7:02",
    # "was on this morning". Those are the record's business, not now.
    r"|\b(at\s+\d|\d{1,2}[:.]\d{2}|\d\s*(am|pm)|yesterday|today|tonight|this (morning|evening|"
    r"afternoon|week)|last|ago|earlier|since|between|times|hours?|minutes?)\b", re.I)
# The bare "Lamp on" form is read only in a short one-line reply ("Lamp on.
# TV on."), the agent's report of what it just did; in a longer answer it is
# as likely a line of a list of events.
SHORT_REPLY = 160
# Clauses: sentences, then the parts joined by these.
_SENTENCES = re.compile(r"(?<=[.!?;])\s+|\n+")
_JOINS = re.compile(r",\s*(?:and\s+|but\s+)?|\s+but\s+|\s+while\s+|\s+whereas\s+", re.I)


def _names(device):
    names = {device["name"].lower()}
    names.add(device["id"].lower().replace("_", " "))
    return [n for n in names if n]


def false_claims(reply, devices):
    """[(device name, state the reply gives, state it is in)] for each device the
    reply puts in a state it is not in. `devices`: dicts with id, name, state."""
    found = {}
    short = len(reply or "") <= SHORT_REPLY and "\n" not in (reply or "").strip()
    for sentence in _SENTENCES.split(reply or ""):
        for clause in _JOINS.split(sentence):
            if _NOT_A_CLAIM.search(clause):
                continue
            low = clause.lower().strip(" .!;:")
            states = [(m.start(), m.group("state").lower()) for m in _STATE_RE.finditer(low)]
            for device in devices:
                if device.get("state") not in ("on", "off"):
                    continue
                claimed = None
                for n in _names(device):
                    name = re.escape(n)
                    # "Lamp on", the agent's own shorthand for what it did, and
                    # "switched the fan off".
                    direct = ((short and re.search(rf"(?:^|:\s*)(?:the\s+)?{name}\s+(on|off)$", low))
                              or re.search(rf"\b(?:turned|switched|put|flipped)\s+(?:the\s+)?"
                                           rf"{name}s?\s+(on|off)\b", low))
                    if direct:
                        claimed = direct.group(1)
                        break
                    seen = re.search(rf"\b{name}s?\b", low)
                    if seen and states:
                        # The state said after the name ("the fan is on"), else the
                        # one before it ("turned on the lamp").
                        after = [st for pos, st in states if pos > seen.start()]
                        claimed = after[0] if after else states[-1][1]
                if claimed and device["state"] != claimed:
                    found[device["id"]] = (device["name"], claimed, device["state"])
    return list(found.values())


def correction(wrong, devices):
    """The note the model gets when its reply misstated the house."""
    said = "; ".join(f"{name} is {actual}, not {claimed}" for name, claimed, actual in wrong)
    states = ", ".join(f"{d['name']} {d['state']}" for d in devices if d.get("state") in ("on", "off"))
    return ("(From the house, not the person: your reply misstates the devices: "
            f"{said}. Right now: {states}. No tool changed that. If the person asked for a "
            "change, make it with the tools now; otherwise answer again with the true state. "
            "Do not mention this note.)")
