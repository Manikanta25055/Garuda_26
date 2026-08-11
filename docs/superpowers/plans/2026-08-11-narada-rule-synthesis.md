# Narada-RS: Spoken Rule Synthesis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend Narada so that an unrecognised spoken instruction is compiled by a cloud LLM into a persistent local rule, which then drives physical relays autonomously with no further network calls.

**Architecture:** Two tiers. A reflex tier runs on the Pi at 2 Hz, builds a symbolic scene descriptor from the existing YOLO detections plus a DHT22, evaluates a local rule base against it, and switches relay channels. A synthesis tier is invoked only when a spoken utterance fails local matching; it sends the utterance text and the descriptor *schema* to NVIDIA NIM, receives a structured rule, validates it on-device, and persists it. No frames, no audio, and no live descriptor values ever leave the Pi.

**Tech Stack:** Python 3.11, gpiozero (relays), adafruit-circuitpython-dht (DHT22), rapidfuzz (matcher baseline), optional sentence-transformers (matcher ablation), requests (NIM, already a dependency), pytest.

## Global Constraints

- Target host is the Pi: `manikanta@ai:~/Projects/Garuda_26`. Tasks 1–4, 7, 8 are pure Python and testable on any machine; Tasks 5, 6, 9–12 need the Pi.
- GPIO uses **gpiozero only**. Never `RPi.GPIO` — it does not work on Pi 5.
- No emojis or decorative symbols in code, comments, logs, or spoken output.
- New code lives in `basic_pipelines/garuda_auto/`. `Garuda_web.py` is already ~3,100 lines; add only the integration calls to it, nothing else.
- Egress boundary is absolute: the only things that may be sent to NIM are the utterance text, the descriptor schema (field names and legal values), the device list, and a summary of existing rules. Never frames, crops, keypoints, audio, or live descriptor values. Any task that appears to require otherwise is a mistake in this plan — stop and report it.
- Scene descriptor fields are fixed by `rule_schema.py`. A rule referencing any field outside that table is rejected, never coerced.
- Rule base is capped at 64 rules.
- Tests go in `tests/garuda_auto/`. The repo's existing `tests/conftest.py` and `pytest.ini` apply.
- Commit after every task. Small commits.

---

## File Structure

| File | Responsibility |
|---|---|
| `basic_pipelines/garuda_auto/__init__.py` | package marker |
| `basic_pipelines/garuda_auto/rule_schema.py` | the single source of truth for legal fields, ops, devices, actions |
| `basic_pipelines/garuda_auto/validator.py` | reject anything the model returns that is not safe and well-formed |
| `basic_pipelines/garuda_auto/rule_store.py` | load, persist, add, delete, conflict-check the rule base |
| `basic_pipelines/garuda_auto/scene_state.py` | build the symbolic descriptor from detections and sensors |
| `basic_pipelines/garuda_auto/rule_engine.py` | evaluate rules against a descriptor, emit actions |
| `basic_pipelines/garuda_auto/actuators.py` | gpiozero relay driver |
| `basic_pipelines/garuda_auto/sensors.py` | DHT22 reader with retry and last-good cache |
| `basic_pipelines/garuda_auto/matcher.py` | decide whether an utterance needs the cloud |
| `basic_pipelines/garuda_auto/nim_client.py` | NIM synthesis request and response parsing |
| `basic_pipelines/garuda_auto/audio_out.py` | TTS with a half-duplex mic gate |
| `basic_pipelines/garuda_auto/reflex_loop.py` | the 2 Hz tick that ties descriptor to engine to actuators |
| `evaluation/narada_rs/corpus.py` | 30 rule requests plus 3 paraphrases each |
| `evaluation/narada_rs/run_eval.py` | metrics harness |

Tasks are ordered so that everything testable without hardware comes first. If the lamp and fan have not arrived, Tasks 1–4, 7, 8 and 12's corpus can all be completed anyway.

---

### Task 1: Rule schema and validator

The validator is the security boundary for model output. Everything else depends on it, so it is first.

**Files:**
- Create: `basic_pipelines/garuda_auto/__init__.py`
- Create: `basic_pipelines/garuda_auto/rule_schema.py`
- Create: `basic_pipelines/garuda_auto/validator.py`
- Test: `tests/garuda_auto/test_validator.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `FIELDS: dict[str, dict]`, `OPS: frozenset[str]`, `DEVICES: dict[str, frozenset[str]]`, `MAX_RULES: int`, `schema_for_prompt() -> dict`; and `validate_rule(rule: dict) -> tuple[bool, str]` returning `(True, "")` on success or `(False, reason)` on rejection.

- [ ] **Step 1: Write the failing test**

```python
# tests/garuda_auto/test_validator.py
import pytest
from basic_pipelines.garuda_auto.validator import validate_rule

BASE = {
    "id": "r_001",
    "source_utterance": "turn the fan off when the room is empty for five minutes",
    "when": {"all": [
        {"field": "occupancy", "op": "==", "value": "empty"},
        {"field": "occupancy_duration_s", "op": ">=", "value": 300},
    ]},
    "then": [{"device": "fan", "action": "off"}],
    "cooldown_s": 60,
    "enabled": True,
}


def test_accepts_a_well_formed_rule():
    ok, reason = validate_rule(BASE)
    assert ok is True, reason


def test_rejects_unknown_field():
    bad = {**BASE, "when": {"all": [{"field": "face_id", "op": "==", "value": "manikanta"}]}}
    ok, reason = validate_rule(bad)
    assert ok is False
    assert "face_id" in reason


def test_rejects_unknown_device():
    bad = {**BASE, "then": [{"device": "front_door_lock", "action": "off"}]}
    ok, reason = validate_rule(bad)
    assert ok is False
    assert "front_door_lock" in reason


def test_rejects_action_not_legal_for_device():
    bad = {**BASE, "then": [{"device": "fan", "action": "unlock"}]}
    ok, reason = validate_rule(bad)
    assert ok is False


def test_rejects_wrong_value_type_for_enum_field():
    bad = {**BASE, "when": {"all": [{"field": "occupancy", "op": "==", "value": 7}]}}
    ok, reason = validate_rule(bad)
    assert ok is False


def test_rejects_out_of_range_numeric():
    bad = {**BASE, "when": {"all": [
        {"field": "occupancy_duration_s", "op": ">=", "value": 999999},
    ]}}
    ok, reason = validate_rule(bad)
    assert ok is False


def test_rejects_nested_predicate_beyond_one_level():
    bad = {**BASE, "when": {"all": [{"any": [
        {"field": "occupancy", "op": "==", "value": "empty"},
    ]}]}}
    ok, reason = validate_rule(bad)
    assert ok is False


def test_rejects_empty_action_list():
    bad = {**BASE, "then": []}
    ok, reason = validate_rule(bad)
    assert ok is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/garuda_auto/test_validator.py -v`
Expected: collection error, `ModuleNotFoundError: basic_pipelines.garuda_auto`.

- [ ] **Step 3: Write the schema**

```python
# basic_pipelines/garuda_auto/rule_schema.py
"""Single source of truth for what a synthesised rule may reference.

This module is the allowlist. The cloud model is told these field names and
legal values; it is never told their current readings. Adding a field here
widens the model's vocabulary, so treat edits as a design decision.
"""

MAX_RULES = 64
MAX_DURATION_S = 86_400

OPS = frozenset({"==", "!=", "<", "<=", ">", ">="})
_NUM_OPS = frozenset({"==", "!=", "<", "<=", ">", ">="})
_ENUM_OPS = frozenset({"==", "!="})

# kind: "enum" -> values, or "num" -> lo/hi inclusive bounds
FIELDS = {
    "occupancy":            {"kind": "enum", "values": ("empty", "occupied"), "ops": _ENUM_OPS},
    "person_count":         {"kind": "num", "lo": 0, "hi": 16, "ops": _NUM_OPS},
    "occupancy_duration_s": {"kind": "num", "lo": 0, "hi": MAX_DURATION_S, "ops": _NUM_OPS},
    "zone":                 {"kind": "enum", "values": ("none", "desk", "door", "center"), "ops": _ENUM_OPS},
    "posture":              {"kind": "enum", "values": ("none", "standing", "seated", "walking"), "ops": _ENUM_OPS},
    "ambient_luma":         {"kind": "num", "lo": 0, "hi": 255, "ops": _NUM_OPS},
    "temperature_c":        {"kind": "num", "lo": -10, "hi": 60, "ops": _NUM_OPS},
    "humidity_pct":         {"kind": "num", "lo": 0, "hi": 100, "ops": _NUM_OPS},
    "hour":                 {"kind": "num", "lo": 0, "hi": 23, "ops": _NUM_OPS},
    "lamp_state":           {"kind": "enum", "values": ("on", "off"), "ops": _ENUM_OPS},
    "fan_state":            {"kind": "enum", "values": ("on", "off"), "ops": _ENUM_OPS},
}

DEVICES = {
    "lamp": frozenset({"on", "off"}),
    "fan":  frozenset({"on", "off"}),
}

COOLDOWN_MIN_S = 0
COOLDOWN_MAX_S = 3600


def schema_for_prompt():
    """The exact structure sent to NIM. Field names and legal values only.

    Never include readings here. The model compiles rules; it does not need
    to know what the room currently looks like.
    """
    out = {}
    for name, spec in FIELDS.items():
        if spec["kind"] == "enum":
            out[name] = {"type": "enum", "values": list(spec["values"])}
        else:
            out[name] = {"type": "number", "min": spec["lo"], "max": spec["hi"]}
    return {"fields": out, "devices": {d: sorted(a) for d, a in DEVICES.items()},
            "operators": sorted(OPS)}
```

- [ ] **Step 4: Write the validator**

```python
# basic_pipelines/garuda_auto/validator.py
"""Reject anything the cloud model returns that is not safe and well-formed.

Model output is untrusted input. Nothing here coerces or repairs a rule --
a rule is either legal as written or it is refused with a reason the user
can hear.
"""
from .rule_schema import FIELDS, OPS, DEVICES, COOLDOWN_MIN_S, COOLDOWN_MAX_S

_REQUIRED = ("source_utterance", "when", "then")


def _check_condition(cond):
    if not isinstance(cond, dict):
        return f"condition is not an object: {cond!r}"
    extra = set(cond) - {"field", "op", "value"}
    if extra:
        return f"condition has unsupported keys: {sorted(extra)}"
    field, op, value = cond.get("field"), cond.get("op"), cond.get("value")
    spec = FIELDS.get(field)
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


