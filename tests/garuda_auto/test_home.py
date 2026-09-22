"""Scenes, schedules, usage, habits and the house loop, over a temp context."""
import time

import pytest

from basic_pipelines import drishti_api
from basic_pipelines.garuda_auto import habits, usage
from basic_pipelines.garuda_auto.home import HomeServices
from basic_pipelines.garuda_auto.runtime import DrishtiRuntime

LAMP = {"id": "lamp", "name": "Lamp", "type": "light", "room": "study",
        "transport": {"kind": "relay", "channel": 1}, "watts": 60}
FAN = {"id": "fan", "name": "Fan", "type": "fan", "room": "study",
       "transport": {"kind": "relay", "channel": 2}}


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def house(tmp_path):
    ctx = drishti_api.build_context(data_dir=str(tmp_path), relay_channels=(1, 2, 3),
                                    channel_to_pin={1: 17, 2: 27, 3: 22})
    for d in (LAMP, FAN):
        assert ctx.registry.add(d)[0]
    ctx.rebuild()
    # 2026-09-21 is a Monday; 18:59:30 local.
    clock = Clock(time.mktime((2026, 9, 21, 18, 59, 30, 0, 0, -1)))
    runtime = DrishtiRuntime(ctx, clock=clock)
    ctx.on_registry_change = runtime.rebind
    home = HomeServices(ctx, runtime, str(tmp_path), clock=clock)
    runtime.context_provider = home.context
    try:
        yield home, ctx, clock
    finally:
        ctx.relay_bank.close()


# ── control ──────────────────────────────────────────────────────────────────

def test_set_logs_source_and_updates_the_rule_base(house):
    home, ctx, _ = house
    ok, _ = home.set("lamp", "on", source="manual", actor="mani")
    assert ok
    assert ctx.descriptor["lamp_state"] == "on"
    entry = home.log_entries()[-1]
    assert (entry["source"], entry["actor"], entry["device"]) == ("manual", "mani", "lamp")


def test_all_off_turns_off_only_what_is_on(house):
    home, _, _ = house
    home.set("lamp", "on")
    done, failed = home.all_off()
    assert done == ["Lamp"] and failed == []
    assert home.on_devices() == []


# ── scenes ───────────────────────────────────────────────────────────────────

def test_a_scene_runs_every_step(house):
    home, _, _ = house
    ok, _, scene = home.scenes.add("Study time", [{"device": "lamp", "action": "on"},
                                                  {"device": "fan", "action": "on"}])
    assert ok and scene["id"] == "study_time"
    ok, _, results = home.run_scene("study_time")
    assert ok and len(results) == 2
    assert {d["id"] for d in home.on_devices()} == {"lamp", "fan"}


@pytest.mark.parametrize("actions, reason", [
    ([], "at least one step"),
    ([{"device": "ghost", "action": "on"}], "unknown device"),
    ([{"device": "lamp", "action": "dim"}], "cannot be switched"),
    ([{"device": "lamp", "action": "on"}, {"device": "lamp", "action": "off"}], "twice"),
])
def test_bad_scenes_are_refused(house, actions, reason):
    home, _, _ = house
    ok, why, _ = home.scenes.add("Bad", actions)
    assert not ok and reason in why


# ── schedules ────────────────────────────────────────────────────────────────

def test_a_daily_schedule_runs_once_in_its_minute(house):
    home, _, clock = house
    ok, _, entry = home.add_schedule({"device": "lamp", "action": "on"},
                                     time_hhmm="19:00", days=[0])
    assert ok
    home.tick()
    assert home.on_devices() == []
    clock.t += 45                      # 19:00:15
    home.tick()
    clock.t += 10                      # still 19:00
    home.tick()
    assert [d["id"] for d in home.on_devices()] == ["lamp"]
    assert sum(1 for e in home.log_entries() if e.get("source", "").startswith("schedule:")) == 1


