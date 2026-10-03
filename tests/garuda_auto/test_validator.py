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
