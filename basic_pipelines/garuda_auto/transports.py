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