def validate_rule(rule):
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
        problem = _check_condition(cond)
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
        legal = DEVICES.get(device)
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/garuda_auto/test_validator.py -v`
Expected: 8 passed.

- [ ] **Step 6: Commit**

```bash
git add basic_pipelines/garuda_auto/__init__.py basic_pipelines/garuda_auto/rule_schema.py basic_pipelines/garuda_auto/validator.py tests/garuda_auto/test_validator.py
git commit -m "feat(auto): rule schema and validator for synthesised rules"
```

---

### Task 2: Rule store with conflict detection

**Files:**
- Create: `basic_pipelines/garuda_auto/rule_store.py`
- Test: `tests/garuda_auto/test_rule_store.py`

**Interfaces:**
- Consumes: `validate_rule` from Task 1, `MAX_RULES` from `rule_schema`.
- Produces: class `RuleStore(path: str)` with `.rules -> list[dict]`, `.add(rule) -> tuple[bool, str]`, `.delete(rule_id) -> bool`, `.find_conflict(rule) -> dict | None`, `.save()`, `.load()`.

Conflict rule: two rules conflict when they drive the same device to opposite states and their predicates can both be satisfied at once. Full satisfiability is overkill here; the check used is that no pair of conditions on the same field is provably disjoint.

- [ ] **Step 1: Write the failing test**

```python
# tests/garuda_auto/test_rule_store.py
import json
from basic_pipelines.garuda_auto.rule_store import RuleStore


def _rule(rid, field, op, value, device, action):
    return {
        "id": rid,
        "source_utterance": f"{action} the {device}",
        "when": {"all": [{"field": field, "op": op, "value": value}]},
        "then": [{"device": device, "action": action}],
        "cooldown_s": 60,
        "enabled": True,
    }


def test_add_then_persist_and_reload(tmp_path):
    path = tmp_path / "rules.json"
    store = RuleStore(str(path))
    ok, reason = store.add(_rule("r_001", "occupancy", "==", "empty", "fan", "off"))
    assert ok is True, reason
    assert len(store.rules) == 1

    reloaded = RuleStore(str(path))
    assert len(reloaded.rules) == 1
    assert reloaded.rules[0]["id"] == "r_001"


def test_add_rejects_invalid_rule(tmp_path):
    store = RuleStore(str(tmp_path / "rules.json"))
    ok, reason = store.add(_rule("r_002", "face_id", "==", "x", "fan", "off"))
    assert ok is False
    assert "face_id" in reason
    assert store.rules == []


def test_detects_opposite_action_on_overlapping_predicate(tmp_path):
    store = RuleStore(str(tmp_path / "rules.json"))
    store.add(_rule("r_003", "occupancy", "==", "empty", "fan", "off"))
    clash = _rule("r_004", "occupancy", "==", "empty", "fan", "on")
    assert store.find_conflict(clash) is not None


def test_no_conflict_when_predicates_are_disjoint(tmp_path):
    store = RuleStore(str(tmp_path / "rules.json"))
    store.add(_rule("r_005", "occupancy", "==", "empty", "fan", "off"))
    fine = _rule("r_006", "occupancy", "==", "occupied", "fan", "on")
    assert store.find_conflict(fine) is None


def test_no_conflict_across_different_devices(tmp_path):
    store = RuleStore(str(tmp_path / "rules.json"))
    store.add(_rule("r_007", "occupancy", "==", "empty", "fan", "off"))
    fine = _rule("r_008", "occupancy", "==", "empty", "lamp", "on")
    assert store.find_conflict(fine) is None


def test_delete_removes_and_persists(tmp_path):
    path = tmp_path / "rules.json"
    store = RuleStore(str(path))
    store.add(_rule("r_009", "occupancy", "==", "empty", "fan", "off"))
    assert store.delete("r_009") is True
    assert RuleStore(str(path)).rules == []


def test_rejects_beyond_max_rules(tmp_path, monkeypatch):
    import basic_pipelines.garuda_auto.rule_store as rs
    monkeypatch.setattr(rs, "MAX_RULES", 2)
    store = rs.RuleStore(str(tmp_path / "rules.json"))
    store.add(_rule("r_a", "occupancy", "==", "empty", "fan", "off"))
    store.add(_rule("r_b", "occupancy", "==", "occupied", "fan", "on"))
    ok, reason = store.add(_rule("r_c", "hour", ">=", 22, "lamp", "off"))
    assert ok is False
    assert "limit" in reason.lower()


