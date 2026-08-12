import pytest
from basic_pipelines.garuda_auto.pending_store import PendingStore, MAX_PENDING, TTL_S

pytestmark = pytest.mark.unit

RULE = {
    "source_utterance": "turn the fan off when the room is empty",
    "when": {"all": [{"field": "occupancy", "op": "==", "value": "empty"}]},
    "then": [{"device": "fan", "action": "off"}],
    "cooldown_s": 60,
}


def test_add_returns_an_id_that_fetches_the_rule(tmp_path):
    store = PendingStore(str(tmp_path / "pending.json"))
    pid = store.add(RULE)
    assert store.get(pid)["rule"]["source_utterance"] == RULE["source_utterance"]


def test_pop_returns_once_then_nothing(tmp_path):
    store = PendingStore(str(tmp_path / "pending.json"))
    pid = store.add(RULE)
    assert store.pop(pid) is not None
    assert store.pop(pid) is None


def test_a_conflict_is_carried_with_the_proposal(tmp_path):
    store = PendingStore(str(tmp_path / "pending.json"))
    pid = store.add(RULE, conflict={"id": "r_009", "source_utterance": "keep the fan on"})
    assert store.get(pid)["conflict"]["id"] == "r_009"


def test_proposals_survive_a_reload(tmp_path):
    path = str(tmp_path / "pending.json")
    pid = PendingStore(path).add(RULE)
    assert PendingStore(path).get(pid) is not None


def test_expired_proposals_are_purged(tmp_path):
    now = [1000.0]
    store = PendingStore(str(tmp_path / "pending.json"), clock=lambda: now[0])
    pid = store.add(RULE)
    now[0] += TTL_S + 1
    assert store.purge() == 1
    assert store.get(pid) is None


def test_oldest_is_evicted_past_the_cap(tmp_path):
    store = PendingStore(str(tmp_path / "pending.json"))
    ids = [store.add({**RULE, "source_utterance": f"rule {i}"})
           for i in range(MAX_PENDING + 1)]
    assert store.get(ids[0]) is None
    assert len(store.all()) == MAX_PENDING


def test_corrupt_file_starts_empty(tmp_path):
    path = tmp_path / "pending.json"
    path.write_text("not json at all")
    assert PendingStore(str(path)).all() == []


def test_a_proposal_is_never_a_rule(tmp_path):
    """Pending proposals live in their own file, so nothing that reads the
    rule base can mistake one for something the house knows."""
    path = tmp_path / "pending.json"
    PendingStore(str(path)).add(RULE)
    assert path.name == "pending.json"
    assert not (path.parent / "rules.json").exists()
