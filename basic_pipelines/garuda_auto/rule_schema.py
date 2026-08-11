"""Single source of truth for what a synthesised rule may reference.

This module is the allowlist. The cloud model is told these field names and
legal values; it is never told their current readings. Adding a field here
widens the model's vocabulary, so treat edits as a design decision.
"""

MAX_RULES = 64
MAX_DURATION_S = 86_400

OPS = frozenset({"==", "!=", "<", "<=", ">", ">="})
_NUM_OPS = frozenset({"==", "!=", "<", "<=", ">", ">="})
_ENUM_OPS = frozenset({"==", "!="})

# kind: "enum" -> values, or "num" -> lo/hi inclusive bounds
FIELDS = {
    "occupancy":            {"kind": "enum", "values": ("empty", "occupied"), "ops": _ENUM_OPS},
    "person_count":         {"kind": "num", "lo": 0, "hi": 16, "ops": _NUM_OPS},
    "occupancy_duration_s": {"kind": "num", "lo": 0, "hi": MAX_DURATION_S, "ops": _NUM_OPS},
    "zone":                 {"kind": "enum", "values": ("none", "desk", "door", "center"), "ops": _ENUM_OPS},
    "posture":              {"kind": "enum", "values": ("none", "standing", "seated", "walking"), "ops": _ENUM_OPS},
    "ambient_luma":         {"kind": "num", "lo": 0, "hi": 255, "ops": _NUM_OPS},
    "temperature_c":        {"kind": "num", "lo": -10, "hi": 60, "ops": _NUM_OPS},
    "humidity_pct":         {"kind": "num", "lo": 0, "hi": 100, "ops": _NUM_OPS},
    "hour":                 {"kind": "num", "lo": 0, "hi": 23, "ops": _NUM_OPS},
    "lamp_state":           {"kind": "enum", "values": ("on", "off"), "ops": _ENUM_OPS},
    "fan_state":            {"kind": "enum", "values": ("on", "off"), "ops": _ENUM_OPS},
}

DEVICES = {
    "lamp": frozenset({"on", "off"}),
    "fan":  frozenset({"on", "off"}),
}

COOLDOWN_MIN_S = 0
COOLDOWN_MAX_S = 3600


def schema_for_prompt():
    """The exact structure sent to NIM. Field names and legal values only.

    Never include readings here. The model compiles rules; it does not need
    to know what the room currently looks like.
    """
    out = {}
    for name, spec in FIELDS.items():
        if spec["kind"] == "enum":
            out[name] = {"type": "enum", "values": list(spec["values"])}
        else:
            out[name] = {"type": "number", "min": spec["lo"], "max": spec["hi"]}
    return {"fields": out, "devices": {d: sorted(a) for d, a in DEVICES.items()},
            "operators": sorted(OPS)}
