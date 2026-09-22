"""How long each device was on, and roughly what that cost.

Reconstructed from the actuation log: every successful "on" opens an interval
and the next successful "off" closes it. The state at the start of the window
is whatever the last entry before it said. Energy is on-time times the
wattage the owner entered for the device, so it is an estimate and is labelled
as one; a device with no wattage reports hours only.
"""
import time

DAY_S = 86_400


def _day_start(ts):
    lt = time.localtime(ts)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def on_intervals(entries, device, start, end):
    """[(a, b), ...] clipped to [start, end] for one device. entries oldest first."""
    state, since, out = "off", start, []
    for entry in entries:
        if entry.get("device") != device or not entry.get("ok"):
            continue
        ts, action = entry.get("ts", 0), entry.get("action")
        if action not in ("on", "off"):
            continue
        if ts <= start:
            state = action
            continue
        if ts >= end:
            break
        if action == "on" and state != "on":
            state, since = "on", ts
        elif action == "off" and state == "on":
            out.append((since, ts))
            state = "off"
    if state == "on":
        out.append((max(since, start), end))
    return out


def summary(entries, devices, *, days=7, now=None, tariff_per_kwh=0.0):
    """Per-device on-hours and kWh, per day and in total, for the last `days`."""
    now = time.time() if now is None else now
    entries = sorted(entries, key=lambda e: e.get("ts", 0))
    day0 = _day_start(now) - (days - 1) * DAY_S
    labels = [time.strftime("%Y-%m-%d", time.localtime(day0 + i * DAY_S + 3600))
              for i in range(days)]
    rows, total_kwh = [], 0.0
    for device in devices:
        per_day = [0.0] * days
        for a, b in on_intervals(entries, device["id"], day0, now):
            # Split an interval across the midnights it spans.
            while a < b:
                idx = int((a - day0) // DAY_S)
                boundary = min(b, day0 + (idx + 1) * DAY_S)
                if 0 <= idx < days:
                    per_day[idx] += boundary - a
                a = boundary
        hours = [round(s / 3600, 2) for s in per_day]
        watts = device.get("watts")
        kwh = round(sum(per_day) / 3600 * watts / 1000, 3) if watts else None
        switches = sum(1 for e in entries
                       if e.get("device") == device["id"] and e.get("ok")
                       and e.get("ts", 0) >= day0)
        total_kwh += kwh or 0.0
        rows.append({"id": device["id"], "name": device.get("name", device["id"]),
                     "watts": watts, "hours_by_day": hours,
                     "hours": round(sum(per_day) / 3600, 2), "kwh": kwh,
                     "switches": switches})
    rows.sort(key=lambda r: -r["hours"])
    return {"days": labels, "devices": rows, "total_kwh": round(total_kwh, 3),
            "cost": round(total_kwh * tariff_per_kwh, 2) if tariff_per_kwh else None,
            "estimate": True}
