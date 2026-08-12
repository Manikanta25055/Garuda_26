# Drishti Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Narada-RS automation engine driveable by user-added devices, and expose it through a Drishti API with a three-lane assistant that keeps live scene values on-device.

**Architecture:** The rule vocabulary stops being a module constant and becomes an object built from a device registry at runtime. A device-type catalogue in code constrains what a user can declare; the registry supplies the rest. A `DeviceRouter` dispatches actuation across relay and MQTT transports behind one interface, so `rule_engine.py` never learns there is more than one. A new `drishti_api.py` router carries the endpoints, keeping `Garuda_web.py` from growing.

**Tech Stack:** Python 3.11+, FastAPI, pytest, `gpiozero` (relays, Pi only), `paho-mqtt`, `requests`, `rapidfuzz`.

## Global Constraints

- `gpiozero` only for GPIO. `RPi.GPIO` does not work on the Pi 5.
- Every new module degrades gracefully when its hardware dependency is absent, so the suite runs on the Mac. `gpiozero` and `paho-mqtt` are optional imports.
- Model output is untrusted input. Nothing coerces or repairs a rule — it is legal as written or refused with a reason a user can hear.
- The egress boundary is absolute: schema, device identifiers, types, capabilities and existing source utterances may cross to NVIDIA. No frames, crops, keypoints, audio, or any current reading of any descriptor field.
- Existing tests in `tests/garuda_auto/` must pass at every commit. Where a signature change breaks them, the same commit updates them.
- Data files live in `basic_pipelines/system_logs/`, written atomically via temp file + `os.replace`.
- `MAX_RULES = 64` and `MAX_DURATION_S = 86_400` are unchanged.
- Test markers: `unit` for no-I/O tests, `integration` for tests hitting the app via test client.
- Run the suite with `python3 -m pytest tests/garuda_auto -v` from the repository root.

---

## File Structure

**Created:**

| File | Responsibility |
|---|---|
| `basic_pipelines/garuda_auto/device_types.py` | The trusted catalogue: what each device type can do |
| `basic_pipelines/garuda_auto/device_registry.py` | User-declared devices; validation against the catalogue; CRUD |
| `basic_pipelines/garuda_auto/transports.py` | `MqttBank` and `DeviceRouter` |
| `basic_pipelines/garuda_auto/actuation_log.py` | Every actuation, with the rule and conditions that caused it |
| `basic_pipelines/garuda_auto/local_lane.py` | Answers that never touch the network |
| `basic_pipelines/garuda_auto/pending_store.py` | Proposals awaiting confirmation |
| `basic_pipelines/drishti_auth.py` | Drishti's own sessions and cookie |
| `basic_pipelines/drishti_api.py` | The `/api/drishti` router |

**Modified:**

| File | Change |
|---|---|
| `garuda_auto/rule_schema.py` | `FIELDS`/`DEVICES` constants → `build_schema(registry)` |
| `garuda_auto/validator.py` | `validate_rule(rule, schema)` |
| `garuda_auto/rule_store.py` | Takes a schema; `load()` partitions orphans instead of deleting |
| `garuda_auto/scene_state.py` | Device state comes from the registry |
| `garuda_auto/nim_client.py` | Takes a schema; blocking call moved off the event loop |
| `Garuda_web.py` | Includes the Drishti router; builds registry/schema/router at startup |

---

### Task 1: Device type catalogue

**Files:**
- Create: `basic_pipelines/garuda_auto/device_types.py`
- Test: `tests/garuda_auto/test_device_types.py`

**Interfaces:**
- Consumes: nothing
- Produces: `TYPES: dict`, `TRANSPORTS: tuple`, `actions_for(type_name) -> frozenset`, `state_spec(type_name) -> dict | None`, `is_actuator(type_name) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# tests/garuda_auto/test_device_types.py
import pytest
from basic_pipelines.garuda_auto import device_types as dt

pytestmark = pytest.mark.unit


def test_light_is_an_actuator_with_on_off():
    assert dt.is_actuator("light") is True
    assert dt.actions_for("light") == frozenset({"on", "off"})


def test_temperature_sensor_has_no_actions():
    assert dt.is_actuator("sensor.temperature") is False
    assert dt.actions_for("sensor.temperature") == frozenset()


def test_temperature_sensor_declares_numeric_bounds():
    spec = dt.state_spec("sensor.temperature")
    assert spec["kind"] == "num"
    assert spec["lo"] == -10 and spec["hi"] == 60


def test_contact_sensor_declares_enum_values():
    assert dt.state_spec("sensor.contact")["values"] == ("open", "closed")


def test_unknown_type_has_no_spec():
    assert dt.state_spec("nuclear_reactor") is None
    assert dt.actions_for("nuclear_reactor") == frozenset()


def test_transports_are_relay_and_mqtt():
    assert dt.TRANSPORTS == ("relay", "mqtt")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/garuda_auto/test_device_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'basic_pipelines.garuda_auto.device_types'`

- [ ] **Step 3: Write minimal implementation**

```python
# basic_pipelines/garuda_auto/device_types.py
"""What a device of a given type is allowed to do.

This catalogue is code, not configuration. A user picks a type; the
capabilities follow from it. That indirection is the whole safety argument
for letting people add devices: a hand-declared capability the actuator
layer cannot honour would produce rules that validate and then fail at the
relay.
"""

TRANSPORTS = ("relay", "mqtt")

# state.kind: "enum" -> values, or "num" -> lo/hi inclusive bounds.
TYPES = {
    "light":  {"actions": ("on", "off"),
               "state": {"kind": "enum", "values": ("on", "off")}},
    "fan":    {"actions": ("on", "off"),
               "state": {"kind": "enum", "values": ("on", "off")}},
    "switch": {"actions": ("on", "off"),
               "state": {"kind": "enum", "values": ("on", "off")}},
    "sensor.temperature": {"actions": (),
                           "state": {"kind": "num", "lo": -10, "hi": 60}},
    "sensor.humidity":    {"actions": (),
                           "state": {"kind": "num", "lo": 0, "hi": 100}},
    "sensor.contact":     {"actions": (),
                           "state": {"kind": "enum", "values": ("open", "closed")}},
}


def actions_for(type_name):
    spec = TYPES.get(type_name)
    return frozenset(spec["actions"]) if spec else frozenset()


def state_spec(type_name):
    spec = TYPES.get(type_name)
    return dict(spec["state"]) if spec else None


def is_actuator(type_name):
    return bool(actions_for(type_name))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/garuda_auto/test_device_types.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add basic_pipelines/garuda_auto/device_types.py tests/garuda_auto/test_device_types.py
git commit -m "feat(devices): device type catalogue with derived capabilities"
```

---

### Task 2: Device registry

**Files:**
- Create: `basic_pipelines/garuda_auto/device_registry.py`
- Test: `tests/garuda_auto/test_device_registry.py`

**Interfaces:**
- Consumes: `device_types.TYPES`, `actions_for`, `state_spec`, `is_actuator`, `TRANSPORTS`
- Produces: `MAX_DEVICES = 32`, `validate_device(entry, existing_ids, relay_channels) -> (bool, str)`, and `DeviceRegistry(path, relay_channels)` with attribute `devices: list[dict]` and methods `load()`, `save()`, `add(entry) -> (bool, str)`, `delete(device_id) -> bool`, `get(device_id) -> dict | None`, `actuators() -> list[dict]`, `sensors() -> list[dict]`

- [ ] **Step 1: Write the failing test**

```python
# tests/garuda_auto/test_device_registry.py
import json
import pytest
from basic_pipelines.garuda_auto.device_registry import DeviceRegistry, validate_device

pytestmark = pytest.mark.unit

CHANNELS = (1, 2, 3, 4, 5, 6, 7)

LAMP = {"id": "lamp_desk", "name": "Desk lamp", "type": "light",
        "room": "study", "transport": {"kind": "relay", "channel": 3}}


def reg(tmp_path):
    return DeviceRegistry(str(tmp_path / "devices.json"), relay_channels=CHANNELS)


def test_accepts_a_well_formed_relay_device(tmp_path):
    ok, reason = reg(tmp_path).add(LAMP)
    assert ok is True, reason


def test_rejects_unknown_type(tmp_path):
    ok, reason = reg(tmp_path).add({**LAMP, "type": "reactor"})
    assert ok is False and "reactor" in reason


def test_rejects_channel_outside_the_configured_set(tmp_path):
    bad = {**LAMP, "transport": {"kind": "relay", "channel": 12}}
    ok, reason = reg(tmp_path).add(bad)
    assert ok is False and "channel" in reason


def test_rejects_duplicate_id(tmp_path):
    r = reg(tmp_path)
    r.add(LAMP)
    ok, reason = r.add({**LAMP, "transport": {"kind": "relay", "channel": 4}})
    assert ok is False and "already" in reason


def test_rejects_two_devices_on_one_channel(tmp_path):
    r = reg(tmp_path)
    r.add(LAMP)
    ok, reason = r.add({**LAMP, "id": "fan_desk", "type": "fan"})
    assert ok is False and "channel" in reason


def test_rejects_id_that_is_not_a_safe_identifier(tmp_path):
    ok, reason = reg(tmp_path).add({**LAMP, "id": "Desk Lamp!"})
    assert ok is False and "id" in reason


def test_mqtt_device_requires_a_topic_base(tmp_path):
    bad = {**LAMP, "transport": {"kind": "mqtt"}}
    ok, reason = reg(tmp_path).add(bad)
    assert ok is False and "topic" in reason


def test_devices_survive_a_reload(tmp_path):
    path = str(tmp_path / "devices.json")
    first = DeviceRegistry(path, relay_channels=CHANNELS)
    first.add(LAMP)
    second = DeviceRegistry(path, relay_channels=CHANNELS)
    assert [d["id"] for d in second.devices] == ["lamp_desk"]


def test_corrupt_file_starts_empty_rather_than_raising(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text("{ not json")
    assert DeviceRegistry(str(path), relay_channels=CHANNELS).devices == []


def test_actuators_and_sensors_partition_the_registry(tmp_path):
    r = reg(tmp_path)
    r.add(LAMP)
    r.add({"id": "porch", "name": "Porch door", "type": "sensor.contact",
           "room": "porch", "transport": {"kind": "mqtt", "topic_base": "drishti/porch"}})
    assert [d["id"] for d in r.actuators()] == ["lamp_desk"]
    assert [d["id"] for d in r.sensors()] == ["porch"]


def test_delete_removes_the_device(tmp_path):
    r = reg(tmp_path)
    r.add(LAMP)
    assert r.delete("lamp_desk") is True
    assert r.devices == []
    assert r.delete("lamp_desk") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/garuda_auto/test_device_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'basic_pipelines.garuda_auto.device_registry'`

- [ ] **Step 3: Write minimal implementation**

