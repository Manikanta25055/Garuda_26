from basic_pipelines.garuda_auto.rule_engine import RuleEngine


class FakeStore:
    def __init__(self, rules):
        self.rules = rules


def _rule(rid, conds, device, action, combinator="all", cooldown=60, enabled=True):
    return {
        "id": rid,
        "source_utterance": "x",
        "when": {combinator: conds},
        "then": [{"device": device, "action": action}],
        "cooldown_s": cooldown,
        "enabled": enabled,
    }


BASE = {
    "occupancy": "empty", "person_count": 0, "occupancy_duration_s": 400,
    "zone": "none", "posture": "none", "ambient_luma": 20,
    "temperature_c": 31.0, "humidity_pct": 50.0, "hour": 22,
    "lamp_state": "off", "fan_state": "on",
}


def test_all_combinator_fires_when_every_condition_holds():
    rule = _rule("r1", [
        {"field": "occupancy", "op": "==", "value": "empty"},
        {"field": "occupancy_duration_s", "op": ">=", "value": 300},
    ], "fan", "off")
    engine = RuleEngine(FakeStore([rule]), clock=lambda: 1000.0)
    assert engine.evaluate(BASE) == [{"device": "fan", "action": "off", "rule_id": "r1"}]


def test_all_combinator_does_not_fire_when_one_fails():
    rule = _rule("r2", [
        {"field": "occupancy", "op": "==", "value": "empty"},
        {"field": "occupancy_duration_s", "op": ">=", "value": 900},
    ], "fan", "off")
    engine = RuleEngine(FakeStore([rule]), clock=lambda: 1000.0)
    assert engine.evaluate(BASE) == []


def test_any_combinator_fires_on_a_single_match():
    rule = _rule("r3", [
        {"field": "hour", "op": ">=", "value": 22},
        {"field": "occupancy", "op": "==", "value": "occupied"},
    ], "lamp", "off", combinator="any")
    engine = RuleEngine(FakeStore([rule]), clock=lambda: 1000.0)
    assert engine.evaluate(BASE)[0]["rule_id"] == "r3"


def test_disabled_rule_never_fires():
    rule = _rule("r4", [{"field": "occupancy", "op": "==", "value": "empty"}], "fan", "off", enabled=False)
    engine = RuleEngine(FakeStore([rule]), clock=lambda: 1000.0)
    assert engine.evaluate(BASE) == []


def test_cooldown_suppresses_a_repeat_within_the_window():
    rule = _rule("r5", [{"field": "occupancy", "op": "==", "value": "empty"}], "fan", "off", cooldown=60)
    times = iter([1000.0, 1030.0, 1100.0])
    engine = RuleEngine(FakeStore([rule]), clock=lambda: next(times))
    assert len(engine.evaluate(BASE)) == 1
    assert engine.evaluate(BASE) == []
    assert len(engine.evaluate(BASE)) == 1


def test_first_rule_wins_when_two_target_the_same_device():
    first = _rule("r6", [{"field": "occupancy", "op": "==", "value": "empty"}], "fan", "off")
    second = _rule("r7", [{"field": "hour", "op": ">=", "value": 20}], "fan", "on")
    engine = RuleEngine(FakeStore([first, second]), clock=lambda: 1000.0)
    actions = engine.evaluate(BASE)
    assert actions == [{"device": "fan", "action": "off", "rule_id": "r6"}]


def test_missing_descriptor_field_does_not_raise():
    rule = _rule("r8", [{"field": "posture", "op": "==", "value": "seated"}], "lamp", "on")
    engine = RuleEngine(FakeStore([rule]), clock=lambda: 1000.0)
    incomplete = {k: v for k, v in BASE.items() if k != "posture"}
    assert engine.evaluate(incomplete) == []


def test_type_mismatch_is_treated_as_no_match():
    rule = _rule("r9", [{"field": "temperature_c", "op": ">", "value": 30}], "fan", "on")
    engine = RuleEngine(FakeStore([rule]), clock=lambda: 1000.0)
    broken = {**BASE, "temperature_c": "warm"}
    assert engine.evaluate(broken) == []
