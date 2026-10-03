import pytest

from basic_pipelines.garuda_auto.device_registry import DeviceRegistry
from basic_pipelines.garuda_auto.rule_schema import build_schema
from scripts.seed_drishti_devices import SEEDS, seed

pytestmark = pytest.mark.unit


def _registry(tmp_path):
    return DeviceRegistry(str(tmp_path / "devices.json"), relay_channels=(1, 2, 3))


def test_seed_creates_lamp_and_fan(tmp_path):
    registry = _registry(tmp_path)
    assert sorted(seed(registry)) == ["fan", "lamp"]
    schema = build_schema(registry)
    assert "lamp" in schema.devices and "fan" in schema.devices
    assert "lamp_state" in schema.fields and "fan_state" in schema.fields


def test_seed_is_idempotent(tmp_path):
    registry = _registry(tmp_path)
    seed(registry)
    assert seed(registry) == []
    assert len(registry.devices) == 2


def test_seed_persists_across_reload(tmp_path):
    seed(_registry(tmp_path))
    assert sorted(d["id"] for d in _registry(tmp_path).devices) == ["fan", "lamp"]


def test_seed_does_not_mutate_the_module_constant(tmp_path):
    before = [dict(e) for e in SEEDS]
    seed(_registry(tmp_path))
    assert SEEDS == before


def test_seed_channels_are_deployment_channels():
    from basic_pipelines.drishti_config import RELAY_CHANNELS
    for entry in SEEDS:
        assert entry["transport"]["channel"] in RELAY_CHANNELS


def test_seeded_devices_are_actuators(tmp_path):
    from basic_pipelines.garuda_auto.device_types import is_actuator
    registry = _registry(tmp_path)
    seed(registry)
    assert {d["id"] for d in registry.actuators()} == {"lamp", "fan"}
    assert all(is_actuator(e["type"]) for e in SEEDS)