```python
# basic_pipelines/garuda_auto/device_registry.py
"""Devices the user has declared.

Entries are user-authored, so every field is checked. A device names a type
from the catalogue and a transport; its capabilities are derived, never
entered. Relay devices name a channel, not a pin -- a user who could enter a
BCM pin could drive one the Hailo HAT, camera or I2C bus is using.
"""
import json
import os
import re
import tempfile
import threading

from .device_types import TYPES, TRANSPORTS, is_actuator

MAX_DEVICES = 32
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_REQUIRED = ("id", "name", "type", "room", "transport")


def validate_device(entry, existing_ids, relay_channels):
    """Return (True, "") when the entry is safe to store, else (False, reason)."""
    if not isinstance(entry, dict):
        return False, "device is not an object"
    for key in _REQUIRED:
        if key not in entry:
            return False, f"missing required key: {key}"

    device_id = entry["id"]
    if not isinstance(device_id, str) or not _ID_RE.match(device_id):
        return False, ("id must be lowercase letters, digits and underscores, "
                       f"starting with a letter: {device_id!r}")
    if device_id in existing_ids:
        return False, f"a device with id {device_id!r} already exists"

    if entry["type"] not in TYPES:
        return False, f"unknown device type: {entry['type']!r}"

    for key in ("name", "room"):
        if not isinstance(entry[key], str) or not entry[key].strip():
            return False, f"{key} must be a non-empty string"
        if len(entry[key]) > 64:
            return False, f"{key} must be at most 64 characters"

    transport = entry["transport"]
    if not isinstance(transport, dict) or transport.get("kind") not in TRANSPORTS:
        return False, f"transport.kind must be one of {list(TRANSPORTS)}"

    if transport["kind"] == "relay":
        channel = transport.get("channel")
        if isinstance(channel, bool) or not isinstance(channel, int):
            return False, "relay transport needs an integer channel"
        if channel not in relay_channels:
            return False, f"channel {channel} is not one of {sorted(relay_channels)}"
    else:
        topic = transport.get("topic_base")
        if not isinstance(topic, str) or not topic.strip():
            return False, "mqtt transport needs a topic_base"
        if len(topic) > 128 or any(c in topic for c in "+#"):
            return False, "topic_base must be a literal topic under 128 characters"

    return True, ""


class DeviceRegistry:
    def __init__(self, path, relay_channels):
        self.path = path
        self.relay_channels = frozenset(relay_channels)
        self._lock = threading.Lock()
        self.devices = []
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            self.devices = []
            return
        if not isinstance(data, list):
            self.devices = []
            return
        kept, seen = [], set()
        for entry in data:
            ok, _ = validate_device(entry, seen, self.relay_channels)
            if ok:
                kept.append(entry)
                seen.add(entry["id"])
        self.devices = kept

    def save(self):
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.devices, fh, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def _channel_taken(self, channel):
        return any(d["transport"].get("channel") == channel
                   for d in self.devices if d["transport"]["kind"] == "relay")

    def add(self, entry):
        with self._lock:
            if len(self.devices) >= MAX_DEVICES:
                return False, f"device limit reached ({MAX_DEVICES})"
            ok, reason = validate_device(
                entry, {d["id"] for d in self.devices}, self.relay_channels)
            if not ok:
                return False, reason
            transport = entry["transport"]
            if transport["kind"] == "relay" and self._channel_taken(transport["channel"]):
                return False, f"channel {transport['channel']} is already in use"
            entry = dict(entry)
            entry.setdefault("enabled", True)
            self.devices.append(entry)
            self.save()
        return True, ""

    def delete(self, device_id):
        with self._lock:
            before = len(self.devices)
            self.devices = [d for d in self.devices if d["id"] != device_id]
            if len(self.devices) == before:
                return False
            self.save()
        return True

    def get(self, device_id):
        for device in self.devices:
            if device["id"] == device_id:
                return device
        return None

    def actuators(self):
        return [d for d in self.devices if is_actuator(d["type"])]

    def sensors(self):
        return [d for d in self.devices if not is_actuator(d["type"])]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/garuda_auto/test_device_registry.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add basic_pipelines/garuda_auto/device_registry.py tests/garuda_auto/test_device_registry.py
git commit -m "feat(devices): user device registry validated against the type catalogue"
```

---

### Task 3: Dynamic rule schema

**Files:**
- Modify: `basic_pipelines/garuda_auto/rule_schema.py`
- Test: `tests/garuda_auto/test_rule_schema.py`

**Interfaces:**
- Consumes: `DeviceRegistry.actuators()`, `DeviceRegistry.sensors()`, `device_types.state_spec`, `device_types.actions_for`
- Produces: `BASE_FIELDS: dict`, `MAX_RULES`, `MAX_DURATION_S`, `OPS`, `COOLDOWN_MIN_S`, `COOLDOWN_MAX_S`, and `build_schema(registry) -> Schema` where `Schema` has attributes `fields: dict`, `devices: dict[str, frozenset]` and method `schema_for_prompt() -> dict`

Every device contributes exactly one field, named `<id>_state`, whose kind comes from its type. One rule, no special cases. `temperature_c` and `humidity_pct` stay in `BASE_FIELDS`: the built-in DHT22 is part of the platform, not a user-added device, and the 30-entry evaluation corpus references those names.

- [ ] **Step 1: Write the failing test**

```python
# tests/garuda_auto/test_rule_schema.py
import pytest
from basic_pipelines.garuda_auto.device_registry import DeviceRegistry
from basic_pipelines.garuda_auto.rule_schema import build_schema, BASE_FIELDS

pytestmark = pytest.mark.unit

CHANNELS = (1, 2, 3)


def registry_with(tmp_path, *entries):
    r = DeviceRegistry(str(tmp_path / "devices.json"), relay_channels=CHANNELS)
    for entry in entries:
        ok, reason = r.add(entry)
        assert ok, reason
    return r


LAMP = {"id": "lamp_desk", "name": "Desk lamp", "type": "light", "room": "study",
        "transport": {"kind": "relay", "channel": 1}}
PROBE = {"id": "probe", "name": "Loft probe", "type": "sensor.temperature",
         "room": "loft", "transport": {"kind": "mqtt", "topic_base": "drishti/probe"}}


def test_base_fields_are_always_present(tmp_path):
    schema = build_schema(registry_with(tmp_path))
    for field in ("occupancy", "zone", "posture", "ambient_luma",
                  "temperature_c", "humidity_pct", "hour"):
        assert field in schema.fields


def test_base_fields_do_not_include_hardcoded_lamp_or_fan():
    assert "lamp_state" not in BASE_FIELDS
    assert "fan_state" not in BASE_FIELDS


def test_actuator_contributes_a_state_field_and_a_device(tmp_path):
    schema = build_schema(registry_with(tmp_path, LAMP))
    assert schema.fields["lamp_desk_state"]["values"] == ("on", "off")
    assert schema.devices["lamp_desk"] == frozenset({"on", "off"})


def test_sensor_contributes_a_field_but_not_a_device(tmp_path):
    schema = build_schema(registry_with(tmp_path, PROBE))
    assert schema.fields["probe_state"]["kind"] == "num"
    assert schema.fields["probe_state"]["lo"] == -10
    assert "probe" not in schema.devices


def test_disabled_device_contributes_nothing(tmp_path):
    schema = build_schema(registry_with(tmp_path, {**LAMP, "enabled": False}))
    assert "lamp_desk_state" not in schema.fields
    assert "lamp_desk" not in schema.devices


def test_prompt_schema_carries_names_and_bounds_only(tmp_path):
    payload = build_schema(registry_with(tmp_path, LAMP, PROBE)).schema_for_prompt()
    assert payload["fields"]["lamp_desk_state"] == {"type": "enum", "values": ["on", "off"]}
    assert payload["fields"]["probe_state"] == {"type": "number", "min": -10, "max": 60}
    assert payload["devices"] == {"lamp_desk": ["off", "on"]}
    assert "<=" in payload["operators"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/garuda_auto/test_rule_schema.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_schema'`

- [ ] **Step 3: Write minimal implementation**

Replace the `FIELDS` and `DEVICES` constants and `schema_for_prompt()` in `basic_pipelines/garuda_auto/rule_schema.py` with the following. `MAX_RULES`, `MAX_DURATION_S`, `OPS`, `COOLDOWN_MIN_S` and `COOLDOWN_MAX_S` keep their current values.

```python
"""What a synthesised rule may reference.

The vocabulary is built at runtime from the device registry, so adding a
device widens what the user can talk about without a code change. Camera-
derived fields are fixed: they come from the detector, not from anything a
user can declare.

The cloud model is told these names and legal values; it is never told their
current readings.
"""
from .device_types import actions_for, state_spec, is_actuator

MAX_RULES = 64
MAX_DURATION_S = 86_400

OPS = frozenset({"==", "!=", "<", "<=", ">", ">="})
_NUM_OPS = frozenset({"==", "!=", "<", "<=", ">", ">="})
_ENUM_OPS = frozenset({"==", "!="})

COOLDOWN_MIN_S = 0
COOLDOWN_MAX_S = 3600

BASE_FIELDS = {
    "occupancy":            {"kind": "enum", "values": ("empty", "occupied"), "ops": _ENUM_OPS},
    "person_count":         {"kind": "num", "lo": 0, "hi": 16, "ops": _NUM_OPS},
    "occupancy_duration_s": {"kind": "num", "lo": 0, "hi": MAX_DURATION_S, "ops": _NUM_OPS},
    "zone":                 {"kind": "enum", "values": ("none", "desk", "door", "center"), "ops": _ENUM_OPS},
    "posture":              {"kind": "enum", "values": ("none", "standing", "seated", "walking"), "ops": _ENUM_OPS},
    "ambient_luma":         {"kind": "num", "lo": 0, "hi": 255, "ops": _NUM_OPS},
    "temperature_c":        {"kind": "num", "lo": -10, "hi": 60, "ops": _NUM_OPS},
    "humidity_pct":         {"kind": "num", "lo": 0, "hi": 100, "ops": _NUM_OPS},
    "hour":                 {"kind": "num", "lo": 0, "hi": 23, "ops": _NUM_OPS},
}


def state_field(device_id):
    """The one field a device contributes. Uniform across types."""
    return f"{device_id}_state"


class Schema:
    def __init__(self, fields, devices):
        self.fields = fields
        self.devices = devices

    def schema_for_prompt(self):
        """The exact structure sent to NIM. Names and legal values only.

        Never include readings here. The model compiles rules; it does not
        need to know what the room currently looks like.
        """
        out = {}
        for name, spec in self.fields.items():
            if spec["kind"] == "enum":
                out[name] = {"type": "enum", "values": list(spec["values"])}
            else:
                out[name] = {"type": "number", "min": spec["lo"], "max": spec["hi"]}
        return {"fields": out,
                "devices": {d: sorted(a) for d, a in self.devices.items()},
                "operators": sorted(OPS)}


def build_schema(registry):
    fields = {name: dict(spec) for name, spec in BASE_FIELDS.items()}
    devices = {}
    for device in registry.devices:
        if not device.get("enabled", True):
            continue
        spec = state_spec(device["type"])
        if spec is None:
            continue
        spec["ops"] = _ENUM_OPS if spec["kind"] == "enum" else _NUM_OPS
        fields[state_field(device["id"])] = spec
        if is_actuator(device["type"]):
            devices[device["id"]] = actions_for(device["type"])
    return Schema(fields, devices)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/garuda_auto/test_rule_schema.py -v`
Expected: 6 passed. Other `garuda_auto` tests fail at this point — Tasks 4 to 7 repair them.

- [ ] **Step 5: Commit**

```bash
git add basic_pipelines/garuda_auto/rule_schema.py tests/garuda_auto/test_rule_schema.py
git commit -m "feat(schema): build the rule vocabulary from the device registry"
```

---

### Task 4: Validator takes a schema

**Files:**
- Modify: `basic_pipelines/garuda_auto/validator.py`
- Modify: `tests/garuda_auto/test_validator.py`

**Interfaces:**
- Consumes: `Schema.fields`, `Schema.devices`, `rule_schema.OPS`, `COOLDOWN_MIN_S`, `COOLDOWN_MAX_S`
- Produces: `validate_rule(rule, schema) -> (bool, str)`

- [ ] **Step 1: Write the failing test**

Rewrite `tests/garuda_auto/test_validator.py` in full. The existing tests referenced `lamp`/`fan` as built-ins; they now come from a registry.

```python
# tests/garuda_auto/test_validator.py
import pytest
from basic_pipelines.garuda_auto.device_registry import DeviceRegistry
from basic_pipelines.garuda_auto.rule_schema import build_schema
from basic_pipelines.garuda_auto.validator import validate_rule

pytestmark = pytest.mark.unit

FAN = {"id": "fan", "name": "Fan", "type": "fan", "room": "study",
       "transport": {"kind": "relay", "channel": 2}}

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


@pytest.fixture
def schema(tmp_path):
    registry = DeviceRegistry(str(tmp_path / "devices.json"), relay_channels=(1, 2, 3))
    ok, reason = registry.add(FAN)
    assert ok, reason
    return build_schema(registry)


def test_accepts_a_well_formed_rule(schema):
    ok, reason = validate_rule(BASE, schema)
    assert ok is True, reason


def test_rejects_unknown_field(schema):
    bad = {**BASE, "when": {"all": [{"field": "face_id", "op": "==", "value": "manikanta"}]}}
    ok, reason = validate_rule(bad, schema)
    assert ok is False and "face_id" in reason


def test_rejects_unknown_device(schema):
    bad = {**BASE, "then": [{"device": "front_door_lock", "action": "off"}]}
    ok, reason = validate_rule(bad, schema)
    assert ok is False and "front_door_lock" in reason


def test_rejects_action_not_legal_for_device(schema):
    bad = {**BASE, "then": [{"device": "fan", "action": "unlock"}]}
    ok, reason = validate_rule(bad, schema)
    assert ok is False


def test_rejects_value_outside_field_bounds(schema):
    bad = {**BASE, "when": {"all": [{"field": "ambient_luma", "op": "<", "value": 900}]}}
    ok, reason = validate_rule(bad, schema)
    assert ok is False and "ambient_luma" in reason


def test_rejects_ordering_operator_on_an_enum_field(schema):
    bad = {**BASE, "when": {"all": [{"field": "occupancy", "op": "<", "value": "empty"}]}}
    ok, reason = validate_rule(bad, schema)
    assert ok is False


def test_rejects_missing_source_utterance(schema):
    bad = {k: v for k, v in BASE.items() if k != "source_utterance"}
    ok, reason = validate_rule(bad, schema)
    assert ok is False and "source_utterance" in reason


def test_rejects_cooldown_out_of_range(schema):
    ok, reason = validate_rule({**BASE, "cooldown_s": 99_999}, schema)
    assert ok is False and "cooldown_s" in reason


def test_a_registry_device_becomes_addressable(tmp_path):
    registry = DeviceRegistry(str(tmp_path / "devices.json"), relay_channels=(1, 2, 3))
    registry.add(FAN)
    registry.add({"id": "lamp_desk", "name": "Desk lamp", "type": "light",
                  "room": "study", "transport": {"kind": "relay", "channel": 1}})
    rule = {**BASE, "then": [{"device": "lamp_desk", "action": "on"}],
            "when": {"all": [{"field": "lamp_desk_state", "op": "==", "value": "off"}]}}
    ok, reason = validate_rule(rule, build_schema(registry))
    assert ok is True, reason


def test_a_deleted_device_makes_its_rule_invalid(tmp_path):
    registry = DeviceRegistry(str(tmp_path / "devices.json"), relay_channels=(1, 2, 3))
    registry.add(FAN)
    schema_before = build_schema(registry)
    assert validate_rule(BASE, schema_before)[0] is True
    registry.delete("fan")
    ok, reason = validate_rule(BASE, build_schema(registry))
    assert ok is False and "fan" in reason
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/garuda_auto/test_validator.py -v`
Expected: FAIL with `TypeError: validate_rule() takes 1 positional argument but 2 were given`

