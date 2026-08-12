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
