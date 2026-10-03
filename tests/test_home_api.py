"""/api/home/* over a temp context, with the role rules of the merged app."""
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from basic_pipelines import drishti_api
from basic_pipelines.garuda_auto.agent import HomeAgent
from basic_pipelines.garuda_auto.decision import DecisionEngine, LocalBackend
from basic_pipelines.garuda_auto.digest import Digest
from basic_pipelines.garuda_auto.home import HomeServices
from basic_pipelines.garuda_auto.runtime import DrishtiRuntime
from basic_pipelines.home_api import build_home_router

pytestmark = pytest.mark.integration

LAMP = {"id": "lamp", "name": "Lamp", "type": "light", "room": "study",
        "transport": {"kind": "relay", "channel": 1}}
RULE = {"source_utterance": "lamp off when empty",
        "when": {"all": [{"field": "occupancy", "op": "==", "value": "empty"}]},
        "then": [{"device": "lamp", "action": "off"}]}


def _session(request: Request):
    role = request.headers.get("X-Role")
    if not role:
        raise HTTPException(401, "Not authenticated")
    return {"username": request.headers.get("X-User", role), "role": role}


def _admin(request: Request):
    session = _session(request)
    if session["role"] != "admin":
        raise HTTPException(403, "Admin access required")
    return session


@pytest.fixture
def api(tmp_path):
    ctx = drishti_api.build_context(data_dir=str(tmp_path), relay_channels=(1, 2, 3),
                                    channel_to_pin={1: 17, 2: 27, 3: 22})
    ctx.registry.add(LAMP)
    ctx.rebuild()
    runtime = DrishtiRuntime(ctx)
    home = HomeServices(ctx, runtime, str(tmp_path))
    decision = DecisionEngine(LocalBackend(lambda: ctx.registry.devices, lambda: home.scenes.scenes))
    agent = HomeAgent(ctx, home, None, decision)
    configured = []
    app = FastAPI()
    app.include_router(build_home_router(
        ctx, home, agent, Digest(home), session_dep=_session, admin_dep=_admin,
        ai_configure=lambda fields, actor: configured.append((fields, actor)),
        ai_test=lambda: {"ok": True}))
    client = TestClient(app)
    try:
        yield client, ctx, home, configured
    finally:
        ctx.relay_bank.close()


ADMIN = {"X-Role": "admin", "X-User": "mani"}
USER = {"X-Role": "user", "X-User": "guest"}


def test_everything_needs_a_session(api):
    client, *_ = api
    assert client.get("/api/home/overview").status_code == 401
    assert client.post("/api/home/devices/lamp/set", json={"action": "on"}).status_code == 401


def test_a_user_can_switch_devices_and_it_is_logged_as_them(api):
    client, ctx, home, _ = api
    r = client.post("/api/home/devices/lamp/set", json={"action": "on"}, headers=USER)
    assert r.status_code == 200 and r.json()["state"] == "on"
    entry = client.get("/api/home/activity", headers=USER).json()["entries"][0]
    assert (entry["actor"], entry["source"], entry["device_name"]) == ("guest", "manual", "Lamp")


def test_an_illegal_action_is_a_400(api):
    client, *_ = api
    assert client.post("/api/home/devices/lamp/set", json={"action": "dim"},
                       headers=USER).status_code == 400
    assert client.post("/api/home/devices/ghost/set", json={"action": "on"},
                       headers=USER).status_code == 404


def test_overview_shape(api):
    client, *_ = api
    body = client.get("/api/home/overview", headers=USER).json()
    assert body["devices"][0]["id"] == "lamp"
    assert body["context"]["owner_presence"] == "home"
    assert body["role"] == "user"


