import pytest
from basic_pipelines.garuda_auto.device_registry import DeviceRegistry
from basic_pipelines.garuda_auto.rule_schema import build_schema
from basic_pipelines.garuda_auto.scene_state import SceneBuilder

ZONES = {"desk": (0.0, 0.0, 0.33, 1.0), "door": (0.67, 0.0, 1.0, 1.0), "center": (0.33, 0.0, 0.67, 1.0)}

LAMP = {"id": "lamp", "name": "Lamp", "type": "light", "room": "study",
        "transport": {"kind": "relay", "channel": 1}}
FAN = {"id": "fan", "name": "Fan", "type": "fan", "room": "study",
       "transport": {"kind": "relay", "channel": 2}}


@pytest.fixture
def registry(tmp_path):
    r = DeviceRegistry(str(tmp_path / "devices.json"), relay_channels=(1, 2, 3))
    for entry in (LAMP, FAN):
        ok, reason = r.add(entry)
        assert ok, reason
    return r


@pytest.fixture
def empty_registry(tmp_path):
    return DeviceRegistry(str(tmp_path / "devices.json"), relay_channels=(1, 2, 3))


def _person(x0, x1, posture="standing"):
    return {"label": "person", "confidence": 0.9, "bbox": (x0, 0.2, x1, 0.9), "posture": posture}


# ── behaviour carried over ──────────────────────────────────────────────────

def test_descriptor_covers_every_declared_field(registry):
    clock = iter([100.0, 100.0])
    b = SceneBuilder(ZONES, registry, clock=lambda: next(clock))
    d = b.update([], luma=100, temperature_c=28.0, humidity_pct=50.0, hour=14)
    assert set(d) == set(build_schema(registry).fields)


def test_empty_room_reports_empty(registry):
    b = SceneBuilder(ZONES, registry, clock=lambda: 100.0)
    d = b.update([], luma=100, temperature_c=28.0, humidity_pct=50.0, hour=14)
    assert d["occupancy"] == "empty"
    assert d["person_count"] == 0
    assert d["zone"] == "none"
    assert d["posture"] == "none"


def test_person_sets_occupied_and_zone_from_centroid(registry):
    b = SceneBuilder(ZONES, registry, clock=lambda: 100.0)
    d = b.update([_person(0.05, 0.20, "seated")], luma=40, temperature_c=30.0, humidity_pct=55.0, hour=21)
    assert d["occupancy"] == "occupied"
    assert d["person_count"] == 1
    assert d["zone"] == "desk"
    assert d["posture"] == "seated"


def test_duration_accumulates_while_state_holds(registry):
    ticks = iter([100.0, 130.0, 160.0])
    b = SceneBuilder(ZONES, registry, clock=lambda: next(ticks))
    b.update([], luma=10, temperature_c=25.0, humidity_pct=40.0, hour=3)
    b.update([], luma=10, temperature_c=25.0, humidity_pct=40.0, hour=3)
    d = b.update([], luma=10, temperature_c=25.0, humidity_pct=40.0, hour=3)
    assert d["occupancy_duration_s"] == 60


def test_duration_resets_when_state_flips(registry):
    ticks = iter([100.0, 160.0, 170.0])
    b = SceneBuilder(ZONES, registry, clock=lambda: next(ticks))
    b.update([], luma=10, temperature_c=25.0, humidity_pct=40.0, hour=3)
    b.update([], luma=10, temperature_c=25.0, humidity_pct=40.0, hour=3)
    d = b.update([_person(0.4, 0.5)], luma=10, temperature_c=25.0, humidity_pct=40.0, hour=3)
    assert d["occupancy"] == "occupied"
    assert d["occupancy_duration_s"] == 0


def test_non_person_labels_do_not_create_occupancy(registry):
    b = SceneBuilder(ZONES, registry, clock=lambda: 100.0)
    knife = {"label": "knife", "confidence": 0.8, "bbox": (0.4, 0.4, 0.5, 0.5), "posture": "none"}
    d = b.update([knife], luma=100, temperature_c=28.0, humidity_pct=50.0, hour=14)
    assert d["occupancy"] == "empty"
    assert d["person_count"] == 0


