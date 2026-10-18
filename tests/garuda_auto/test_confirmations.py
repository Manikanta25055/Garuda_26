"""Cards waiting for a Confirm tap: kept in a file, so a restart does not lose them."""
import json
import os
import stat

from basic_pipelines.garuda_auto import capabilities
from basic_pipelines.garuda_auto.confirmations import TTL_S, Confirmations


def _cap():
    return next(c for c in capabilities.BY_NAME.values() if c.tier == "confirm")


def test_a_card_outlives_a_restart(tmp_path):
    path = str(tmp_path / "confirmations.json")
    now = [1000.0]
    first = Confirmations(clock=lambda: now[0], path=path)
    card = first.add(_cap(), {"x": 1}, "asha", key="asha")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    again = Confirmations(clock=lambda: now[0], path=path)
    assert [c["id"] for c in again.cards("asha")] == [card["id"]]
    assert again.take(card["id"], "someone else") is None
    assert again.take(card["id"], "asha")["args"] == {"x": 1}
    assert Confirmations(clock=lambda: now[0], path=path).cards("asha") == []


def test_an_expired_card_is_not_brought_back(tmp_path):
    path = str(tmp_path / "confirmations.json")
    now = [1000.0]
    Confirmations(clock=lambda: now[0], path=path).add(_cap(), {}, "asha")
    now[0] += TTL_S + 1
    assert Confirmations(clock=lambda: now[0], path=path).cards("asha") == []
    assert json.load(open(path)) == []


def test_a_shortcut_card_shows_the_shortcut_not_the_json(tmp_path):
    cap = capabilities.BY_NAME["create_shortcut"]
    card = Confirmations(path=str(tmp_path / "c.json")).add(
        cap, {"program": {"name": "x"}}, "asha", shortcut={"name": "x", "when": "When you run it"})
    assert card["shortcut"]["name"] == "x"
    assert all(line["name"] != "program" for line in card["lines"])


def test_a_bad_file_starts_empty(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{not json")
    assert Confirmations(path=str(path)).cards("asha") == []
