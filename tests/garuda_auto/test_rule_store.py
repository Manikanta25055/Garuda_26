import json
from basic_pipelines.garuda_auto.rule_store import RuleStore


def _rule(rid, field, op, value, device, action):
    return {
        "id": rid,
        "source_utterance": f"{action} the {device}",
        "when": {"all": [{"field": field, "op": op, "value": value}]},
        "then": [{"device": device, "action": action}],
        "cooldown_s": 60,
        "enabled": True,
    }


def test_add_then_persist_and_reload(tmp_path):
    path = tmp_path / "rules.json"
    store = RuleStore(str(path))
    ok, reason = store.add(_rule("r_001", "occupancy", "==", "empty", "fan", "off"))
    assert ok is True, reason
    assert len(store.rules) == 1

    reloaded = RuleStore(str(path))
    assert len(reloaded.rules) == 1
    assert reloaded.rules[0]["id"] == "r_001"


def test_add_rejects_invalid_rule(tmp_path):
    store = RuleStore(str(tmp_path / "rules.json"))
    ok, reason = store.add(_rule("r_002", "face_id", "==", "x", "fan", "off"))
    assert ok is False
    assert "face_id" in reason
    assert store.rules == []


def test_detects_opposite_action_on_overlapping_predicate(tmp_path):
    store = RuleStore(str(tmp_path / "rules.json"))
    store.add(_rule("r_003", "occupancy", "==", "empty", "fan", "off"))
    clash = _rule("r_004", "occupancy", "==", "empty", "fan", "on")
    assert store.find_conflict(clash) is not None


def test_no_conflict_when_predicates_are_disjoint(tmp_path):
    store = RuleStore(str(tmp_path / "rules.json"))
    store.add(_rule("r_005", "occupancy", "==", "empty", "fan", "off"))
    fine = _rule("r_006", "occupancy", "==", "occupied", "fan", "on")
    assert store.find_conflict(fine) is None


def test_no_conflict_across_different_devices(tmp_path):
    store = RuleStore(str(tmp_path / "rules.json"))
    store.add(_rule("r_007", "occupancy", "==", "empty", "fan", "off"))
    fine = _rule("r_008", "occupancy", "==", "empty", "lamp", "on")
    assert store.find_conflict(fine) is None


def test_delete_removes_and_persists(tmp_path):
    path = tmp_path / "rules.json"
    store = RuleStore(str(path))
    store.add(_rule("r_009", "occupancy", "==", "empty", "fan", "off"))
    assert store.delete("r_009") is True
    assert RuleStore(str(path)).rules == []


def test_rejects_beyond_max_rules(tmp_path, monkeypatch):
    import basic_pipelines.garuda_auto.rule_store as rs
    monkeypatch.setattr(rs, "MAX_RULES", 2)
    store = rs.RuleStore(str(tmp_path / "rules.json"))
    store.add(_rule("r_a", "occupancy", "==", "empty", "fan", "off"))
    store.add(_rule("r_b", "occupancy", "==", "occupied", "fan", "on"))
    ok, reason = store.add(_rule("r_c", "hour", ">=", 22, "lamp", "off"))
    assert ok is False
    assert "limit" in reason.lower()


def test_corrupt_file_does_not_crash(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text("{ not json")
    store = RuleStore(str(path))
    assert store.rules == []


def test_assigns_id_when_missing(tmp_path):
    store = RuleStore(str(tmp_path / "rules.json"))
    rule = _rule("", "occupancy", "==", "empty", "fan", "off")
    del rule["id"]
    ok, _ = store.add(rule)
    assert ok is True
    assert store.rules[0]["id"]
