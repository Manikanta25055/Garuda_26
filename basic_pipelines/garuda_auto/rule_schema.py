"""What a synthesised rule may reference.

The vocabulary is built at runtime from the device registry, so adding a
device widens what the user can talk about without a code change. Camera-
derived fields are fixed: they come from the detector, not from anything a
user can declare.

The cloud model is told these names and legal values; it is never told their
current readings.
"""
from .device_types import actions_for, state_spec, is_actuator

MAX_RULES = 64
MAX_DURATION_S = 86_400

OPS = frozenset({"==", "!=", "<", "<=", ">", ">="})
_NUM_OPS = frozenset({"==", "!=", "<", "<=", ">", ">="})
_ENUM_OPS = frozenset({"==", "!="})

COOLDOWN_MIN_S = 0
COOLDOWN_MAX_S = 3600

BASE_FIELDS = {
    "occupancy":            {"kind": "enum", "values": ("empty", "occupied"), "ops": _ENUM_OPS},
    "person_count":         {"kind": "num", "lo": 0, "hi": 16, "ops": _NUM_OPS},
    "occupancy_duration_s": {"kind": "num", "lo": 0, "hi": MAX_DURATION_S, "ops": _NUM_OPS},
    "zone":                 {"kind": "enum", "values": ("none", "desk", "door", "center"), "ops": _ENUM_OPS},
    "posture":              {"kind": "enum", "values": ("none", "standing", "seated", "walking"), "ops": _ENUM_OPS},
    "ambient_luma":         {"kind": "num", "lo": 0, "hi": 255, "ops": _NUM_OPS},
    "temperature_c":        {"kind": "num", "lo": -10, "hi": 60, "ops": _NUM_OPS},
    "humidity_pct":         {"kind": "num", "lo": 0, "hi": 100, "ops": _NUM_OPS},
    "hour":                 {"kind": "num", "lo": 0, "hi": 23, "ops": _NUM_OPS},
}


def state_field(device_id):
    """The one field a device contributes. Uniform across types."""
    return f"{device_id}_state"


class Schema:
    def __init__(self, fields, devices):
        self.fields = fields
        self.devices = devices

    def schema_for_prompt(self):
        """The exact structure sent to NIM. Names and legal values only.

        Never include readings here. The model compiles rules; it does not
        need to know what the room currently looks like.
        """
        out = {}
        for name, spec in self.fields.items():
            if spec["kind"] == "enum":
                out[name] = {"type": "enum", "values": list(spec["values"])}
            else:
                out[name] = {"type": "number", "min": spec["lo"], "max": spec["hi"]}
        return {"fields": out,
                "devices": {d: sorted(a) for d, a in self.devices.items()},
                "operators": sorted(OPS)}


def build_schema(registry):
    fields = {name: dict(spec) for name, spec in BASE_FIELDS.items()}
    devices = {}
    for device in registry.devices:
        if not device.get("enabled", True):
            continue
        spec = state_spec(device["type"])
        if spec is None:
            continue
        spec["ops"] = _ENUM_OPS if spec["kind"] == "enum" else _NUM_OPS
        fields[state_field(device["id"])] = spec
        if is_actuator(device["type"]):
            devices[device["id"]] = actions_for(device["type"])
    return Schema(fields, devices)
