"""A scripted day in one room, replayed against a rule base at the reflex
tier's own rate.

This is a replay, not a deployment: descriptors are generated from the script
below rather than from the camera, and device states are fed back from the
engine's own actions instead of from a relay. What it tests is whether a
learned rule base drives the two loads the same way the reference rule base
does when both see the same day.

The script is fixed and was written before any synthesis output was scored.
"""
TICK_S = 0.5
DAY_S = 24 * 3600


def _t(h, m=0, s=0):
    return h * 3600 + m * 60 + s


# (start, end, people, zone, posture). Gaps are an empty room.
PRESENCE = [
    (_t(7, 0), _t(7, 0, 20), 1, "door", "walking"),
    (_t(7, 0, 20), _t(7, 1, 30), 1, "center", "standing"),
    (_t(7, 1, 30), _t(8, 30), 1, "desk", "seated"),
    # 12 minutes away: crosses the two, five and ten minute empties.
    (_t(8, 42), _t(8, 42, 15), 1, "door", "walking"),
    (_t(8, 42, 15), _t(12, 0), 1, "desk", "seated"),
    # lunch, 50 minutes
    (_t(12, 50), _t(12, 50, 20), 1, "door", "walking"),
    (_t(12, 50, 20), _t(14, 0), 1, "desk", "seated"),
    (_t(14, 0), _t(14, 30), 3, "center", "standing"),
    (_t(14, 30), _t(15, 0), 2, "center", "standing"),
    (_t(15, 0), _t(17, 30), 1, "desk", "seated"),
    # 80 minutes away: crosses the one hour empty.
    (_t(18, 50), _t(18, 50, 20), 1, "door", "walking"),
    (_t(18, 50, 20), _t(18, 51, 30), 1, "door", "standing"),
    (_t(18, 51, 30), _t(18, 53), 1, "center", "standing"),
    (_t(18, 53), _t(21, 30), 1, "desk", "seated"),
    (_t(21, 30), _t(21, 31), 1, "none", "walking"),
    (_t(23, 40), _t(23, 40, 20), 1, "door", "walking"),
    (_t(23, 40, 20), _t(23, 59, 59), 1, "center", "standing"),
]


def _lerp(points, x):
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


_TEMP = [(0, 24.0), (6, 22.0), (9, 26.0), (13, 31.0), (15, 33.5), (18, 29.0), (21, 26.0), (24, 24.0)]
_HUMID = [(0, 55.0), (6, 62.0), (9, 45.0), (11, 36.0), (14, 58.0), (16, 74.0), (19, 64.0), (24, 55.0)]
_LUMA = [(0, 5), (5.5, 5), (7, 70), (9, 160), (12, 215), (16, 180), (18, 90), (19, 25), (24, 5)]


def descriptors():
    """Yield (t_seconds, descriptor) for every tick of the day."""
    prev_occupied, since = False, 0.0
    steps = int(DAY_S / TICK_S)
    for k in range(steps):
        t = k * TICK_S
        seg = next((p for p in PRESENCE if p[0] <= t < p[1]), None)
        occupied = seg is not None
        if occupied != prev_occupied:
            prev_occupied, since = occupied, t
        hours = t / 3600.0
        yield t, {
            "occupancy": "occupied" if occupied else "empty",
            "person_count": seg[2] if seg else 0,
            "occupancy_duration_s": int(t - since),
            "zone": seg[3] if seg else "none",
            "posture": seg[4] if seg else "none",
            "ambient_luma": int(_lerp(_LUMA, hours)),
            "temperature_c": round(_lerp(_TEMP, hours), 1),
            "humidity_pct": round(_lerp(_HUMID, hours), 1),
            "hour": int(hours),
        }
