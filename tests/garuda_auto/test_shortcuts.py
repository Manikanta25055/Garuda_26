"""Shortcuts: checked as data, triggered by the clock and by facts, run step by step."""
import time

import pytest

from basic_pipelines.garuda_auto import shortcuts as sc

FIELDS = {"occupancy", "fan_state", "temperature_c", "mode_dnd", "owner_presence"}


def program(**over):
    base = {"name": "Evening", "trigger": {"type": "manual"},
            "steps": [{"do": "set_device", "args": {"device": "fan", "action": "on"}}]}
    base.update(over)
    return base


def check(p, role="admin", **kw):
    return sc.validate(p, role=role, fields=FIELDS, **kw)


@pytest.mark.parametrize("bad, why", [
    (program(name=""), "name"),
    (program(extra=1), "unknown keys"),
    (program(steps=[]), "non-empty"),
    (program(steps=[{"do": "fly"}]), "no capability"),
    (program(steps=[{"do": "delete_device", "args": {"device_id": "fan"}}]), "tap"),
    (program(steps=[{"do": "create_shortcut", "args": {"program": {}}}]), "tap"),
    (program(steps=[{"do": "remember_fact", "args": {"text": "x"}}]), "cannot be a step"),
    (program(steps=[{"do": "set_device", "args": {"device": "fan"}}]), "needs"),
    (program(steps=[{"do": "set_device", "args": {"device": "fan", "action": "on", "x": 1}}]),
     "takes no"),
    (program(steps=[{"wait": 10 ** 6}]), "wait"),
    (program(steps=[{"wait": 5, "stop": True}]), "exactly one"),
    (program(steps=[{"if": {"field": "nope", "op": "==", "value": 1}, "then": [{"stop": True}]}]),
     "unknown field"),
    (program(steps=[{"if": {"field": "occupancy", "op": "~", "value": 1},
                     "then": [{"stop": True}]}]), "operator"),
    (program(steps=[{"repeat": 500, "steps": [{"stop": True}]}]), "repeat"),
    (program(steps=[{"run": "abcd"}]), "no shortcut"),
    (program(trigger={"type": "time", "at": "25:00"}), "HH:MM"),
    (program(trigger={"type": "time", "at": "07:00", "days": [9]}), "days"),
    (program(trigger={"type": "every", "minutes": 0}), "minutes"),
    (program(trigger={"type": "when", "condition": {"between": ["7", "8"]}}), "between"),
    (program(trigger={"type": "sometimes"}), "trigger type"),
    (program(cooldown_s=0), "cooldown"),
])
def test_what_is_refused(bad, why):
    with pytest.raises(sc.Invalid, match=why):
        check(bad)


def test_a_user_may_keep_a_manual_shortcut_but_not_one_that_runs_itself():
    assert check(program(), role="user")["trigger"] == {"type": "manual"}
    with pytest.raises(sc.Invalid, match="only an admin can make"):
        check(program(trigger={"type": "time", "at": "07:00"}), role="user")
    with pytest.raises(sc.Invalid, match="only an admin's shortcut"):
        check(program(steps=[{"do": "create_backup"}]), role="user")


def test_too_many_steps_are_counted_through_nesting():
    inner = [{"wait": 1}] * 21
    with pytest.raises(sc.Invalid, match="at most"):
        check(program(steps=[{"repeat": 2, "steps": inner}, {"repeat": 2, "steps": inner}]))


def test_conditions():
    facts = {"occupancy": "empty", "temperature_c": 31.5, "mode_dnd": "on", "time": "23:10"}
    assert sc.holds({"field": "occupancy", "op": "==", "value": "empty"}, facts)
    assert sc.holds({"field": "temperature_c", "op": ">", "value": 30}, facts)
    assert not sc.holds({"field": "temperature_c", "op": ">", "value": "30"}, facts)
    assert sc.holds({"field": "mode_dnd", "op": "==", "value": True}, facts)
    assert sc.holds({"between": ["22:00", "06:00"]}, facts)
    assert not sc.holds({"between": ["06:00", "22:00"]}, facts)
    assert sc.holds({"all": [{"not": {"field": "occupancy", "op": "==", "value": "occupied"}},
                             {"any": [{"field": "missing", "op": "==", "value": 1},
                                      {"between": ["23:00", "23:30"]}]}]}, facts)


