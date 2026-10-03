import pytest
from basic_pipelines.garuda_auto.matcher import LocalMatcher


class FakeStore:
    def __init__(self, rules):
        self.rules = rules


def _rule(rid, utterance):
    return {
        "id": rid,
        "source_utterance": utterance,
        "when": {"all": [{"field": "occupancy", "op": "==", "value": "empty"}]},
        "then": [{"device": "fan", "action": "off"}],
        "cooldown_s": 60,
        "enabled": True,
    }


STORE = FakeStore([
    _rule("r1", "turn the fan off when the room is empty for five minutes"),
    _rule("r2", "switch on the lamp when i sit at the desk"),
])


def test_exact_repeat_matches_locally():
    m = LocalMatcher(STORE, backend="fuzzy")
    assert m.match("turn the fan off when the room is empty for five minutes")["id"] == "r1"


def test_close_paraphrase_matches_locally():
    m = LocalMatcher(STORE, backend="fuzzy", threshold=0.6)
    assert m.match("turn off the fan when the room is empty for 5 minutes")["id"] == "r1"


def test_unrelated_request_does_not_match():
    m = LocalMatcher(STORE, backend="fuzzy")
    assert m.match("what is the weather in bangalore tomorrow") is None


def test_matches_the_nearer_of_two_rules():
    m = LocalMatcher(STORE, backend="fuzzy", threshold=0.5)
    assert m.match("switch the lamp on when i sit down at my desk")["id"] == "r2"


def test_empty_store_returns_none():
    m = LocalMatcher(FakeStore([]), backend="fuzzy")
    assert m.match("anything at all") is None


def test_disabled_rules_are_not_matched():
    disabled = _rule("r3", "turn the fan off when the room is empty for five minutes")
    disabled["enabled"] = False
    m = LocalMatcher(FakeStore([disabled]), backend="fuzzy")
    assert m.match("turn the fan off when the room is empty for five minutes") is None


def test_backend_name_is_reported():
    assert LocalMatcher(STORE, backend="fuzzy").backend_name == "fuzzy"


def test_unknown_backend_is_rejected():
    with pytest.raises(ValueError):
        LocalMatcher(STORE, backend="telepathy")
