"""Shortcuts over HTTP and through Narada's confirm card."""
import time

import pytest

import Garuda_web as gw


@pytest.fixture(autouse=True)
def empty_store(monkeypatch, tmp_path):
    store = gw._shortcuts_mod.ShortcutStore(str(tmp_path / "shortcuts.json"))
    monkeypatch.setattr(gw.SHORTCUTS, "store", store)


def device():
    return next(d["id"] for d in gw.HOME.devices() if d["actuator"])


def program(**over):
    base = {"name": "Evening", "trigger": {"type": "manual"},
            "steps": [{"do": "set_device", "args": {"device": device(), "action": "on"}}]}
    base.update(over)
    return base


def test_needs_a_session(app_client):
    assert app_client.get("/api/home/shortcuts").status_code == 401


def test_check_says_it_back_or_gives_the_reason(app_client, user_headers):
    ok = app_client.post("/api/home/shortcuts/check", json={"program": program()},
                         headers=user_headers)
    assert ok.status_code == 200 and ok.json()["rendered"]["when"] == "When you run it"
    bad = app_client.post("/api/home/shortcuts/check",
                          json={"program": program(steps=[{"do": "fly"}])}, headers=user_headers)
    assert bad.status_code == 400 and "no capability" in bad.json()["detail"]


def test_a_user_keeps_and_runs_a_manual_one_and_only_they_or_an_admin_change_it(
        app_client, user_headers, admin_headers):
    made = app_client.post("/api/home/shortcuts", json={"program": program()},
                           headers=user_headers)
    assert made.status_code == 200, made.text
    sid = made.json()["shortcut"]["id"]
    refused = app_client.post("/api/home/shortcuts", headers=user_headers, json={
        "program": program(name="Timed", trigger={"type": "time", "at": "07:00"})})
    assert refused.status_code == 400 and "admin" in refused.json()["detail"]
    ran = app_client.post(f"/api/home/shortcuts/{sid}/run", headers=user_headers)
    assert ran.status_code == 200
    for _ in range(40):
        if gw.SHORTCUTS.store.get(sid)["runs"]:
            break
        time.sleep(0.05)
    assert gw.SHORTCUTS.store.get(sid)["runs"][0]["outcome"] == "done"
    listed = app_client.get("/api/home/shortcuts", headers=admin_headers).json()
    assert listed["shortcuts"][0]["rendered"]["steps"]
    assert app_client.request("DELETE", f"/api/home/shortcuts/{sid}",
                              headers=admin_headers).status_code == 200
    assert gw.SHORTCUTS.store.all() == []


def test_the_vocabulary_names_facts_and_steps(app_client, user_headers, admin_headers):
    mine = app_client.get("/api/home/shortcuts/vocabulary", headers=user_headers).json()
    assert {"time", "weekday", "mode_dnd", "alert", "camera"} <= set(mine["facts"])
    steps = {s["name"] for s in mine["steps"]}
    assert "set_device" in steps and "create_backup" not in steps and "delete_device" not in steps
    theirs = app_client.get("/api/home/shortcuts/vocabulary", headers=admin_headers).json()
    assert "create_backup" in {s["name"] for s in theirs["steps"]}


def test_narada_proposes_a_shortcut_as_a_card_and_confirm_saves_it(app_client, admin_headers):
    gw.AGENT._turn.confirms, gw.AGENT._turn.voice, gw.AGENT._turn.key = [], False, "admin"
    bad = gw.AGENT._run_tool("create_shortcut", {"program": program(steps=[{"do": "fly"}])},
                             "admin", "admin")
    assert "no capability" in bad["error"] and gw.AGENT._turn.confirms == []
    out = gw.AGENT._run_tool("create_shortcut", {"program": program(
        trigger={"type": "time", "at": "22:30"})}, "admin", "admin")
    card = gw.AGENT._turn.confirms[0]
    assert "NOT done" in out["waiting"] and gw.SHORTCUTS.store.all() == []
    assert card["shortcut"]["when"] == "At 22:30 every day" and card["lines"] == []
    r = app_client.post(f"/api/narada/actions/{card['id']}/confirm", json={},
                        headers=admin_headers)
    assert r.status_code == 200, r.text
    assert gw.SHORTCUTS.store.all()[0]["created_by"] == "admin"
    done = gw.AGENT._run_tool("run_shortcut", {"shortcut_id": "evening"}, "admin", "admin")
    assert done["_action"] and "Evening" in done["result"]
