"""Score a synthesised rule against its reference by behaviour, not by text.

Two rules are equivalent when they fire on the same descriptor states and
drive the same devices the same way. The states compared are a grid built
from every constant either rule mentions, with one step either side, so an
off-by-one on a threshold or a flipped comparison shows up as a disagreement.

Only physically consistent states are compared. An empty room has no zone and
no posture, so a rule that adds a redundant occupancy guard is not penalised
for it, which is what the corpus header promises.
"""
import itertools

from basic_pipelines.garuda_auto.rule_engine import _holds

_FLOAT_FIELDS = ("temperature_c", "humidity_pct")
_PRESENCE = ("occupancy", "person_count", "zone", "posture")


def _conds(rule):
    return next(iter(rule["when"].values()))


def _fires(rule, state):
    combinator, conditions = next(iter(rule["when"].items()))
    results = [_holds(c, state) for c in conditions]
    return all(results) if combinator == "all" else any(results)


def _points(field, spec, constants):
    if spec["kind"] == "enum":
        return list(spec["values"])
    lo, hi = spec["lo"], spec["hi"]
    step = 0.5 if field in _FLOAT_FIELDS else 1
    pts = {lo, hi}
    for c in constants:
        for d in (-2 * step, -step, 0, step, 2 * step):
            pts.add(min(hi, max(lo, c + d)))
    return sorted(pts)


def _consistent(state):
    occ = state.get("occupancy")
    count = state.get("person_count")
    if occ == "empty":
        if state.get("zone", "none") != "none" or state.get("posture", "none") != "none":
            return False
        if count not in (None, 0):
            return False
    if occ == "occupied" and count == 0:
        return False
    return True


def _grid(rules, schema):
    fields = {}
    for rule in rules:
        for c in _conds(rule):
            fields.setdefault(c["field"], set())
            if isinstance(c["value"], (int, float)) and not isinstance(c["value"], bool):
                fields[c["field"]].add(c["value"])
    if any(f in fields for f in _PRESENCE):
        for f in _PRESENCE:
            fields.setdefault(f, set())
    names = sorted(fields)
    axes = [_points(n, schema.fields[n], fields[n]) for n in names]
    for combo in itertools.product(*axes):
        state = dict(zip(names, combo))
        if _consistent(state):
            yield state


def _actions(rule):
    return sorted((a["device"], a["action"]) for a in rule["then"])


def equivalent(rule, reference, schema):
    """Return (same, n_states, n_disagreements)."""
    if _actions(rule) != _actions(reference):
        return False, 0, 0
    total = wrong = 0
    for state in _grid([rule, reference], schema):
        total += 1
        if _fires(rule, state) != _fires(reference, state):
            wrong += 1
    return wrong == 0, total, wrong


def _direction(op):
    if op in ("<", "<="):
        return "lt"
    if op in (">", ">="):
        return "gt"
    return op


def normalise_vague(rule, reference, vague):
    """Copy the reference's constant (and strictness) onto the rule's
    conditions on fields whose threshold the utterance left unstated, pairing
    conditions by the direction of the comparison."""
    if not vague:
        return rule
    ref_by_key = {}
    for c in _conds(reference):
        if c["field"] in vague:
            ref_by_key[(c["field"], _direction(c["op"]))] = c
    out = {**rule, "when": {}}
    combinator, conditions = next(iter(rule["when"].items()))
    patched = []
    for c in conditions:
        ref = ref_by_key.get((c["field"], _direction(c["op"])))
        patched.append({**c, "op": ref["op"], "value": ref["value"]} if ref else dict(c))
    out["when"][combinator] = patched
    return out


def score(rule, reference, vague, schema):
    """Return one of 'exact', 'threshold', 'wrong', with detail."""
    same, total, wrong = equivalent(rule, reference, schema)
    if same:
        return "exact", {"states": total, "disagreements": 0}
    if vague:
        norm = normalise_vague(rule, reference, vague)
        same2, total2, wrong2 = equivalent(norm, reference, schema)
        if same2:
            return "threshold", {"states": total2, "disagreements": 0}
        return "wrong", {"states": total2, "disagreements": wrong2}
    return "wrong", {"states": total, "disagreements": wrong}
