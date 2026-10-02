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