def test_device_management_is_admin_only(api):
    client, ctx, *_ = api
    fan = {"id": "fan", "name": "Fan", "type": "fan", "room": "study",
           "transport": {"kind": "relay", "channel": 2}, "watts": 45}
    assert client.post("/api/home/devices", json=fan, headers=USER).status_code == 403
    assert client.post("/api/home/devices", json=fan, headers=ADMIN).status_code == 200
    assert ctx.registry.get("fan")["watts"] == 45
    assert client.patch("/api/home/devices/fan", json={"watts": 60, "room": "bedroom"},
                        headers=ADMIN).status_code == 200
    assert ctx.registry.get("fan")["room"] == "bedroom"
    assert client.patch("/api/home/devices/fan", json={"watts": 99999},
                        headers=ADMIN).status_code == 422
    assert client.delete("/api/home/devices/fan", headers=USER).status_code == 403
    assert client.delete("/api/home/devices/fan", headers=ADMIN).status_code == 200


def test_used_channels_are_reported(api):
    client, *_ = api
    assert client.get("/api/home/device-types", headers=USER).json()["used_channels"] == [1]


def test_scenes_admin_creates_user_runs(api):
    client, _, home, _ = api
    scene = {"name": "Reading", "actions": [{"device": "lamp", "action": "on"}]}
    assert client.post("/api/home/scenes", json=scene, headers=USER).status_code == 403
    assert client.post("/api/home/scenes", json=scene, headers=ADMIN).status_code == 200
    r = client.post("/api/home/scenes/reading/run", headers=USER)
    assert r.status_code == 200 and r.json()["ok"]
    assert home.on_devices()[0]["id"] == "lamp"
    assert client.post("/api/home/scenes/nope/run", headers=USER).status_code == 404


def test_users_may_set_timers_but_not_recurring_schedules(api):
    client, *_ = api
    timer = {"device": "lamp", "action": "off", "in_minutes": 15}
    daily = {"device": "lamp", "action": "on", "time": "19:00", "days": [0, 1, 2, 3, 4]}
    assert client.post("/api/home/schedules", json=timer, headers=USER).status_code == 200
    assert client.post("/api/home/schedules", json=daily, headers=USER).status_code == 403
    r = client.post("/api/home/schedules", json=daily, headers=ADMIN)
    assert r.status_code == 200
    listed = client.get("/api/home/schedules", headers=USER).json()["schedules"]
    assert {s["kind"] for s in listed} == {"once", "daily"}
    assert all(s["next_run"] for s in listed)


def test_only_the_creator_or_an_admin_can_cancel_a_timer(api):
    client, *_ = api
    sid = client.post("/api/home/schedules", json={"device": "lamp", "action": "off",
                                                   "in_minutes": 15},
                      headers=USER).json()["schedule"]["id"]
    other = {"X-Role": "user", "X-User": "someone_else"}
    assert client.delete(f"/api/home/schedules/{sid}", headers=other).status_code == 403
    assert client.delete(f"/api/home/schedules/{sid}", headers=USER).status_code == 200


def test_proposals_need_an_admin_to_confirm(api):
    client, ctx, *_ = api
    pid = ctx.pending.add(dict(RULE))
    assert client.post(f"/api/home/proposals/{pid}/confirm", headers=USER).status_code == 403
    assert client.post(f"/api/home/proposals/{pid}/confirm", headers=ADMIN).status_code == 200
    rules = client.get("/api/home/rules", headers=USER).json()
    assert len(rules["rules"]) == 1 and rules["proposals"] == []
    rid = rules["rules"][0]["id"]
    assert client.post(f"/api/home/rules/{rid}/toggle", headers=USER).status_code == 403
    assert client.post(f"/api/home/rules/{rid}/toggle", headers=ADMIN).json()["enabled"] is False


def test_instruct_uses_the_agent_and_needs_nim(api):
    # The fixture's agent has no NIM, so even a clear command changes nothing.
    client, _, home, _ = api
    body = client.post("/api/home/instruct", json={"text": "lamp on"}, headers=USER).json()
    assert body["lane"] == "unavailable" and not home.on_devices()
    assert body["route"]["intent"]["value"] == "device_control"


