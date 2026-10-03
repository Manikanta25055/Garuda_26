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
