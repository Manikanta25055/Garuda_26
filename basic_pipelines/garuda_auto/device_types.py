"""What a device of a given type is allowed to do.

This catalogue is code, not configuration. A user picks a type; the
capabilities follow from it. That indirection is the whole safety argument
for letting people add devices: a hand-declared capability the actuator
layer cannot honour would produce rules that validate and then fail at the
relay.
"""

TRANSPORTS = ("relay", "mqtt")

# state.kind: "enum" -> values, or "num" -> lo/hi inclusive bounds.
TYPES = {
    "light":  {"actions": ("on", "off"),
               "state": {"kind": "enum", "values": ("on", "off")}},
    "fan":    {"actions": ("on", "off"),
               "state": {"kind": "enum", "values": ("on", "off")}},
    "switch": {"actions": ("on", "off"),
               "state": {"kind": "enum", "values": ("on", "off")}},
    "sensor.temperature": {"actions": (),
                           "state": {"kind": "num", "lo": -10, "hi": 60}},
    "sensor.humidity":    {"actions": (),
                           "state": {"kind": "num", "lo": 0, "hi": 100}},
    "sensor.contact":     {"actions": (),
                           "state": {"kind": "enum", "values": ("open", "closed")}},
}


def actions_for(type_name):
    spec = TYPES.get(type_name)
    return frozenset(spec["actions"]) if spec else frozenset()


def state_spec(type_name):
    spec = TYPES.get(type_name)
    return dict(spec["state"]) if spec else None


def is_actuator(type_name):
    return bool(actions_for(type_name))