def test_said_in_words():
    out = sc.describe(check(program(
        trigger={"type": "when", "for_minutes": 10,
                 "condition": {"field": "occupancy", "op": "==", "value": "empty"}},
        conditions={"between": ["22:00", "06:00"]},
        steps=[{"do": "all_off", "args": {}}, {"wait": 120},
               {"if": {"field": "fan_state", "op": "==", "value": "on"},
                "then": [{"notify": "Fan is still on", "email": True}], "else": [{"stop": True}]}])))
    assert out["when"] == "When occupancy is empty for 10 min"
    assert out["only_if"] == "between 22:00 and 06:00"
    assert out["steps"] == ["All off", "Wait 2 min", "If fan state is on:",
                            "  Notify by email: Fan is still on", "Otherwise:", "  Stop"]


class House:
    """A clock that moves only when told, facts that are set by hand, and a record of what was done."""

    def __init__(self, tmp_path):
        self.now = time.mktime((2026, 10, 5, 21, 59, 30, 0, 0, -1))      # a Monday
        self.facts = {"occupancy": "occupied", "fan_state": "off", "temperature_c": 25,
                      "mode_dnd": "off", "owner_presence": "home"}
        self.done, self.notices, self.fail = [], [], set()
        self.roles = {"admin": "admin", "asha": "user"}
        self.engine = sc.ShortcutEngine(
            sc.ShortcutStore(str(tmp_path / "shortcuts.json"), clock=lambda: self.now),
            do_fn=self.do, facts_fn=lambda: self.facts, role_of=self.roles.get,
            notify_fn=lambda text, email: self.notices.append((text, email)),
            clock=lambda: self.now, sleep=self.sleep)

    def do(self, name, args, user, role):
        self.done.append((name, args, user, role))
        return {"error": "it broke"} if name in self.fail else {"ok": True, "result": "fine"}

    def sleep(self, seconds):
        self.now += seconds
        return False

    def add(self, p, by="admin"):
        clean = self.engine.check(p, self.roles[by])
        return self.engine.store.add(clean, created_by=by)["id"]

    def run(self, sid, by="admin"):
        return self.engine.run(sid, by=by, wait=True)


@pytest.fixture
def house(tmp_path):
    return House(tmp_path)


def test_steps_run_in_order_and_the_run_is_kept(house):
    sid = house.add(program(steps=[
        {"do": "set_device", "args": {"device": "fan", "action": "on"}}, {"wait": 90},
        {"repeat": 2, "steps": [{"do": "all_off", "args": {}}]},
        {"if": {"field": "temperature_c", "op": ">", "value": 30},
         "then": [{"notify": "hot"}], "else": [{"notify": "It is {temperature_c} and {unknown}"}]},
        {"stop": True}, {"do": "all_off", "args": {}}]))
    started = house.now
    assert house.run(sid)[0]
    assert [d[0] for d in house.done] == ["set_device", "all_off", "all_off"]
    assert house.done[0][2:] == ("admin", "admin")
    assert house.now - started == 90
    assert house.notices == [("It is 25 and {unknown}", False)]
    kept = house.engine.store.get(sid)
    assert kept["runs"][0]["outcome"] == "stopped" and kept["last_run"] == started
    assert sc.ShortcutStore(house.engine.store._path).get(sid)["runs"]     # on disk


def test_a_failed_step_ends_the_run_unless_it_is_optional(house):
    house.fail = {"all_off"}
    sid = house.add(program(steps=[{"do": "all_off", "args": {}},
                                   {"do": "set_device", "args": {"device": "fan", "action": "on"}}]))
    house.run(sid)
    assert len(house.done) == 1
    assert house.engine.store.get(sid)["runs"][0]["outcome"].startswith("failed: all_off")
    other = house.add(program(name="Two", steps=[
        {"do": "all_off", "args": {}, "optional": True},
        {"do": "set_device", "args": {"device": "fan", "action": "on"}}]))
    house.run(other)
    assert house.engine.store.get(other)["runs"][0]["outcome"] == "done"


def test_a_shortcut_runs_as_its_maker_with_the_role_they_have_now(house):
    sid = house.add(program(), by="asha")
    house.run(sid, by="asha")
    assert house.done[-1][2:] == ("asha", "user")
    del house.roles["asha"]
    ok, reason, _ = house.run(sid, by="asha")
    assert not ok and "no longer" in reason


