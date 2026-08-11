"""Durable rule base. Learned rules must survive a reboot, or the whole
premise of the system fails.
"""
import json
import os
import tempfile
import threading
import time

from .rule_schema import MAX_RULES, FIELDS
from .validator import validate_rule

def _provably_disjoint(a, b):
    """True when two conditions on the same field can never hold together.

    Deliberately conservative: when in doubt, say they can overlap, so the
    conflict check errs towards asking the user rather than silently
    installing a contradictory rule.
    """
    if a["field"] != b["field"]:
        return False
    spec = FIELDS[a["field"]]
    av, bv, ao, bo = a["value"], b["value"], a["op"], b["op"]
    if spec["kind"] == "enum":
        if ao == "==" and bo == "==":
            return av != bv
        if ao == "==" and bo == "!=":
            return av == bv
        if ao == "!=" and bo == "==":
            return av == bv
        return False
    if ao == "==" and bo == "==":
        return av != bv
    if ao in ("<", "<=") and bo in (">", ">="):
        return av <= bv
    if ao in (">", ">=") and bo in ("<", "<="):
        return av >= bv
    return False


def _conditions(rule):
    return next(iter(rule["when"].values()))


class RuleStore:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self.rules = []
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.rules = [r for r in data if validate_rule(r)[0]] if isinstance(data, list) else []
        except (OSError, ValueError):
            # Missing or corrupt store is not fatal -- start empty rather than
            # taking the whole voice loop down.
            self.rules = []

    def save(self):
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.rules, fh, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def find_conflict(self, rule):
        """Return an existing rule that drives a shared device the opposite way
        under a predicate that can hold at the same time, else None."""
        new_actions = {(a["device"], a["action"]) for a in rule["then"]}
        new_conds = _conditions(rule)
        for existing in self.rules:
            for device, action in {(a["device"], a["action"]) for a in existing["then"]}:
                opposite = {(device, "on"), (device, "off")} - {(device, action)}
                if not (new_actions & opposite):
                    continue
                disjoint = any(
                    _provably_disjoint(nc, ec)
                    for nc in new_conds for ec in _conditions(existing)
                )
                if not disjoint:
                    return existing
        return None

    def add(self, rule):
        ok, reason = validate_rule(rule)
        if not ok:
            return False, reason
        with self._lock:
            if len(self.rules) >= MAX_RULES:
                return False, f"rule limit reached ({MAX_RULES})"
            rule = dict(rule)
            if not rule.get("id"):
                rule["id"] = f"r_{int(time.time() * 1000):x}"
            rule.setdefault("cooldown_s", 60)
            rule.setdefault("enabled", True)
            rule.setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
            self.rules.append(rule)
            self.save()
        return True, ""

    def delete(self, rule_id):
        with self._lock:
            before = len(self.rules)
            self.rules = [r for r in self.rules if r.get("id") != rule_id]
            if len(self.rules) == before:
                return False
            self.save()
        return True