def test_corrupt_file_does_not_crash(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text("{ not json")
    store = RuleStore(str(path))
    assert store.rules == []


def test_assigns_id_when_missing(tmp_path):
    store = RuleStore(str(tmp_path / "rules.json"))
    rule = _rule("", "occupancy", "==", "empty", "fan", "off")
    del rule["id"]
    ok, _ = store.add(rule)
    assert ok is True
    assert store.rules[0]["id"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/garuda_auto/test_rule_store.py -v`
Expected: `ModuleNotFoundError: ... rule_store`.

- [ ] **Step 3: Write the implementation**

```python
# basic_pipelines/garuda_auto/rule_store.py
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

_ORDER = {"<": 0, "<=": 1, "==": 2, ">=": 3, ">": 4}


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/garuda_auto/test_rule_store.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add basic_pipelines/garuda_auto/rule_store.py tests/garuda_auto/test_rule_store.py
git commit -m "feat(auto): durable rule store with conflict detection"
```

---

### Task 3: Scene descriptor

**Files:**
- Create: `basic_pipelines/garuda_auto/scene_state.py`
- Test: `tests/garuda_auto/test_scene_state.py`

**Interfaces:**
- Consumes: `FIELDS` from Task 1 (to assert the descriptor covers every declared field).
- Produces: `SceneBuilder(zones: dict[str, tuple[float, float, float, float]], clock=time.time)` with `.update(detections, luma, temperature_c, humidity_pct, hour) -> dict`. `detections` is a list of `{"label": str, "confidence": float, "bbox": (x0, y0, x1, y1), "posture": str}` in normalised 0..1 coordinates. Returns a dict whose keys are exactly `FIELDS.keys()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/garuda_auto/test_scene_state.py
from basic_pipelines.garuda_auto.rule_schema import FIELDS
from basic_pipelines.garuda_auto.scene_state import SceneBuilder

ZONES = {"desk": (0.0, 0.0, 0.33, 1.0), "door": (0.67, 0.0, 1.0, 1.0), "center": (0.33, 0.0, 0.67, 1.0)}


def _person(x0, x1, posture="standing"):
    return {"label": "person", "confidence": 0.9, "bbox": (x0, 0.2, x1, 0.9), "posture": posture}


def test_descriptor_covers_every_declared_field():
    clock = iter([100.0, 100.0])
    b = SceneBuilder(ZONES, clock=lambda: next(clock))
    d = b.update([], luma=100, temperature_c=28.0, humidity_pct=50.0, hour=14)
    assert set(d) == set(FIELDS)


def test_empty_room_reports_empty():
    b = SceneBuilder(ZONES, clock=lambda: 100.0)
    d = b.update([], luma=100, temperature_c=28.0, humidity_pct=50.0, hour=14)
    assert d["occupancy"] == "empty"
    assert d["person_count"] == 0
    assert d["zone"] == "none"
    assert d["posture"] == "none"


def test_person_sets_occupied_and_zone_from_centroid():
    b = SceneBuilder(ZONES, clock=lambda: 100.0)
    d = b.update([_person(0.05, 0.20, "seated")], luma=40, temperature_c=30.0, humidity_pct=55.0, hour=21)
    assert d["occupancy"] == "occupied"
    assert d["person_count"] == 1
    assert d["zone"] == "desk"
    assert d["posture"] == "seated"


def test_duration_accumulates_while_state_holds():
    ticks = iter([100.0, 130.0, 160.0])
    b = SceneBuilder(ZONES, clock=lambda: next(ticks))
    b.update([], luma=10, temperature_c=25.0, humidity_pct=40.0, hour=3)
    b.update([], luma=10, temperature_c=25.0, humidity_pct=40.0, hour=3)
    d = b.update([], luma=10, temperature_c=25.0, humidity_pct=40.0, hour=3)
    assert d["occupancy_duration_s"] == 60


def test_duration_resets_when_state_flips():
    ticks = iter([100.0, 160.0, 170.0])
    b = SceneBuilder(ZONES, clock=lambda: next(ticks))
    b.update([], luma=10, temperature_c=25.0, humidity_pct=40.0, hour=3)
    b.update([], luma=10, temperature_c=25.0, humidity_pct=40.0, hour=3)
    d = b.update([_person(0.4, 0.5)], luma=10, temperature_c=25.0, humidity_pct=40.0, hour=3)
    assert d["occupancy"] == "occupied"
    assert d["occupancy_duration_s"] == 0


def test_non_person_labels_do_not_create_occupancy():
    b = SceneBuilder(ZONES, clock=lambda: 100.0)
    knife = {"label": "knife", "confidence": 0.8, "bbox": (0.4, 0.4, 0.5, 0.5), "posture": "none"}
    d = b.update([knife], luma=100, temperature_c=28.0, humidity_pct=50.0, hour=14)
    assert d["occupancy"] == "empty"
    assert d["person_count"] == 0


def test_device_states_reflect_setter():
    b = SceneBuilder(ZONES, clock=lambda: 100.0)
    b.set_device_state("lamp", "on")
    d = b.update([], luma=100, temperature_c=28.0, humidity_pct=50.0, hour=14)
    assert d["lamp_state"] == "on"
    assert d["fan_state"] == "off"


def test_values_are_clamped_into_schema_range():
    b = SceneBuilder(ZONES, clock=lambda: 100.0)
    d = b.update([], luma=9999, temperature_c=-99.0, humidity_pct=250.0, hour=14)
    assert d["ambient_luma"] == 255
    assert d["temperature_c"] == -10
    assert d["humidity_pct"] == 100
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/garuda_auto/test_scene_state.py -v`
Expected: `ModuleNotFoundError: ... scene_state`.

- [ ] **Step 3: Write the implementation**

```python
# basic_pipelines/garuda_auto/scene_state.py
"""Turn raw detections and sensor readings into the symbolic descriptor.

Everything downstream -- rule evaluation, and the schema shown to the cloud
model -- speaks this vocabulary and nothing else.
"""
import time

from .rule_schema import FIELDS


def _clamp(value, field):
    spec = FIELDS[field]
    return max(spec["lo"], min(spec["hi"], value))


class SceneBuilder:
    def __init__(self, zones, clock=time.time):
        self.zones = zones
        self._clock = clock
        self._state_since = None
        self._last_occupancy = None
        self._device_state = {"lamp": "off", "fan": "off"}

    def set_device_state(self, device, state):
        if device in self._device_state and state in ("on", "off"):
            self._device_state[device] = state

    def _zone_for(self, bbox):
        cx = (bbox[0] + bbox[2]) / 2.0
        cy = (bbox[1] + bbox[3]) / 2.0
        for name, (x0, y0, x1, y1) in self.zones.items():
            if x0 <= cx <= x1 and y0 <= cy <= y1:
                return name
        return "none"

    def update(self, detections, luma, temperature_c, humidity_pct, hour):
        now = self._clock()
        people = [d for d in detections if d.get("label") == "person"]
        occupancy = "occupied" if people else "empty"

        if occupancy != self._last_occupancy:
            self._last_occupancy = occupancy
            self._state_since = now
        duration = int(now - self._state_since) if self._state_since is not None else 0

        if people:
            # The largest box is the closest person, and the one whose zone and
            # posture the user means when they say "when I sit at the desk".
            primary = max(people, key=lambda d: (d["bbox"][2] - d["bbox"][0]) * (d["bbox"][3] - d["bbox"][1]))
            zone = self._zone_for(primary["bbox"])
            posture = primary.get("posture") or "none"
            if posture not in FIELDS["posture"]["values"]:
                posture = "none"
        else:
            zone, posture = "none", "none"

        return {
            "occupancy": occupancy,
            "person_count": int(_clamp(len(people), "person_count")),
            "occupancy_duration_s": int(_clamp(duration, "occupancy_duration_s")),
            "zone": zone,
            "posture": posture,
            "ambient_luma": int(_clamp(int(luma), "ambient_luma")),
            "temperature_c": _clamp(float(temperature_c), "temperature_c"),
            "humidity_pct": _clamp(float(humidity_pct), "humidity_pct"),
            "hour": int(_clamp(int(hour), "hour")),
            "lamp_state": self._device_state["lamp"],
            "fan_state": self._device_state["fan"],
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/garuda_auto/test_scene_state.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add basic_pipelines/garuda_auto/scene_state.py tests/garuda_auto/test_scene_state.py
git commit -m "feat(auto): symbolic scene descriptor builder"
```

---

### Task 4: Rule engine

**Files:**
- Create: `basic_pipelines/garuda_auto/rule_engine.py`
- Test: `tests/garuda_auto/test_rule_engine.py`

**Interfaces:**
- Consumes: descriptor dict from Task 3, `RuleStore` from Task 2.
- Produces: `RuleEngine(store, clock=time.time)` with `.evaluate(descriptor) -> list[dict]` returning a list of `{"device": str, "action": str, "rule_id": str}` in rule order, deduplicated so a device appears at most once per tick, and suppressed by per-rule cooldown.

- [ ] **Step 1: Write the failing test**

```python
# tests/garuda_auto/test_rule_engine.py
from basic_pipelines.garuda_auto.rule_engine import RuleEngine


class FakeStore:
    def __init__(self, rules):
        self.rules = rules


def _rule(rid, conds, device, action, combinator="all", cooldown=60, enabled=True):
    return {
        "id": rid,
        "source_utterance": "x",
        "when": {combinator: conds},
        "then": [{"device": device, "action": action}],
        "cooldown_s": cooldown,
        "enabled": enabled,
    }


BASE = {
    "occupancy": "empty", "person_count": 0, "occupancy_duration_s": 400,
    "zone": "none", "posture": "none", "ambient_luma": 20,
    "temperature_c": 31.0, "humidity_pct": 50.0, "hour": 22,
    "lamp_state": "off", "fan_state": "on",
}


def test_all_combinator_fires_when_every_condition_holds():
    rule = _rule("r1", [
        {"field": "occupancy", "op": "==", "value": "empty"},
        {"field": "occupancy_duration_s", "op": ">=", "value": 300},
    ], "fan", "off")
    engine = RuleEngine(FakeStore([rule]), clock=lambda: 1000.0)
    assert engine.evaluate(BASE) == [{"device": "fan", "action": "off", "rule_id": "r1"}]


def test_all_combinator_does_not_fire_when_one_fails():
    rule = _rule("r2", [
        {"field": "occupancy", "op": "==", "value": "empty"},
        {"field": "occupancy_duration_s", "op": ">=", "value": 900},
    ], "fan", "off")
    engine = RuleEngine(FakeStore([rule]), clock=lambda: 1000.0)
    assert engine.evaluate(BASE) == []


def test_any_combinator_fires_on_a_single_match():
    rule = _rule("r3", [
        {"field": "hour", "op": ">=", "value": 22},
        {"field": "occupancy", "op": "==", "value": "occupied"},
    ], "lamp", "off", combinator="any")
    engine = RuleEngine(FakeStore([rule]), clock=lambda: 1000.0)
    assert engine.evaluate(BASE)[0]["rule_id"] == "r3"


def test_disabled_rule_never_fires():
    rule = _rule("r4", [{"field": "occupancy", "op": "==", "value": "empty"}], "fan", "off", enabled=False)
    engine = RuleEngine(FakeStore([rule]), clock=lambda: 1000.0)
    assert engine.evaluate(BASE) == []


def test_cooldown_suppresses_a_repeat_within_the_window():
    rule = _rule("r5", [{"field": "occupancy", "op": "==", "value": "empty"}], "fan", "off", cooldown=60)
    times = iter([1000.0, 1030.0, 1100.0])
    engine = RuleEngine(FakeStore([rule]), clock=lambda: next(times))
    assert len(engine.evaluate(BASE)) == 1
    assert engine.evaluate(BASE) == []
    assert len(engine.evaluate(BASE)) == 1


def test_first_rule_wins_when_two_target_the_same_device():
    first = _rule("r6", [{"field": "occupancy", "op": "==", "value": "empty"}], "fan", "off")
    second = _rule("r7", [{"field": "hour", "op": ">=", "value": 20}], "fan", "on")
    engine = RuleEngine(FakeStore([first, second]), clock=lambda: 1000.0)
    actions = engine.evaluate(BASE)
    assert actions == [{"device": "fan", "action": "off", "rule_id": "r6"}]


def test_missing_descriptor_field_does_not_raise():
    rule = _rule("r8", [{"field": "posture", "op": "==", "value": "seated"}], "lamp", "on")
    engine = RuleEngine(FakeStore([rule]), clock=lambda: 1000.0)
    incomplete = {k: v for k, v in BASE.items() if k != "posture"}
    assert engine.evaluate(incomplete) == []


def test_type_mismatch_is_treated_as_no_match():
    rule = _rule("r9", [{"field": "temperature_c", "op": ">", "value": 30}], "fan", "on")
    engine = RuleEngine(FakeStore([rule]), clock=lambda: 1000.0)
    broken = {**BASE, "temperature_c": "warm"}
    assert engine.evaluate(broken) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/garuda_auto/test_rule_engine.py -v`
Expected: `ModuleNotFoundError: ... rule_engine`.

- [ ] **Step 3: Write the implementation**

```python
# basic_pipelines/garuda_auto/rule_engine.py
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/garuda_auto/test_rule_engine.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add basic_pipelines/garuda_auto/rule_engine.py tests/garuda_auto/test_rule_engine.py
git commit -m "feat(auto): rule evaluation engine with cooldown and device arbitration"
```

---

### Task 5: Relay actuator driver

Hardware task. Needs the Pi, the relay board, and the 12 V supply.

**Wiring before you write code.** Relay board `VCC` and `GND` go to the **12 V adapter**, not the Pi — the Pi supplies signal only. Tie the adapter ground to a Pi ground pin so the opto inputs share a reference. Lamp and fan go through the relay's normally-open contacts. Confirm the chosen BCM pins are not claimed by the Hailo HAT before wiring: run `pinctrl get` and check 17, 27 and 22 read as unused inputs. Most opto-isolated boards are **active low** — the driver assumes this and `ACTIVE_HIGH = False` is the setting to change if your board is the other kind.

**Files:**
- Create: `basic_pipelines/garuda_auto/actuators.py`
- Test: `tests/garuda_auto/test_actuators.py`

**Interfaces:**
- Consumes: `DEVICES` from Task 1.
- Produces: `RelayBank(pin_map: dict[str, int], active_high: bool = False)` with `.set(device, action) -> bool`, `.state(device) -> str`, `.all_off()`, `.close()`. Falls back to a logging no-op backend when gpiozero is unavailable, so the module imports on a laptop.

- [ ] **Step 1: Write the failing test**

```python
# tests/garuda_auto/test_actuators.py
import basic_pipelines.garuda_auto.actuators as actuators


class FakeOutput:
    instances = []

    def __init__(self, pin, active_high=True, initial_value=False):
        self.pin = pin
        self.active_high = active_high
        self.value = initial_value
        self.closed = False
        FakeOutput.instances.append(self)

    def on(self):
        self.value = True

    def off(self):
        self.value = False

    def close(self):
        self.closed = True


def _bank(monkeypatch):
    FakeOutput.instances = []
    monkeypatch.setattr(actuators, "OutputDevice", FakeOutput)
    monkeypatch.setattr(actuators, "GPIO_AVAILABLE", True)
    return actuators.RelayBank({"lamp": 17, "fan": 27})


def test_devices_start_off(monkeypatch):
    bank = _bank(monkeypatch)
    assert bank.state("lamp") == "off"
    assert bank.state("fan") == "off"


def test_set_on_drives_the_pin_and_updates_state(monkeypatch):
    bank = _bank(monkeypatch)
    assert bank.set("lamp", "on") is True
    assert bank.state("lamp") == "on"
    assert FakeOutput.instances[0].value is True


def test_opto_boards_are_configured_active_low(monkeypatch):
    bank = _bank(monkeypatch)
    assert FakeOutput.instances[0].active_high is False


def test_unknown_device_is_refused(monkeypatch):
    bank = _bank(monkeypatch)
    assert bank.set("front_door_lock", "on") is False


def test_unknown_action_is_refused(monkeypatch):
    bank = _bank(monkeypatch)
    assert bank.set("lamp", "explode") is False


def test_all_off_clears_every_device(monkeypatch):
    bank = _bank(monkeypatch)
    bank.set("lamp", "on")
    bank.set("fan", "on")
    bank.all_off()
    assert bank.state("lamp") == "off"
    assert bank.state("fan") == "off"


def test_works_without_gpio_present(monkeypatch):
    monkeypatch.setattr(actuators, "GPIO_AVAILABLE", False)
    bank = actuators.RelayBank({"lamp": 17})
    assert bank.set("lamp", "on") is True
    assert bank.state("lamp") == "on"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/garuda_auto/test_actuators.py -v`
Expected: `ModuleNotFoundError: ... actuators`.

- [ ] **Step 3: Write the implementation**

```python
# basic_pipelines/garuda_auto/actuators.py
"""Relay control via gpiozero.

gpiozero only -- RPi.GPIO does not work on the Pi 5. When gpiozero is absent
(laptop, CI) the bank degrades to bookkeeping so the rest of the system can be
exercised without hardware.
"""
import logging

log = logging.getLogger(__name__)

try:
    from gpiozero import OutputDevice
    GPIO_AVAILABLE = True
except Exception:
    OutputDevice = None
    GPIO_AVAILABLE = False

# Most opto-isolated relay boards pull the input low to energise the coil.
ACTIVE_HIGH = False


class RelayBank:
    def __init__(self, pin_map, active_high=ACTIVE_HIGH):
        self.pin_map = dict(pin_map)
        self._state = {device: "off" for device in self.pin_map}
        self._outputs = {}
        if GPIO_AVAILABLE:
            for device, pin in self.pin_map.items():
                self._outputs[device] = OutputDevice(
                    pin, active_high=active_high, initial_value=False
                )
        else:
            log.warning("gpiozero unavailable -- relay bank running in no-op mode")

    def state(self, device):
        return self._state.get(device, "off")

    def set(self, device, action):
        if device not in self.pin_map:
            log.warning("refusing unknown device: %s", device)
            return False
        if action not in ("on", "off"):
            log.warning("refusing unknown action: %s", action)
            return False
        output = self._outputs.get(device)
        if output is not None:
            output.on() if action == "on" else output.off()
        self._state[device] = action
        return True

    def all_off(self):
        for device in self.pin_map:
            self.set(device, "off")

    def close(self):
        self.all_off()
        for output in self._outputs.values():
            try:
                output.close()
            except Exception:
                pass
        self._outputs.clear()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/garuda_auto/test_actuators.py -v`
Expected: 7 passed.

- [ ] **Step 5: Verify on real hardware**

```bash
python3 -c "
from basic_pipelines.garuda_auto.actuators import RelayBank
import time
bank = RelayBank({'lamp': 17, 'fan': 27})
for device in ('lamp', 'fan'):
    bank.set(device, 'on'); print(device, 'on'); time.sleep(2)
    bank.set(device, 'off'); print(device, 'off'); time.sleep(1)
bank.close()
"
```

Expected: each relay clicks audibly, the lamp lights for two seconds, the fan spins for two seconds. If a relay is inverted, flip `ACTIVE_HIGH`. If the Pi reboots when the fan starts, the loads are still on Pi power — fix the wiring, do not work around it in software.

- [ ] **Step 6: Commit**

```bash
git add basic_pipelines/garuda_auto/actuators.py tests/garuda_auto/test_actuators.py
git commit -m "feat(auto): gpiozero relay bank with no-op fallback"
```

---

### Task 6: DHT22 sensor reader

Hardware task. The DHT22 is a one-wire protocol part and gpiozero does not speak it, so this is the one deliberate exception to the gpiozero-only rule. `adafruit-circuitpython-dht` is used because it is not `RPi.GPIO` and works on Pi 5 through libgpiod. Reads fail intermittently by design of the part; the reader retries and serves the last good value rather than propagating a failure into the descriptor.

**Files:**
- Create: `basic_pipelines/garuda_auto/sensors.py`
- Test: `tests/garuda_auto/test_sensors.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ClimateSensor(pin: int = 4, retries: int = 3, fallback=(28.0, 50.0))` with `.read() -> tuple[float, float]` returning `(temperature_c, humidity_pct)`, never raising.

- [ ] **Step 1: Install the dependency**

```bash
python3 -m pip install adafruit-circuitpython-dht
```

- [ ] **Step 2: Write the failing test**

```python
# tests/garuda_auto/test_sensors.py
import basic_pipelines.garuda_auto.sensors as sensors


class FakeDevice:
    def __init__(self, readings):
        self._readings = list(readings)

    @property
    def temperature(self):
        value = self._readings[0][0]
        if value is None:
            raise RuntimeError("checksum error")
        return value

    @property
    def humidity(self):
        return self._readings.pop(0)[1]


def _sensor(monkeypatch, readings):
    monkeypatch.setattr(sensors, "DHT_AVAILABLE", True)
    s = sensors.ClimateSensor(pin=4, retries=3)
    s._device = FakeDevice(readings)
    return s


def test_returns_a_successful_reading(monkeypatch):
    s = _sensor(monkeypatch, [(29.5, 61.0)])
    assert s.read() == (29.5, 61.0)


def test_retries_past_a_transient_failure(monkeypatch):
    s = _sensor(monkeypatch, [(None, 0.0), (30.0, 55.0)])
    monkeypatch.setattr(sensors.time, "sleep", lambda _: None)
    assert s.read() == (30.0, 55.0)


def test_serves_last_good_value_when_every_retry_fails(monkeypatch):
    s = _sensor(monkeypatch, [(27.0, 45.0)])
    assert s.read() == (27.0, 45.0)
    s._device = FakeDevice([(None, 0.0)] * 5)
    monkeypatch.setattr(sensors.time, "sleep", lambda _: None)
    assert s.read() == (27.0, 45.0)


def test_serves_fallback_before_any_good_reading(monkeypatch):
    s = _sensor(monkeypatch, [(None, 0.0)] * 5)
    monkeypatch.setattr(sensors.time, "sleep", lambda _: None)
    assert s.read() == (28.0, 50.0)


def test_returns_fallback_when_library_absent(monkeypatch):
    monkeypatch.setattr(sensors, "DHT_AVAILABLE", False)
    s = sensors.ClimateSensor(pin=4)
    assert s.read() == (28.0, 50.0)


def test_rejects_physically_impossible_reading(monkeypatch):
    s = _sensor(monkeypatch, [(500.0, 61.0), (29.0, 60.0)])
    monkeypatch.setattr(sensors.time, "sleep", lambda _: None)
    assert s.read() == (29.0, 60.0)
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python -m pytest tests/garuda_auto/test_sensors.py -v`
Expected: `ModuleNotFoundError: ... sensors`.

- [ ] **Step 4: Write the implementation**

```python
# basic_pipelines/garuda_auto/sensors.py
"""DHT22 temperature and humidity.

The DHT22 fails a read fairly often -- a bad checksum is normal, not a bug.
This reader retries, sanity-checks the result, and serves the last good value
rather than letting a sensor hiccup change what the rules see.
"""
import logging
import time

log = logging.getLogger(__name__)

try:
    import adafruit_dht
    import board
    DHT_AVAILABLE = True
except Exception:
    adafruit_dht = None
    board = None
    DHT_AVAILABLE = False

_TEMP_RANGE = (-40.0, 80.0)
_HUMIDITY_RANGE = (0.0, 100.0)


class ClimateSensor:
    def __init__(self, pin=4, retries=3, fallback=(28.0, 50.0)):
        self.pin = pin
        self.retries = retries
        self.fallback = fallback
        self._last_good = None
        self._device = None
        if DHT_AVAILABLE:
            try:
                self._device = adafruit_dht.DHT22(getattr(board, f"D{pin}"))
            except Exception as exc:
                log.warning("DHT22 init failed on pin %s: %s", pin, exc)

    def read(self):
        """Return (temperature_c, humidity_pct). Never raises."""
        if self._device is not None:
            for attempt in range(self.retries):
                try:
                    temperature = self._device.temperature
                    humidity = self._device.humidity
                    if temperature is None or humidity is None:
                        raise RuntimeError("null reading")
                    if not (_TEMP_RANGE[0] <= temperature <= _TEMP_RANGE[1]):
                        raise RuntimeError(f"temperature out of range: {temperature}")
                    if not (_HUMIDITY_RANGE[0] <= humidity <= _HUMIDITY_RANGE[1]):
                        raise RuntimeError(f"humidity out of range: {humidity}")
                    self._last_good = (float(temperature), float(humidity))
                    return self._last_good
                except Exception as exc:
                    log.debug("DHT22 read %d/%d failed: %s", attempt + 1, self.retries, exc)
                    time.sleep(2.0)
        return self._last_good if self._last_good is not None else self.fallback
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/garuda_auto/test_sensors.py -v`
Expected: 6 passed.

- [ ] **Step 6: Verify on real hardware**

```bash
python3 -c "
from basic_pipelines.garuda_auto.sensors import ClimateSensor
s = ClimateSensor(pin=4)
for _ in range(5):
    print(s.read())
"
```

Expected: five plausible readings for the room. If every read fails, the DHT22 data line needs a 10 kΩ pull-up to 3V3. If reads stay unreliable after that, fall back to reading the DHT22 from the spare ESP32 over USB serial and note the change in the plan — do not ship a sensor that silently reports its fallback.

- [ ] **Step 7: Commit**

```bash
git add basic_pipelines/garuda_auto/sensors.py tests/garuda_auto/test_sensors.py
git commit -m "feat(auto): DHT22 reader with retry and last-good cache"
```

---

### Task 7: Local utterance matcher

This component decides whether the cloud is needed, so the paper's headline metric is a direct measurement of it. Two backends, because the embedding backend may not install cleanly on the Pi and the system must work either way — and comparing them is an evaluation result in its own right.

**Files:**
- Create: `basic_pipelines/garuda_auto/matcher.py`
- Test: `tests/garuda_auto/test_matcher.py`

**Interfaces:**
- Consumes: `RuleStore` from Task 2.
- Produces: `LocalMatcher(store, backend: str = "fuzzy", threshold: float = 0.72)` with `.match(utterance) -> dict | None` returning the matched rule, and `.backend_name -> str`.

- [ ] **Step 1: Install the dependency**

```bash
python3 -m pip install rapidfuzz
```

- [ ] **Step 2: Write the failing test**

```python
# tests/garuda_auto/test_matcher.py
import pytest
from basic_pipelines.garuda_auto.matcher import LocalMatcher


class FakeStore:
    def __init__(self, rules):
        self.rules = rules


def _rule(rid, utterance):
    return {
        "id": rid,
        "source_utterance": utterance,
        "when": {"all": [{"field": "occupancy", "op": "==", "value": "empty"}]},
        "then": [{"device": "fan", "action": "off"}],
        "cooldown_s": 60,
        "enabled": True,
    }


STORE = FakeStore([
    _rule("r1", "turn the fan off when the room is empty for five minutes"),
    _rule("r2", "switch on the lamp when i sit at the desk"),
])


def test_exact_repeat_matches_locally():
    m = LocalMatcher(STORE, backend="fuzzy")
    assert m.match("turn the fan off when the room is empty for five minutes")["id"] == "r1"


def test_close_paraphrase_matches_locally():
    m = LocalMatcher(STORE, backend="fuzzy", threshold=0.6)
    assert m.match("turn off the fan when the room is empty for 5 minutes")["id"] == "r1"


def test_unrelated_request_does_not_match():
    m = LocalMatcher(STORE, backend="fuzzy")
    assert m.match("what is the weather in bangalore tomorrow") is None


def test_matches_the_nearer_of_two_rules():
    m = LocalMatcher(STORE, backend="fuzzy", threshold=0.5)
    assert m.match("switch the lamp on when i sit down at my desk")["id"] == "r2"


def test_empty_store_returns_none():
    m = LocalMatcher(FakeStore([]), backend="fuzzy")
    assert m.match("anything at all") is None


def test_disabled_rules_are_not_matched():
    disabled = _rule("r3", "turn the fan off when the room is empty for five minutes")
    disabled["enabled"] = False
    m = LocalMatcher(FakeStore([disabled]), backend="fuzzy")
    assert m.match("turn the fan off when the room is empty for five minutes") is None


def test_backend_name_is_reported():
    assert LocalMatcher(STORE, backend="fuzzy").backend_name == "fuzzy"


def test_unknown_backend_is_rejected():
    with pytest.raises(ValueError):
        LocalMatcher(STORE, backend="telepathy")
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python -m pytest tests/garuda_auto/test_matcher.py -v`
Expected: `ModuleNotFoundError: ... matcher`.

- [ ] **Step 4: Write the implementation**

```python
# basic_pipelines/garuda_auto/matcher.py
"""Decide whether an utterance is already covered by a learned rule.

Every local hit is a cloud call that did not happen, which is exactly what the
evaluation measures. Two backends are provided so the comparison between them
is a result rather than an implementation detail.
"""
import logging

log = logging.getLogger(__name__)

try:
    from rapidfuzz import fuzz
    FUZZ_AVAILABLE = True
except Exception:
    fuzz = None
    FUZZ_AVAILABLE = False

_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class LocalMatcher:
    def __init__(self, store, backend="fuzzy", threshold=0.72):
        if backend not in ("fuzzy", "embed"):
            raise ValueError(f"unknown backend: {backend}")
        self.store = store
        self.backend_name = backend
        self.threshold = threshold
        self._encoder = None
        if backend == "embed":
            self._encoder = self._load_encoder()
            if self._encoder is None:
                log.warning("embedding backend unavailable -- falling back to fuzzy")
                self.backend_name = "fuzzy"

    @staticmethod
    def _load_encoder():
        try:
            from sentence_transformers import SentenceTransformer
            return SentenceTransformer(_EMBED_MODEL)
        except Exception as exc:
            log.warning("could not load %s: %s", _EMBED_MODEL, exc)
            return None

    def _score(self, utterance, candidate):
        if self.backend_name == "embed" and self._encoder is not None:
            from sentence_transformers import util
            vectors = self._encoder.encode([utterance, candidate], convert_to_tensor=True)
            return float(util.cos_sim(vectors[0], vectors[1]).item())
        if not FUZZ_AVAILABLE:
            return 1.0 if utterance.strip().lower() == candidate.strip().lower() else 0.0
        return fuzz.token_set_ratio(utterance.lower(), candidate.lower()) / 100.0

    def match(self, utterance):
        """Return the closest enabled rule above threshold, or None."""
        best, best_score = None, 0.0
        for rule in self.store.rules:
            if not rule.get("enabled", True):
                continue
            score = self._score(utterance, rule.get("source_utterance", ""))
            if score > best_score:
                best, best_score = rule, score
        return best if best_score >= self.threshold else None
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/garuda_auto/test_matcher.py -v`
Expected: 8 passed.

- [ ] **Step 6: Commit**

```bash
git add basic_pipelines/garuda_auto/matcher.py tests/garuda_auto/test_matcher.py
git commit -m "feat(auto): local utterance matcher with fuzzy and embedding backends"
```

---

### Task 8: NIM synthesis client

The egress boundary lives here. There is a test that asserts no descriptor values appear in the request body; treat a failure of that test as a design breach, not a flaky assertion.

**Files:**
- Create: `basic_pipelines/garuda_auto/nim_client.py`
- Test: `tests/garuda_auto/test_nim_client.py`

**Interfaces:**
- Consumes: `schema_for_prompt` from Task 1.
- Produces: `NimClient(api_key, model, base_url="https://integrate.api.nvidia.com/v1", timeout=20)` with `.synthesize(utterance: str, existing_rules: list[dict]) -> tuple[dict | None, str]` returning `(rule, "")` or `(None, reason)`, and `.build_request(utterance, existing_rules) -> dict`, and `.tokens_used -> int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/garuda_auto/test_nim_client.py
import json
from basic_pipelines.garuda_auto.nim_client import NimClient

RULE_JSON = json.dumps({
    "source_utterance": "turn the fan off when the room is empty for five minutes",
    "when": {"all": [
        {"field": "occupancy", "op": "==", "value": "empty"},
        {"field": "occupancy_duration_s", "op": ">=", "value": 300},
    ]},
    "then": [{"device": "fan", "action": "off"}],
    "cooldown_s": 60,
})


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _completion(content, tokens=120):
    return {"choices": [{"message": {"content": content}}], "usage": {"total_tokens": tokens}}


def test_request_carries_the_schema_but_no_readings():
    client = NimClient("key", "some-model")
    body = json.dumps(client.build_request("turn the fan off when empty", []))
    assert "occupancy_duration_s" in body
    for leak in ("occupied", "ambient_luma\": 1", "temperature_c\": 3", "person_count\": 1"):
        assert leak not in body


def test_successful_synthesis_returns_a_rule(monkeypatch):
    import basic_pipelines.garuda_auto.nim_client as mod
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: FakeResponse(_completion(RULE_JSON)))
    client = NimClient("key", "some-model")
    rule, reason = client.synthesize("turn the fan off when the room is empty for five minutes", [])
    assert reason == ""
    assert rule["then"] == [{"device": "fan", "action": "off"}]
    assert client.tokens_used == 120


def test_fenced_json_is_unwrapped(monkeypatch):
    import basic_pipelines.garuda_auto.nim_client as mod
    fenced = f"```json\n{RULE_JSON}\n```"
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: FakeResponse(_completion(fenced)))
    rule, reason = NimClient("key", "m").synthesize("x", [])
    assert reason == ""
    assert rule is not None


def test_unparseable_response_is_reported_not_raised(monkeypatch):
    import basic_pipelines.garuda_auto.nim_client as mod
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: FakeResponse(_completion("I cannot help with that")))
    rule, reason = NimClient("key", "m").synthesize("x", [])
    assert rule is None
    assert "parse" in reason.lower()


