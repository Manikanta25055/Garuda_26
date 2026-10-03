"""Artifacts over HTTP: served with no origin and no network; calls relayed as the person."""
import pytest

import Garuda_web as gw
from basic_pipelines.garuda_auto.artifacts import ArtifactStore
from basic_pipelines.garuda_routes.artifacts import with_bootstrap

PAGE = "<!doctype html><html><head><title>t</title></head><body>hello</body></html>"


@pytest.fixture
def art(monkeypatch):
    store = ArtifactStore()
    monkeypatch.setattr(gw, "ARTIFACTS", store)
    monkeypatch.setattr(gw.AGENT, "artifacts", store)
    entry, _ = store.add("Energy", PAGE, by="admin")
    return entry


def test_the_page_is_served_by_its_key_with_a_policy_of_its_own(app_client, art):
    assert app_client.get(f"/api/narada/artifacts/{art['id']}/view").status_code == 404
    assert app_client.get(f"/api/narada/artifacts/{art['id']}/view?k=wrong").status_code == 404
    r = app_client.get(f"/api/narada/artifacts/{art['id']}/view?k={art['key']}")
    assert r.status_code == 200 and "garuda.call" not in PAGE and "window.garuda" in r.text
    csp = r.headers["content-security-policy"]
    assert "connect-src 'none'" in csp and "default-src 'none'" in csp
    assert csp.endswith("sandbox allow-scripts") and "frame-ancestors 'self'" in csp
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    # Every other answer keeps the site's policy.
    assert app_client.get("/api/health").headers["x-frame-options"] == "DENY"


def test_the_bridge_goes_first_in_the_head():
    assert with_bootstrap(PAGE).index("<script>") < with_bootstrap(PAGE).index("<title>")
    assert with_bootstrap("<p>bare</p>").startswith("<!doctype html><script>")


def test_a_page_may_use_what_its_owner_may_and_nothing_that_needs_a_card(
        app_client, art, admin_headers, user_headers):
    url = f"/api/narada/artifacts/{art['id']}/call"
    assert app_client.post(url, json={"capability": "get_house_state"}).status_code in (401, 403)
    ok = app_client.post(url, json={"capability": "get_house_state"}, headers=admin_headers)
    assert ok.status_code == 200 and "devices" in ok.json()["result"]
    for name in ("delete_device", "create_shortcut", "remember_fact", "show_artifact", "nope"):
        r = app_client.post(url, json={"capability": name, "args": {}}, headers=admin_headers)
        assert r.status_code == 400, name
    # Of the things that change the house, only what a page of buttons is for.
    for name in ("set_security_mode", "mute_observation", "edit_device", "send_test_email",
                 "start_clip"):
        r = app_client.post(url, json={"capability": name, "args": {}}, headers=admin_headers)
        assert r.status_code == 400 and "ask Narada" in r.json()["detail"], name
    try:
        switched = app_client.post(url, headers=admin_headers, json={
            "capability": "set_device", "args": {"device": "lamp", "action": "on"}})
        assert switched.status_code == 200 and switched.json()["result"]["ok"]
    finally:
        # A (mock) pin is reserved for the whole process once claimed: give it
        # back, or the next test that builds a relay bank of its own cannot.
        gw.DRISHTI_CTX.relay_bank.close()
    # A page in a loop is stopped before it can chatter a relay.
    from basic_pipelines.garuda_routes import artifacts as routes
    codes = [app_client.post(url, json={"capability": "get_house_state"}, headers=admin_headers).status_code
             for _ in range(routes.CALLS_PER_MINUTE + 2)]
    assert codes[0] == 200 and codes[-1] == 429
    # Someone else's page is not theirs to drive.
    assert app_client.post(url, json={"capability": "get_house_state"},
                           headers=user_headers).status_code == 403


def test_keep_and_remove(app_client, art, admin_headers):
    base = f"/api/narada/artifacts/{art['id']}"
    assert app_client.post(base + "/pin", json={"pinned": True},
                           headers=admin_headers).json()["artifact"]["pinned"]
    listed = app_client.get("/api/narada/artifacts", headers=admin_headers).json()["artifacts"]
    assert [a["id"] for a in listed] == [art["id"]]
    assert app_client.request("DELETE", base, headers=admin_headers).status_code == 200
    assert gw.ARTIFACTS.get(art["id"]) is None


def test_old_pages_make_room_but_kept_ones_stay(tmp_path):
    from basic_pipelines.garuda_auto import artifacts as mod
    store = ArtifactStore(str(tmp_path))
    first, _ = store.add("first", PAGE, by="a")
    store.pin(first["id"], True)
    for i in range(mod.MAX_KEPT + 5):
        store.add(f"n{i}", PAGE, by="a")
    assert len(store.all()) == mod.MAX_KEPT and store.get(first["id"]) is not None
    assert ArtifactStore(str(tmp_path)).html(first["id"]) == PAGE
    assert store.html("../../etc/passwd") is None
