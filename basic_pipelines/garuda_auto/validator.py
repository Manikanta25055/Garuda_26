"""Reject anything the cloud model returns that is not safe and well-formed.

Model output is untrusted input. Nothing here coerces or repairs a rule --
a rule is either legal as written or it is refused with a reason the user
can hear.

The vocabulary is not fixed: it is the schema built from the current device
registry, passed in by the caller.
"""
from .rule_schema import OPS, COOLDOWN_MIN_S, COOLDOWN_MAX_S

_REQUIRED = ("source_utterance", "when", "then")


def _check_condition(cond, schema):
    if not isinstance(cond, dict):
        return f"condition is not an object: {cond!r}"
    extra = set(cond) - {"field", "op", "value"}
    if extra:
        return f"condition has unsupported keys: {sorted(extra)}"
    field, op, value = cond.get("field"), cond.get("op"), cond.get("value")
    spec = schema.fields.get(field)
    if spec is None:
        return f"unknown field: {field!r}"
    if op not in OPS:
        return f"unknown operator: {op!r}"
    if op not in spec["ops"]:
        return f"operator {op!r} is not legal for field {field!r}"
    if spec["kind"] == "enum":
        if not isinstance(value, str) or value not in spec["values"]:
            return f"value {value!r} is not one of {list(spec['values'])} for {field!r}"
    else:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"value {value!r} is not numeric for {field!r}"
        if not (spec["lo"] <= value <= spec["hi"]):
            return f"value {value} is outside {spec['lo']}..{spec['hi']} for {field!r}"
    return None


def validate_rule(rule, schema):
    """Return (True, "") when the rule is safe to store, else (False, reason)."""
    if not isinstance(rule, dict):
        return False, "rule is not an object"
    for key in _REQUIRED:
        if key not in rule:
            return False, f"missing required key: {key}"

    if not isinstance(rule["source_utterance"], str) or not rule["source_utterance"].strip():
        return False, "source_utterance must be a non-empty string"

    when = rule["when"]
    if not isinstance(when, dict) or len(when) != 1:
        return False, "when must be an object with exactly one of 'all' or 'any'"
    combinator, conditions = next(iter(when.items()))
    if combinator not in ("all", "any"):
        return False, f"unknown combinator: {combinator!r}"
    if not isinstance(conditions, list) or not conditions:
        return False, "when.%s must be a non-empty list" % combinator
    if len(conditions) > 8:
        return False, "at most 8 conditions per rule"
    for cond in conditions:
        problem = _check_condition(cond, schema)
        if problem:
            return False, problem

    actions = rule["then"]
    if not isinstance(actions, list) or not actions:
        return False, "then must be a non-empty list"
    if len(actions) > 4:
        return False, "at most 4 actions per rule"
    for act in actions:
        if not isinstance(act, dict):
            return False, f"action is not an object: {act!r}"
        device, action = act.get("device"), act.get("action")
        legal = schema.devices.get(device)
        if legal is None:
            return False, f"unknown device: {device!r}"
        if action not in legal:
            return False, f"action {action!r} is not legal for device {device!r}"

    cooldown = rule.get("cooldown_s", 60)
    if isinstance(cooldown, bool) or not isinstance(cooldown, (int, float)):
        return False, "cooldown_s must be numeric"
    if not (COOLDOWN_MIN_S <= cooldown <= COOLDOWN_MAX_S):
        return False, f"cooldown_s must be between {COOLDOWN_MIN_S} and {COOLDOWN_MAX_S}"

    return True, ""
