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
