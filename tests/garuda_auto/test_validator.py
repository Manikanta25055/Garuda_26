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