def test_an_admin_running_a_users_shortcut_runs_it_as_a_user(house):
    """A step checked only at run time must not borrow the runner's admin rights."""
    sid = house.add(program(), by="asha")
    house.engine.run(sid, by="admin", role="admin", wait=True)
    assert house.done[-1][2:] == ("admin", "user")
    mine = house.add(program(name="Mine"))
    house.engine.run(mine, by="admin", role="admin", wait=True)
    assert house.done[-1][2:] == ("admin", "admin")


def test_one_shortcut_can_run_another(house):
    inner = house.add(program(name="Inner"))
    outer_clean = house.engine.check({"name": "Outer", "steps": [{"run": inner}]}, "admin")
    outer = house.engine.store.add(outer_clean, created_by="admin")["id"]
    house.run(outer)
    assert [d[0] for d in house.done] == ["set_device"]
    assert house.engine.store.get(inner)["runs"][0]["cause"] == "shortcut"


def test_the_clock_trigger_fires_once_in_its_minute(house):
    house.add(program(trigger={"type": "time", "at": "22:00", "days": [0]}))
    for _ in range(90):
        house.engine.tick()
        house.now += 1
        time.sleep(0)
    time.sleep(0.2)
    assert len(house.done) == 1


def test_a_when_trigger_fires_on_becoming_true_and_not_again_until_false(house):
    house.add(program(cooldown_s=5, trigger={
        "type": "when", "for_minutes": 1,
        "condition": {"field": "occupancy", "op": "==", "value": "empty"}}))

    def ticks(seconds):
        for _ in range(seconds):
            house.engine.tick()
            house.now += 1
        time.sleep(0.15)

    ticks(5)
    assert house.done == []
    house.facts["occupancy"] = "empty"
    ticks(50)
    assert house.done == []                 # not yet a minute
    ticks(15)
    assert len(house.done) == 1
    ticks(300)
    assert len(house.done) == 1             # still empty: the same stretch
    house.facts["occupancy"] = "occupied"
    ticks(3)
    house.facts["occupancy"] = "empty"
    ticks(70)
    assert len(house.done) == 2


def test_conditions_gate_an_automatic_run(house):
    house.add(program(trigger={"type": "every", "minutes": 1},
                      conditions={"field": "owner_presence", "op": "==", "value": "away"}))
    for _ in range(130):
        house.engine.tick()
        house.now += 1
    time.sleep(0.15)
    assert house.done == []
    house.facts["owner_presence"] = "away"
    for _ in range(70):
        house.engine.tick()
        house.now += 1
    time.sleep(0.15)
    assert len(house.done) == 1


def test_names_are_unique_and_found_by_words(house):
    house.add(program(name="Movie Night"))
    with pytest.raises(sc.Invalid, match="already"):
        house.add(program(name="movie night"))
    assert house.engine.store.find("movie")["name"] == "Movie Night"


def test_a_restart_does_not_run_a_when_shortcut_again(house, tmp_path):
    """The service restarts; the condition has held all along. That is not a new moment."""
    sid = house.add(program(cooldown_s=5, trigger={
        "type": "when", "condition": {"field": "occupancy", "op": "==", "value": "empty"}}))
    house.facts["occupancy"] = "empty"
    house.engine.tick()
    time.sleep(0.15)
    assert len(house.done) == 1
    again = sc.ShortcutEngine(sc.ShortcutStore(house.engine.store._path, clock=lambda: house.now),
                              do_fn=house.do, facts_fn=lambda: house.facts, role_of=house.roles.get,
                              clock=lambda: house.now, sleep=house.sleep)
    for _ in range(5):
        again.tick()
        house.now += 1
    time.sleep(0.15)
    assert len(house.done) == 1
    house.facts["occupancy"] = "occupied"
    again.tick()
    house.facts["occupancy"] = "empty"
    house.now += 10
    again.tick()
    time.sleep(0.15)
    assert len(house.done) == 2 and again.store.get(sid)["runs"]


def test_a_restart_inside_its_minute_does_not_run_a_timed_shortcut_twice(house):
    house.add(program(trigger={"type": "time", "at": "22:00", "days": [0]}))
    house.now += 31                                  # 22:00:01
    house.engine.tick()
    time.sleep(0.15)
    again = sc.ShortcutEngine(sc.ShortcutStore(house.engine.store._path, clock=lambda: house.now),
                              do_fn=house.do, facts_fn=lambda: house.facts, role_of=house.roles.get,
                              clock=lambda: house.now, sleep=house.sleep)
    house.now += 20
    again.tick()
    time.sleep(0.15)
    assert len(house.done) == 1