- [ ] **Step 3: Write minimal implementation**

In `basic_pipelines/garuda_auto/validator.py`, change the import line and the two function signatures. Everything else in the file is unchanged.

```python
from .rule_schema import OPS, COOLDOWN_MIN_S, COOLDOWN_MAX_S


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
    # ... body unchanged from here, still reading `spec` ...


def validate_rule(rule, schema):
    # ... body unchanged, except:
    #   `_check_condition(cond)`      -> `_check_condition(cond, schema)`
    #   `legal = DEVICES.get(device)` -> `legal = schema.devices.get(device)`
```

Apply exactly those four edits: drop `FIELDS`/`DEVICES` from the import, add the `schema` parameter to both functions, read `schema.fields` in place of `FIELDS`, and read `schema.devices` in place of `DEVICES`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/garuda_auto/test_validator.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add basic_pipelines/garuda_auto/validator.py tests/garuda_auto/test_validator.py
git commit -m "feat(validator): validate against a runtime schema instead of module constants"
```

---

### Task 5: Rule store keeps orphans instead of deleting them

**Files:**
- Modify: `basic_pipelines/garuda_auto/rule_store.py`
- Modify: `tests/garuda_auto/test_rule_store.py`

**Interfaces:**
- Consumes: `validate_rule(rule, schema)`, `Schema.fields`, `rule_schema.MAX_RULES`
- Produces: `RuleStore(path, schema)` with attributes `rules: list[dict]` (active) and `orphaned: list[dict]` (retained, invalid under the current schema), and methods `load()`, `save()`, `add(rule) -> (bool, str)`, `delete(rule_id) -> bool`, `find_conflict(rule) -> dict | None`, `rebind(schema)`

This is the data-loss fix. The current `load()` line

```python
self.rules = [r for r in data if validate_rule(r)[0]] if isinstance(data, list) else []
```

deletes from disk any rule that fails validation. Once the schema depends on the registry, removing a device silently destroys everything the user taught about it.

The on-disk format does not change: it stays one flat list. Orphans are marked in place with `"orphaned": true`, so an old file loads and a new file is still readable by anything expecting a list.

- [ ] **Step 1: Write the failing test**

Append to `tests/garuda_auto/test_rule_store.py`, and update its existing `RuleStore(path)` constructions to `RuleStore(path, schema)` using the fixture below.

```python
import json
import pytest
from basic_pipelines.garuda_auto.device_registry import DeviceRegistry
from basic_pipelines.garuda_auto.rule_schema import build_schema
from basic_pipelines.garuda_auto.rule_store import RuleStore

FAN = {"id": "fan", "name": "Fan", "type": "fan", "room": "study",
       "transport": {"kind": "relay", "channel": 2}}

RULE = {
    "id": "r_001",
    "source_utterance": "turn the fan off when the room is empty",
    "when": {"all": [{"field": "occupancy", "op": "==", "value": "empty"}]},
    "then": [{"device": "fan", "action": "off"}],
    "cooldown_s": 60,
    "enabled": True,
}


@pytest.fixture
def registry(tmp_path):
    r = DeviceRegistry(str(tmp_path / "devices.json"), relay_channels=(1, 2, 3))
    r.add(FAN)
    return r


def test_deleting_a_device_orphans_its_rules_without_losing_them(tmp_path, registry):
    path = str(tmp_path / "rules.json")
    store = RuleStore(path, build_schema(registry))
    ok, reason = store.add(RULE)
    assert ok, reason

    registry.delete("fan")
    reloaded = RuleStore(path, build_schema(registry))

    assert reloaded.rules == []
    assert [r["id"] for r in reloaded.orphaned] == ["r_001"]
    assert reloaded.orphaned[0]["source_utterance"] == RULE["source_utterance"]


def test_orphans_survive_a_save_and_a_second_reload(tmp_path, registry):
    path = str(tmp_path / "rules.json")
    RuleStore(path, build_schema(registry)).add(RULE)
    registry.delete("fan")

    first = RuleStore(path, build_schema(registry))
    first.save()
    second = RuleStore(path, build_schema(registry))

    assert [r["id"] for r in second.orphaned] == ["r_001"]
    assert json.loads(open(path).read())[0]["orphaned"] is True


def test_readding_the_device_restores_the_rule(tmp_path, registry):
    path = str(tmp_path / "rules.json")
    RuleStore(path, build_schema(registry)).add(RULE)
    registry.delete("fan")
    store = RuleStore(path, build_schema(registry))
    assert store.rules == []

    registry.add(FAN)
    store.rebind(build_schema(registry))

    assert [r["id"] for r in store.rules] == ["r_001"]
    assert store.orphaned == []


def test_orphans_do_not_count_towards_the_rule_limit(tmp_path, registry):
    path = str(tmp_path / "rules.json")
    store = RuleStore(path, build_schema(registry))
    store.add(RULE)
    registry.delete("fan")
    store.rebind(build_schema(registry))
    assert len(store.rules) == 0


