"""Confirm cards: Narada proposes, a person taps, the site's own endpoint decides."""
import Garuda_web as gw


def propose(name, args=None, user="admin", role="admin", voice=False):
    gw.AGENT._turn.confirms = []
    gw.AGENT._turn.voice = voice
    gw.AGENT._turn.key = user
    out = gw.AGENT._run_tool(name, args or {}, user, role)
    return out, list(gw.AGENT._turn.confirms)


def test_a_confirm_capability_is_proposed_not_done(app_client, monkeypatch):
    monkeypatch.setattr(gw.STATE.config, "known_devices", [])
    out, cards = propose("add_tracked_phone", {"name": "Asha's phone", "mac": "aa:bb:cc:dd:ee:ff"})
    assert "NOT done" in out["waiting"] and "_action" not in out
    assert gw.STATE.config.known_devices == []
    assert cards[0]["title"] == "Add tracked phone"
    assert {"name": "mac", "value": "aa:bb:cc:dd:ee:ff"} in cards[0]["lines"]


def test_confirming_runs_it_as_the_person(app_client, admin_headers, monkeypatch):
    monkeypatch.setattr(gw.STATE.config, "known_devices", [])
    _, cards = propose("add_tracked_phone", {"name": "Asha's phone", "mac": "aa:bb:cc:dd:ee:ff"})
    r = app_client.post(f"/api/narada/actions/{cards[0]['id']}/confirm", json={},
                        headers=admin_headers)
    assert r.status_code == 200, r.text
    assert gw.STATE.config.known_devices == [{"name": "Asha's phone", "mac": "aa:bb:cc:dd:ee:ff"}]
    # A card is used once.
    assert app_client.post(f"/api/narada/actions/{cards[0]['id']}/confirm", json={},
                           headers=admin_headers).status_code == 404


def test_a_card_belongs_to_the_person_who_asked(app_client, user_headers, monkeypatch):
    monkeypatch.setattr(gw.STATE.config, "known_devices", [])
    _, cards = propose("add_tracked_phone", {"name": "x", "mac": "aa:bb:cc:dd:ee:ff"})
    r = app_client.post(f"/api/narada/actions/{cards[0]['id']}/confirm", json={},
                        headers=user_headers)
    assert r.status_code == 404 and gw.STATE.config.known_devices == []


def test_a_user_is_not_even_shown_an_admin_card(app_client):
    out, cards = propose("delete_user", {"username": "someone"}, user="asha", role="user")
    assert out == {"error": "only an admin can do that"} and cards == []


def test_nothing_is_proposed_by_voice(app_client):
    out, cards = propose("emergency_stop", voice=True)
    assert "tap" in out["error"] and cards == []


def test_cancel_drops_the_card(app_client, admin_headers):
    _, cards = propose("emergency_stop")
    assert app_client.post(f"/api/narada/actions/{cards[0]['id']}/cancel",
                           headers=admin_headers).status_code == 200
    assert cards[0]["id"] not in [e["id"] for e in gw.AGENT.confirmations.waiting("admin")]


def test_a_password_is_typed_on_the_card_and_never_comes_from_the_model(app_client, admin_headers,
                                                                        monkeypatch):
    monkeypatch.setattr(gw.STATE.auth, "users", dict(gw.STATE.auth.users))
    monkeypatch.setattr(gw, "save_users", lambda: None)
    out, cards = propose("add_user", {"username": "ravi_k", "display_name": "Ravi",
                                      "password": "FromTheModel#1", "role": "admin"})
    card = cards[0]
    assert all(line["name"] not in ("password", "role") for line in card["lines"])
    assert card["typed"] == [{"name": "password", "label": "Password", "secret": True,
                              "required": True}]
    url = f"/api/narada/actions/{card['id']}/confirm"
    assert app_client.post(url, json={}, headers=admin_headers).status_code == 400
    # A weak password is the endpoint's refusal; the card can be tried again.
    assert app_client.post(url, json={"fields": {"password": "abc"}},
                           headers=admin_headers).status_code == 400
    r = app_client.post(url, json={"fields": {"password": "Str0ng!Passw0rd#26"}},
                        headers=admin_headers)
    assert r.status_code == 200, r.text
    assert gw.STATE.auth.users["ravi_k"]["role"] == "user"


def test_an_injected_turn_cannot_propose(app_client):
    from basic_pipelines.garuda_auto.agent import CHANGING_TOOLS
    assert {"delete_device", "add_user", "emergency_stop"} <= set(CHANGING_TOOLS)