def test_a_notice_fills_in_facts_and_nothing_else():
    facts = {"temperature_c": 27, "time": "22:00"}
    assert sc.fill("It is {temperature_c} at {time}", facts) == "It is 27 at 22:00"
    assert sc.fill("{time.__class__} {0} {nope} {{time}}", facts) == "{time.__class__} {0} {nope} {22:00}"


def test_what_cannot_be_a_step():
    assert {"take_snapshot", "show_artifact", "hand_to_planner", "create_shortcut"} <= sc.NOT_STEPS


def test_a_shortcut_that_silences_alerts_says_so():
    quiet = check(program(trigger={"type": "when", "condition": {"field": "owner_presence", "op": "==", "value": "away"}},
                          steps=[{"do": "all_off", "args": {}},
                                 {"if": {"field": "occupancy", "op": "==", "value": "empty"},
                                  "then": [{"do": "set_security_mode", "args": {"mode": "idle", "on": True}}]}]))
    assert sc.cautions(quiet) == ["Turns on idle mode: all alerts off while it is on."]
    assert sc.cautions(check(program(steps=[{"do": "set_security_mode", "args": {"mode": "night", "on": True}},
                                            {"do": "set_security_mode", "args": {"mode": "idle", "on": False}}]))) == []
    assert "every 2 min" in sc.cautions(check(program(trigger={"type": "every", "minutes": 2})))[0]


def test_a_second_without_facts_is_not_a_change_of_state(house):
    house.add(program(cooldown_s=5, trigger={
        "type": "when", "condition": {"field": "occupancy", "op": "==", "value": "empty"}}))
    house.facts["occupancy"] = "empty"
    house.engine.tick()
    time.sleep(0.15)
    good = house.engine.facts_fn

    def broken():
        raise RuntimeError("the state could not be read")
    house.engine.facts_fn = broken
    house.now += 30
    house.engine.tick()
    house.engine.facts_fn = good
    house.now += 30
    house.engine.tick()
    time.sleep(0.15)
    assert len(house.done) == 1 and "could not be read" in house.engine.last_error


def test_stopping_a_shortcut_stops_the_one_it_is_running(house):
    import threading
    inner = house.add(program(name="Inner", steps=[{"wait": 600}, {"do": "all_off", "args": {}}]))
    outer = house.engine.store.add(house.engine.check(
        {"name": "Outer", "steps": [{"run": inner}, {"do": "all_off", "args": {}}]}, "admin"),
        created_by="admin")["id"]
    gate = threading.Event()
    house.engine._sleep = lambda seconds: gate.wait(2) and False
    house.engine.run(outer, by="admin")
    for _ in range(50):
        if any(r["shortcut"] == inner for r in house.engine.running()):
            break
        time.sleep(0.02)
    assert house.engine.cancel(outer)
    gate.set()
    for _ in range(100):
        if not house.engine.running():
            break
        time.sleep(0.02)
    assert house.done == [] and house.engine.store.get(inner)["runs"][0]["outcome"] == "cancelled"


# ── a restart in the middle of a run ──────────────────────────────────────────

def _stop_after(house, seconds):
    """The service stops `seconds` into the run: the next sleep past that says so."""
    stop_at = house.now + seconds

    def sleep(s):
        if house.now + s >= stop_at:
            house.now = stop_at
            house.engine._stop.set()
            return True
        house.now += s
        return False
    house.engine._sleep = sleep


def _restarted(house, tmp_path):
    house.engine = sc.ShortcutEngine(
        sc.ShortcutStore(house.engine.store._path, clock=lambda: house.now),
        do_fn=house.do, facts_fn=lambda: house.facts, role_of=house.roles.get,
        notify_fn=lambda text, email: house.notices.append((text, email)),
        clock=lambda: house.now, sleep=house.sleep)
    return house.engine


LAMP_TIMER = [{"do": "set_device", "args": {"device": "lamp", "action": "on"}},
              {"wait": 5400},
              {"do": "set_device", "args": {"device": "lamp", "action": "off"}}]