def test_a_daily_schedule_skips_other_weekdays(house):
    home, _, clock = house
    home.add_schedule({"device": "lamp", "action": "on"}, time_hhmm="19:00", days=[1, 2])
    clock.t += 45
    home.tick()
    assert home.on_devices() == []


def test_a_timer_runs_once_and_is_removed(house):
    home, _, clock = house
    ok, _, entry = home.add_schedule({"device": "fan", "action": "on"}, at=clock.t + 600)
    assert ok and home.schedules.get(entry["id"])
    clock.t += 601
    home.tick()
    assert [d["id"] for d in home.on_devices()] == ["fan"]
    assert home.schedules.get(entry["id"]) is None


def test_a_long_missed_timer_is_dropped_not_replayed(house):
    home, _, clock = house
    home.add_schedule({"device": "fan", "action": "on"}, at=clock.t + 60)
    clock.t += 60 + 3600
    home.tick()
    assert home.on_devices() == []
    assert home.schedules.entries == []


def test_schedule_validation(house):
    home, _, clock = house
    assert not home.add_schedule({"device": "lamp", "action": "on"}, time_hhmm="25:00")[0]
    assert not home.add_schedule({"device": "ghost", "action": "on"}, time_hhmm="10:00")[0]
    assert not home.add_schedule({"device": "lamp", "action": "on"}, at=clock.t - 5)[0]
    assert not home.add_schedule({"scene": "nope"}, time_hhmm="10:00")[0]


# ── presence ─────────────────────────────────────────────────────────────────

def test_leaving_with_lights_on_raises_a_notice_with_a_button(house):
    home, ctx, clock = house
    state = {"home": True}
    home.presence_fn = lambda: state["home"]
    home.tick()                        # first reading is not a trip
    home.set("lamp", "on")
    state["home"] = False
    clock.t += 5
    home.tick()
    assert home.notices[0]["kind"] == "away"
    assert home.notices[0]["actions"][0]["call"] == "all_off"
    assert home.context()["owner_event"] == "left"
    assert [d["id"] for d in home.on_devices()] == ["lamp"]   # asked, not done


def test_away_auto_off_turns_things_off(house):
    home, _, clock = house
    state = {"home": True}
    home.presence_fn = lambda: state["home"]
    home.update_settings({"away_auto_off": True})
    home.tick()
    home.set("lamp", "on")
    state["home"] = False
    home.tick()
    assert home.on_devices() == []
    assert "Turned off Lamp" in home.notices[0]["text"]


def test_owner_event_expires(house):
    home, _, clock = house
    state = {"home": False}
    home.presence_fn = lambda: state["home"]
    home.tick()
    state["home"] = True
    home.tick()
    assert home.context()["owner_event"] == "arrived"
    clock.t += 121
    assert home.context()["owner_event"] == "none"
    assert home.context()["owner_presence"] == "home"


def test_no_registered_phone_means_home(house):
    home, _, _ = house
    home.presence_fn = lambda: None
    home.tick()
    assert home.context()["owner_presence"] == "home"


def test_the_rule_base_sees_house_context(house):
    home, ctx, _ = house
    home.security_fn = lambda: "danger"
    home.runtime.tick()
    assert ctx.descriptor["security"] == "danger"


def test_left_on_watchdog_warns_once(house):
    home, _, clock = house
    state = {"home": True}
    home.presence_fn = lambda: state["home"]
    home.update_settings({"left_on_minutes": 30})
    home.tick()
    home.set("fan", "on")
    state["home"] = False
    home.tick()
    clock.t += 31 * 60
    home.tick()
    home.tick()
    warned = [n for n in home.notices if n["kind"] == "left_on"]
    assert len(warned) == 1 and warned[0]["actions"][0]["device"] == "fan"