def test_notices_can_be_dismissed(api):
    client, _, home, _ = api
    item = home.notice("away", "You left with the lamp on.")
    assert client.get("/api/home/overview", headers=USER).json()["notices"][0]["id"] == item["id"]
    client.post(f"/api/home/notices/{item['id']}/dismiss", headers=USER)
    assert home.notices == []


def test_settings_admin_only_and_validated(api):
    client, *_ = api
    assert client.post("/api/home/settings", json={"settings": {"away_auto_off": True}},
                       headers=USER).status_code == 403
    assert client.post("/api/home/settings", json={"settings": {"away_auto_off": "x"}},
                       headers=ADMIN).status_code == 400
    assert client.post("/api/home/settings", json={"settings": {"away_auto_off": True}},
                       headers=ADMIN).status_code == 200
    assert client.get("/api/home/settings", headers=USER).json()["away_auto_off"] is True


def test_ai_settings_are_admin_only(api):
    client, *_, configured = api
    assert client.post("/api/home/ai", json={"nim_api_key": "nvapi-x"}, headers=USER).status_code == 403
    assert client.get("/api/home/ai", headers=USER).status_code == 403
    assert client.post("/api/home/ai", json={"nim_api_key": "nvapi-x"}, headers=ADMIN).status_code == 200
    assert configured == [({"nim_api_key": "nvapi-x"}, "mani")]
    status = client.get("/api/home/ai", headers=ADMIN).json()
    assert "nvapi" not in str(status)


def test_usage_and_digest(api):
    client, *_ = api
    client.post("/api/home/devices/lamp/set", json={"action": "on"}, headers=USER)
    use = client.get("/api/home/usage?days=3", headers=USER).json()
    assert len(use["days"]) == 3 and use["devices"][0]["id"] == "lamp"
    digest = client.get("/api/home/digest", headers=USER).json()
    assert digest["source"] == "local" and "1 device actions" in digest["text"]


def test_cross_origin_front_end_may_patch_and_delete():
    Garuda_web = pytest.importorskip("Garuda_web")
    client = TestClient(Garuda_web.fastapi_app)
    for method in ("PATCH", "DELETE"):
        r = client.options("/api/home/scenes/x", headers={
            "Origin": "https://garuda.veeramanikanta.in",
            "Access-Control-Request-Method": method})
        assert r.status_code == 200, method


def test_adding_a_device_does_not_switch_the_others_off(api):
    client, ctx, home, _ = api
    client.post("/api/home/devices/lamp/set", json={"action": "on"}, headers=USER)
    fan = {"id": "fan", "name": "Fan", "type": "fan", "room": "study",
           "transport": {"kind": "relay", "channel": 2}}
    assert client.post("/api/home/devices", json=fan, headers=ADMIN).status_code == 200
    assert ctx.device_router.state("lamp") == "on"
    client.delete("/api/home/devices/fan", headers=ADMIN)
    assert ctx.device_router.state("lamp") == "on"


@pytest.mark.parametrize("field", ["nim_api_key", "nim_model", "jev_api_key"])
def test_ai_settings_refuse_line_breaks(api, field):
    client, *_, configured = api
    r = client.post("/api/home/ai", json={field: "x\nGARUDA_EVAL_OTP_BYPASS=1"}, headers=ADMIN)
    assert r.status_code == 422 and configured == []


def test_env_writer_refuses_line_breaks(tmp_path):
    from basic_pipelines.garuda_auto.envfile import set_vars
    env = tmp_path / ".env"
    with pytest.raises(ValueError):
        set_vars(env, {"NIM_API_KEY": "a\nGARUDA_EVAL_OTP_BYPASS=1"})
    set_vars(env, {"NIM_API_KEY": "nvapi-ok"})
    assert env.read_text() == "NIM_API_KEY=nvapi-ok\n"