def test_a_restart_during_a_wait_carries_on_with_the_rest_of_it(house, tmp_path):
    sid = house.add(program(steps=LAMP_TIMER))
    started = house.now
    _stop_after(house, 600)
    assert house.run(sid)[0]
    assert [d[1]["action"] for d in house.done] == ["on"]
    assert house.engine.store.get(sid)["runs"] == []          # not over, not recorded
    house.now += 120                                          # the restart takes two minutes
    engine = _restarted(house, tmp_path)
    engine._resume_waits(wait=True)
    assert [d[1]["action"] for d in house.done] == ["on", "off"]
    assert house.now - started == 5400                        # the wait kept its end
    run = engine.store.get(sid)["runs"][0]
    assert run["outcome"] == "done" and run["started"] == started
    assert engine.store.waits() == {}


def test_a_wait_inside_an_if_a_repeat_and_another_shortcut_is_found_again(house, tmp_path):
    inner = house.add(program(name="Inner", steps=LAMP_TIMER))
    outer_clean = house.engine.check({"name": "Outer", "steps": [
        {"repeat": 2, "steps": [{"if": {"field": "occupancy", "op": "==", "value": "occupied"},
                                 "then": [{"run": inner}]}]},
        {"notify": "all done"}]}, "admin")
    outer = house.engine.store.add(outer_clean, created_by="admin")["id"]
    _stop_after(house, 5400 + 60)                             # in the second round's wait
    house.run(outer)
    assert [d[1]["action"] for d in house.done] == ["on", "off", "on"]
    assert list(house.engine.store.waits()) == [outer]
    house.facts["occupancy"] = "empty"                        # the if is not asked again
    engine = _restarted(house, tmp_path)
    engine._resume_waits(wait=True)
    assert [d[1]["action"] for d in house.done] == ["on", "off", "on", "off"]
    assert house.notices == [("all done", False)]
    assert engine.store.get(outer)["runs"][0]["outcome"] == "done"


@pytest.mark.parametrize("change, why", [
    ("edit", "changed"), ("off", "switched off"), ("late", "down for too long")])
def test_a_wait_is_not_picked_up_when_things_moved_on(house, tmp_path, change, why):
    sid = house.add(program(steps=LAMP_TIMER))
    _stop_after(house, 600)
    house.run(sid)
    store = house.engine.store
    if change == "edit":
        store.replace(sid, {"steps": LAMP_TIMER[:1]})
    elif change == "off":
        store.toggle(sid)
    else:
        house.now += 5400 + sc.MAX_LATE_S
    engine = _restarted(house, tmp_path)
    engine._resume_waits(wait=True)
    assert [d[1]["action"] for d in house.done] == ["on"]
    run = engine.store.get(sid)["runs"][0]
    assert run["outcome"].startswith("not picked up") and why in run["outcome"]
    assert engine.store.waits() == {}


def test_cancelling_a_waiting_run_forgets_its_place(house):
    sid = house.add(program(steps=LAMP_TIMER))

    def sleep(s):
        house.engine.cancel(sid)
        house.now += s
        return False
    house.engine._sleep = sleep
    house.run(sid)
    assert house.engine.store.get(sid)["runs"][0]["outcome"] == "cancelled"
    assert house.engine.store.waits() == {}


# ── sunrise and sunset ────────────────────────────────────────────────────────

def test_a_sun_trigger_needs_the_location_and_says_itself_in_words():
    trig = {"type": "sun", "event": "sunset", "offset_minutes": -30}
    with pytest.raises(sc.Invalid, match="location"):
        check(program(trigger=trig))
    clean = sc.validate(program(trigger=trig), role="admin", fields=FIELDS | {"sunrise", "sunset"})
    assert clean["trigger"]["days"] == list(range(7))
    assert sc.describe(clean)["when"] == "At 30 min before sunset"
    with pytest.raises(sc.Invalid, match="offset"):
        sc.validate(program(trigger={**trig, "offset_minutes": 600}), role="admin",
                    fields=FIELDS | {"sunset"})


def test_a_sun_trigger_fires_once_at_its_shifted_minute(house):
    house.facts.update(sunrise="06:09", sunset="22:30", sun="up")
    house.add(program(trigger={"type": "sun", "event": "sunset", "offset_minutes": -30}))
    for _ in range(90):          # 21:59:30 to 22:01
        house.engine.tick()
        house.now += 1
    time.sleep(0.2)
    assert len(house.done) == 1