def test_invalid_rule_is_rejected_by_the_validator(monkeypatch):
    import basic_pipelines.garuda_auto.nim_client as mod
    bad = json.dumps({
        "source_utterance": "unlock the door when i get home",
        "when": {"all": [{"field": "occupancy", "op": "==", "value": "occupied"}]},
        "then": [{"device": "front_door_lock", "action": "unlock"}],
    })
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: FakeResponse(_completion(bad)))
    rule, reason = NimClient("key", "m").synthesize("unlock the door when i get home", [])
    assert rule is None
    assert "front_door_lock" in reason


def test_network_failure_is_reported_not_raised(monkeypatch):
    import basic_pipelines.garuda_auto.nim_client as mod

    def boom(*a, **k):
        raise mod.requests.exceptions.ConnectionError("no route to host")

    monkeypatch.setattr(mod.requests, "post", boom)
    rule, reason = NimClient("key", "m").synthesize("x", [])
    assert rule is None
    assert reason


def test_missing_api_key_short_circuits():
    rule, reason = NimClient("", "m").synthesize("x", [])
    assert rule is None
    assert "key" in reason.lower()


def test_source_utterance_is_forced_to_the_user_text(monkeypatch):
    import basic_pipelines.garuda_auto.nim_client as mod
    drifted = json.loads(RULE_JSON)
    drifted["source_utterance"] = "something the model made up"
    monkeypatch.setattr(mod.requests, "post",
                        lambda *a, **k: FakeResponse(_completion(json.dumps(drifted))))
    rule, _ = NimClient("key", "m").synthesize("the exact words i said", [])
    assert rule["source_utterance"] == "the exact words i said"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/garuda_auto/test_nim_client.py -v`
Expected: `ModuleNotFoundError: ... nim_client`.

- [ ] **Step 3: Write the implementation**

```python
# basic_pipelines/garuda_auto/nim_client.py
"""Compile an utterance into a rule using NVIDIA NIM.

The egress boundary is enforced here. What leaves the device: the user's
transcribed words, the descriptor SCHEMA, the device list, and the utterances
of existing rules. What never leaves: frames, crops, keypoints, audio, and any
current reading of any descriptor field. A compiler does not need to know what
the room looks like right now, so it is not told.
"""
import json
import logging
import re

