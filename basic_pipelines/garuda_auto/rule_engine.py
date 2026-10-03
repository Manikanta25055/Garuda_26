"""Evaluate the rule base against one descriptor. Pure and side-effect free --
it returns the actions it wants; the caller decides whether to perform them.
"""
import time

_COMPARE = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<":  lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">":  lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


def _holds(cond, descriptor):
    if cond["field"] not in descriptor:
        return False
    actual = descriptor[cond["field"]]
    expected = cond["value"]
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if isinstance(actual, bool) or not isinstance(actual, (int, float)):
            return False
    elif isinstance(expected, str) and not isinstance(actual, str):
        return False
    try:
        return _COMPARE[cond["op"]](actual, expected)
    except TypeError:
        return False


class RuleEngine:
    def __init__(self, store, clock=time.time):
        self.store = store
        self._clock = clock
        self._last_fired = {}

    def _matches(self, rule, descriptor):
        combinator, conditions = next(iter(rule["when"].items()))
        results = (_holds(c, descriptor) for c in conditions)
        return all(results) if combinator == "all" else any(results)

    def evaluate(self, descriptor):
        """Return the actions to perform this tick.

        Rules are considered in store order and the first rule to claim a
        device wins, so a later rule cannot immediately undo an earlier one
        within the same tick.
        """
        now = self._clock()
        actions, claimed = [], set()
        for rule in self.store.rules:
            if not rule.get("enabled", True):
                continue
            if not self._matches(rule, descriptor):
                continue
            rule_id = rule.get("id", "")
            cooldown = rule.get("cooldown_s", 60)
            last = self._last_fired.get(rule_id)
            if last is not None and (now - last) < cooldown:
                continue
            fired = False
            for act in rule["then"]:
                if act["device"] in claimed:
                    continue
                claimed.add(act["device"])
                actions.append({"device": act["device"], "action": act["action"], "rule_id": rule_id})
                fired = True
            if fired:
                self._last_fired[rule_id] = now
        return actions
