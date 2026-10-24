"""The home-automation context: devices, rules, relays and MQTT in one object.

Garuda_web builds one (DRISHTI_CTX) and hands it to the home routes, the rule
loop and Narada; tests build one over a temporary directory with no hardware
present. The standalone Drishti app's own API (/api/drishti) and sign-in lived
here too until they were removed on 2026-10-24; its features are Garuda's
Home / Automations / Insights pages.
"""
import os

from dataclasses import dataclass, field

from .garuda_auto.actuators import RelayBank
from .garuda_auto.device_registry import DeviceRegistry
from .garuda_auto.matcher import LocalMatcher
from .garuda_auto.nim_client import NimClient
from .garuda_auto.pending_store import PendingStore
from .garuda_auto.rule_schema import build_schema
from .garuda_auto.rule_store import RuleStore
from .garuda_auto.transports import DeviceRouter, MqttBank


@dataclass
class DrishtiContext:
    registry: DeviceRegistry
    schema: object
    store: RuleStore
    pending: PendingStore
    device_router: DeviceRouter
    matcher: LocalMatcher
    nim: NimClient
    log_path: str
    channel_to_pin: dict
    relay_bank: RelayBank
    mqtt_bank: MqttBank
    descriptor: dict = field(default_factory=dict)
    synthesis_cache: dict = field(default_factory=dict)
    suppression_log: list = field(default_factory=list)

    # Called after the device registry changes, so anything holding derived
    # state (the scene builder's device slots) can rebuild. Without it a device
    # added through the API never appears in the descriptor until a restart.
    on_registry_change: object = None

    def rebuild(self):
        """Re-derive everything that depends on the device registry."""
        self.schema = build_schema(self.registry)
        self.store.rebind(self.schema)
        # Rebind rather than replace: a new bank starts with every relay off.
        self.relay_bank.rebind({
            d["id"]: self.channel_to_pin[d["transport"]["channel"]]
            for d in self.registry.actuators()
            if d["transport"]["kind"] == "relay" and d.get("enabled", True)
        })
        self.mqtt_bank.bind(self.registry)
        # The switch guard carries over: editing a device must not forget how
        # recently every relay was switched.
        self.device_router = DeviceRouter(self.registry, self.relay_bank, self.mqtt_bank,
                                          guard=self.device_router.guard)
        if self.on_registry_change is not None:
            self.on_registry_change()


def build_context(*, data_dir, relay_channels, channel_to_pin,
                  mqtt_host="localhost", mqtt_user="", mqtt_password="", nim_key="", nim_model="",
                  matcher_backend="fuzzy", mqtt_tls=False):
    registry = DeviceRegistry(os.path.join(data_dir, "devices.json"), relay_channels)
    schema = build_schema(registry)
    store = RuleStore(os.path.join(data_dir, "rules.json"), schema)
    pending = PendingStore(os.path.join(data_dir, "pending.json"))
    relay_bank = RelayBank({
        d["id"]: channel_to_pin[d["transport"]["channel"]]
        for d in registry.actuators() if d["transport"]["kind"] == "relay"
    })
    mqtt_bank = MqttBank(mqtt_host, username=mqtt_user, password=mqtt_password, tls=mqtt_tls)
    mqtt_bank.bind(registry)
    return DrishtiContext(
        registry=registry, schema=schema, store=store, pending=pending,
        device_router=DeviceRouter(registry, relay_bank, mqtt_bank),
        matcher=LocalMatcher(store, backend=matcher_backend),
        nim=NimClient(nim_key, nim_model),
        log_path=os.path.join(data_dir, "actuations.jsonl"),
        channel_to_pin=dict(channel_to_pin),
        relay_bank=relay_bank, mqtt_bank=mqtt_bank,
    )
