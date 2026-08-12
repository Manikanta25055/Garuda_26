import json
import pytest
from basic_pipelines.garuda_auto.device_registry import DeviceRegistry
from basic_pipelines.garuda_auto.rule_schema import build_schema
from basic_pipelines.garuda_auto.rule_store import RuleStore

FAN = {"id": "fan", "name": "Fan", "type": "fan", "room": "study",
       "transport": {"kind": "relay", "channel": 2}}
LAMP = {"id": "lamp", "name": "Lamp", "type": "light", "room": "study",
        "transport": {"kind": "relay", "channel": 1}}


@pytest.fixture
def registry(tmp_path):
    r = DeviceRegistry(str(tmp_path / "devices.json"), relay_channels=(1, 2, 3))
    for entry in (FAN, LAMP):
        ok, reason = r.add(entry)
        assert ok, reason
    return r


@pytest.fixture
def schema(registry):
    return build_schema(registry)


def _rule(rid, field, op, value, device, action):
    return {
        "id": rid,
        "source_utterance": f"{action} the {device}",
        "when": {"all": [{"field": field, "op": op, "value": value}]},
        "then": [{"device": device, "action": action}],
        "cooldown_s": 60,
        "enabled": True,
    }


# ── behaviour carried over from before the schema became dynamic ────────────

def test_add_then_persist_and_reload(tmp_path, schema):
    path = tmp_path / "rules.json"
    store = RuleStore(str(path), schema)
    ok, reason = store.add(_rule("r_001", "occupancy", "==", "empty", "fan", "off"))
    assert ok is True, reason
    assert len(store.rules) == 1

    reloaded = RuleStore(str(path), schema)
    assert len(reloaded.rules) == 1
    assert reloaded.rules[0]["id"] == "r_001"


def test_add_rejects_invalid_rule(tmp_path, schema):
    store = RuleStore(str(tmp_path / "rules.json"), schema)
    ok, reason = store.add(_rule("r_002", "face_id", "==", "x", "fan", "off"))
    assert ok is False
    assert "face_id" in reason
    assert store.rules == []


def test_detects_opposite_action_on_overlapping_predicate(tmp_path, schema):
    store = RuleStore(str(tmp_path / "rules.json"), schema)
    store.add(_rule("r_003", "occupancy", "==", "empty", "fan", "off"))
    clash = _rule("r_004", "occupancy", "==", "empty", "fan", "on")
    assert store.find_conflict(clash) is not None


def test_no_conflict_when_predicates_are_disjoint(tmp_path, schema):
    store = RuleStore(str(tmp_path / "rules.json"), schema)
    store.add(_rule("r_005", "occupancy", "==", "empty", "fan", "off"))
    fine = _rule("r_006", "occupancy", "==", "occupied", "fan", "on")
    assert store.find_conflict(fine) is None


def test_no_conflict_across_different_devices(tmp_path, schema):
    store = RuleStore(str(tmp_path / "rules.json"), schema)
    store.add(_rule("r_007", "occupancy", "==", "empty", "fan", "off"))
    fine = _rule("r_008", "occupancy", "==", "empty", "lamp", "on")
    assert store.find_conflict(fine) is None


def test_delete_removes_and_persists(tmp_path, schema):
    path = tmp_path / "rules.json"
    store = RuleStore(str(path), schema)
    store.add(_rule("r_009", "occupancy", "==", "empty", "fan", "off"))
    assert store.delete("r_009") is True
    assert RuleStore(str(path), schema).rules == []


def test_rejects_beyond_max_rules(tmp_path, monkeypatch, schema):
    import basic_pipelines.garuda_auto.rule_store as rs
    monkeypatch.setattr(rs, "MAX_RULES", 2)
    store = rs.RuleStore(str(tmp_path / "rules.json"), schema)
    store.add(_rule("r_a", "occupancy", "==", "empty", "fan", "off"))
    store.add(_rule("r_b", "occupancy", "==", "occupied", "fan", "on"))
    ok, reason = store.add(_rule("r_c", "hour", ">=", 22, "lamp", "off"))
    assert ok is False
    assert "limit" in reason.lower()


def test_corrupt_file_does_not_crash(tmp_path, schema):
    path = tmp_path / "rules.json"
    path.write_text("{ not json")
    store = RuleStore(str(path), schema)
    assert store.rules == []


def test_assigns_id_when_missing(tmp_path, schema):
    store = RuleStore(str(tmp_path / "rules.json"), schema)
    rule = _rule("", "occupancy", "==", "empty", "fan", "off")
    del rule["id"]
    ok, _ = store.add(rule)
    assert ok is True
    assert store.rules[0]["id"]


# ── the data-loss fix ───────────────────────────────────────────────────────

RULE = {
    "id": "r_001",
    "source_utterance": "turn the fan off when the room is empty",
    "when": {"all": [{"field": "occupancy", "op": "==", "value": "empty"}]},
    "then": [{"device": "fan", "action": "off"}],
    "cooldown_s": 60,
    "enabled": True,
}


def test_deleting_a_device_orphans_its_rules_without_losing_them(tmp_path, registry):
    path = str(tmp_path / "rules.json")
    store = RuleStore(path, build_schema(registry))
    ok, reason = store.add(RULE)
    assert ok, reason

    registry.delete("fan")
    reloaded = RuleStore(path, build_schema(registry))

    assert reloaded.rules == []
    assert [r["id"] for r in reloaded.orphaned] == ["r_001"]
    assert reloaded.orphaned[0]["source_utterance"] == RULE["source_utterance"]


def test_orphans_survive_a_save_and_a_second_reload(tmp_path, registry):
    path = str(tmp_path / "rules.json")
    RuleStore(path, build_schema(registry)).add(RULE)
    registry.delete("fan")

    first = RuleStore(path, build_schema(registry))
    first.save()
    second = RuleStore(path, build_schema(registry))

    assert [r["id"] for r in second.orphaned] == ["r_001"]
    assert json.loads(open(path).read())[0]["orphaned"] is True


def test_readding_the_device_restores_the_rule(tmp_path, registry):
    path = str(tmp_path / "rules.json")
    RuleStore(path, build_schema(registry)).add(RULE)
    registry.delete("fan")
    store = RuleStore(path, build_schema(registry))
    assert store.rules == []

    registry.add(FAN)
    store.rebind(build_schema(registry))

    assert [r["id"] for r in store.rules] == ["r_001"]
    assert store.orphaned == []


def test_orphans_do_not_count_towards_the_rule_limit(tmp_path, registry):
    path = str(tmp_path / "rules.json")
    store = RuleStore(path, build_schema(registry))
    store.add(RULE)
    registry.delete("fan")
    store.rebind(build_schema(registry))
    assert len(store.rules) == 0


def test_a_genuinely_malformed_entry_is_kept_as_an_orphan(tmp_path, registry):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps([{"id": "junk", "source_utterance": "x"}]))
    store = RuleStore(str(path), build_schema(registry))
    assert store.rules == []
    assert [r["id"] for r in store.orphaned] == ["junk"]