import requests

from .rule_schema import schema_for_prompt
from .validator import validate_rule

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"

_SYSTEM_PROMPT = """You compile spoken home-automation instructions into JSON rules.

Return exactly one JSON object and nothing else. No prose, no code fence.

Shape:
{"source_utterance": str,
 "when": {"all": [ {"field": str, "op": str, "value": str|number}, ... ]},
 "then": [ {"device": str, "action": str}, ... ],
 "cooldown_s": number}

Use "any" instead of "all" when the user means one condition is enough.
Only use the fields, operators, devices and actions given in the schema.
Never invent a field or a device. If the instruction cannot be expressed with
the given schema, return {"error": "<short reason>"} instead.
Durations are in seconds. "five minutes" is 300.
"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class NimClient:
    def __init__(self, api_key, model, base_url=DEFAULT_BASE_URL, timeout=20):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.tokens_used = 0

    def build_request(self, utterance, existing_rules):
        """Assemble the request body. Schema only -- never live values."""
        known = [r.get("source_utterance", "") for r in existing_rules][:32]
        user_content = json.dumps({
            "schema": schema_for_prompt(),
            "already_known": known,
            "instruction": utterance,
        })
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.1,
            "max_tokens": 512,
        }

    @staticmethod
    def _extract_json(content):
        content = content.strip()
        if content.startswith("```"):
            content = content.strip("`")
            content = content[content.find("\n") + 1:] if "\n" in content else content
            if content.lstrip().startswith("json"):
                content = content.lstrip()[4:]
        match = _JSON_RE.search(content)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except ValueError:
            return None

    def synthesize(self, utterance, existing_rules):
        """Return (rule, "") on success or (None, reason) on any failure."""
        if not self.api_key:
            return None, "no NIM API key configured"
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                json=self.build_request(utterance, existing_rules),
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            log.warning("NIM request failed: %s", exc)
            return None, f"could not reach the rule service: {type(exc).__name__}"

        self.tokens_used += int(payload.get("usage", {}).get("total_tokens", 0) or 0)
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None, "malformed response from the rule service"

        parsed = self._extract_json(content)
        if parsed is None:
            return None, "could not parse a rule from the response"
        if "error" in parsed:
            return None, str(parsed["error"])

        # The model does not get to decide what the user said.
        parsed["source_utterance"] = utterance

        ok, reason = validate_rule(parsed)
        if not ok:
            return None, reason
        return parsed, ""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/garuda_auto/test_nim_client.py -v`
Expected: 8 passed.

- [ ] **Step 5: Verify against the live endpoint**

Get a key from build.nvidia.com and confirm the model id is current — the catalogue changes, so do not trust a hardcoded name.

```bash
NIM_API_KEY=nvapi-xxx python3 -c "
import os
from basic_pipelines.garuda_auto.nim_client import NimClient
c = NimClient(os.environ['NIM_API_KEY'], 'meta/llama-3.3-70b-instruct')
print(c.synthesize('turn the fan off when the room has been empty for five minutes', []))
print('tokens:', c.tokens_used)
"
```

Expected: a valid rule dict and an empty reason. If the model id 404s, list the catalogue at build.nvidia.com and pick a current instruct model.

- [ ] **Step 6: Commit**

```bash
git add basic_pipelines/garuda_auto/nim_client.py tests/garuda_auto/test_nim_client.py
git commit -m "feat(auto): NIM rule synthesis client with schema-only egress"
```

---

### Task 9: Speech output with a half-duplex mic gate

Without this the system hears itself. The existing replies say the wake word aloud ([Garuda_web.py:1786](../../../basic_pipelines/Garuda_web.py), `:1790`), so an ungated loop retriggers on its own voice.

**Files:**
- Create: `basic_pipelines/garuda_auto/audio_out.py`
- Test: `tests/garuda_auto/test_audio_out.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Speaker(wake_word: str = "narada")` with `.say(text) -> bool`, `.is_speaking -> bool`, `.strip_wake_word(text) -> str`, and a context manager `.muted_mic()` that the capture loop checks.

- [ ] **Step 1: Install the dependency**

```bash
sudo apt-get install -y espeak-ng
python3 -m pip install pyttsx3
```

- [ ] **Step 2: Write the failing test**

```python
# tests/garuda_auto/test_audio_out.py
import threading
import basic_pipelines.garuda_auto.audio_out as audio_out


class FakeEngine:
    def __init__(self):
        self.spoken = []

    def say(self, text):
        self.spoken.append(text)

    def runAndWait(self):
        pass

    def setProperty(self, *_):
        pass


def _speaker(monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(audio_out, "TTS_AVAILABLE", True)
    s = audio_out.Speaker(wake_word="narada")
    s._engine = engine
    return s, engine


def test_wake_word_is_stripped_before_speaking(monkeypatch):
    s, engine = _speaker(monkeypatch)
    s.say("Hello, I am Narada, your assistant.")
    assert "narada" not in engine.spoken[0].lower()


def test_wake_word_stripping_is_case_insensitive(monkeypatch):
    s, _ = _speaker(monkeypatch)
    assert "narada" not in s.strip_wake_word("NARADA reporting").lower()


def test_is_speaking_is_false_when_idle(monkeypatch):
    s, _ = _speaker(monkeypatch)
    assert s.is_speaking is False


def test_is_speaking_is_true_during_playback(monkeypatch):
    s, engine = _speaker(monkeypatch)
    observed = []

    def watching_run():
        observed.append(s.is_speaking)

    engine.runAndWait = watching_run
    s.say("the room is warm")
    assert observed == [True]
    assert s.is_speaking is False


def test_flag_clears_even_when_playback_raises(monkeypatch):
    s, engine = _speaker(monkeypatch)

    def boom():
        raise RuntimeError("audio device busy")

    engine.runAndWait = boom
    assert s.say("anything") is False
    assert s.is_speaking is False


def test_muted_mic_context_reports_speaking(monkeypatch):
    s, _ = _speaker(monkeypatch)
    with s.muted_mic():
        assert s.is_speaking is True
    assert s.is_speaking is False


def test_returns_false_without_tts_backend(monkeypatch):
    monkeypatch.setattr(audio_out, "TTS_AVAILABLE", False)
    s = audio_out.Speaker()
    assert s.say("hello") is False


def test_concurrent_speech_is_serialised(monkeypatch):
    s, engine = _speaker(monkeypatch)
    order = []
    engine.runAndWait = lambda: order.append(len(order))
    threads = [threading.Thread(target=s.say, args=(f"line {i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(order) == 4
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python -m pytest tests/garuda_auto/test_audio_out.py -v`
Expected: `ModuleNotFoundError: ... audio_out`.

- [ ] **Step 4: Write the implementation**

```python
# basic_pipelines/garuda_auto/audio_out.py
"""Speech output with a half-duplex gate.

