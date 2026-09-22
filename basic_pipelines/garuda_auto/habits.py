"""Spot routines in what people switch by hand, and offer to automate them.

If the lamp goes on by hand around 19:05 on most weekdays, the house should
offer "Turn the lamp on at 19:05 on weekdays?" once, and never do it without
a yes. Everything here is local counting over the actuation log; no model sees
the history.

A routine needs the same device and action within a 40-minute window on at
least MIN_DAYS distinct days of the lookback. Suggestions already covered by
a schedule, or dismissed, are not offered again.
"""
import hashlib
import time

LOOKBACK_DAYS = 21
MIN_DAYS = 4
WINDOW_MIN = 40
MANUAL_SOURCES = ("", "manual", "assistant")


def _minute_of_day(ts):
    lt = time.localtime(ts)
    return lt.tm_hour * 60 + lt.tm_min


def _day(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _suggestion_id(device, action, hhmm, days):
    raw = f"{device}|{action}|{hhmm}|{','.join(map(str, days))}"
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


def suggest(entries, registry, schedules, *, dismissed=(), now=None):
    now = time.time() if now is None else now
    since = now - LOOKBACK_DAYS * 86400
    groups = {}
    for e in entries:
        if not e.get("ok") or e.get("ts", 0) < since:
            continue
        # Rule and schedule actions are the house's habits, not the person's.
        if e.get("rule_id") or e.get("source", "") not in MANUAL_SOURCES:
            continue
        groups.setdefault((e["device"], e["action"]), []).append(e["ts"])

    out = []
    for (device_id, action), stamps in groups.items():
        device = registry.get(device_id)
        if device is None:
            continue
        minutes = sorted((_minute_of_day(ts), ts) for ts in stamps)
        best, days_seen = [], set()
        for i, (m0, _) in enumerate(minutes):
            window = [(m, ts) for m, ts in minutes[i:] if m - m0 <= WINDOW_MIN]
            window_days = {_day(ts) for _, ts in window}
            if len(window_days) > len(days_seen):
                best, days_seen = window, window_days
        if len(days_seen) < MIN_DAYS:
            continue
        mids = sorted(m for m, _ in best)
        median = mids[len(mids) // 2]
        median = int(round(median / 5.0) * 5) % (24 * 60)
        hhmm = f"{median // 60:02d}:{median % 60:02d}"
        weekdays = sorted({time.localtime(ts).tm_wday for _, ts in best})
        days = [0, 1, 2, 3, 4] if set(weekdays) <= {0, 1, 2, 3, 4} and len(weekdays) >= 3 \
            else [0, 1, 2, 3, 4, 5, 6]
        if schedules.covers(device_id, action, median):
            continue
        sid = _suggestion_id(device_id, action, hhmm, days)
        if sid in dismissed:
            continue
        when = "on weekdays" if days == [0, 1, 2, 3, 4] else "every day"
        out.append({
            "id": sid, "device": device_id, "action": action, "time": hhmm, "days": days,
            "support_days": len(days_seen),
            "text": f"You usually turn the {device['name']} {action} around {hhmm}. "
                    f"Do it automatically {when}?",
        })
    out.sort(key=lambda s: -s["support_days"])
    return out[:8]
