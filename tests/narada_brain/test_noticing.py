"""Phase 5: remarks nobody asked for, computed from recorded data, named and throttled."""
import time

import pytest

from basic_pipelines.narada_brain import Brain, noticing as nz
from basic_pipelines.narada_brain.memory import MemoryStore

H, D = nz.HOUR_S, nz.DAY_S
# A fixed Wednesday evening, 20:00 local time.
NOW = time.mktime((2026, 9, 30, 20, 0, 0, 0, 0, -1))


def on_off(device, start, hours):
    return [{"device": device, "action": "on", "ok": True, "ts": start},
            {"device": device, "action": "off", "ok": True, "ts": start + hours * H}]


def snap(entries=(), devices=(), routines=(), modes=None, security=None, owner_home=True, now=NOW):
    return nz.snapshot(entries=list(entries), devices=list(devices), routines=list(routines),
                       modes=modes or {}, security=security or {}, owner_home=owner_home, now=now)


def fresh():
    return {"fired": {}, "last": 0.0, "since": {}}


FAN = {"id": "fan", "name": "Fan", "state": "on"}
LAMP_OFF = {"id": "lamp", "name": "Lamp", "state": "off"}
ROUTINE = {"id": "s1", "device": "lamp", "action": "on", "time": "19:05", "days": [0, 1, 2, 3, 4]}


def fan_history(now=NOW, open_hours=9):
    past = []
    for day in range(2, 7):
        past += on_off("fan", now - day * D, 3)                       # about three hours, five times
    return past + [{"device": "fan", "action": "on", "ok": True, "ts": now - open_hours * H}]


# ── the snapshot ──────────────────────────────────────────────────────────────

def test_snapshot_measures_a_devices_own_normal():
    s = snap(fan_history(), [FAN])
    fan = s["devices"][0]
    assert fan["on_since"] == NOW - 9 * H and fan["typical_on_s"] == 3 * H
    assert round(fan["hours_this_week"]) == 5 * 3 + 9 and fan["hours_last_week"] == 0


def test_no_baseline_without_enough_history():
    entries = on_off("fan", NOW - 3 * D, 3) + [{"device": "fan", "action": "on", "ok": True, "ts": NOW - 9 * H}]
    assert snap(entries, [FAN])["devices"][0]["typical_on_s"] is None


# ── each rule ─────────────────────────────────────────────────────────────────

def test_running_long_names_both_numbers():
    found = nz.evaluate(snap(fan_history(), [FAN]), fresh())
    assert found["rule"] == "running-long" and found["title"] == "Running longer than usual"
    assert found["text"] == "The Fan has been on for 9 hours. It usually runs about 3 hours at a time."


def test_not_long_enough_is_not_mentioned():
    assert nz.evaluate(snap(fan_history(open_hours=4), [FAN]), fresh()) is None


def test_no_remark_without_a_baseline():
    entries = [{"device": "fan", "action": "on", "ok": True, "ts": NOW - 30 * H}]
    assert nz.evaluate(snap(entries, [FAN]), fresh()) is None


def test_a_missed_routine_is_mentioned_within_its_window_only():
    assert nz.evaluate(snap(devices=[LAMP_OFF], routines=[ROUTINE]), fresh())["text"] == \
        "The Lamp is usually turned on around 19:05. It is still off."
    early = NOW - 40 * 60                                            # 19:20: give it time
    late = NOW + 3 * H                                               # 23:00: the evening is over
    assert nz.evaluate(snap(devices=[LAMP_OFF], routines=[ROUTINE], now=early), fresh()) is None
    assert nz.evaluate(snap(devices=[LAMP_OFF], routines=[ROUTINE], now=late), fresh()) is None


def test_a_routine_is_not_missed_when_done_away_or_on_another_day():
    done = {"id": "lamp", "name": "Lamp", "state": "on"}
    assert nz.evaluate(snap(devices=[done], routines=[ROUTINE]), fresh()) is None
    assert nz.evaluate(snap(devices=[LAMP_OFF], routines=[ROUTINE], owner_home=False), fresh()) is None
    weekend = {**ROUTINE, "days": [5, 6]}
    assert nz.evaluate(snap(devices=[LAMP_OFF], routines=[weekend]), fresh()) is None


def test_usage_up_compares_two_weeks():
    entries = []
    for day in range(8, 14):
        entries += on_off("fan", NOW - day * D, 3)                    # 18 h the week before
    for day in range(1, 7):
        entries += on_off("fan", NOW - day * D, 6)                    # 36 h this week
    found = nz.evaluate(snap(entries, [{"id": "fan", "name": "Fan", "state": "off"}]), fresh())
    assert found["rule"] == "usage-up"
    assert found["text"] == "The Fan has run 36 hours in the last seven days, up from 18 the week before."