The USB microphone has no echo cancellation, so the capture loop must stop
listening while the speaker is talking. Spoken text also has the wake word
removed, because saying "Narada" out loud into your own microphone starts an
endless conversation with yourself.
"""
import contextlib
import logging
import re
import threading

log = logging.getLogger(__name__)

try:
    import pyttsx3
    TTS_AVAILABLE = True
except Exception:
    pyttsx3 = None
    TTS_AVAILABLE = False


class Speaker:
    def __init__(self, wake_word="narada", rate=165):
        self.wake_word = wake_word
        self._speaking = threading.Event()
        self._lock = threading.Lock()
        self._engine = None
        if TTS_AVAILABLE:
            try:
                self._engine = pyttsx3.init()
                self._engine.setProperty("rate", rate)
            except Exception as exc:
                log.warning("TTS init failed: %s", exc)

    @property
    def is_speaking(self):
        return self._speaking.is_set()

    def strip_wake_word(self, text):
        cleaned = re.sub(re.escape(self.wake_word), "", text, flags=re.IGNORECASE)
        return re.sub(r"\s{2,}", " ", cleaned).strip(" ,.")

    @contextlib.contextmanager
    def muted_mic(self):
        """Hold the mic gate shut. The capture loop must skip while set."""
        self._speaking.set()
        try:
            yield
        finally:
            self._speaking.clear()

    def say(self, text):
        if not TTS_AVAILABLE or self._engine is None:
            log.info("TTS unavailable, would have said: %s", text)
            return False
        spoken = self.strip_wake_word(text)
        if not spoken:
            return False
        with self._lock:
            with self.muted_mic():
                try:
                    self._engine.say(spoken)
                    self._engine.runAndWait()
                except Exception as exc:
                    log.warning("TTS playback failed: %s", exc)
                    return False
        return True
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/garuda_auto/test_audio_out.py -v`
Expected: 8 passed.

- [ ] **Step 6: Verify on real hardware**

```bash
python3 -c "
from basic_pipelines.garuda_auto.audio_out import Speaker
s = Speaker()
print('spoke:', s.say('Narada here. The fan is now off.'))
"
```

Expected: the USB speaker says "here. The fan is now off." without the wake word. If nothing plays, check `aplay -l` lists the USB device and set it as the default in `~/.asoundrc`.

- [ ] **Step 7: Commit**

```bash
git add basic_pipelines/garuda_auto/audio_out.py tests/garuda_auto/test_audio_out.py
git commit -m "feat(auto): TTS output with half-duplex mic gate and wake-word stripping"
```

---

### Task 10: Reflex loop

Ties descriptor, engine and relays into one 2 Hz thread. Actuation does not need frame rate; 2 Hz keeps CPU away from the detection pipeline.

**Files:**
- Create: `basic_pipelines/garuda_auto/reflex_loop.py`
- Test: `tests/garuda_auto/test_reflex_loop.py`

**Interfaces:**
- Consumes: `SceneBuilder` (Task 3), `RuleEngine` (Task 4), `RelayBank` (Task 5), `ClimateSensor` (Task 6).
- Produces: `ReflexLoop(builder, engine, relays, sensor, detection_source, luma_source, period_s=0.5, clock=time.time)` with `.tick() -> dict` returning `{"descriptor": dict, "actions": list}`, plus `.start(stop_event)` and `.last_descriptor`.

- [ ] **Step 1: Write the failing test**

```python
# tests/garuda_auto/test_reflex_loop.py
import threading
from basic_pipelines.garuda_auto.reflex_loop import ReflexLoop
from basic_pipelines.garuda_auto.scene_state import SceneBuilder
from basic_pipelines.garuda_auto.rule_engine import RuleEngine
from basic_pipelines.garuda_auto.actuators import RelayBank

ZONES = {"desk": (0.0, 0.0, 0.33, 1.0), "door": (0.67, 0.0, 1.0, 1.0), "center": (0.33, 0.0, 0.67, 1.0)}


class FakeStore:
    def __init__(self, rules):
        self.rules = rules


class FakeSensor:
    def read(self):
        return (31.0, 55.0)


EMPTY_FAN_OFF = {
    "id": "r1",
    "source_utterance": "turn the fan off when the room is empty",
    "when": {"all": [{"field": "occupancy", "op": "==", "value": "empty"}]},
    "then": [{"device": "fan", "action": "off"}],
    "cooldown_s": 0,
    "enabled": True,
}

OCCUPIED_FAN_ON = {
    "id": "r2",
    "source_utterance": "turn the fan on when someone is in the room and it is warm",
    "when": {"all": [
        {"field": "occupancy", "op": "==", "value": "occupied"},
        {"field": "temperature_c", "op": ">", "value": 29},
    ]},
    "then": [{"device": "fan", "action": "on"}],
    "cooldown_s": 0,
    "enabled": True,
}


def _loop(rules, detections):
    builder = SceneBuilder(ZONES, clock=lambda: 1000.0)
    engine = RuleEngine(FakeStore(rules), clock=lambda: 1000.0)
    relays = RelayBank({"lamp": 17, "fan": 27})
    return ReflexLoop(builder, engine, relays, FakeSensor(),
                      detection_source=lambda: detections,
                      luma_source=lambda: 30,
                      clock=lambda: 1000.0), relays


def test_tick_returns_descriptor_and_actions():
    loop, _ = _loop([EMPTY_FAN_OFF], [])
    result = loop.tick()
    assert result["descriptor"]["occupancy"] == "empty"
    assert result["actions"] == [{"device": "fan", "action": "off", "rule_id": "r1"}]


def test_tick_drives_the_relay():
    person = [{"label": "person", "confidence": 0.9, "bbox": (0.4, 0.2, 0.6, 0.9), "posture": "standing"}]
    loop, relays = _loop([OCCUPIED_FAN_ON], person)
    loop.tick()
    assert relays.state("fan") == "on"


def test_device_state_feeds_back_into_the_next_descriptor():
    person = [{"label": "person", "confidence": 0.9, "bbox": (0.4, 0.2, 0.6, 0.9), "posture": "standing"}]
    loop, _ = _loop([OCCUPIED_FAN_ON], person)
    loop.tick()
    assert loop.tick()["descriptor"]["fan_state"] == "on"


def test_a_failing_detection_source_does_not_kill_the_loop():
    def boom():
        raise RuntimeError("pipeline stalled")

    builder = SceneBuilder(ZONES, clock=lambda: 1000.0)
    engine = RuleEngine(FakeStore([EMPTY_FAN_OFF]), clock=lambda: 1000.0)
    loop = ReflexLoop(builder, engine, RelayBank({"fan": 27}), FakeSensor(),
                      detection_source=boom, luma_source=lambda: 30, clock=lambda: 1000.0)
    result = loop.tick()
    assert result["descriptor"]["occupancy"] == "empty"


def test_start_runs_until_the_stop_event_is_set():
    loop, _ = _loop([EMPTY_FAN_OFF], [])
    stop = threading.Event()
    thread = threading.Thread(target=loop.start, args=(stop,), daemon=True)
    thread.start()
    stop.set()
    thread.join(timeout=3)
    assert not thread.is_alive()


def test_last_descriptor_is_exposed_after_a_tick():
    loop, _ = _loop([EMPTY_FAN_OFF], [])
    loop.tick()
    assert loop.last_descriptor["occupancy"] == "empty"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/garuda_auto/test_reflex_loop.py -v`
Expected: `ModuleNotFoundError: ... reflex_loop`.

- [ ] **Step 3: Write the implementation**

```python
# basic_pipelines/garuda_auto/reflex_loop.py
"""The local control loop. Runs at 2 Hz, never touches the network.

