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


def test_a_disabled_device_is_refused(tmp_path):
    registry = DeviceRegistry(str(tmp_path / "devices.json"), relay_channels=(1, 2, 3))
    registry.add({**LAMP, "enabled": False})
    mqtt = MqttBank("localhost", client_factory=lambda: FakeMqttClient())
    mqtt.bind(registry)
    router = DeviceRouter(registry, RelayBank({"lamp_desk": 17}), mqtt)
    ok, reason = router.set("lamp_desk", "on")
    assert ok is False and "lamp_desk" in reason