def test_sun_times_are_local_and_missing_in_a_polar_night():
    from basic_pipelines.garuda_auto import sun
    noon = time.mktime((2026, 6, 21, 12, 0, 0, 0, 0, -1))
    t = sun.times(17.385, 78.4867, noon)
    assert t["sunrise"] < "12:00" < t["sunset"]
    assert sun.times(78.2, 15.6, time.mktime((2026, 12, 21, 12, 0, 0, 0, 0, -1))) == {}
    assert sun.shift("23:50", 20) == "00:10" and sun.shift("00:10", -20) == "23:50"


# ── alerts and captures ──────────────────────────────────────────────────────

ALERT_FIELDS = FIELDS | {"alert", "night_presence_alert", "camera_covered"}


def test_an_alert_trigger_is_checked_and_said_in_words():
    clean = sc.validate(program(trigger={"type": "alert"}), role="admin", fields=ALERT_FIELDS)
    assert clean["trigger"] == {"type": "alert", "kind": "any"}
    assert sc.describe(clean)["when"] == "When an alert starts"
    danger = sc.validate(program(trigger={"type": "alert", "kind": "danger"}), role="admin",
                         fields=ALERT_FIELDS)
    assert sc.describe(danger)["when"] == "When a danger alert starts"
    with pytest.raises(sc.Invalid, match="kind is one of"):
        sc.validate(program(trigger={"type": "alert", "kind": "fire"}), role="admin",
                    fields=ALERT_FIELDS)
    with pytest.raises(sc.Invalid, match="does not report"):
        check(program(trigger={"type": "alert"}))
    with pytest.raises(sc.Invalid, match="only an admin"):
        sc.validate(program(trigger={"type": "alert"}), role="user", fields=ALERT_FIELDS)


def test_showing_in_the_chat_is_not_a_step_and_says_what_is():
    with pytest.raises(sc.Invalid, match="use capture_snapshot"):
        check(program(steps=[{"do": "take_snapshot"}]))
    with pytest.raises(sc.Invalid, match="use capture_audio"):
        check(program(steps=[{"do": "play_room_sound", "args": {"seconds": 10}}]))


def test_capture_steps_are_steps():
    clean = check(program(steps=[{"do": "capture_snapshot"},
                                 {"do": "capture_audio", "args": {"seconds": 10}},
                                 {"do": "capture_video", "args": {"seconds": 10, "wait": False}},
                                 {"do": "email_captures"}]), role="user")
    assert sc.describe(clean)["steps"][1] == "Capture audio: seconds 10"
    assert sc.cautions({**clean, "trigger": {"type": "alert"}}) == ["Sends email each time it runs."]


def test_on_an_alert_a_picture_and_ten_seconds_of_sound_are_saved_together(house):
    house.facts.update(alert="clear", night_presence_alert="off", camera_covered="no")
    sid = house.add({"name": "Alert evidence", "trigger": {"type": "alert"},
                     "steps": [{"do": "capture_snapshot"},
                               {"do": "capture_audio", "args": {"seconds": 10}},
                               {"do": "email_captures", "args": {"note": "look"}}]})
    for _ in range(3):
        house.engine.tick()
        house.now += 1
    time.sleep(0.15)
    assert house.done == []
    house.facts["alert"] = "active"
    house.engine.tick()
    time.sleep(0.2)
    assert [d[0] for d in house.done] == ["capture_snapshot", "capture_audio", "email_captures"]
    run_id = house.engine.store.get(sid)["runs"][0]["id"]
    assert all(d[1]["group"] == run_id for d in house.done)
    assert [d[1].get("label") for d in house.done] == ["Alert evidence", "Alert evidence", None]
    assert house.done[1][1]["seconds"] == 10 and house.done[2][1]["note"] == "look"
    house.now += 120
    house.engine.tick()                     # the same alert: not again
    time.sleep(0.15)
    assert len(house.done) == 3
    house.facts["alert"] = "clear"
    house.engine.tick()
    house.facts["night_presence_alert"] = "on"     # any kind of alert
    house.engine.tick()
    time.sleep(0.2)
    assert len(house.done) == 6
    assert house.done[3][1]["group"] != run_id


def test_a_step_may_name_its_own_captures(house):
    sid = house.add(program(steps=[{"do": "email_captures", "args": {"ids": ["abc"]}},
                                   {"do": "capture_snapshot", "args": {"label": "door"}}]))
    house.run(sid)
    assert "group" not in house.done[0][1] and house.done[1][1]["label"] == "door"