Actuation does not need frame rate, and the detection pipeline needs the CPU
more than this does. Any failure inside a tick is logged and swallowed: a
stalled camera must not leave the house unable to switch off a fan.
"""
import logging
import time

log = logging.getLogger(__name__)


class ReflexLoop:
    def __init__(self, builder, engine, relays, sensor, detection_source,
                 luma_source, period_s=0.5, clock=time.time):
        self.builder = builder
        self.engine = engine
        self.relays = relays
        self.sensor = sensor
        self.detection_source = detection_source
        self.luma_source = luma_source
        self.period_s = period_s
        self._clock = clock
        self.last_descriptor = {}

    def tick(self):
        try:
            detections = self.detection_source() or []
        except Exception as exc:
            log.warning("detection source failed: %s", exc)
            detections = []
        try:
            luma = self.luma_source()
        except Exception as exc:
            log.warning("luma source failed: %s", exc)
            luma = 0
        temperature, humidity = self.sensor.read()

        for device in ("lamp", "fan"):
            self.builder.set_device_state(device, self.relays.state(device))

        descriptor = self.builder.update(
            detections, luma=luma, temperature_c=temperature,
            humidity_pct=humidity, hour=time.localtime(self._clock()).tm_hour,
        )
        self.last_descriptor = descriptor

        actions = self.engine.evaluate(descriptor)
        for action in actions:
            self.relays.set(action["device"], action["action"])
            log.info("rule %s set %s %s", action["rule_id"], action["device"], action["action"])
        return {"descriptor": descriptor, "actions": actions}

    def start(self, stop_event):
        log.info("reflex loop started at %.1f Hz", 1.0 / self.period_s)
        while not stop_event.is_set():
            try:
                self.tick()
            except Exception as exc:
                log.exception("reflex tick failed: %s", exc)
            stop_event.wait(self.period_s)
        log.info("reflex loop stopped")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/garuda_auto/test_reflex_loop.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add basic_pipelines/garuda_auto/reflex_loop.py tests/garuda_auto/test_reflex_loop.py
git commit -m "feat(auto): 2 Hz reflex loop tying descriptor to engine to relays"
```

---

### Task 11: Wire into Garuda_web.py

Integration only. Keep `Garuda_web.py` edits to the smallest surface that works.

**Files:**
- Modify: `basic_pipelines/Garuda_web.py` — config block near line 227, `voice_assistant_loop` at line 1961, and a startup hook
- Test: `tests/garuda_auto/test_voice_integration.py`

**Interfaces:**
- Consumes: everything from Tasks 1–10.
- Produces: `handle_utterance(text, matcher, store, nim, speaker) -> str` in `basic_pipelines/garuda_auto/dispatch.py`, plus `NIM_API_KEY`, `NIM_MODEL`, `RULES_PATH`, `RELAY_PINS`, `ZONES` config in `Garuda_web.py`. `handle_utterance` returns the sentence to speak.

The dispatch order is: existing built-in command table first (unchanged behaviour), then local rule matcher, then cloud synthesis. Only the third path costs a network call, and that ordering is what the evaluation measures.

- [ ] **Step 1: Write the failing test**

```python
# tests/garuda_auto/test_voice_integration.py
from basic_pipelines.garuda_auto.dispatch import handle_utterance


class FakeMatcher:
    def __init__(self, result=None):
        self.result = result
        self.calls = 0

    def match(self, utterance):
        self.calls += 1
        return self.result


class FakeStore:
    def __init__(self):
        self.rules = []
        self.added = []
        self.conflict = None

    def find_conflict(self, rule):
        return self.conflict

    def add(self, rule):
        self.added.append(rule)
        self.rules.append(rule)
        return True, ""


class FakeNim:
    def __init__(self, rule=None, reason=""):
        self.rule = rule
        self.reason = reason
        self.calls = 0

    def synthesize(self, utterance, existing):
        self.calls += 1
        return self.rule, self.reason


class FakeSpeaker:
    def __init__(self):
        self.said = []

    def say(self, text):
        self.said.append(text)
        return True


RULE = {
    "id": "r1",
    "source_utterance": "turn the fan off when the room is empty",
    "when": {"all": [{"field": "occupancy", "op": "==", "value": "empty"}]},
    "then": [{"device": "fan", "action": "off"}],
    "cooldown_s": 60,
    "enabled": True,
}


def test_local_match_never_calls_the_cloud():
    matcher, nim, store, speaker = FakeMatcher(RULE), FakeNim(), FakeStore(), FakeSpeaker()
    reply = handle_utterance("turn the fan off when the room is empty", matcher, store, nim, speaker)
    assert nim.calls == 0
    assert "already" in reply.lower()


def test_unmatched_utterance_is_synthesised_and_stored():
    matcher, nim, store, speaker = FakeMatcher(None), FakeNim(rule=RULE), FakeStore(), FakeSpeaker()
    reply = handle_utterance("turn the fan off when nobody is here", matcher, store, nim, speaker)
    assert nim.calls == 1
    assert store.added == [RULE]
    assert "fan" in reply.lower()


def test_conflict_is_surfaced_and_the_rule_is_not_stored():
    store = FakeStore()
    store.conflict = {"id": "r0", "source_utterance": "turn the fan on when the room is empty"}
    reply = handle_utterance("turn the fan off when nobody is here",
                             FakeMatcher(None), store, FakeNim(rule=RULE), FakeSpeaker())
    assert store.added == []
    assert "conflict" in reply.lower()


def test_synthesis_failure_is_explained_to_the_user():
    reply = handle_utterance("unlock the front door when i get home",
                             FakeMatcher(None), FakeStore(),
                             FakeNim(rule=None, reason="unknown device: 'front_door_lock'"),
                             FakeSpeaker())
    assert "front_door_lock" in reply


def test_reply_is_spoken():
    speaker = FakeSpeaker()
    handle_utterance("turn the fan off when nobody is here",
                     FakeMatcher(None), FakeStore(), FakeNim(rule=RULE), speaker)
    assert len(speaker.said) == 1


def test_blank_utterance_is_ignored_without_a_cloud_call():
    nim = FakeNim()
    reply = handle_utterance("   ", FakeMatcher(None), FakeStore(), nim, FakeSpeaker())
    assert nim.calls == 0
    assert reply
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/garuda_auto/test_voice_integration.py -v`
Expected: `ModuleNotFoundError: ... dispatch`.

- [ ] **Step 3: Write the dispatcher**

```python
# basic_pipelines/garuda_auto/dispatch.py
"""Route one utterance: local match first, cloud synthesis only as a last
resort. Every local hit is a cloud call that did not happen, which is the
result the evaluation reports.
"""
import logging

log = logging.getLogger(__name__)


def handle_utterance(text, matcher, store, nim, speaker):
    """Return the sentence spoken back to the user."""
    utterance = (text or "").strip()
    if not utterance:
        reply = "I did not catch that."
        speaker.say(reply)
        return reply

    existing = matcher.match(utterance)
    if existing is not None:
        reply = f"I already know that one. It is set as: {existing['source_utterance']}."
        speaker.say(reply)
        return reply

    rule, reason = nim.synthesize(utterance, store.rules)
    if rule is None:
        reply = f"I could not turn that into a rule: {reason}"
        speaker.say(reply)
        return reply

    clash = store.find_conflict(rule)
    if clash is not None:
        reply = (f"That conflicts with an existing rule: {clash['source_utterance']}. "
                 "Delete that one first if you want this instead.")
        speaker.say(reply)
        return reply

    ok, why = store.add(rule)
    if not ok:
        reply = f"I could not save that rule: {why}"
        speaker.say(reply)
        return reply

    action = rule["then"][0]
    reply = f"Learned it. I will turn the {action['device']} {action['action']} when that happens."
    log.info("learned rule from utterance: %s", utterance)
    speaker.say(reply)
    return reply
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/garuda_auto/test_voice_integration.py -v`
Expected: 6 passed.

- [ ] **Step 5: Add config to Garuda_web.py**

Insert after the `NARADA_WAKE_WORD` line at `basic_pipelines/Garuda_web.py:227`:

```python
# --- Narada-RS: spoken rule synthesis -------------------------------------
NIM_API_KEY   = os.getenv("NIM_API_KEY", "")
NIM_MODEL     = os.getenv("NIM_MODEL", "meta/llama-3.3-70b-instruct")
NIM_BASE_URL  = os.getenv("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
RULES_PATH    = os.getenv("GARUDA_RULES_PATH", "system_logs/rules.json")
MATCHER_BACKEND = os.getenv("GARUDA_MATCHER", "fuzzy")   # fuzzy | embed
RELAY_PINS    = {"lamp": 17, "fan": 27}
ZONES         = {"desk": (0.0, 0.0, 0.33, 1.0),
                 "center": (0.33, 0.0, 0.67, 1.0),
                 "door": (0.67, 0.0, 1.0, 1.0)}
```

- [ ] **Step 6: Wire the voice loop**

In `voice_assistant_loop` (`basic_pipelines/Garuda_web.py:1961`), replace the escalation block at lines 2002–2008:

```python
        # Rule-based table first; only an UNMATCHED text transcript escalates
        # to the external LLM (text only — audio stays on-device).
        response = match_rule_based_command(user_input_lower)
        if response is None:
            response = handle_utterance(
                user_input_lower, _rs_matcher, _rs_store, _rs_nim, _rs_speaker
            )
```

Add the mic gate immediately before `recognizer.listen` at line 1981:

```python
            if _rs_speaker.is_speaking:
                time.sleep(0.2)
                continue
```

Add the module-level singletons and the reflex thread near the other startup wiring:

```python
from basic_pipelines.garuda_auto.rule_store import RuleStore
from basic_pipelines.garuda_auto.matcher import LocalMatcher
from basic_pipelines.garuda_auto.nim_client import NimClient
from basic_pipelines.garuda_auto.audio_out import Speaker
from basic_pipelines.garuda_auto.actuators import RelayBank
from basic_pipelines.garuda_auto.sensors import ClimateSensor
from basic_pipelines.garuda_auto.scene_state import SceneBuilder
from basic_pipelines.garuda_auto.rule_engine import RuleEngine
from basic_pipelines.garuda_auto.reflex_loop import ReflexLoop
from basic_pipelines.garuda_auto.dispatch import handle_utterance

_rs_store   = RuleStore(RULES_PATH)
_rs_matcher = LocalMatcher(_rs_store, backend=MATCHER_BACKEND)
_rs_nim     = NimClient(NIM_API_KEY, NIM_MODEL, base_url=NIM_BASE_URL)
_rs_speaker = Speaker(wake_word=NARADA_WAKE_WORD)
_rs_relays  = RelayBank(RELAY_PINS)
_rs_reflex  = ReflexLoop(
    SceneBuilder(ZONES), RuleEngine(_rs_store), _rs_relays, ClimateSensor(pin=4),
    detection_source=lambda: _latest_detections, luma_source=lambda: _latest_luma,
)
threading.Thread(target=_rs_reflex.start, args=(_rs_stop_event,), daemon=True).start()
```

`_latest_detections` and `_latest_luma` are set by the existing GStreamer app callback. In that callback, after the detections are parsed, add:

```python
        _latest_detections = [
            {"label": d.get_label(), "confidence": d.get_confidence(),
             "bbox": (bbox.xmin(), bbox.ymin(), bbox.xmax(), bbox.ymax()),
             "posture": "none"}
            for d in detections
        ]
        _latest_luma = int(frame.mean()) if frame is not None else 0
```

`posture` stays `"none"` until pose estimation is merged into this pipeline. Rules that depend on posture will simply not fire, which the evaluation reports as a known subset rather than hiding.

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest tests/ -v`
Expected: every pre-existing test still passes, plus the new ones. If an existing test breaks, the integration is wrong — fix it rather than editing the old test.

- [ ] **Step 8: Verify end to end on the Pi**

```bash
NIM_API_KEY=nvapi-xxx python3 basic_pipelines/Garuda_web.py
```

Say: "Narada, turn the lamp on when someone is at the desk." Expect a spoken confirmation, a new entry in `system_logs/rules.json`, and the lamp switching when you sit in the desk zone. Then say the same thing rephrased and confirm no second NIM call appears in the logs.

- [ ] **Step 9: Commit**

```bash
git add basic_pipelines/garuda_auto/dispatch.py basic_pipelines/Garuda_web.py tests/garuda_auto/test_voice_integration.py
git commit -m "feat(auto): wire rule synthesis and reflex loop into Garuda_web"
```

---

### Task 12: Evaluation harness

**Files:**
- Create: `evaluation/__init__.py` (empty — `evaluation/` is not currently a package, and `run_eval.py` imports through it)
- Create: `evaluation/narada_rs/__init__.py`
- Create: `evaluation/narada_rs/corpus.py`
- Create: `evaluation/narada_rs/run_eval.py`
- Test: `tests/garuda_auto/test_corpus.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `CORPUS: list[dict]` with keys `id`, `utterance`, `paraphrases` (3 each), `expected_devices`, `expected_fields`; and `run_eval.py` writing `evaluation/out/narada_rs_<timestamp>/summary.json`.

Write all 120 utterances before running anything. Writing paraphrases after seeing results is how a suppression number stops meaning anything.

- [ ] **Step 1: Write the corpus test**

```python
# tests/garuda_auto/test_corpus.py
from evaluation.narada_rs.corpus import CORPUS
from basic_pipelines.garuda_auto.rule_schema import FIELDS, DEVICES


def test_corpus_has_thirty_entries():
    assert len(CORPUS) == 30


def test_every_entry_has_three_paraphrases():
    for entry in CORPUS:
        assert len(entry["paraphrases"]) == 3, entry["id"]


def test_ids_are_unique():
    assert len({e["id"] for e in CORPUS}) == 30


def test_expected_fields_are_all_in_the_schema():
    for entry in CORPUS:
        for field in entry["expected_fields"]:
            assert field in FIELDS, f"{entry['id']} references unknown field {field}"


def test_expected_devices_are_all_real():
    for entry in CORPUS:
        for device in entry["expected_devices"]:
            assert device in DEVICES, f"{entry['id']} references unknown device {device}"


def test_every_schema_field_is_exercised_somewhere():
    covered = {f for e in CORPUS for f in e["expected_fields"]}
    uncovered = set(FIELDS) - covered
    assert not uncovered, f"corpus never exercises: {sorted(uncovered)}"


def test_no_paraphrase_is_identical_to_its_source():
    for entry in CORPUS:
        assert entry["utterance"] not in entry["paraphrases"], entry["id"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/garuda_auto/test_corpus.py -v`
Expected: `ModuleNotFoundError: evaluation.narada_rs.corpus`.

- [ ] **Step 3: Write the corpus**

Thirty entries in this shape. The first three are written out; write the remaining 27 covering every field in `FIELDS` — the `test_every_schema_field_is_exercised_somewhere` test fails until they do.

```python
# evaluation/narada_rs/corpus.py
"""Scripted rule-request corpus.