def test_vacation_lights_only_while_away_in_the_window(house):
    home, _, clock = house
    state = {"home": False}
    home.presence_fn = lambda: state["home"]
    home.update_settings({"vacation_mode": True, "vacation_start": "19:00",
                          "vacation_end": "23:00"})
    home.tick()                        # 18:59 -- outside
    assert home.on_devices() == []
    clock.t += 60                      # 19:00:30 -- inside
    home.tick()
    assert [d["id"] for d in home.on_devices()] == ["lamp"]    # lights only
    clock.t = time.mktime((2026, 9, 21, 23, 5, 0, 0, 0, -1))
    home.tick()
    assert home.on_devices() == []


def test_settings_are_validated(house):
    home, _, _ = house
    assert not home.update_settings({"away_auto_off": "yes"})[0]
    assert not home.update_settings({"vacation_start": "7pm"})[0]
    assert not home.update_settings({"left_on_minutes": -1})[0]
    assert not home.update_settings({"bogus": 1})[0]
    assert home.update_settings({"tariff_per_kwh": 8.5})[0]


# ── usage and habits ─────────────────────────────────────────────────────────

def test_usage_counts_on_time_and_energy():
    day = time.mktime((2026, 9, 21, 0, 0, 0, 0, 0, -1))
    entries = [
        {"ts": day + 3600, "device": "lamp", "action": "on", "ok": True},
        {"ts": day + 3 * 3600, "device": "lamp", "action": "off", "ok": True},
        {"ts": day + 4 * 3600, "device": "lamp", "action": "on", "ok": False},
    ]
    out = usage.summary(entries, [LAMP], days=1, now=day + 10 * 3600, tariff_per_kwh=10)
    row = out["devices"][0]
    assert row["hours"] == 2.0 and row["kwh"] == 0.12
    assert out["cost"] == 1.2


def test_usage_carries_state_in_from_before_the_window():
    day = time.mktime((2026, 9, 21, 0, 0, 0, 0, 0, -1))
    entries = [{"ts": day - 3600, "device": "lamp", "action": "on", "ok": True}]
    out = usage.summary(entries, [LAMP], days=1, now=day + 2 * 3600)
    assert out["devices"][0]["hours"] == 2.0


def test_a_weekday_routine_becomes_a_suggestion(house):
    home, ctx, clock = house
    base = time.mktime((2026, 9, 14, 19, 5, 0, 0, 0, -1))       # a Monday
    entries = [{"ts": base + d * 86400 + (d % 3) * 60, "device": "lamp", "action": "on",
                "ok": True, "rule_id": "", "source": "manual"} for d in range(5)]
    found = habits.suggest(entries, ctx.registry, home.schedules, now=clock.t)
    assert len(found) == 1
    s = found[0]
    assert (s["device"], s["action"], s["days"]) == ("lamp", "on", [0, 1, 2, 3, 4])
    assert s["time"] in ("19:05", "19:10")


def test_rule_driven_actions_are_not_habits(house):
    home, ctx, clock = house
    base = time.mktime((2026, 9, 14, 19, 5, 0, 0, 0, -1))
    entries = [{"ts": base + d * 86400, "device": "lamp", "action": "on", "ok": True,
                "rule_id": "r1", "source": "rule"} for d in range(6)]
    assert habits.suggest(entries, ctx.registry, home.schedules, now=clock.t) == []


def test_a_covered_or_dismissed_routine_is_not_offered(house):
    home, ctx, clock = house
    base = time.mktime((2026, 9, 14, 19, 5, 0, 0, 0, -1))
    entries = [{"ts": base + d * 86400, "device": "lamp", "action": "on", "ok": True,
                "source": "manual"} for d in range(5)]
    sid = habits.suggest(entries, ctx.registry, home.schedules, now=clock.t)[0]["id"]
    assert habits.suggest(entries, ctx.registry, home.schedules, dismissed={sid},
                          now=clock.t) == []
    home.add_schedule({"device": "lamp", "action": "on"}, time_hhmm="19:00", days=[0, 1, 2, 3, 4])
    assert habits.suggest(entries, ctx.registry, home.schedules, now=clock.t) == []