def test_device_states_reflect_setter(registry):
    b = SceneBuilder(ZONES, registry, clock=lambda: 100.0)
    b.set_device_state("lamp", "on")
    d = b.update([], luma=100, temperature_c=28.0, humidity_pct=50.0, hour=14)
    assert d["lamp_state"] == "on"
    assert d["fan_state"] == "off"


def test_values_are_clamped_into_schema_range(registry):
    b = SceneBuilder(ZONES, registry, clock=lambda: 100.0)
    d = b.update([], luma=9999, temperature_c=-99.0, humidity_pct=250.0, hour=14)
    assert d["ambient_luma"] == 255
    assert d["temperature_c"] == -10
    assert d["humidity_pct"] == 100


# ── registry-driven device state ────────────────────────────────────────────

def test_descriptor_carries_a_field_per_registered_device(tmp_path):
    r = DeviceRegistry(str(tmp_path / "devices.json"), relay_channels=(1, 2, 3))
    r.add({"id": "lamp_desk", "name": "Desk lamp", "type": "light", "room": "study",
           "transport": {"kind": "relay", "channel": 1}})
    r.add({"id": "porch", "name": "Porch door", "type": "sensor.contact", "room": "porch",
           "transport": {"kind": "mqtt", "topic_base": "drishti/porch"}})
    builder = SceneBuilder(ZONES, r, clock=lambda: 100.0)
    descriptor = builder.update([], luma=100, temperature_c=22.0, humidity_pct=50.0, hour=12)
    assert descriptor["lamp_desk_state"] == "off"
    assert descriptor["porch_state"] == "closed"


def test_descriptor_has_no_hardcoded_lamp_or_fan(empty_registry):
    descriptor = SceneBuilder(ZONES, empty_registry, clock=lambda: 100.0).update(
        [], luma=100, temperature_c=22.0, humidity_pct=50.0, hour=12)
    assert "lamp_state" not in descriptor
    assert "fan_state" not in descriptor


def test_set_device_state_refuses_a_value_outside_the_type(registry):
    builder = SceneBuilder(ZONES, registry, clock=lambda: 100.0)
    builder.set_device_state("lamp", "sideways")
    descriptor = builder.update([], luma=100, temperature_c=22.0, humidity_pct=50.0, hour=12)
    assert descriptor["lamp_state"] == "off"


def test_numeric_sensor_state_is_clamped_to_its_bounds(tmp_path):
    r = DeviceRegistry(str(tmp_path / "devices.json"), relay_channels=(1,))
    r.add({"id": "probe", "name": "Probe", "type": "sensor.temperature", "room": "loft",
           "transport": {"kind": "mqtt", "topic_base": "drishti/probe"}})
    builder = SceneBuilder(ZONES, r, clock=lambda: 100.0)
    builder.set_device_state("probe", 900)
    descriptor = builder.update([], luma=100, temperature_c=22.0, humidity_pct=50.0, hour=12)
    assert descriptor["probe_state"] == 60


def test_rebind_picks_up_a_newly_added_device(registry):
    builder = SceneBuilder(ZONES, registry, clock=lambda: 100.0)
    registry.add({"id": "heater", "name": "Heater", "type": "switch", "room": "loft",
                  "transport": {"kind": "relay", "channel": 3}})
    builder.rebind()
    descriptor = builder.update([], luma=100, temperature_c=22.0, humidity_pct=50.0, hour=12)
    assert descriptor["heater_state"] == "off"


def test_rebind_preserves_a_known_state(registry):
    builder = SceneBuilder(ZONES, registry, clock=lambda: 100.0)
    builder.set_device_state("lamp", "on")
    builder.rebind()
    descriptor = builder.update([], luma=100, temperature_c=22.0, humidity_pct=50.0, hour=12)
    assert descriptor["lamp_state"] == "on"
