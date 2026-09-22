"""Time-based actions: "lamp on at 19:00 on weekdays", "fan off in 30 minutes".

Two kinds. A daily entry names a wall-clock minute and the weekdays it runs
on. A once entry (a timer) names an absolute time and is removed after it
runs. A target is a device and action, or a scene.

Missed runs are not replayed. A service that was down at 19:00 does not turn
the lamp on at 19:40 when it comes back: late automation surprises more than
missing automation. The exception is a timer only a few minutes late, which is
still what the person asked for.
"""
import re
import threading
import time
import uuid

from . import jsonfile
from .device_types import actions_for

MAX_SCHEDULES = 64
TIMER_GRACE_S = 600
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]


def validate_target(target, registry, scenes):
    if not isinstance(target, dict):
        return False, "a schedule needs a device and action, or a scene"
    if "scene" in target:
        if scenes.get(target["scene"]) is None:
            return False, f"unknown scene: {target['scene']}"
        return True, ""
    device = registry.get(target.get("device"))
    if device is None:
        return False, f"unknown device: {target.get('device')}"
    if target.get("action") not in actions_for(device["type"]):
        return False, f"{device['name']} cannot be switched {target.get('action')!r}"
    return True, ""


class ScheduleStore:
    def __init__(self, path, clock=time.time):
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()
        self.entries = jsonfile.load(path, [])

    def save(self):
        jsonfile.save(self.path, self.entries)

    def get(self, schedule_id):
        for entry in self.entries:
            if entry["id"] == schedule_id:
                return entry
        return None

    def add_daily(self, target, time_hhmm, days=None, label="", created_by=""):
        if not isinstance(time_hhmm, str) or not _HHMM.match(time_hhmm):
            return False, "time must be HH:MM (24-hour)", None
        days = ALL_DAYS if days is None else days
        if (not isinstance(days, list) or not days
                or any(isinstance(d, bool) or d not in ALL_DAYS for d in days)):
            return False, "days must be a list of weekday numbers, Monday = 0", None
        return self._add({"kind": "daily", "time": time_hhmm, "days": sorted(set(days)),
                          "target": target, "label": label[:64], "created_by": created_by})

    def add_once(self, target, at, label="", created_by=""):
        now = self._clock()
        if isinstance(at, bool) or not isinstance(at, (int, float)) or at <= now:
            return False, "a timer must be in the future", None
        if at - now > 7 * 86400:
            return False, "a timer can be at most a week away", None
        return self._add({"kind": "once", "at": float(at), "target": target,
                          "label": label[:64], "created_by": created_by})

    def _add(self, entry):
        with self._lock:
            if len(self.entries) >= MAX_SCHEDULES:
                return False, f"schedule limit reached ({MAX_SCHEDULES})", None
            entry = {"id": uuid.uuid4().hex[:10], "enabled": True, "last_run": None,
                     "created_at": self._clock(), **entry}
            self.entries.append(entry)
            self.save()
        return True, "", entry

    def delete(self, schedule_id):
        with self._lock:
            before = len(self.entries)
            self.entries = [e for e in self.entries if e["id"] != schedule_id]
            if len(self.entries) == before:
                return False
            self.save()
        return True

    def toggle(self, schedule_id):
        with self._lock:
            entry = self.get(schedule_id)
            if entry is None:
                return None
            entry["enabled"] = not entry.get("enabled", True)
            self.save()
            return entry["enabled"]

    def due(self, now=None):
        """Entries to run now. Marks them run (and drops spent timers)."""
        now = self._clock() if now is None else now
        local = time.localtime(now)
        minute_key = time.strftime("%Y-%m-%d %H:%M", local)
        hhmm = time.strftime("%H:%M", local)
        out, changed = [], False
        with self._lock:
            keep = []
            for entry in self.entries:
                if entry["kind"] == "once":
                    if entry["at"] <= now:
                        changed = True
                        if entry.get("enabled", True) and now - entry["at"] <= TIMER_GRACE_S:
                            out.append(entry)
                        continue
                elif (entry.get("enabled", True) and entry["time"] == hhmm
                      and local.tm_wday in entry["days"] and entry.get("last_run") != minute_key):
                    entry["last_run"] = minute_key
                    out.append(entry)
                    changed = True
                keep.append(entry)
            self.entries = keep
            if changed:
                self.save()
        return out

    def next_run(self, entry, now=None):
        """Epoch of the next run, for display. None when disabled or spent."""
        now = self._clock() if now is None else now
        if not entry.get("enabled", True):
            return None
        if entry["kind"] == "once":
            return entry["at"]
        hh, mm = map(int, entry["time"].split(":"))
        for offset in range(8):
            day = time.localtime(now + offset * 86400)
            candidate = time.mktime((day.tm_year, day.tm_mon, day.tm_mday, hh, mm, 0, 0, 0, -1))
            if candidate > now and time.localtime(candidate).tm_wday in entry["days"]:
                return candidate
        return None

    def covers(self, device, action, minute_of_day, tolerance=20):
        """True when a daily schedule already does roughly this."""
        for entry in self.entries:
            if entry["kind"] != "daily":
                continue
            target = entry["target"]
            if target.get("device") != device or target.get("action") != action:
                continue
            hh, mm = map(int, entry["time"].split(":"))
            if abs(hh * 60 + mm - minute_of_day) <= tolerance:
                return True
        return False
