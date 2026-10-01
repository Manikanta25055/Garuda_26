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
            self._wire(self._client)
        elif MQTT_AVAILABLE:
            try:
                # paho-mqtt 2.x wants the callback API named; 1.x has no such
                # argument. Built inside the try: a constructor that raised
                # here took the whole web service down at import.
                version = getattr(paho, "CallbackAPIVersion", None)
                self._client = paho.Client(version.VERSION1) if version else paho.Client()
                self._wire(self._client)
                # Non-blocking, with paho's own retry loop: a broker that is
                # down at start-up is picked up when it comes back instead of
                # leaving MQTT devices dead until the service is restarted.
                self._client.connect_async(broker_host, broker_port, keepalive=60)
                self._client.loop_start()
            except Exception as exc:
                log.warning("MQTT connect failed: %s", exc)
                self._client = None
        else:
            log.warning("paho-mqtt unavailable -- MQTT devices will be unreachable")

    def _wire(self, client):
        """Hear what devices report, and ask again after every reconnect.

        Topics were subscribed but nothing was listening, so on_state() was
        never called: an MQTT device stayed "unavailable" for ever, which also
        kept it out of "all off" and the vacation lights. Subscriptions do not
        survive a reconnect either, so they are renewed in on_connect.
        """
        try:
            client.on_message = self._on_message
            client.on_connect = self._on_connect
        except Exception:
            pass

    def _on_connect(self, client, userdata, flags, rc, *extra):
        for topic in list(self._topics.values()):
            try:
                client.subscribe(f"{topic}/state")
            except Exception as exc:
                log.warning("MQTT subscribe failed for %s: %s", topic, exc)

    def _on_message(self, client, userdata, message):
        try:
            topic = str(message.topic)
            payload = message.payload.decode("utf-8", "replace").strip().lower()[:64]
        except Exception:
            return
        for device_id, base in list(self._topics.items()):
            if topic == f"{base}/state":
                value = payload
                try:
                    value = float(payload)        # a sensor reading
                except ValueError:
                    pass
                self.on_state(device_id, value)
                return

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
        # A command sent while the broker is away goes nowhere; say so rather
        # than record the device as switched.
        connected = getattr(self._client, "is_connected", None)
        if callable(connected) and not connected():
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