def test_a_genuinely_malformed_entry_is_kept_as_an_orphan(tmp_path, registry):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps([{"id": "junk", "source_utterance": "x"}]))
    store = RuleStore(str(path), build_schema(registry))
    assert store.rules == []
    assert [r["id"] for r in store.orphaned] == ["junk"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/garuda_auto/test_rule_store.py -v`
Expected: FAIL with `TypeError: RuleStore.__init__() takes 2 positional arguments but 3 were given`

- [ ] **Step 3: Write minimal implementation**

In `basic_pipelines/garuda_auto/rule_store.py`:

Change the import to `from .rule_schema import MAX_RULES`, drop the `FIELDS` import, and make `_provably_disjoint` take the schema:

```python
def _provably_disjoint(a, b, schema):
    if a["field"] != b["field"]:
        return False
    spec = schema.fields[a["field"]]
    # ... body unchanged ...
```

Replace `__init__`, `load` and `save`, and add `rebind`:

```python
    def __init__(self, path, schema):
        self.path = path
        self.schema = schema
        self._lock = threading.Lock()
        self.rules = []
        self.orphaned = []
        self.load()

    def _partition(self, entries):
        """Split entries into rules valid under the current schema and the rest.

        Invalid entries are retained, not discarded. A rule that no longer
        validates because its device was removed is knowledge the user gave
        us; dropping it silently would delete their work.
        """
        active, orphaned = [], []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            ok, _ = validate_rule(entry, self.schema)
            if ok:
                clean = dict(entry)
                clean.pop("orphaned", None)
                active.append(clean)
            else:
                orphaned.append({**entry, "orphaned": True, "enabled": False})
        return active, orphaned

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            # Missing or corrupt store is not fatal -- start empty rather than
            # taking the whole voice loop down.
            self.rules, self.orphaned = [], []
            return
        if not isinstance(data, list):
            self.rules, self.orphaned = [], []
            return
        self.rules, self.orphaned = self._partition(data)

    def save(self):
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.rules + self.orphaned, fh, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def rebind(self, schema):
        """Re-partition against a new schema, after the registry changed."""
        with self._lock:
            self.schema = schema
            self.rules, self.orphaned = self._partition(self.rules + self.orphaned)
            self.save()
```

In `add`, change `validate_rule(rule)` to `validate_rule(rule, self.schema)`. In `find_conflict`, change the `_provably_disjoint(nc, ec)` call to `_provably_disjoint(nc, ec, self.schema)`. In `delete`, also filter `self.orphaned` so a user can remove an orphan they do not want repaired.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/garuda_auto/test_rule_store.py -v`
Expected: all pass, including the five new tests

- [ ] **Step 5: Commit**

```bash
git add basic_pipelines/garuda_auto/rule_store.py tests/garuda_auto/test_rule_store.py
git commit -m "fix(rules): retain rules invalidated by device removal instead of deleting them

RuleStore.load() dropped any rule that failed validation. Once the schema is
built from the device registry, removing a device invalidates its rules, so
the next restart erased them from disk with no record. load() now partitions
into active and orphaned, and save() writes both."
```

---

### Task 6: Scene builder reads device state from the registry

**Files:**
- Modify: `basic_pipelines/garuda_auto/scene_state.py`
- Modify: `tests/garuda_auto/test_scene_state.py`

**Interfaces:**
- Consumes: `DeviceRegistry.devices`, `Schema.fields`, `rule_schema.state_field`
- Produces: `SceneBuilder(zones, registry, clock=time.time)` with methods `set_device_state(device_id, state)`, `update(detections, luma, temperature_c, humidity_pct, hour) -> dict`

- [ ] **Step 1: Write the failing test**

```python
# tests/garuda_auto/test_scene_state.py — append these, and update existing
# SceneBuilder(zones) constructions to SceneBuilder(zones, registry)
import pytest
from basic_pipelines.garuda_auto.device_registry import DeviceRegistry
from basic_pipelines.garuda_auto.scene_state import SceneBuilder

ZONES = {"desk": (0, 0, 100, 100)}


@pytest.fixture
def registry(tmp_path):
    r = DeviceRegistry(str(tmp_path / "devices.json"), relay_channels=(1, 2, 3))
    r.add({"id": "lamp_desk", "name": "Desk lamp", "type": "light", "room": "study",
           "transport": {"kind": "relay", "channel": 1}})
    r.add({"id": "porch", "name": "Porch door", "type": "sensor.contact", "room": "porch",
           "transport": {"kind": "mqtt", "topic_base": "drishti/porch"}})
    return r


def test_descriptor_carries_a_field_per_registered_device(registry):
    builder = SceneBuilder(ZONES, registry)
    descriptor = builder.update([], luma=100, temperature_c=22.0, humidity_pct=50.0, hour=12)
    assert descriptor["lamp_desk_state"] == "off"
    assert descriptor["porch_state"] == "closed"


def test_descriptor_has_no_hardcoded_lamp_or_fan(registry):
    descriptor = SceneBuilder(ZONES, registry).update(
        [], luma=100, temperature_c=22.0, humidity_pct=50.0, hour=12)
    assert "lamp_state" not in descriptor
    assert "fan_state" not in descriptor


def test_set_device_state_is_reflected(registry):
    builder = SceneBuilder(ZONES, registry)
    builder.set_device_state("lamp_desk", "on")
    descriptor = builder.update([], luma=100, temperature_c=22.0, humidity_pct=50.0, hour=12)
    assert descriptor["lamp_desk_state"] == "on"


def test_set_device_state_refuses_a_value_outside_the_type(registry):
    builder = SceneBuilder(ZONES, registry)
    builder.set_device_state("lamp_desk", "sideways")
    descriptor = builder.update([], luma=100, temperature_c=22.0, humidity_pct=50.0, hour=12)
    assert descriptor["lamp_desk_state"] == "off"


def test_numeric_sensor_state_is_clamped_to_its_bounds(tmp_path):
    r = DeviceRegistry(str(tmp_path / "devices.json"), relay_channels=(1,))
    r.add({"id": "probe", "name": "Probe", "type": "sensor.temperature", "room": "loft",
           "transport": {"kind": "mqtt", "topic_base": "drishti/probe"}})
    builder = SceneBuilder(ZONES, r)
    builder.set_device_state("probe", 900)
    descriptor = builder.update([], luma=100, temperature_c=22.0, humidity_pct=50.0, hour=12)
    assert descriptor["probe_state"] == 60
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/garuda_auto/test_scene_state.py -v`
Expected: FAIL with `TypeError: SceneBuilder.__init__() takes 2 positional arguments but 3 were given`

- [ ] **Step 3: Write minimal implementation**

In `basic_pipelines/garuda_auto/scene_state.py`, change the import to
`from .rule_schema import BASE_FIELDS, build_schema, state_field`, and replace `__init__`, `set_device_state`, `_clamp` and the tail of `update`:

```python
def _clamp(value, spec):
    return max(spec["lo"], min(spec["hi"], value))


class SceneBuilder:
    def __init__(self, zones, registry, clock=time.time):
        self.zones = zones
        self.registry = registry
        self._clock = clock
        self._state_since = None
        self._last_occupancy = None
        self._device_state = {}
        self.rebind()

    def rebind(self):
        """Rebuild device-state slots after the registry changed.

        Known states are preserved; new devices start at their type's first
        legal value, or its lower bound for numeric types.
        """
        schema = build_schema(self.registry)
        fresh = {}
        for device in self.registry.devices:
            if not device.get("enabled", True):
                continue
            field = state_field(device["id"])
            spec = schema.fields.get(field)
            if spec is None:
                continue
            default = spec["values"][-1] if spec["kind"] == "enum" else spec["lo"]
            fresh[device["id"]] = self._device_state.get(device["id"], default)
        self._device_state = fresh
        self._schema = schema

    def set_device_state(self, device_id, state):
        spec = self._schema.fields.get(state_field(device_id))
        if spec is None:
            return
        if spec["kind"] == "enum":
            if state in spec["values"]:
                self._device_state[device_id] = state
        else:
            if isinstance(state, bool) or not isinstance(state, (int, float)):
                return
            self._device_state[device_id] = _clamp(state, spec)
```

For `light`, `fan` and `switch` the enum is `("on", "off")`, so `values[-1]` is `"off"` — a device starts off. For `sensor.contact` it is `("open", "closed")`, so a door starts closed. Both are the safe default.

In `update`, replace the fixed `_clamp(value, "field_name")` calls with `_clamp(value, BASE_FIELDS["field_name"])`, and replace the two hardcoded trailing entries with the registry-derived ones:

```python
        descriptor = {
            "occupancy": occupancy,
            "person_count": int(_clamp(len(people), BASE_FIELDS["person_count"])),
            "occupancy_duration_s": int(_clamp(duration, BASE_FIELDS["occupancy_duration_s"])),
            "zone": zone,
            "posture": posture,
            "ambient_luma": int(_clamp(int(luma), BASE_FIELDS["ambient_luma"])),
            "temperature_c": _clamp(float(temperature_c), BASE_FIELDS["temperature_c"]),
            "humidity_pct": _clamp(float(humidity_pct), BASE_FIELDS["humidity_pct"]),
            "hour": int(_clamp(int(hour), BASE_FIELDS["hour"])),
        }
        for device_id, value in self._device_state.items():
            descriptor[state_field(device_id)] = value
        return descriptor
```

Also change the `posture` guard from `FIELDS["posture"]["values"]` to `BASE_FIELDS["posture"]["values"]`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/garuda_auto/test_scene_state.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add basic_pipelines/garuda_auto/scene_state.py tests/garuda_auto/test_scene_state.py
git commit -m "feat(scene): derive device state fields from the registry"
```

---

### Task 7: NIM client takes a schema and stops blocking the event loop

**Files:**
- Modify: `basic_pipelines/garuda_auto/nim_client.py`
- Modify: `tests/garuda_auto/test_nim_client.py`

**Interfaces:**
- Consumes: `Schema.schema_for_prompt()`, `validate_rule(rule, schema)`
- Produces: `NimClient(api_key, model, base_url=DEFAULT_BASE_URL, timeout=20)` with `build_request(utterance, existing_rules, schema)`, `synthesize(utterance, existing_rules, schema) -> (dict | None, str)`, `async synthesize_async(utterance, existing_rules, schema)`, and attribute `tokens_used: int`

- [ ] **Step 1: Write the failing test**

```python
# tests/garuda_auto/test_nim_client.py — append these; update existing calls
# to pass the schema fixture as the third argument
import json
import pytest
from basic_pipelines.garuda_auto.device_registry import DeviceRegistry
from basic_pipelines.garuda_auto.rule_schema import build_schema
from basic_pipelines.garuda_auto.nim_client import NimClient


@pytest.fixture
def schema(tmp_path):
    r = DeviceRegistry(str(tmp_path / "devices.json"), relay_channels=(1, 2, 3))
    r.add({"id": "lamp_desk", "name": "Desk lamp", "type": "light", "room": "study",
           "transport": {"kind": "relay", "channel": 1}})
    return build_schema(r)


def test_request_names_a_user_added_device(schema):
    client = NimClient("key", "model")
    body = client.build_request("turn the desk lamp on when I sit down", [], schema)
    payload = json.loads(body["messages"][1]["content"])
    assert "lamp_desk" in payload["schema"]["devices"]


def test_request_carries_no_descriptor_value(schema):
    """Structural egress test: only names and bounds may cross."""
    client = NimClient("key", "model")
    body = client.build_request("turn the lamp on", [], schema)
    payload = json.loads(body["messages"][1]["content"])
    for name, field in payload["schema"]["fields"].items():
        assert set(field) <= {"type", "values", "min", "max"}, name


@pytest.mark.asyncio
async def test_synthesize_async_returns_the_same_shape(monkeypatch, schema):
    client = NimClient("", "model")
    rule, reason = await client.synthesize_async("anything", [], schema)
    assert rule is None
    assert reason == "no NIM API key configured"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/garuda_auto/test_nim_client.py -v`
Expected: FAIL with `TypeError: build_request() takes 3 positional arguments but 4 were given`

- [ ] **Step 3: Write minimal implementation**

In `basic_pipelines/garuda_auto/nim_client.py`, drop the `from .rule_schema import schema_for_prompt` import, add `import anyio` at the top, and change the three methods:

```python
    def build_request(self, utterance, existing_rules, schema):
        """Assemble the request body. Schema only -- never live values."""
        known = [r.get("source_utterance", "") for r in existing_rules][:32]
        user_content = json.dumps({
            "schema": schema.schema_for_prompt(),
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

    def synthesize(self, utterance, existing_rules, schema):
        """Return (rule, "") on success or (None, reason) on any failure.

        Blocking. Call synthesize_async from async code -- a 20 second
        timeout on the event loop stalls the MJPEG stream, the websocket
        broadcaster and every concurrent request for its duration.
        """
        if not self.api_key:
            return None, "no NIM API key configured"
        # ... body unchanged, except the two calls below ...
        #   json=self.build_request(utterance, existing_rules, schema),
        #   ok, reason = validate_rule(parsed, schema)

    async def synthesize_async(self, utterance, existing_rules, schema):
        return await anyio.to_thread.run_sync(
            self.synthesize, utterance, existing_rules, schema)
```

Add `anyio` and `pytest-asyncio` to `requirements.txt` and `requirements-test.txt` respectively. FastAPI already depends on `anyio`, so this adds no new runtime dependency in practice.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/garuda_auto -v`
Expected: the whole `garuda_auto` suite passes — Tasks 3 to 7 together restore it

- [ ] **Step 5: Commit**

```bash
git add basic_pipelines/garuda_auto/nim_client.py tests/garuda_auto/test_nim_client.py requirements.txt requirements-test.txt
git commit -m "fix(nim): take a runtime schema and move the blocking call off the event loop"
```

---

### Task 8: Transports — MQTT bank and device router

**Files:**
- Create: `basic_pipelines/garuda_auto/transports.py`
- Test: `tests/garuda_auto/test_transports.py`

**Interfaces:**
- Consumes: `DeviceRegistry.get`, `DeviceRegistry.devices`, `actuators.RelayBank`, `device_types.actions_for`
- Produces: `MqttBank(broker_host, broker_port=1883, client_factory=None)` with `set(device_id, action) -> bool`, `state(device_id) -> str | None`, `available(device_id) -> bool`, `bind(registry)`, `on_state(device_id, value)`; and `DeviceRouter(registry, relay_bank, mqtt_bank)` with `set(device_id, action) -> (bool, str)`, `state(device_id)`, `available(device_id) -> bool`

`MqttBank` takes a `client_factory` so tests inject a fake and no broker is needed. `paho-mqtt` is an optional import, matching how `actuators.py` treats `gpiozero`.

- [ ] **Step 1: Write the failing test**

```python
# tests/garuda_auto/test_transports.py
import pytest
from basic_pipelines.garuda_auto.actuators import RelayBank
from basic_pipelines.garuda_auto.device_registry import DeviceRegistry
from basic_pipelines.garuda_auto.transports import DeviceRouter, MqttBank

pytestmark = pytest.mark.unit

LAMP = {"id": "lamp_desk", "name": "Desk lamp", "type": "light", "room": "study",
        "transport": {"kind": "relay", "channel": 1}}
HEATER = {"id": "heater", "name": "Heater", "type": "switch", "room": "loft",
          "transport": {"kind": "mqtt", "topic_base": "drishti/heater"}}


class FakeMqttClient:
    def __init__(self):
        self.published = []
        self.connected = True

    def publish(self, topic, payload):
        if not self.connected:
            raise OSError("broker unreachable")
        self.published.append((topic, payload))

    def subscribe(self, topic):
        pass


@pytest.fixture
def parts(tmp_path):
    registry = DeviceRegistry(str(tmp_path / "devices.json"), relay_channels=(1, 2, 3))
    registry.add(LAMP)
    registry.add(HEATER)
    client = FakeMqttClient()
    mqtt = MqttBank("localhost", client_factory=lambda: client)
    mqtt.bind(registry)
    relays = RelayBank({"lamp_desk": 17})
    return registry, DeviceRouter(registry, relays, mqtt), client


def test_relay_device_routes_to_the_relay_bank(parts):
    _, router, client = parts
    ok, reason = router.set("lamp_desk", "on")
    assert ok is True, reason
    assert client.published == []
    assert router.state("lamp_desk") == "on"


def test_mqtt_device_publishes_to_its_set_topic(parts):
    _, router, client = parts
    ok, reason = router.set("heater", "on")
    assert ok is True, reason
    assert client.published == [("drishti/heater/set", "on")]


def test_unknown_device_is_refused(parts):
    _, router, _ = parts
    ok, reason = router.set("ghost", "on")
    assert ok is False and "ghost" in reason


def test_action_illegal_for_the_type_is_refused(parts):
    _, router, _ = parts
    ok, reason = router.set("lamp_desk", "unlock")
    assert ok is False and "unlock" in reason


def test_unreachable_mqtt_device_fails_loudly(parts):
    _, router, client = parts
    client.connected = False
    ok, reason = router.set("heater", "on")
    assert ok is False and "unreachable" in reason.lower()


def test_relay_devices_are_always_available(parts):
    _, router, _ = parts
    assert router.available("lamp_desk") is True


def test_mqtt_device_is_unavailable_until_it_reports(parts):
    _, router, _ = parts
    assert router.available("heater") is False
    router._mqtt.on_state("heater", "off")
    assert router.available("heater") is True
    assert router.state("heater") == "off"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/garuda_auto/test_transports.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'basic_pipelines.garuda_auto.transports'`

- [ ] **Step 3: Write minimal implementation**

```python
# basic_pipelines/garuda_auto/transports.py
"""Dispatch actuation across transports behind one interface.

rule_engine.py asks the router to set a device and never learns whether that
device is a relay on the Pi or an ESP32 on the network.

A relay is always in a known state because we commanded it. An MQTT device is
not: an unplugged board reports nothing. Availability is tracked here rather
than by adding "unknown" to the state vocabulary, which would let people write
rules about reachability -- a different concept from a device's state.
"""
import logging

from .device_types import actions_for

log = logging.getLogger(__name__)

try:
    import paho.mqtt.client as paho
    MQTT_AVAILABLE = True
except Exception:
    paho = None
    MQTT_AVAILABLE = False


class MqttBank:
    def __init__(self, broker_host, broker_port=1883, client_factory=None):
        self.broker_host = broker_host
        self.broker_port = broker_port
        self._topics = {}
        self._state = {}
        self._seen = set()
        self._client = None
        if client_factory is not None:
            self._client = client_factory()
        elif MQTT_AVAILABLE:
            self._client = paho.Client()
            try:
                self._client.connect(broker_host, broker_port, keepalive=60)
                self._client.loop_start()
            except Exception as exc:
                log.warning("MQTT connect failed: %s", exc)
                self._client = None
        else:
            log.warning("paho-mqtt unavailable -- MQTT devices will be unreachable")

    def bind(self, registry):
        self._topics = {d["id"]: d["transport"]["topic_base"]
                        for d in registry.devices
                        if d["transport"]["kind"] == "mqtt"}
        if self._client is not None:
            for topic in self._topics.values():
                try:
                    self._client.subscribe(f"{topic}/state")
                except Exception as exc:
                    log.warning("MQTT subscribe failed for %s: %s", topic, exc)

    def on_state(self, device_id, value):
        """Called when a device reports. Marks it available."""
        self._state[device_id] = value
        self._seen.add(device_id)

    def available(self, device_id):
        return device_id in self._seen

    def state(self, device_id):
        return self._state.get(device_id)

    def set(self, device_id, action):
        topic = self._topics.get(device_id)
        if topic is None or self._client is None:
            return False
        try:
            self._client.publish(f"{topic}/set", action)
        except Exception as exc:
            log.warning("MQTT publish failed for %s: %s", device_id, exc)
            return False
        self._state[device_id] = action
        return True


class DeviceRouter:
    def __init__(self, registry, relay_bank, mqtt_bank):
        self.registry = registry
        self._relays = relay_bank
        self._mqtt = mqtt_bank

    def _device(self, device_id):
        device = self.registry.get(device_id)
        if device is None or not device.get("enabled", True):
            return None
        return device

    def set(self, device_id, action):
        device = self._device(device_id)
        if device is None:
            return False, f"unknown device: {device_id!r}"
        if action not in actions_for(device["type"]):
            return False, f"action {action!r} is not legal for {device_id!r}"
        if device["transport"]["kind"] == "relay":
            if self._relays.set(device_id, action):
                return True, ""
            return False, f"relay refused {device_id!r}"
        if self._mqtt.set(device_id, action):
            return True, ""
        return False, f"device {device_id!r} is unreachable"

    def state(self, device_id):
        device = self._device(device_id)
        if device is None:
            return None
        if device["transport"]["kind"] == "relay":
            return self._relays.state(device_id)
        return self._mqtt.state(device_id)

    def available(self, device_id):
        device = self._device(device_id)
        if device is None:
            return False
        if device["transport"]["kind"] == "relay":
            return True
        return self._mqtt.available(device_id)
```

`RelayBank` is constructed from the registry by the caller: `RelayBank({d["id"]: channel_to_pin[d["transport"]["channel"]] for d in registry.actuators() if d["transport"]["kind"] == "relay"})`. Task 12 wires that up.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/garuda_auto/test_transports.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add basic_pipelines/garuda_auto/transports.py tests/garuda_auto/test_transports.py
git commit -m "feat(devices): route actuation across relay and MQTT transports"
```

---

### Task 9: Actuation log

**Files:**
- Create: `basic_pipelines/garuda_auto/actuation_log.py`
- Test: `tests/garuda_auto/test_actuation_log.py`

**Interfaces:**
- Consumes: nothing
- Produces: `record(path, *, device, action, rule_id, matched, ok, reason="", clock=time.time) -> None`, `recent(path, limit=200) -> list[dict]`, `last_for(path, device) -> dict | None`

Newline-delimited JSON, appended. This is what makes "why did the fan turn on" answerable without a model: the matched conditions are stored alongside the action.

- [ ] **Step 1: Write the failing test**

```python
# tests/garuda_auto/test_actuation_log.py
import pytest
from basic_pipelines.garuda_auto import actuation_log as alog

pytestmark = pytest.mark.unit

MATCHED = [{"field": "occupancy", "op": "==", "value": "empty"}]


def test_record_then_read_back(tmp_path):
    path = str(tmp_path / "actuations.jsonl")
    alog.record(path, device="fan", action="off", rule_id="r_001",
                matched=MATCHED, ok=True)
    entries = alog.recent(path)
    assert len(entries) == 1
    assert entries[0]["device"] == "fan"
    assert entries[0]["matched"] == MATCHED
    assert entries[0]["ok"] is True


def test_failed_actuation_keeps_its_reason(tmp_path):
    path = str(tmp_path / "actuations.jsonl")
    alog.record(path, device="heater", action="on", rule_id="r_002",
                matched=MATCHED, ok=False, reason="device 'heater' is unreachable")
    assert alog.recent(path)[0]["reason"] == "device 'heater' is unreachable"


def test_recent_returns_newest_first(tmp_path):
    path = str(tmp_path / "actuations.jsonl")
    ticks = iter([1.0, 2.0, 3.0])
    for device in ("a", "b", "c"):
        alog.record(path, device=device, action="on", rule_id="r",
                    matched=[], ok=True, clock=lambda: next(ticks))
    assert [e["device"] for e in alog.recent(path)] == ["c", "b", "a"]


def test_recent_respects_the_limit(tmp_path):
    path = str(tmp_path / "actuations.jsonl")
    for i in range(10):
        alog.record(path, device=f"d{i}", action="on", rule_id="r", matched=[], ok=True)
    assert len(alog.recent(path, limit=3)) == 3


def test_last_for_finds_the_most_recent_entry_for_one_device(tmp_path):
    path = str(tmp_path / "actuations.jsonl")
    alog.record(path, device="fan", action="on", rule_id="r_001", matched=[], ok=True)
    alog.record(path, device="lamp", action="on", rule_id="r_002", matched=[], ok=True)
    alog.record(path, device="fan", action="off", rule_id="r_003", matched=[], ok=True)
    assert alog.last_for(path, "fan")["rule_id"] == "r_003"


def test_missing_file_reads_as_empty(tmp_path):
    assert alog.recent(str(tmp_path / "nothing.jsonl")) == []
    assert alog.last_for(str(tmp_path / "nothing.jsonl"), "fan") is None


def test_a_corrupt_line_does_not_break_the_read(tmp_path):
    path = tmp_path / "actuations.jsonl"
    alog.record(str(path), device="fan", action="on", rule_id="r", matched=[], ok=True)
    with open(path, "a") as fh:
        fh.write("{ not json\n")
    assert len(alog.recent(str(path))) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/garuda_auto/test_actuation_log.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'basic_pipelines.garuda_auto.actuation_log'`

- [ ] **Step 3: Write minimal implementation**

```python
# basic_pipelines/garuda_auto/actuation_log.py
"""Every actuation, with the rule and the conditions that caused it.

This is what lets the system answer "why did the fan turn on" locally, with
no model involved: the answer is already written down.

Newline-delimited JSON so an append is one write and a partial line cannot
corrupt what came before it.
"""
import json
import os
import time

MAX_LINES = 20_000


def record(path, *, device, action, rule_id, matched, ok, reason="", clock=time.time):
    entry = {
        "ts": clock(),
        "device": device,
        "action": action,
        "rule_id": rule_id,
        "matched": matched,
        "ok": ok,
        "reason": reason,
    }
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()[-MAX_LINES:]
    except OSError:
        return []
    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries


def recent(path, limit=200):
    return list(reversed(_read(path)))[:limit]


def last_for(path, device):
    for entry in reversed(_read(path)):
        if entry.get("device") == device:
            return entry
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/garuda_auto/test_actuation_log.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add basic_pipelines/garuda_auto/actuation_log.py tests/garuda_auto/test_actuation_log.py
git commit -m "feat(activity): record every actuation with the rule and conditions behind it"
```

---

### Task 10: Local lane

**Files:**
- Create: `basic_pipelines/garuda_auto/local_lane.py`
- Test: `tests/garuda_auto/test_local_lane.py`

**Interfaces:**
- Consumes: `DeviceRegistry`, `DeviceRouter`, `actuation_log.last_for`, `rule_schema.state_field`
- Produces: `answer(text, *, registry, descriptor, router, log_path, store) -> dict | None`, returning `{"kind": "state" | "why" | "control", "text": str, "resolved": "on-device"}` or `None` when the utterance is not a local question

Matching is deliberately literal: device names and a small verb vocabulary. Anything ambiguous returns `None` and falls through to the matcher and then the compiler. A local lane that guesses is worse than one that declines.

- [ ] **Step 1: Write the failing test**

```python
# tests/garuda_auto/test_local_lane.py
import pytest
from basic_pipelines.garuda_auto import local_lane
from basic_pipelines.garuda_auto.device_registry import DeviceRegistry

pytestmark = pytest.mark.unit

LAMP = {"id": "lamp_desk", "name": "Desk lamp", "type": "light", "room": "study",
        "transport": {"kind": "relay", "channel": 1}}

DESCRIPTOR = {
    "occupancy": "occupied", "person_count": 1, "occupancy_duration_s": 120,
    "zone": "desk", "posture": "seated", "ambient_luma": 90,
    "temperature_c": 24.5, "humidity_pct": 48.0, "hour": 19,
    "lamp_desk_state": "off",
}


class FakeRouter:
    def __init__(self):
        self.calls = []

    def set(self, device_id, action):
        self.calls.append((device_id, action))
        return True, ""

    def state(self, device_id):
        return "off"

    def available(self, device_id):
        return True


class FakeStore:
    rules = [{"id": "r_001", "source_utterance": "turn the desk lamp on when I sit down"}]


@pytest.fixture
def parts(tmp_path):
    registry = DeviceRegistry(str(tmp_path / "devices.json"), relay_channels=(1, 2, 3))
    registry.add(LAMP)
    return registry, FakeRouter(), str(tmp_path / "actuations.jsonl")


def call(text, parts):
    registry, router, log_path = parts
    return local_lane.answer(text, registry=registry, descriptor=DESCRIPTOR,
                             router=router, log_path=log_path, store=FakeStore())


def test_answers_whether_anyone_is_home(parts):
    result = call("is anyone home?", parts)
    assert result["kind"] == "state"
    assert result["resolved"] == "on-device"
    assert "1" in result["text"]


def test_answers_the_temperature(parts):
    assert "24.5" in call("what's the temperature?", parts)["text"]


def test_answers_a_device_state_by_name(parts):
    result = call("is the desk lamp on?", parts)
    assert result["kind"] == "state"
    assert "off" in result["text"].lower()


def test_turns_a_device_on(parts):
    _, router, _ = parts
    result = call("turn the desk lamp on", parts)
    assert result["kind"] == "control"
    assert router.calls == [("lamp_desk", "on")]


def test_turns_a_device_off(parts):
    _, router, _ = parts
    call("turn off the desk lamp", parts)
    assert router.calls == [("lamp_desk", "off")]


def test_explains_why_a_device_changed(parts, tmp_path):
    from basic_pipelines.garuda_auto import actuation_log
    _, _, log_path = parts
    actuation_log.record(log_path, device="lamp_desk", action="on", rule_id="r_001",
                         matched=[{"field": "posture", "op": "==", "value": "seated"}], ok=True)
    result = call("why did the desk lamp turn on?", parts)
    assert result["kind"] == "why"
    assert "turn the desk lamp on when I sit down" in result["text"]
    assert "posture" in result["text"]


def test_why_with_no_history_says_so(parts):
    assert "no record" in call("why did the desk lamp turn on?", parts)["text"].lower()


def test_declines_a_teaching_instruction(parts):
    assert call("turn the lamp on whenever it gets dark", parts) is None


def test_declines_something_it_does_not_understand(parts):
    assert call("book me a flight to Chennai", parts) is None


def test_control_of_an_unknown_device_declines_rather_than_guessing(parts):
    assert call("turn the garage door on", parts) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/garuda_auto/test_local_lane.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'basic_pipelines.garuda_auto.local_lane'`

- [ ] **Step 3: Write minimal implementation**

```python
# basic_pipelines/garuda_auto/local_lane.py
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

_CONDITIONAL = re.compile(
    r"\b(when|whenever|if|after|unless|every time|as soon as)\b", re.I)
_WHY = re.compile(r"\bwhy\b", re.I)
_ON = re.compile(r"\b(turn|switch|put)\b.*\bon\b|\bon\b.*\b(the|my)\b", re.I)

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
    lowered = text.lower()
    if re.search(r"\b(off|stop|kill|shut)\b", lowered):
        return "off"
    if re.search(r"\b(on|start)\b", lowered):
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
    source = rule["source_utterance"] if rule else "a rule that no longer exists"
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

    # A conditional is a rule being taught, not a question. Never intercept it.
    if _CONDITIONAL.search(text):
        return None

    if _WHY.search(text):
        return _explain(text, registry, log_path, store)

    lowered = text.lower()

    if re.search(r"\b(anyone|anybody|someone) (home|here|in|there)\b", lowered) \
            or "is anyone" in lowered:
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

    action = _wanted_action(text)

    # A question about the device, not a command.
    if lowered.strip().startswith(("is ", "are ", "what")) or lowered.rstrip().endswith("?"):
        value = descriptor.get(state_field(device["id"]), router.state(device["id"]))
        if value is None:
            return {"kind": "state", "resolved": _RESOLVED,
                    "text": f"{device['name']} has not reported its state."}
        return {"kind": "state", "resolved": _RESOLVED,
                "text": f"{device['name']} is {value}."}

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/garuda_auto/test_local_lane.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add basic_pipelines/garuda_auto/local_lane.py tests/garuda_auto/test_local_lane.py
git commit -m "feat(assistant): local lane for state, explanation and direct control"
```

---

### Task 11: Pending proposal store

**Files:**
- Create: `basic_pipelines/garuda_auto/pending_store.py`
- Test: `tests/garuda_auto/test_pending_store.py`

**Interfaces:**
- Consumes: nothing
- Produces: `MAX_PENDING = 8`, `TTL_S = 900`, and `PendingStore(path, clock=time.time)` with `add(rule, conflict=None) -> str`, `get(proposal_id) -> dict | None`, `pop(proposal_id) -> dict | None`, `purge() -> int`, `all() -> list[dict]`

Proposals live in their own file, not in the rule store. Storing them there disabled would be safe — both `RuleEngine` and `LocalMatcher` skip disabled rules — but they would count against `MAX_RULES` and pollute the file that means "what the house knows".

- [ ] **Step 1: Write the failing test**

```python
# tests/garuda_auto/test_pending_store.py
import pytest
from basic_pipelines.garuda_auto.pending_store import PendingStore, MAX_PENDING, TTL_S

pytestmark = pytest.mark.unit

RULE = {
    "source_utterance": "turn the fan off when the room is empty",
    "when": {"all": [{"field": "occupancy", "op": "==", "value": "empty"}]},
    "then": [{"device": "fan", "action": "off"}],
    "cooldown_s": 60,
}


def test_add_returns_an_id_that_fetches_the_rule(tmp_path):
    store = PendingStore(str(tmp_path / "pending.json"))
    pid = store.add(RULE)
    assert store.get(pid)["rule"]["source_utterance"] == RULE["source_utterance"]


def test_pop_returns_once_then_nothing(tmp_path):
    store = PendingStore(str(tmp_path / "pending.json"))
    pid = store.add(RULE)
    assert store.pop(pid) is not None
    assert store.pop(pid) is None


def test_a_conflict_is_carried_with_the_proposal(tmp_path):
    store = PendingStore(str(tmp_path / "pending.json"))
    pid = store.add(RULE, conflict={"id": "r_009", "source_utterance": "keep the fan on"})
    assert store.get(pid)["conflict"]["id"] == "r_009"


def test_proposals_survive_a_reload(tmp_path):
    path = str(tmp_path / "pending.json")
    pid = PendingStore(path).add(RULE)
    assert PendingStore(path).get(pid) is not None


def test_expired_proposals_are_purged(tmp_path):
    now = [1000.0]
    store = PendingStore(str(tmp_path / "pending.json"), clock=lambda: now[0])
    pid = store.add(RULE)
    now[0] += TTL_S + 1
    assert store.purge() == 1
    assert store.get(pid) is None


def test_oldest_is_evicted_past_the_cap(tmp_path):
    store = PendingStore(str(tmp_path / "pending.json"))
    ids = [store.add({**RULE, "source_utterance": f"rule {i}"})
           for i in range(MAX_PENDING + 1)]
    assert store.get(ids[0]) is None
    assert len(store.all()) == MAX_PENDING


def test_corrupt_file_starts_empty(tmp_path):
    path = tmp_path / "pending.json"
    path.write_text("not json at all")
    assert PendingStore(str(path)).all() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/garuda_auto/test_pending_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'basic_pipelines.garuda_auto.pending_store'`

- [ ] **Step 3: Write minimal implementation**

```python
# basic_pipelines/garuda_auto/pending_store.py
"""Rules awaiting the user's confirmation.

A synthesised rule is not yet knowledge. The model can return something that
passes every validation check and still means the opposite of what was asked,
so nothing enters the rule base until a person agrees it is right.

Separate file from the rule store: a proposal is not something the house knows.
"""
import json
import os
import tempfile
import threading
import time
import uuid

MAX_PENDING = 8
TTL_S = 900


class PendingStore:
    def __init__(self, path, clock=time.time):
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._items = []
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self._items = data if isinstance(data, list) else []
        except (OSError, ValueError):
            self._items = []

    def save(self):
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._items, fh, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def _drop_expired(self):
        cutoff = self._clock() - TTL_S
        before = len(self._items)
        self._items = [i for i in self._items if i["created_at"] >= cutoff]
        return before - len(self._items)

    def add(self, rule, conflict=None):
        with self._lock:
            self._drop_expired()
            proposal_id = uuid.uuid4().hex[:12]
            self._items.append({
                "id": proposal_id,
                "rule": rule,
                "conflict": conflict,
                "created_at": self._clock(),
            })
            if len(self._items) > MAX_PENDING:
                self._items = self._items[-MAX_PENDING:]
            self.save()
        return proposal_id

    def get(self, proposal_id):
        with self._lock:
            self._drop_expired()
            for item in self._items:
                if item["id"] == proposal_id:
                    return item
        return None

    def pop(self, proposal_id):
        with self._lock:
            self._drop_expired()
            for index, item in enumerate(self._items):
                if item["id"] == proposal_id:
                    self._items.pop(index)
                    self.save()
                    return item
        return None

    def purge(self):
        with self._lock:
            dropped = self._drop_expired()
            if dropped:
                self.save()
        return dropped

    def all(self):
        with self._lock:
            self._drop_expired()
            return list(self._items)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/garuda_auto/test_pending_store.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add basic_pipelines/garuda_auto/pending_store.py tests/garuda_auto/test_pending_store.py
git commit -m "feat(assistant): pending proposal store with TTL and cap"
```

---

### Task 12: Drishti authentication

**Files:**
- Create: `basic_pipelines/drishti_auth.py`
- Test: `tests/test_drishti_auth.py`

**Interfaces:**
- Consumes: `Garuda_web.USERS`, `Garuda_web._verify_password`, `Garuda_web._check_rate_limit`, `Garuda_web._is_login_locked`, `Garuda_web._record_login_failure`, `Garuda_web._clear_login_failure`
- Produces: `COOKIE_NAME = "drishti_session"`, `create_session(username, role, duration=None) -> str`, `get_session(token) -> dict | None`, `destroy_session(token) -> bool`, `require_drishti_session(request) -> dict`, `require_drishti_admin(request) -> dict`, `prune_expired() -> int`

Drishti has its own login and its own host-scoped cookie. Widening Garuda's cookie to `.veeramanikanta.in` would hand every Drishti session to `garuda.` and `api.` as well. The user *accounts* are shared — same `users.json`, same people — only the sessions are separate.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_drishti_auth.py
import time
import pytest
from fastapi import HTTPException
from basic_pipelines import drishti_auth

pytestmark = pytest.mark.unit


class FakeRequest:
    def __init__(self, cookies=None):
        self.cookies = cookies or {}


def test_session_round_trips():
    token = drishti_auth.create_session("mani", "admin")
    session = drishti_auth.get_session(token)
    assert session["username"] == "mani"
    assert session["role"] == "admin"


def test_unknown_token_has_no_session():
    assert drishti_auth.get_session("nope") is None


def test_expired_session_is_not_returned():
    token = drishti_auth.create_session("mani", "user", duration=-1)
    assert drishti_auth.get_session(token) is None


def test_destroy_removes_the_session():
    token = drishti_auth.create_session("mani", "user")
    assert drishti_auth.destroy_session(token) is True
    assert drishti_auth.get_session(token) is None


def test_require_session_rejects_a_request_with_no_cookie():
    with pytest.raises(HTTPException) as exc:
        drishti_auth.require_drishti_session(FakeRequest())
    assert exc.value.status_code == 401


def test_require_session_accepts_a_valid_cookie():
    token = drishti_auth.create_session("mani", "user")
    request = FakeRequest({drishti_auth.COOKIE_NAME: token})
    assert drishti_auth.require_drishti_session(request)["username"] == "mani"


def test_require_admin_rejects_a_non_admin():
    token = drishti_auth.create_session("guest", "user")
    request = FakeRequest({drishti_auth.COOKIE_NAME: token})
    with pytest.raises(HTTPException) as exc:
        drishti_auth.require_drishti_admin(request)
    assert exc.value.status_code == 403


def test_a_garuda_cookie_does_not_authenticate_drishti():
    token = drishti_auth.create_session("mani", "admin")
    request = FakeRequest({"session": token})
    with pytest.raises(HTTPException):
        drishti_auth.require_drishti_session(request)


def test_prune_removes_only_expired_sessions():
    live = drishti_auth.create_session("a", "user")
    drishti_auth.create_session("b", "user", duration=-1)
    drishti_auth.prune_expired()
    assert drishti_auth.get_session(live) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_drishti_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'basic_pipelines.drishti_auth'`

- [ ] **Step 3: Write minimal implementation**

```python
# basic_pipelines/drishti_auth.py
"""Drishti's own sessions.

Accounts are shared with Garuda -- the same users.json, the same people --
but sessions and cookies are not. Widening Garuda's cookie to
.veeramanikanta.in would hand every Drishti session to garuda. and api. as
well, so Drishti issues its own host-scoped cookie instead.
"""
import secrets
import threading
import time

from fastapi import HTTPException, Request

COOKIE_NAME = "drishti_session"
DEFAULT_DURATION_S = 8 * 3600

_sessions = {}
_lock = threading.Lock()


def create_session(username, role, duration=None):
    token = secrets.token_urlsafe(32)
    expires = time.time() + (DEFAULT_DURATION_S if duration is None else duration)
    with _lock:
        _sessions[token] = {"username": username, "role": role, "expires": expires}
    return token


def get_session(token):
    if not token:
        return None
    with _lock:
        session = _sessions.get(token)
        if session is None:
            return None
        if session["expires"] < time.time():
            _sessions.pop(token, None)
            return None
        return dict(session)


def destroy_session(token):
    with _lock:
        return _sessions.pop(token, None) is not None


def prune_expired():
    now = time.time()
    with _lock:
        stale = [t for t, s in _sessions.items() if s["expires"] < now]
        for token in stale:
            _sessions.pop(token, None)
    return len(stale)


def invalidate_user(username):
    """Drop every session belonging to one account, after a password change."""
    with _lock:
        stale = [t for t, s in _sessions.items() if s["username"] == username]
        for token in stale:
            _sessions.pop(token, None)
    return len(stale)


def require_drishti_session(request: Request):
    session = get_session(request.cookies.get(COOKIE_NAME))
    if session is None:
        raise HTTPException(status_code=401, detail="not signed in")
    return session


def require_drishti_admin(request: Request):
    session = require_drishti_session(request)
    if session.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    return session
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_drishti_auth.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add basic_pipelines/drishti_auth.py tests/test_drishti_auth.py
git commit -m "feat(auth): Drishti sessions with their own host-scoped cookie"
```

---

### Task 13: Drishti API router

**Files:**
- Create: `basic_pipelines/drishti_api.py`
- Test: `tests/test_drishti_api.py`

**Interfaces:**
- Consumes: everything from Tasks 1–12
- Produces: `build_router(ctx) -> APIRouter` mounted at `/api/drishti`, and a `DrishtiContext` dataclass holding `registry`, `schema`, `store`, `pending`, `router` (device router), `matcher`, `nim`, `scene`, `log_path`, `synthesis_cache`

**Endpoints:**

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/login` | — | Issue a Drishti session |
| POST | `/logout` | session | Destroy it |
| GET | `/devices` | session | List registry entries with live state and availability |
| POST | `/devices` | admin | Add a device; rebuild schema; rebind store and scene |
| DELETE | `/devices/{id}` | admin | Remove; rebind; report how many rules were orphaned |
| GET | `/device-types` | session | The catalogue, for the onboarding form |
| POST | `/instruct` | session | The three lanes |
| GET | `/proposals` | session | Pending proposals |
| POST | `/proposals/{id}/confirm` | session | Store the rule |
| DELETE | `/proposals/{id}` | session | Discard |
| GET | `/rules` | session | Active and orphaned rules |
| DELETE | `/rules/{id}` | session | Delete a rule |
| POST | `/rules/{id}/toggle` | session | Enable or disable |
| GET | `/activity` | session | Recent actuations |

- [ ] **Step 1: Write the failing test**

```python
# tests/test_drishti_api.py
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from basic_pipelines import drishti_api, drishti_auth

pytestmark = pytest.mark.integration

LAMP = {"id": "lamp_desk", "name": "Desk lamp", "type": "light", "room": "study",
        "transport": {"kind": "relay", "channel": 1}}


@pytest.fixture
def client(tmp_path, monkeypatch):
    ctx = drishti_api.build_context(
        data_dir=str(tmp_path), relay_channels=(1, 2, 3), channel_to_pin={1: 17, 2: 27, 3: 22})
    app = FastAPI()
    app.include_router(drishti_api.build_router(ctx))
    test_client = TestClient(app)
    token = drishti_auth.create_session("mani", "admin")
    test_client.cookies.set(drishti_auth.COOKIE_NAME, token)
    return test_client, ctx


def test_device_types_are_listed(client):
    test_client, _ = client
    body = test_client.get("/api/drishti/device-types").json()
    assert "light" in body["types"]
    assert body["types"]["light"]["actions"] == ["on", "off"]


def test_adding_a_device_widens_the_vocabulary(client):
    test_client, ctx = client
    assert test_client.post("/api/drishti/devices", json=LAMP).status_code == 200
    assert "lamp_desk_state" in ctx.schema.fields
    assert "lamp_desk" in ctx.schema.devices


def test_adding_a_device_with_a_bad_channel_is_refused(client):
    test_client, _ = client
    response = test_client.post("/api/drishti/devices",
                                json={**LAMP, "transport": {"kind": "relay", "channel": 99}})
    assert response.status_code == 400
    assert "channel" in response.json()["detail"]


def test_deleting_a_device_reports_orphaned_rules(client):
    test_client, ctx = client
    test_client.post("/api/drishti/devices", json=LAMP)
    ctx.store.add({
        "source_utterance": "turn the desk lamp off when the room is empty",
        "when": {"all": [{"field": "occupancy", "op": "==", "value": "empty"}]},
        "then": [{"device": "lamp_desk", "action": "off"}],
    })
    body = test_client.delete("/api/drishti/devices/lamp_desk").json()
    assert body["orphaned"] == 1
    assert ctx.store.rules == []
    assert len(ctx.store.orphaned) == 1


def test_instruct_answers_a_state_question_locally(client):
    test_client, _ = client
    body = test_client.post("/api/drishti/instruct",
                            json={"text": "is anyone home?"}).json()
    assert body["lane"] == "local"
    assert body["resolved"] == "on-device"


def test_instruct_reports_an_already_known_rule(client):
    test_client, ctx = client
    test_client.post("/api/drishti/devices", json=LAMP)
    ctx.store.add({
        "source_utterance": "turn the desk lamp off when the room is empty",
        "when": {"all": [{"field": "occupancy", "op": "==", "value": "empty"}]},
        "then": [{"device": "lamp_desk", "action": "off"}],
    })
    body = test_client.post(
        "/api/drishti/instruct",
        json={"text": "when the room is empty turn the desk lamp off"}).json()
    assert body["lane"] == "known"
    assert body["rule"]["source_utterance"].startswith("turn the desk lamp off")


def test_a_known_hit_is_logged_with_score_and_backend(client):
    test_client, ctx = client
    test_client.post("/api/drishti/devices", json=LAMP)
    ctx.store.add({
        "source_utterance": "turn the desk lamp off when the room is empty",
        "when": {"all": [{"field": "occupancy", "op": "==", "value": "empty"}]},
        "then": [{"device": "lamp_desk", "action": "off"}],
    })
    test_client.post("/api/drishti/instruct",
                     json={"text": "when the room is empty turn the desk lamp off"})
    assert ctx.suppression_log
    entry = ctx.suppression_log[-1]
    assert entry["backend"] in ("fuzzy", "embed")
    assert 0.0 <= entry["score"] <= 1.0


def test_compile_failure_says_existing_rules_still_fire(client):
    test_client, _ = client
    body = test_client.post("/api/drishti/instruct",
                            json={"text": "dim the hallway when it rains"}).json()
    assert body["lane"] == "compile"
    assert body["ok"] is False
    assert body["still_working"] is True


def test_confirming_a_proposal_stores_the_rule(client):
    test_client, ctx = client
    test_client.post("/api/drishti/devices", json=LAMP)
    pid = ctx.pending.add({
        "source_utterance": "turn the desk lamp off when the room is empty",
        "when": {"all": [{"field": "occupancy", "op": "==", "value": "empty"}]},
        "then": [{"device": "lamp_desk", "action": "off"}],
    })
    assert test_client.post(f"/api/drishti/proposals/{pid}/confirm").status_code == 200
    assert len(ctx.store.rules) == 1
    assert ctx.pending.get(pid) is None


def test_discarding_a_proposal_stores_nothing(client):
    test_client, ctx = client
    pid = ctx.pending.add({"source_utterance": "x", "when": {"all": []}, "then": []})
    assert test_client.delete(f"/api/drishti/proposals/{pid}").status_code == 200
    assert ctx.store.rules == []


def test_every_endpoint_requires_a_session(tmp_path):
    ctx = drishti_api.build_context(
        data_dir=str(tmp_path), relay_channels=(1,), channel_to_pin={1: 17})
    app = FastAPI()
    app.include_router(drishti_api.build_router(ctx))
    anonymous = TestClient(app)
    for method, path in [("get", "/api/drishti/devices"),
                         ("get", "/api/drishti/rules"),
                         ("get", "/api/drishti/activity"),
                         ("post", "/api/drishti/instruct")]:
        response = getattr(anonymous, method)(path, json={"text": "hi"})
        assert response.status_code == 401, path
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_drishti_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'basic_pipelines.drishti_api'`

- [ ] **Step 3: Write minimal implementation**

```python
# basic_pipelines/drishti_api.py
"""The Drishti API.

Kept out of Garuda_web.py, which is already ~3,800 lines. Everything the
router needs travels in a context object so tests can build one over a
temporary directory with no hardware present.
"""
import hashlib
import os
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .drishti_auth import (COOKIE_NAME, create_session, destroy_session,
                           require_drishti_admin, require_drishti_session)
from .garuda_auto import actuation_log
from .garuda_auto.actuators import RelayBank
from .garuda_auto.device_registry import DeviceRegistry
from .garuda_auto.device_types import TYPES, actions_for
from .garuda_auto.local_lane import answer as local_answer
from .garuda_auto.matcher import LocalMatcher
from .garuda_auto.nim_client import NimClient
from .garuda_auto.pending_store import PendingStore
from .garuda_auto.rule_schema import build_schema
from .garuda_auto.rule_store import RuleStore
from .garuda_auto.transports import DeviceRouter, MqttBank


@dataclass
class DrishtiContext:
    registry: DeviceRegistry
    schema: object
    store: RuleStore
    pending: PendingStore
    device_router: DeviceRouter
    matcher: LocalMatcher
    nim: NimClient
    log_path: str
    channel_to_pin: dict
    relay_bank: RelayBank
    mqtt_bank: MqttBank
    descriptor: dict = field(default_factory=dict)
    synthesis_cache: dict = field(default_factory=dict)
    suppression_log: list = field(default_factory=list)

    def rebuild(self):
        """Re-derive everything that depends on the device registry."""
        self.schema = build_schema(self.registry)
        self.store.rebind(self.schema)
        self.relay_bank.close()
        self.relay_bank = RelayBank({
            d["id"]: self.channel_to_pin[d["transport"]["channel"]]
            for d in self.registry.actuators()
            if d["transport"]["kind"] == "relay"
        })
        self.mqtt_bank.bind(self.registry)
        self.device_router = DeviceRouter(self.registry, self.relay_bank, self.mqtt_bank)


def build_context(*, data_dir, relay_channels, channel_to_pin,
                  mqtt_host="localhost", nim_key="", nim_model="",
                  matcher_backend="fuzzy"):
    registry = DeviceRegistry(os.path.join(data_dir, "devices.json"), relay_channels)
    schema = build_schema(registry)
    store = RuleStore(os.path.join(data_dir, "rules.json"), schema)
    pending = PendingStore(os.path.join(data_dir, "pending.json"))
    relay_bank = RelayBank({
        d["id"]: channel_to_pin[d["transport"]["channel"]]
        for d in registry.actuators() if d["transport"]["kind"] == "relay"
    })
    mqtt_bank = MqttBank(mqtt_host)
    mqtt_bank.bind(registry)
    return DrishtiContext(
        registry=registry, schema=schema, store=store, pending=pending,
        device_router=DeviceRouter(registry, relay_bank, mqtt_bank),
        matcher=LocalMatcher(store, backend=matcher_backend),
        nim=NimClient(nim_key, nim_model),
        log_path=os.path.join(data_dir, "actuations.jsonl"),
        channel_to_pin=dict(channel_to_pin),
        relay_bank=relay_bank, mqtt_bank=mqtt_bank,
    )


class LoginRequest(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class InstructRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class DeviceRequest(BaseModel):
    id: str = Field(max_length=32)
    name: str = Field(max_length=64)
    type: str = Field(max_length=32)
    room: str = Field(max_length=64)
    transport: dict


def _render(rule):
    """Plain-language rendering of a rule, for the card."""
    combinator, conditions = next(iter(rule["when"].items()))
    joiner = " and " if combinator == "all" else " or "
    when = joiner.join(f"{c['field']} {c['op']} {c['value']}" for c in conditions)
    then = ", ".join(f"{a['device']} → {a['action']}" for a in rule["then"])
    return {"when": when, "then": then}


def build_router(ctx):
    router = APIRouter(prefix="/api/drishti")

    @router.post("/login")
    async def login(data: LoginRequest, response: Response):
        from .Garuda_web import USERS, _verify_password
        user = USERS.get(data.username)
        if user is None or not _verify_password(data.password, user["password"]):
            raise HTTPException(status_code=401, detail="invalid credentials")
        token = create_session(data.username, user.get("role", "user"))
        response.set_cookie(COOKIE_NAME, token, httponly=True,
                            samesite="lax", secure=True, path="/")
        return {"ok": True, "username": data.username, "role": user.get("role", "user")}

    @router.post("/logout")
    async def logout(request: Request, response: Response,
                     session=Depends(require_drishti_session)):
        destroy_session(request.cookies.get(COOKIE_NAME))
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"ok": True}

    @router.get("/device-types")
    async def device_types(session=Depends(require_drishti_session)):
        return {"types": {name: {"actions": sorted(actions_for(name)),
                                 "state": spec["state"]}
                          for name, spec in TYPES.items()}}

    @router.get("/devices")
    async def list_devices(session=Depends(require_drishti_session)):
        return {"devices": [
            {**d,
             "state": ctx.device_router.state(d["id"]),
             "available": ctx.device_router.available(d["id"])}
            for d in ctx.registry.devices
        ]}

    @router.post("/devices")
    async def add_device(data: DeviceRequest, session=Depends(require_drishti_admin)):
        ok, reason = ctx.registry.add(data.model_dump())
        if not ok:
            raise HTTPException(status_code=400, detail=reason)
        ctx.rebuild()
        return {"ok": True, "id": data.id}

    @router.delete("/devices/{device_id}")
    async def delete_device(device_id: str, session=Depends(require_drishti_admin)):
        if not ctx.registry.delete(device_id):
            raise HTTPException(status_code=404, detail=f"unknown device: {device_id}")
        before = len(ctx.store.orphaned)
        ctx.rebuild()
        return {"ok": True, "orphaned": len(ctx.store.orphaned) - before}

    @router.post("/instruct")
    async def instruct(data: InstructRequest, session=Depends(require_drishti_session)):
        text = data.text.strip()

        local = local_answer(text, registry=ctx.registry, descriptor=ctx.descriptor,
                             router=ctx.device_router, log_path=ctx.log_path,
                             store=ctx.store)
        if local is not None:
            return {"lane": "local", "ok": True, **local}

        hit = ctx.matcher.match(text)
        if hit is not None:
            ctx.suppression_log.append({
                "utterance": text,
                "rule_id": hit.get("id", ""),
                "score": ctx.matcher._score(text, hit.get("source_utterance", "")),
                "backend": ctx.matcher.backend_name,
            })
            return {"lane": "known", "ok": True, "resolved": "already-known",
                    "rule": hit, "rendered": _render(hit)}

        key = hashlib.sha256(text.lower().encode()).hexdigest()
        cached = ctx.synthesis_cache.get(key)
        if cached is not None:
            rule, reason = cached, ""
        else:
            rule, reason = await ctx.nim.synthesize_async(
                text, ctx.store.rules, ctx.schema)
            if rule is not None:
                ctx.synthesis_cache[key] = rule

        if rule is None:
            return {"lane": "compile", "ok": False, "resolved": "compiled",
                    "reason": reason, "still_working": True,
                    "vocabulary": sorted(ctx.schema.fields)}

        conflict = ctx.store.find_conflict(rule)
        proposal_id = ctx.pending.add(rule, conflict=conflict)
        return {"lane": "compile", "ok": True, "resolved": "compiled",
                "proposal_id": proposal_id, "rule": rule,
                "rendered": _render(rule), "conflict": conflict}

    @router.get("/proposals")
    async def list_proposals(session=Depends(require_drishti_session)):
        return {"proposals": [{**p, "rendered": _render(p["rule"])}
                              for p in ctx.pending.all()]}

    @router.post("/proposals/{proposal_id}/confirm")
    async def confirm(proposal_id: str, session=Depends(require_drishti_session)):
        item = ctx.pending.get(proposal_id)
        if item is None:
            raise HTTPException(status_code=404, detail="no such proposal")
        ok, reason = ctx.store.add(item["rule"])
        if not ok:
            raise HTTPException(status_code=400, detail=reason)
        ctx.pending.pop(proposal_id)
        return {"ok": True}

    @router.delete("/proposals/{proposal_id}")
    async def discard(proposal_id: str, session=Depends(require_drishti_session)):
        ctx.pending.pop(proposal_id)
        return {"ok": True}

    @router.get("/rules")
    async def list_rules(session=Depends(require_drishti_session)):
        return {
            "rules": [{**r, "rendered": _render(r)} for r in ctx.store.rules],
            "orphaned": ctx.store.orphaned,
        }

    @router.delete("/rules/{rule_id}")
    async def delete_rule(rule_id: str, session=Depends(require_drishti_session)):
        if not ctx.store.delete(rule_id):
            raise HTTPException(status_code=404, detail="no such rule")
        return {"ok": True}

    @router.post("/rules/{rule_id}/toggle")
    async def toggle_rule(rule_id: str, session=Depends(require_drishti_session)):
        for rule in ctx.store.rules:
            if rule.get("id") == rule_id:
                rule["enabled"] = not rule.get("enabled", True)
                ctx.store.save()
                return {"ok": True, "enabled": rule["enabled"]}
        raise HTTPException(status_code=404, detail="no such rule")

    @router.get("/activity")
    async def activity(limit: int = 200, session=Depends(require_drishti_session)):
        return {"entries": actuation_log.recent(ctx.log_path, limit=min(limit, 500))}

    return router
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_drishti_api.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add basic_pipelines/drishti_api.py tests/test_drishti_api.py
git commit -m "feat(api): Drishti router with three-lane instruct and device CRUD"
```

---

### Task 14: Wire the router into the application

**Files:**
- Modify: `basic_pipelines/Garuda_web.py`
- Test: `tests/test_drishti_wiring.py`

**Interfaces:**
- Consumes: `drishti_api.build_context`, `drishti_api.build_router`
- Produces: `DRISHTI_CTX` module global on `Garuda_web`; `/api/drishti/*` served by the running app

- [ ] **Step 1: Write the failing test**

```python
# tests/test_drishti_wiring.py
import pytest

pytestmark = pytest.mark.integration


def test_relay_channel_map_covers_every_configured_channel():
    from basic_pipelines import Garuda_web
    assert set(Garuda_web.RELAY_CHANNELS) <= set(Garuda_web.CHANNEL_TO_PIN)


def test_drishti_routes_are_mounted():
    from basic_pipelines import Garuda_web
    paths = {route.path for route in Garuda_web.app.routes}
    for path in ("/api/drishti/login", "/api/drishti/instruct",
                 "/api/drishti/devices", "/api/drishti/rules"):
        assert path in paths


def test_drishti_context_is_built_at_import():
    from basic_pipelines import Garuda_web
    assert Garuda_web.DRISHTI_CTX is not None
    assert Garuda_web.DRISHTI_CTX.registry is not None


def test_scene_builder_feeds_the_drishti_descriptor():
    from basic_pipelines import Garuda_web
    assert isinstance(Garuda_web.DRISHTI_CTX.descriptor, dict)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_drishti_wiring.py -v`
Expected: FAIL with `AttributeError: module 'basic_pipelines.Garuda_web' has no attribute 'RELAY_CHANNELS'`

- [ ] **Step 3: Write minimal implementation**

Add near the other path constants in `Garuda_web.py` (around line 170):

```python
DRISHTI_DATA_DIR = str(_BASE / "system_logs")

# The Pi's 8-channel opto-isolated relay board. Only these channels may be
# assigned to a device; a user never enters a BCM pin, because a wrong one
# would drive a pin the Hailo HAT, camera or I2C bus is using.
RELAY_CHANNELS = (1, 2, 3, 4, 5, 6, 7)
CHANNEL_TO_PIN = {1: 17, 2: 27, 3: 22, 4: 5, 5: 6, 6: 13, 7: 19}
```

Then after the FastAPI `app` is created and before `app.mount("/static", ...)` at line 2349:

```python
from .drishti_api import build_context as _build_drishti_context, build_router as _build_drishti_router

DRISHTI_CTX = _build_drishti_context(
    data_dir=DRISHTI_DATA_DIR,
    relay_channels=RELAY_CHANNELS,
    channel_to_pin=CHANNEL_TO_PIN,
    mqtt_host=os.environ.get("DRISHTI_MQTT_HOST", "localhost"),
    nim_key=os.environ.get("NIM_API_KEY", ""),
    nim_model=os.environ.get("NIM_MODEL", ""),
    matcher_backend=os.environ.get("DRISHTI_MATCHER", "fuzzy"),
)
app.include_router(_build_drishti_router(DRISHTI_CTX))
```

In the detection callback where the scene descriptor is produced, publish it and mirror device state:

```python
    DRISHTI_CTX.descriptor = descriptor
```

In `_lifespan`, add a periodic `DRISHTI_CTX.pending.purge()` alongside `_prune_expired_sessions`, and call `drishti_auth.prune_expired()` there too.

Confirm the pin numbers against the Pi's wiring before this commit. `CHANNEL_TO_PIN` is the one value in this plan that cannot be verified from the Mac.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_drishti_wiring.py -v`
Expected: 4 passed

- [ ] **Step 5: Run the whole suite and commit**

```bash
python3 -m pytest tests -q
git add basic_pipelines/Garuda_web.py tests/test_drishti_wiring.py
git commit -m "feat(api): mount the Drishti router and build its context at startup"
```

---

### Task 15: Seed the registry so the evaluation corpus still runs

**Files:**
- Create: `scripts/seed_drishti_devices.py`
- Test: `tests/garuda_auto/test_seed_devices.py`

**Interfaces:**
- Consumes: `DeviceRegistry`
- Produces: `seed(registry) -> list[str]` returning the ids it created

The 30-request evaluation corpus in `evaluation/narada_rs/corpus.py` refers to `lamp` and `fan`. Those were built-ins; they are now registry entries, so a fresh install has neither and every corpus entry fails to compile. Seeding restores the baseline without special-casing anything in the schema.

- [ ] **Step 1: Write the failing test**

```python
# tests/garuda_auto/test_seed_devices.py
import pytest
from basic_pipelines.garuda_auto.device_registry import DeviceRegistry
from basic_pipelines.garuda_auto.rule_schema import build_schema
from scripts.seed_drishti_devices import seed

pytestmark = pytest.mark.unit


def test_seed_creates_lamp_and_fan(tmp_path):
    registry = DeviceRegistry(str(tmp_path / "devices.json"), relay_channels=(1, 2, 3))
    assert sorted(seed(registry)) == ["fan", "lamp"]
    schema = build_schema(registry)
    assert "lamp" in schema.devices and "fan" in schema.devices
    assert "lamp_state" in schema.fields and "fan_state" in schema.fields


def test_seed_is_idempotent(tmp_path):
    registry = DeviceRegistry(str(tmp_path / "devices.json"), relay_channels=(1, 2, 3))
    seed(registry)
    assert seed(registry) == []
    assert len(registry.devices) == 2
```

Note the field names: seeding a device with id `lamp` produces the field `lamp_state`, which is exactly what the pre-existing corpus and rules already reference. Nothing in the corpus changes.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/garuda_auto/test_seed_devices.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.seed_drishti_devices'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/seed_drishti_devices.py
"""Create the two devices the evaluation corpus assumes.

lamp and fan used to be hardcoded in rule_schema.DEVICES. They are registry
entries now, so a fresh install has neither and every corpus request fails to
compile. Seeding restores the baseline without special-casing the schema.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from basic_pipelines.garuda_auto.device_registry import DeviceRegistry  # noqa: E402

SEEDS = [
    {"id": "lamp", "name": "Lamp", "type": "light", "room": "study",
     "transport": {"kind": "relay", "channel": 1}},
    {"id": "fan", "name": "Fan", "type": "fan", "room": "study",
     "transport": {"kind": "relay", "channel": 2}},
]


def seed(registry):
    created = []
    for entry in SEEDS:
        if registry.get(entry["id"]) is not None:
            continue
        ok, reason = registry.add(entry)
        if not ok:
            raise SystemExit(f"could not seed {entry['id']}: {reason}")
        created.append(entry["id"])
    return created


if __name__ == "__main__":
    from basic_pipelines.Garuda_web import (CHANNEL_TO_PIN, DRISHTI_DATA_DIR,
                                            RELAY_CHANNELS)
    registry = DeviceRegistry(str(Path(DRISHTI_DATA_DIR) / "devices.json"),
                              relay_channels=RELAY_CHANNELS)
    created = seed(registry)
    print(f"seeded: {created}" if created else "nothing to seed")
```

Add an empty `scripts/__init__.py` so the test can import it.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/garuda_auto/test_seed_devices.py -v`
Expected: 2 passed

- [ ] **Step 5: Run the whole suite and commit**

```bash
python3 -m pytest tests -q
git add scripts/seed_drishti_devices.py scripts/__init__.py tests/garuda_auto/test_seed_devices.py
git commit -m "feat(devices): seed lamp and fan so the evaluation corpus still compiles"
```

---

## Verification on the Pi

Everything above runs on the Mac. Three things can only be checked on the Pi, and none of them is optional before this is called done:

1. **`CHANNEL_TO_PIN` matches the wiring.** Add a device on channel 1, toggle it from `/api/drishti/devices`, and confirm the intended relay clicks — not a different one.
2. **`gpiozero` drives the bank.** On the Mac `RelayBank` runs in no-op mode, so every relay test so far has proven only bookkeeping.
3. **The blocking-call fix holds under load.** Open the MJPEG stream, issue an instruction that reaches the compile lane, and confirm the stream does not stall for the duration of the NIM call. This is the regression the Task 7 change exists to prevent, and it cannot be observed without the camera.

---

## Self-Review

**Spec coverage.** §5 device registry → Tasks 1, 2, 3. §5.4 pin indirection → Task 2 and Task 14. §5.4 orphaning → Task 5. §5.4 unreachable devices → Task 8. §6 request path → Tasks 10, 11, 13. §6.1 proposals → Tasks 11, 13. §6.2 failure surfaces → Task 13. §6.3 resolution marker → Tasks 10, 13. §8.1 signature changes → Tasks 4, 5, 6, 7. §8.2 both defects → Tasks 5, 7. §11 testing → every task. Authentication decision → Task 12.

**Not covered here, by design.** §4.1 Tasks and Routines are deferred to sub-project 6 along with the tool registry. §7 information architecture is the frontend plan. §9 DNS, TLS and tunnel are sub-project 7. Proactive rule suggestion (§4.5) is deferred; it is flagged as excluded from any published evaluation run.

**Gap found and closed during review.** The spec did not account for `evaluation/narada_rs/corpus.py` referencing `lamp` and `fan`, which stop being built-ins the moment the schema comes from the registry. Task 15 was added; without it a fresh install scores zero on the corpus for a reason that has nothing to do with the architecture.

**Type consistency.** `validate_rule(rule, schema)` is used with two arguments in Tasks 4, 5 and 7. `state_field(device_id)` is defined in Task 3 and used in Tasks 6 and 10. `RelayBank.set/state` and `MqttBank.set/state` share a signature so `DeviceRouter` can dispatch without branching on more than transport kind. `DeviceRegistry.get` returns `None` for a missing device and is checked for `None` at every call site.
