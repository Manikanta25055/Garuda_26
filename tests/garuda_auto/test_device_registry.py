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
