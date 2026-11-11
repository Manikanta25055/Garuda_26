"""Evaluate the rule base against one descriptor. Pure and side-effect free --
it returns the actions it wants; the caller decides whether to perform them.
"""
import time

# How long a rule whose switch failed waits before it is tried again.
RETRY_FIRST_S = 30.0
RETRY_MAX_S = 900.0

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
    def __init__(self, store, clock=time.time, once=False):
        self.store = store
        self._clock = clock
        self._last_fired = {}
        # once: a rule acts when its condition becomes true, then stays quiet
        # until the condition has been false again. Without it a rule asks for
        # the same thing on every tick it holds, so "when I am home, lamp on"
        # switched the lamp back on a fifth of a second after a person had
        # switched it off. The house loop runs with once=True.
        self._once = once
        self._holding = set()
        # Rules whose last fire did not reach the device: rule id -> (how many
        # times in a row, when it may be tried again).
        self._failed = {}

    def prime(self, descriptor):
        """Take the conditions that hold now as already acted on.

        Called when the house loop starts: a restart is not somebody coming
        home, so it must not switch on everything a rule would have.
        """
        self._holding = {rule.get("id", "") for rule in self.store.rules
                         if rule.get("enabled", True) and self._matches(rule, descriptor)}

    def release(self, rule_id):
        """The rule fired but the device did not switch: try it again later.

        A fire marks the rule as acted on until its condition has been false
        again. When the switch then failed (the broker was away, the relay
        could not be driven, the switch guard held it back) the rule stayed
        marked, so "when I am home, lamp on" never did turn the lamp on for as
        long as the person stayed home. The pause doubles on each failure in a
        row, from RETRY_FIRST_S to RETRY_MAX_S, so a device that is gone for a
        day is not asked, and logged, twice a second. Returns the pause.
        """
        count = self._failed.get(rule_id, (0, 0.0))[0] + 1
        delay = min(RETRY_MAX_S, RETRY_FIRST_S * 2 ** (count - 1))
        self._failed[rule_id] = (count, self._clock() + delay)
        self._holding.discard(rule_id)
        return delay

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
            rule_id = rule.get("id", "")
            if not rule.get("enabled", True) or not self._matches(rule, descriptor):
                self._holding.discard(rule_id)
                self._failed.pop(rule_id, None)
                continue
            if self._once and rule_id in self._holding:
                continue
            failed = self._failed.get(rule_id)
            if failed is not None and now < failed[1]:
                continue
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
                self._holding.add(rule_id)
        return actions
