"""Noticing things nobody asked about.

The house raises alarms on thresholds: a device left on while nobody is home,
a knife in front of the camera. A lot can happen without crossing one. The fan
has been running three times longer than it ever does; the lamp that goes on
at seven every evening is still off at eight; alerts have been silenced for
three days. The pattern is in the data and nobody is looking at it. This says
so, once, in the conversation.

Two constraints shape everything here (they are the bREADth ProactiveObserver's).

It must not be wrong. A remark nobody asked for costs more trust when it is
false than a wrong answer to a question does. So detection and wording are
both computed from recorded values (no model call, nothing to invent), and
each remark carries the NAME OF THE RULE that produced it, so the person can
see what fired and disagree with it.

It must not talk too much. An assistant that keeps interrupting is ignored,
and an ignored remark teaches people to dismiss the next one unread. So: one
remark at a time, each pattern at most once a day, at most one an hour
overall, and "don't tell me this" silences a pattern for good (it is kept as
the household's choice, in memory, where it can be undone).

`snapshot()` and `evaluate()` are pure functions of recorded data and the
firing history, so all of it is tested without a clock, a house or a model.
"""
import logging
import time

from ..garuda_auto import jsonfile, usage

log = logging.getLogger(__name__)

NOTICES_FILE = "narada_notices.json"
DAY_S, HOUR_S = 86_400, 3_600

# How far back a device's own normal is measured, and how much of it is needed.
BASELINE_DAYS = 14
MIN_INTERVALS = 3
# "Running long": at least twice its usual stretch AND at least two hours over.
LONG_FACTOR, LONG_MARGIN_S = 2.0, 2 * HOUR_S
# "Routine missed": this long after its usual time, and not later than this.
MISSED_AFTER_S, MISSED_UNTIL_S = 45 * 60, 3 * HOUR_S
# "Usage up": this week against the one before.
USAGE_FACTOR, USAGE_MIN_RISE_H, USAGE_MIN_BASE_H = 1.5, 5.0, 2.0
# "Alerts silenced": for at least this long.
SILENCED_AFTER_S = 12 * HOUR_S
CAMERA_DOWN_AFTER_S = 10 * 60

# One remark an hour at most; the same one not again for a day.
GAP_S = HOUR_S
COOLDOWN_S = DAY_S
# The data is re-read at most this often (it means reading the actuation log).
REFRESH_S = 300

RULES = {
    "running-long": "Running longer than usual",
    "routine-missed": "A routine has not happened",
    "usage-up": "Used more than last week",
    "alerts-silenced": "Alerts are silenced",
    "camera-down": "The camera has stopped",
}
# What silences which alerts, in words (see the persona's mode list).
_SILENCERS = {"idle": "idle mode", "email_off": "email alerts off", "dnd": "do not disturb"}


def _hours(seconds):
    hours = seconds / HOUR_S
    return f"{hours:.0f} hours" if hours >= 10 or abs(hours - round(hours)) < 0.05 else f"{hours:.1f} hours"


def _span(seconds):
    if seconds >= 2 * DAY_S:
        return f"{seconds / DAY_S:.0f} days"
    if seconds >= 2 * HOUR_S:
        return f"{seconds / HOUR_S:.0f} hours"
    return f"{max(1, seconds / 60):.0f} minutes"


