"""Turn raw detections and sensor readings into the symbolic descriptor.

Everything downstream -- rule evaluation, and the schema shown to the cloud
model -- speaks this vocabulary and nothing else.

Camera-derived fields are fixed. Device state fields come from the registry,
so adding a device widens the descriptor without a code change.
"""
import time

from .rule_schema import BASE_FIELDS, build_schema, state_field


def _clamp(value, spec):
    return max(spec["lo"], min(spec["hi"], value))


class SceneBuilder:
    def __init__(self, zones, registry, clock=time.time):
        self.zones = zones
        self.registry = registry
        self._clock = clock
        self._state_since = None
        self._last_occupancy = None
        self._device_state = {}
        self._schema = None
        self.rebind()

    def rebind(self):
        """Rebuild device-state slots after the registry changed.

        Known states are preserved; new devices start at their type's safe
        default -- off for a load, closed for a contact, the lower bound for
        a numeric sensor.
        """
        schema = build_schema(self.registry)
        fresh = {}
        for device in self.registry.devices:
            if not device.get("enabled", True):
                continue
            field = state_field(device["id"])
            spec = schema.fields.get(field)
            if spec is None:
                continue
            default = spec["values"][-1] if spec["kind"] == "enum" else spec["lo"]
            fresh[device["id"]] = self._device_state.get(device["id"], default)
        self._device_state = fresh
        self._schema = schema

    def set_device_state(self, device_id, state):
        spec = self._schema.fields.get(state_field(device_id))
        if spec is None:
            return
        if spec["kind"] == "enum":
            if state in spec["values"]:
                self._device_state[device_id] = state
        else:
            if isinstance(state, bool) or not isinstance(state, (int, float)):
                return
            self._device_state[device_id] = _clamp(state, spec)

    def _zone_for(self, bbox):
        cx = (bbox[0] + bbox[2]) / 2.0
        cy = (bbox[1] + bbox[3]) / 2.0
        for name, (x0, y0, x1, y1) in self.zones.items():
            if x0 <= cx <= x1 and y0 <= cy <= y1:
                return name
        return "none"

    def update(self, detections, luma, temperature_c, humidity_pct, hour):
        now = self._clock()
        people = [d for d in detections if d.get("label") == "person"]
        occupancy = "occupied" if people else "empty"

        if occupancy != self._last_occupancy:
            self._last_occupancy = occupancy
            self._state_since = now
        duration = int(now - self._state_since) if self._state_since is not None else 0

        if people:
            # The largest box is the closest person, and the one whose zone and
            # posture the user means when they say "when I sit at the desk".
            primary = max(people, key=lambda d: (d["bbox"][2] - d["bbox"][0]) * (d["bbox"][3] - d["bbox"][1]))
            zone = self._zone_for(primary["bbox"])
            posture = primary.get("posture") or "none"
            if posture not in BASE_FIELDS["posture"]["values"]:
                posture = "none"
        else:
            zone, posture = "none", "none"

        descriptor = {
            "occupancy": occupancy,
            "person_count": int(_clamp(len(people), BASE_FIELDS["person_count"])),
            "occupancy_duration_s": int(_clamp(duration, BASE_FIELDS["occupancy_duration_s"])),
            "zone": zone,
            "posture": posture,
            "ambient_luma": int(_clamp(int(luma), BASE_FIELDS["ambient_luma"])),
            "temperature_c": _clamp(float(temperature_c), BASE_FIELDS["temperature_c"]),
            "humidity_pct": _clamp(float(humidity_pct), BASE_FIELDS["humidity_pct"]),
            "hour": int(_clamp(int(hour), BASE_FIELDS["hour"])),
        }
        for device_id, value in self._device_state.items():
            descriptor[state_field(device_id)] = value
        return descriptor
