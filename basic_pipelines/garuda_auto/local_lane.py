"""Answers that never touch the network.

State questions, explanations and direct control all need current readings,
which is exactly why they are answered here and not by a cloud model.

Matching is literal on purpose. Anything this module is not sure about it
declines, and the utterance falls through to the matcher and then the
compiler. A local lane that guesses is worse than one that says nothing.
"""
import re

from . import actuation_log
from .rule_schema import state_field

# A conditional means a rule is being taught, not a question being asked.
_CONDITIONAL = re.compile(
    r"\b(when|whenever|if|after|unless|every time|as soon as)\b", re.I)
_WHY = re.compile(r"\bwhy\b", re.I)
_PRESENCE = re.compile(r"\b(anyone|anybody|someone)\b", re.I)
_OFF = re.compile(r"\b(off|stop|kill|shut)\b", re.I)
_ON = re.compile(r"\b(on|start)\b", re.I)

_RESOLVED = "on-device"


def _find_device(text, registry):
    """Longest name match wins, so 'desk lamp' beats 'lamp'."""
    lowered = text.lower()
    best = None
    for device in registry.devices:
        if not device.get("enabled", True):
            continue
        for candidate in (device["name"].lower(), device["id"].replace("_", " ")):
            if candidate in lowered and (best is None or len(candidate) > best[1]):
                best = (device, len(candidate))
    return best[0] if best else None


def _wanted_action(text):
    if _OFF.search(text):
        return "off"
    if _ON.search(text):
        return "on"
    return None


def _presence(descriptor):
    count = descriptor.get("person_count", 0)
    if not count:
        return "Nobody is in the room."
    person = "person" if count == 1 else "people"
    return f"Yes — {count} {person} in the room right now."


def _explain(text, registry, log_path, store):
    device = _find_device(text, registry)
    if device is None:
        return None
    entry = actuation_log.last_for(log_path, device["id"])
    if entry is None:
        return {"kind": "why", "resolved": _RESOLVED,
                "text": f"There is no record of {device['name']} changing."}
    rule = next((r for r in store.rules if r.get("id") == entry["rule_id"]), None)
    if rule is not None:
        source = rule["source_utterance"]
    elif entry["rule_id"]:
        source = "a rule that no longer exists"
    else:
        return {"kind": "why", "resolved": _RESOLVED,
                "text": (f"{device['name']} was turned {entry['action']} directly, "
                         "not by a rule.")}
    conditions = ", ".join(
        f"{c['field']} {c['op']} {c['value']}" for c in entry.get("matched", []))
    detail = f" because {conditions}" if conditions else ""
    outcome = "turned" if entry["ok"] else "was asked to turn"
    return {"kind": "why", "resolved": _RESOLVED,
            "text": (f"{device['name']} {outcome} {entry['action']}{detail}. "
                     f"That came from the rule: “{source}”.")}


def answer(text, *, registry, descriptor, router, log_path, store):
    if not isinstance(text, str) or not text.strip():
        return None

    # A conditional is a rule being taught. Never intercept it.
    if _CONDITIONAL.search(text):
        return None

    if _WHY.search(text):
        return _explain(text, registry, log_path, store)

    lowered = text.lower()

    if _PRESENCE.search(lowered):
        return {"kind": "state", "resolved": _RESOLVED, "text": _presence(descriptor)}

    if "temperature" in lowered or "how warm" in lowered or "how cold" in lowered:
        return {"kind": "state", "resolved": _RESOLVED,
                "text": f"It is {descriptor['temperature_c']}°C."}

    if "humidity" in lowered or "how humid" in lowered:
        return {"kind": "state", "resolved": _RESOLVED,
                "text": f"Humidity is {descriptor['humidity_pct']}%."}

    device = _find_device(text, registry)
    if device is None:
        return None

    # A question about the device, not a command.
    stripped = lowered.strip()
    if stripped.startswith(("is ", "are ", "what")) or stripped.endswith("?"):
        value = descriptor.get(state_field(device["id"]), router.state(device["id"]))
        if value is None:
            return {"kind": "state", "resolved": _RESOLVED,
                    "text": f"{device['name']} has not reported its state."}
        return {"kind": "state", "resolved": _RESOLVED,
                "text": f"{device['name']} is {value}."}

    action = _wanted_action(text)
    if action is None:
        return None

    ok, reason = router.set(device["id"], action)
    actuation_log.record(log_path, device=device["id"], action=action,
                         rule_id="", matched=[], ok=ok, reason=reason)
    if not ok:
        return {"kind": "control", "resolved": _RESOLVED,
                "text": f"Could not change {device['name']}: {reason}"}
    return {"kind": "control", "resolved": _RESOLVED,
            "text": f"{device['name']} is now {action}."}