def test_silenced_alerts_are_mentioned_only_after_they_have_lasted():
    state = fresh()
    modes = {"idle": True, "email_off": True, "dnd": False}
    assert nz.evaluate(snap(modes=modes), state) is None              # first seen now
    later = snap(modes=modes, now=NOW + 3 * D)
    found = nz.evaluate(later, state)
    assert found["rule"] == "alerts-silenced"
    assert found["text"].startswith("Idle mode and email alerts off have been on for 3 days.")
    state2 = fresh()
    nz.evaluate(snap(modes=modes), state2)
    nz.evaluate(snap(modes={"idle": False}, now=NOW + H), state2)     # switched back: the clock resets
    assert nz.evaluate(snap(modes=modes, now=NOW + 13 * H), state2) is None


def test_camera_down_needs_a_real_no_and_some_time():
    state = fresh()
    assert nz.evaluate(snap(security={"camera_live": False}), state) is None
    found = nz.evaluate(snap(security={"camera_live": False}, now=NOW + 20 * 60), state)
    assert found["text"] == "The camera has not delivered a frame for 20 minutes."
    assert nz.evaluate(snap(security={}, now=NOW + D), fresh()) is None     # unknown is not down


# ── not talking too much ──────────────────────────────────────────────────────

def test_one_remark_an_hour_and_the_same_one_once_a_day():
    state = fresh()
    world = dict(entries=fan_history(), devices=[FAN, LAMP_OFF], routines=[ROUTINE])
    first = nz.evaluate(snap(**world), state)
    assert first["rule"] == "running-long"
    assert nz.evaluate(snap(**world, now=NOW + 10 * 60), state) is None           # too soon for anything
    second = nz.evaluate(snap(**{**world, "entries": fan_history(NOW + H + 60, 10)}, now=NOW + H + 60), state)
    assert second["rule"] == "routine-missed"                                    # the other one, not a repeat
    assert nz.evaluate(snap(**{**world, "entries": fan_history(NOW + 3 * H, 12)}, now=NOW + 3 * H), state) is None
    again = nz.evaluate(snap(**{**world, "entries": fan_history(NOW + D + H, 34)}, now=NOW + D + H), state)
    assert again["rule"] == "running-long"                                       # a day later, still true


def test_a_muted_remark_is_not_made():
    assert nz.evaluate(snap(fan_history(), [FAN]), fresh(), muted={"running-long:fan"}) is None


def test_the_security_product_hears_only_security_remarks():
    state = fresh()
    assert nz.evaluate(snap(fan_history(), [FAN]), state, scope="security") is None
    silenced = {"idle": True}
    nz.evaluate(snap(modes=silenced), state, scope="security")
    assert nz.evaluate(snap(modes=silenced, now=NOW + D), state, scope="security")["rule"] == "alerts-silenced"


# ── the brain ─────────────────────────────────────────────────────────────────

def test_brain_notices_reads_rarely_survives_a_restart_and_never_raises(tmp_path):
    now = [NOW]
    brain = Brain(str(tmp_path), clock=lambda: now[0])
    calls = []

    def world():
        calls.append(1)
        return snap(fan_history(now[0]), [FAN], now=now[0])
    assert brain.notice(world)["rule"] == "running-long"
    assert brain.notice(world) is None and len(calls) == 1               # not re-read within minutes
    now[0] += nz.REFRESH_S + 1
    assert brain.notice(world) is None and len(calls) == 2               # read, but said recently
    again = Brain(str(tmp_path), clock=lambda: now[0])
    now[0] += 2 * H
    assert again.notice(world) is None                                   # the history came back with it

    def broken():
        raise RuntimeError("log unreadable")
    now[0] += nz.REFRESH_S + 1
    assert again.notice(broken) is None


def test_dont_tell_me_this_is_kept_as_a_choice_and_can_be_undone(tmp_path):
    now = [NOW]
    brain = Brain(str(tmp_path), clock=lambda: now[0])
    world = lambda: snap(fan_history(now[0]), [FAN], now=now[0])        # noqa: E731
    outcome = brain.mute_notice("running-long:fan", {"fan": "Fan"}, by="mani")
    assert outcome["fact"]["text"] == "The household does not want to be told when the Fan runs longer than usual."
    assert outcome["fact"]["origin"] == "choice" and outcome["event"]["status"] == "saved"
    assert brain.notice(world) is None
    brain.memory.forget(outcome["fact"]["id"])                           # removed on the memory page
    now[0] += nz.REFRESH_S + 1
    assert brain.notice(world)["rule"] == "running-long"
    assert brain.mute_notice("no-such-rule:x", {}) is None


def test_mute_text_exists_for_every_rule():
    for rule in nz.RULES:
        assert nz.mute_text(f"{rule}:fan" if rule not in ("alerts-silenced", "camera-down") else rule,
                            {"fan": "Fan"})