def snapshot(*, entries, devices, routines, modes, security, owner_home, now):
    """What the rules look at, from recorded data only.

    entries   the actuation log, any order
    devices   [{"id", "name", "state"}] actuators only
    routines  the house's noticed routines (habits.suggest output)
    modes     {"idle": bool, ...};  security  {"camera_live": bool, ...};
    owner_home  True / False / None (unknown)
    """
    entries = sorted(entries, key=lambda e: e.get("ts", 0))
    out = []
    for d in devices:
        spans = usage.on_intervals(entries, d["id"], now - BASELINE_DAYS * DAY_S, now)
        open_since = spans[-1][0] if spans and spans[-1][1] >= now and d.get("state") == "on" else None
        closed = sorted(b - a for a, b in spans if b < now)
        typical = closed[len(closed) // 2] if len(closed) >= MIN_INTERVALS else None
        week = sum(min(b, now) - max(a, now - 7 * DAY_S) for a, b in spans if b > now - 7 * DAY_S)
        before = sum(min(b, now - 7 * DAY_S) - a for a, b in spans if a < now - 7 * DAY_S)
        out.append({"id": d["id"], "name": d.get("name", d["id"]), "state": d.get("state"),
                    "on_since": open_since, "typical_on_s": typical,
                    "hours_this_week": week / HOUR_S, "hours_last_week": before / HOUR_S})
    return {"now": now, "devices": out, "routines": list(routines or ()), "modes": dict(modes or {}),
            "security": dict(security or {}), "owner_home": owner_home}


def candidates(snap, since):
    """Every remark the rules would make now: [{"key", "rule", "text"}], most urgent first.

    `since` maps a condition key to when it was first seen holding, for the
    rules that are about duration; it is updated in place.
    """
    now, found = snap["now"], []
    by_id = {d["id"]: d for d in snap["devices"]}

    # Alerts silenced: the one that matters most, so it goes first.
    on = [label for mode, label in _SILENCERS.items() if snap["modes"].get(mode)]
    if on:
        started = since.setdefault("silenced", now)
        if now - started >= SILENCED_AFTER_S:
            what = " and ".join(on)
            found.append({"key": "alerts-silenced", "rule": "alerts-silenced",
                          "text": f"{what[0].upper() + what[1:]} {'has' if len(on) == 1 else 'have'} been on "
                                  f"for {_span(now - started)}. While that is so, a detection is not "
                                  f"announced the way it normally would be."})
    else:
        since.pop("silenced", None)

    if snap["security"].get("camera_live") is False:
        started = since.setdefault("camera", now)
        if now - started >= CAMERA_DOWN_AFTER_S:
            found.append({"key": "camera-down", "rule": "camera-down",
                          "text": f"The camera has not delivered a frame for {_span(now - started)}."})
    else:
        since.pop("camera", None)

    for d in snap["devices"]:
        if d["on_since"] and d["typical_on_s"]:
            elapsed = now - d["on_since"]
            if elapsed >= max(LONG_FACTOR * d["typical_on_s"], d["typical_on_s"] + LONG_MARGIN_S):
                found.append({"key": f"running-long:{d['id']}", "rule": "running-long",
                              "text": f"The {d['name']} has been on for {_hours(elapsed)}. It usually "
                                      f"runs about {_hours(d['typical_on_s'])} at a time."})

    if snap["owner_home"] is not False:                 # nobody home: nobody to do the routine
        today = time.localtime(now)
        for r in snap["routines"]:
            d = by_id.get(r.get("device"))
            if d is None or today.tm_wday not in r.get("days", ()) or d["state"] == r.get("action"):
                continue
            hour, minute = (int(x) for x in r["time"].split(":"))
            usual = time.mktime((today.tm_year, today.tm_mon, today.tm_mday, hour, minute, 0, 0, 0, -1))
            if MISSED_AFTER_S <= now - usual <= MISSED_UNTIL_S:
                found.append({"key": f"routine-missed:{d['id']}:{r['action']}", "rule": "routine-missed",
                              "text": f"The {d['name']} is usually turned {r['action']} around {r['time']}. "
                                      f"It is still {d['state']}."})

    for d in snap["devices"]:
        week, before = d["hours_this_week"], d["hours_last_week"]
        if before >= USAGE_MIN_BASE_H and week >= USAGE_FACTOR * before and week - before >= USAGE_MIN_RISE_H:
            found.append({"key": f"usage-up:{d['id']}", "rule": "usage-up",
                          "text": f"The {d['name']} has run {week:.0f} hours in the last seven days, up "
                                  f"from {before:.0f} the week before."})
    return found


def evaluate(snap, state, *, muted=(), scope="home"):
    """The one remark to make now, or None. `state` is the firing history, updated in place.

    state: {"fired": {key: ts}, "last": ts, "since": {key: ts}}
    """
    now = snap["now"]
    since = state.setdefault("since", {})
    found = candidates(snap, since)                     # always run: it keeps the durations
    if now - state.get("last", 0) < GAP_S:
        return None
    fired = state.setdefault("fired", {})
    for c in found:
        # On the security product only the security remarks are its business.
        if scope == "security" and c["rule"] not in ("alerts-silenced", "camera-down"):
            continue
        if c["key"] in muted or now - fired.get(c["key"], 0) < COOLDOWN_S:
            continue
        fired[c["key"]] = now
        state["last"] = now
        return {**c, "title": RULES[c["rule"]]}
    return None


def mute_text(observation_key, names):
    """The household's choice not to hear a remark again, as a memory fact."""
    rule, _, rest = observation_key.partition(":")
    device = names.get(rest.split(":")[0], rest.split(":")[0]) if rest else ""
    return {
        "running-long": f"The household does not want to be told when the {device} runs longer than usual.",
        "routine-missed": f"The household does not want to be told when the {device} routine is missed.",
        "usage-up": f"The household does not want to be told when the {device} is used more than the week before.",
        "alerts-silenced": "The household does not want to be reminded that alerts are silenced.",
        "camera-down": "The household does not want to be told when the camera stops.",
    }.get(rule)


class Noticer:
    """The firing history, kept across restarts, and the throttle on reading the log."""

    def __init__(self, memory, path=None, *, clock=time.time):
        self.memory = memory
        self.path = path
        self._clock = clock
        state = jsonfile.load(path, {}) if path else {}
        self.state = {"fired": dict(state.get("fired", {})), "last": float(state.get("last") or 0),
                      "since": dict(state.get("since", {}))}
        self._checked_at = None

    def muted(self):
        return {f["key"][len("mute:"):] for f in self.memory.facts() if f.get("key", "").startswith("mute:")}

    def notice(self, snapshot_fn, *, scope="home"):
        now = self._clock()
        if self._checked_at is not None and now - self._checked_at < REFRESH_S:
            return None
        self._checked_at = now
        found = evaluate(snapshot_fn(), self.state, muted=self.muted(), scope=scope)
        self._save()
        return found

    def mute(self, observation_key, names, by=""):
        text = mute_text(observation_key, names)
        if text is None:
            return None
        return self.memory.remember(text, category="preference", origin="choice", by=by,
                                    key=f"mute:{observation_key}")

    def _save(self):
        if not self.path:
            return
        now = self._clock()
        self.state["fired"] = {k: v for k, v in self.state["fired"].items() if now - v < 30 * DAY_S}
        try:
            jsonfile.save(self.path, self.state)
        except OSError:
            log.exception("saving notices")
