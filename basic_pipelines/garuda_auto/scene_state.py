"""Turn raw detections and sensor readings into the symbolic descriptor.

Everything downstream -- rule evaluation, and the schema shown to the cloud
model -- speaks this vocabulary and nothing else.
"""
import time

from .rule_schema import FIELDS


def _clamp(value, field):
    spec = FIELDS[field]
    return max(spec["lo"], min(spec["hi"], value))


class SceneBuilder:
    def __init__(self, zones, clock=time.time):
        self.zones = zones
        self._clock = clock
        self._state_since = None
        self._last_occupancy = None
        self._device_state = {"lamp": "off", "fan": "off"}

    def set_device_state(self, device, state):
        if device in self._device_state and state in ("on", "off"):
            self._device_state[device] = state

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
            if posture not in FIELDS["posture"]["values"]:
                posture = "none"
        else:
            zone, posture = "none", "none"

        return {
            "occupancy": occupancy,
            "person_count": int(_clamp(len(people), "person_count")),
            "occupancy_duration_s": int(_clamp(duration, "occupancy_duration_s")),
            "zone": zone,
            "posture": posture,
            "ambient_luma": int(_clamp(int(luma), "ambient_luma")),
            "temperature_c": _clamp(float(temperature_c), "temperature_c"),
            "humidity_pct": _clamp(float(humidity_pct), "humidity_pct"),
            "hour": int(_clamp(int(hour), "hour")),
            "lamp_state": self._device_state["lamp"],
            "fan_state": self._device_state["fan"],
        }