Written in full before any result is seen. Paraphrases exist to measure
whether learning one phrasing suppresses future cloud calls for the same
intent, so revising them after seeing scores would invalidate the number.
"""

CORPUS = [
    {
        "id": "c01",
        "utterance": "turn the fan off when the room has been empty for five minutes",
        "paraphrases": [
            "switch the fan off if nobody has been here for five minutes",
            "kill the fan after the room is empty for 5 minutes",
            "when no one is in the room for five minutes stop the fan",
        ],
        "expected_devices": ["fan"],
        "expected_fields": ["occupancy", "occupancy_duration_s"],
    },
    {
        "id": "c02",
        "utterance": "turn the lamp on when someone sits at the desk",
        "paraphrases": [
            "switch on the lamp if a person is seated at the desk",
            "light up the desk lamp when i sit down there",
            "when somebody is seated in the desk area turn the lamp on",
        ],
        "expected_devices": ["lamp"],
        "expected_fields": ["zone", "posture"],
    },
    {
        "id": "c03",
        "utterance": "turn the fan on when someone is in the room and it is warmer than thirty degrees",
        "paraphrases": [
            "start the fan if a person is here and the temperature is above 30",
            "when the room is occupied and it is over thirty degrees switch the fan on",
            "fan on if it is hotter than 30 and somebody is in the room",
        ],
        "expected_devices": ["fan"],
        "expected_fields": ["occupancy", "temperature_c"],
    },
    # 27 more. Between them they must reference every field in FIELDS:
    # person_count, ambient_luma, humidity_pct, hour, lamp_state, fan_state.
]
```

- [ ] **Step 4: Run the corpus tests to verify they pass**

Run: `python -m pytest tests/garuda_auto/test_corpus.py -v`
Expected: 7 passed.

- [ ] **Step 5: Write the harness**

```python
# evaluation/narada_rs/run_eval.py
"""Measure synthesis success and paraphrase suppression.

Metric 1 -- synthesis success: of 30 requests, how many produce a rule that is
valid and references the expected devices and fields.

Metric 2 -- paraphrase suppression: after learning from the source phrasing,
how many of the 3 paraphrases match locally instead of escalating. This is the
headline number, so it is reported per matcher backend.

Run with GARUDA_EVAL_OFFLINE=1 to exercise the harness without spending
credits; that mode reports synthesis as skipped, never as passing.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from basic_pipelines.garuda_auto.rule_store import RuleStore
from basic_pipelines.garuda_auto.matcher import LocalMatcher
from basic_pipelines.garuda_auto.nim_client import NimClient
from evaluation.narada_rs.corpus import CORPUS

BACKENDS = ("fuzzy", "embed")


def run_backend(backend, outdir, offline):
    store_path = os.path.join(outdir, f"rules_{backend}.json")
    store = RuleStore(store_path)
    matcher = LocalMatcher(store, backend=backend)
    nim = NimClient(os.getenv("NIM_API_KEY", ""), os.getenv("NIM_MODEL", "meta/llama-3.3-70b-instruct"))

    rows, synth_ok, suppressed, total_paraphrases = [], 0, 0, 0
    for entry in CORPUS:
        row = {"id": entry["id"], "utterance": entry["utterance"]}

        if offline:
            rule, reason = None, "offline mode"
            row["synthesis"] = "skipped"
        else:
            started = time.time()
            rule, reason = nim.synthesize(entry["utterance"], store.rules)
            row["synthesis_latency_s"] = round(time.time() - started, 3)
            if rule is None:
                row["synthesis"] = "rejected"
                row["reason"] = reason
            else:
                fields = {c["field"] for c in next(iter(rule["when"].values()))}
                devices = {a["device"] for a in rule["then"]}
                correct = (devices == set(entry["expected_devices"])
                           and fields >= set(entry["expected_fields"]))
                row["synthesis"] = "correct" if correct else "valid_but_wrong"
                row["fields"] = sorted(fields)
                row["devices"] = sorted(devices)
                synth_ok += int(correct)
                store.add(rule)

        hits = []
        for paraphrase in entry["paraphrases"]:
            total_paraphrases += 1
            hit = matcher.match(paraphrase) is not None
            suppressed += int(hit)
            hits.append({"text": paraphrase, "matched_locally": hit})
        row["paraphrases"] = hits
        rows.append(row)

    return {
        "backend": matcher.backend_name,
        "offline": offline,
        "synthesis_correct": synth_ok,
        "synthesis_total": len(CORPUS),
        "paraphrases_suppressed": suppressed,
        "paraphrases_total": total_paraphrases,
        "suppression_rate": round(suppressed / total_paraphrases, 4) if total_paraphrases else 0.0,
        "tokens_used": nim.tokens_used,
        "rows": rows,
    }


def main():
    offline = os.getenv("GARUDA_EVAL_OFFLINE") == "1"
    outdir = os.path.join("evaluation", "out", f"narada_rs_{time.strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(outdir, exist_ok=True)

    summary = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "backends": []}
    for backend in BACKENDS:
        result = run_backend(backend, outdir, offline)
        summary["backends"].append(result)
        print(f"{result['backend']}: synthesis {result['synthesis_correct']}/{result['synthesis_total']}, "
              f"suppression {result['suppression_rate']:.1%}, tokens {result['tokens_used']}")

    with open(os.path.join(outdir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    print(f"wrote {outdir}/summary.json")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Dry-run the harness offline**

```bash
GARUDA_EVAL_OFFLINE=1 python3 evaluation/narada_rs/run_eval.py
```

Expected: two backend lines, synthesis reported as skipped, a `summary.json` written. Suppression will be near zero offline because no rules were learned — that is correct, not a bug.

- [ ] **Step 7: Run the real evaluation**

```bash
NIM_API_KEY=nvapi-xxx python3 evaluation/narada_rs/run_eval.py
```

Expected: synthesis counts, suppression rates for both backends, and a token total. Record the token total — it is the cost figure for the paper.

- [ ] **Step 8: Commit**

```bash
git add evaluation/narada_rs/ tests/garuda_auto/test_corpus.py
git commit -m "feat(eval): synthesis and paraphrase-suppression harness"
```

---

## Remaining work not in this plan

These are known gaps, listed so nobody assumes they were forgotten:

- **Posture** is hardcoded to `"none"` in Task 11 because pose estimation lives in a separate pipeline (`basic_pipelines/pose_estimation.py`). Merging it is a follow-up. Until then, corpus entries that depend on posture will fail synthesis-to-actuation and must be reported as a known subset.
- **Actuation correctness** (§8.2 metric 3 in the spec) needs a scripted 60-minute room scenario, which is a physical protocol rather than code. Write it as a timed script of movements with expected device states, run it, and score by hand against the event log.
- **Offline availability** (metric 5) is a manual test: disconnect the network mid-scenario and confirm every learned rule still fires.
- **Latency distributions** (metric 4) come from the reflex loop log and `synthesis_latency_s` in the harness output.
- **B2 baseline** (LLM-in-the-loop) is a throwaway script, not production code. Write it only when the paper needs the comparison number.
