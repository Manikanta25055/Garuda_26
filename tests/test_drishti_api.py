import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from basic_pipelines import drishti_api, drishti_auth

pytestmark = pytest.mark.integration

LAMP = {"id": "lamp_desk", "name": "Desk lamp", "type": "light", "room": "study",
        "transport": {"kind": "relay", "channel": 1}}

RULE = {
    "source_utterance": "turn the desk lamp off when the room is empty",
    "when": {"all": [{"field": "occupancy", "op": "==", "value": "empty"}]},
    "then": [{"device": "lamp_desk", "action": "off"}],
}


def _app(tmp_path):
    ctx = drishti_api.build_context(
        data_dir=str(tmp_path), relay_channels=(1, 2, 3),
        channel_to_pin={1: 17, 2: 27, 3: 22})
    app = FastAPI()
    app.include_router(drishti_api.build_router(ctx))
    return ctx, app


@pytest.fixture
def context(tmp_path):
    """A context, released afterwards.

    RelayBank reserves its pins with gpiozero, and the pin factory is
    process-wide. A context left open leaks those reservations into the next
    test, which then fails with GPIOPinInUse on the same pin.
    """
    ctx, app = _app(tmp_path)
    try:
        yield ctx, app
    finally:
        ctx.relay_bank.close()


@pytest.fixture
def client(context):
    ctx, app = context
    test_client = TestClient(app)
    test_client.cookies.set(drishti_auth.COOKIE_NAME,
                            drishti_auth.create_session("mani", "admin"))
    return test_client, ctx


@pytest.fixture
def anonymous(context):
    _, app = context
    return TestClient(app)


def test_device_types_are_listed(client):
    test_client, _ = client
    body = test_client.get("/api/drishti/device-types").json()
    assert "light" in body["types"]
    assert body["types"]["light"]["actions"] == ["off", "on"]


def test_adding_a_device_widens_the_vocabulary(client):
    test_client, ctx = client
    assert test_client.post("/api/drishti/devices", json=LAMP).status_code == 200
    assert "lamp_desk_state" in ctx.schema.fields
    assert "lamp_desk" in ctx.schema.devices


def test_adding_a_device_with_a_bad_channel_is_refused(client):
    test_client, _ = client
    response = test_client.post("/api/drishti/devices",
                                json={**LAMP, "transport": {"kind": "relay", "channel": 99}})
    assert response.status_code == 400
    assert "channel" in response.json()["detail"]


def test_devices_are_listed_with_state_and_availability(client):
    test_client, _ = client
    test_client.post("/api/drishti/devices", json=LAMP)
    device = test_client.get("/api/drishti/devices").json()["devices"][0]
    assert device["id"] == "lamp_desk"
    assert device["state"] == "off"
    assert device["available"] is True


def test_deleting_a_device_reports_orphaned_rules(client):
    test_client, ctx = client
    test_client.post("/api/drishti/devices", json=LAMP)
    ok, reason = ctx.store.add(dict(RULE))
    assert ok, reason

    body = test_client.delete("/api/drishti/devices/lamp_desk").json()

    assert body["orphaned"] == 1
    assert ctx.store.rules == []
    assert len(ctx.store.orphaned) == 1


def test_deleting_an_unknown_device_is_a_404(client):
    test_client, _ = client
    assert test_client.delete("/api/drishti/devices/ghost").status_code == 404


def test_instruct_answers_a_state_question_locally(client):
    test_client, ctx = client
    ctx.descriptor = {"occupancy": "occupied", "person_count": 1,
                      "temperature_c": 24.5, "humidity_pct": 48.0}
    body = test_client.post("/api/drishti/instruct",
                            json={"text": "is anyone home?"}).json()
    assert body["lane"] == "local"
    assert body["resolved"] == "on-device"


def test_instruct_reports_an_already_known_rule(client):
    test_client, ctx = client
    test_client.post("/api/drishti/devices", json=LAMP)
    ctx.store.add(dict(RULE))
    body = test_client.post(
        "/api/drishti/instruct",
        json={"text": "when the room is empty turn the desk lamp off"}).json()
    assert body["lane"] == "known"
    assert body["rule"]["source_utterance"].startswith("turn the desk lamp off")


def test_a_known_hit_is_logged_with_score_and_backend(client):
    """Paraphrase suppression is the headline metric. If it is not recorded
    here, the number does not exist."""
    test_client, ctx = client
    test_client.post("/api/drishti/devices", json=LAMP)
    ctx.store.add(dict(RULE))
    test_client.post("/api/drishti/instruct",
                     json={"text": "when the room is empty turn the desk lamp off"})
    assert ctx.suppression_log
    entry = ctx.suppression_log[-1]
    assert entry["backend"] in ("fuzzy", "embed")
    assert 0.0 <= entry["score"] <= 1.0


def test_compile_failure_says_existing_rules_still_fire(client):
    test_client, _ = client
    body = test_client.post("/api/drishti/instruct",
                            json={"text": "dim the hallway when it rains"}).json()
    assert body["lane"] == "compile"
    assert body["ok"] is False
    assert body["still_working"] is True
    assert "occupancy" in body["vocabulary"]


def test_confirming_a_proposal_stores_the_rule(client):
    test_client, ctx = client
    test_client.post("/api/drishti/devices", json=LAMP)
    pid = ctx.pending.add(dict(RULE))
    assert test_client.post(f"/api/drishti/proposals/{pid}/confirm").status_code == 200
    assert len(ctx.store.rules) == 1
    assert ctx.pending.get(pid) is None


def test_confirming_an_unknown_proposal_is_a_404(client):
    test_client, _ = client
    assert test_client.post("/api/drishti/proposals/nope/confirm").status_code == 404


def test_discarding_a_proposal_stores_nothing(client):
    test_client, ctx = client
    pid = ctx.pending.add(dict(RULE))
    assert test_client.delete(f"/api/drishti/proposals/{pid}").status_code == 200
    assert ctx.store.rules == []


def test_rules_are_listed_with_a_plain_language_rendering(client):
    test_client, ctx = client
    test_client.post("/api/drishti/devices", json=LAMP)
    ctx.store.add(dict(RULE))
    rule = test_client.get("/api/drishti/rules").json()["rules"][0]
    assert rule["rendered"]["when"] == "occupancy == empty"
    assert rule["rendered"]["then"] == "lamp_desk → off"


def test_toggling_a_rule_flips_enabled(client):
    test_client, ctx = client
    test_client.post("/api/drishti/devices", json=LAMP)
    ctx.store.add(dict(RULE))
    rule_id = ctx.store.rules[0]["id"]
    assert test_client.post(f"/api/drishti/rules/{rule_id}/toggle").json()["enabled"] is False
    assert test_client.post(f"/api/drishti/rules/{rule_id}/toggle").json()["enabled"] is True


def test_deleting_a_rule_removes_it(client):
    test_client, ctx = client
    test_client.post("/api/drishti/devices", json=LAMP)
    ctx.store.add(dict(RULE))
    rule_id = ctx.store.rules[0]["id"]
    assert test_client.delete(f"/api/drishti/rules/{rule_id}").status_code == 200
    assert ctx.store.rules == []


def test_activity_returns_recorded_actuations(client):
    from basic_pipelines.garuda_auto import actuation_log
    test_client, ctx = client
    actuation_log.record(ctx.log_path, device="lamp_desk", action="on",
                         rule_id="r_1", matched=[], ok=True)
    entries = test_client.get("/api/drishti/activity").json()["entries"]
    assert entries[0]["device"] == "lamp_desk"


def test_every_endpoint_requires_a_session(anonymous):
    for path in ("/api/drishti/devices", "/api/drishti/device-types",
                 "/api/drishti/rules", "/api/drishti/proposals",
                 "/api/drishti/activity"):
        assert anonymous.get(path).status_code == 401, path
    assert anonymous.post("/api/drishti/instruct",
                          json={"text": "hi"}).status_code == 401


def test_adding_a_device_requires_admin(context):
    _, app = context
    test_client = TestClient(app)
    test_client.cookies.set(drishti_auth.COOKIE_NAME,
                            drishti_auth.create_session("guest", "user"))
    assert test_client.post("/api/drishti/devices", json=LAMP).status_code == 403


def test_instruct_rejects_an_overlong_instruction(client):
    test_client, _ = client
    response = test_client.post("/api/drishti/instruct", json={"text": "x" * 501})
    assert response.status_code == 422


# ── State and stream (frontend Task 1) ────────────────────────────────────────

def test_state_is_served_to_a_drishti_session(client):
    test_client, ctx = client
    ctx.descriptor = {"occupancy": "occupied", "person_count": 2,
                      "temperature_c": 24.0, "humidity_pct": 50.0}
    body = test_client.get("/api/drishti/state").json()
    assert body["occupancy"] == "occupied"
    assert body["person_count"] == 2
    assert body["temperature_c"] == 24.0
    assert "uptime_s" in body
    assert isinstance(body["modes"], dict)


def test_state_reports_an_empty_house_when_nothing_is_known(client):
    test_client, ctx = client
    ctx.descriptor = {}
    body = test_client.get("/api/drishti/state").json()
    assert body["occupancy"] == "empty"
    assert body["person_count"] == 0
    assert body["temperature_c"] is None


def test_state_merges_whatever_the_host_application_supplies(client):
    test_client, ctx = client
    ctx.system_state = lambda: {"modes": {"night": True}, "uptime_s": 99,
                                "pipeline": "running"}
    body = test_client.get("/api/drishti/state").json()
    assert body["modes"] == {"night": True}
    assert body["uptime_s"] == 99
    assert body["pipeline"] == "running"


def test_state_requires_a_drishti_session(anonymous):
    assert anonymous.get("/api/drishti/state").status_code == 401


def test_stream_requires_a_drishti_session(anonymous):
    assert anonymous.get("/api/drishti/stream").status_code == 401


def test_a_garuda_cookie_does_not_reach_the_drishti_stream(client):
    test_client, _ = client
    test_client.cookies.clear()
    test_client.cookies.set("garuda_session", "whatever")
    assert test_client.get("/api/drishti/stream").status_code == 401


def test_stream_is_503_when_the_host_has_no_camera(client):
    test_client, ctx = client
    assert ctx.frame_source is None
    assert test_client.get("/api/drishti/stream").status_code == 503


def test_stream_serves_the_injected_frame_source(client):
    test_client, ctx = client

    async def frames(request):
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\nJPEG\r\n"

    ctx.frame_source = frames
    response = test_client.get("/api/drishti/stream")
    assert response.status_code == 200
    assert "multipart/x-mixed-replace" in response.headers["content-type"]
    assert b"JPEG" in response.content


# ── Login goes through the injected authenticator ─────────────────────────────

def test_login_rejects_when_no_authenticator_is_wired(anonymous):
    # A context built without a host application must not let anyone in.
    body = {"username": "mani", "password": "whatever"}
    assert anonymous.post("/api/drishti/login", json=body).status_code == 401


def test_login_uses_the_injected_authenticator(context):
    from fastapi.testclient import TestClient
    ctx, app = context
    ctx.authenticate = lambda u, p: "admin" if (u, p) == ("mani", "pw") else None
    test_client = TestClient(app)
    assert test_client.post("/api/drishti/login",
                            json={"username": "mani", "password": "no"}).status_code == 401
    response = test_client.post("/api/drishti/login",
                                json={"username": "mani", "password": "pw"})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "username": "mani", "role": "admin"}
    assert drishti_auth.COOKIE_NAME in response.cookies


def test_a_logged_in_session_reaches_a_protected_route(context):
    from fastapi.testclient import TestClient
    ctx, app = context
    ctx.authenticate = lambda u, p: "user"
    # The cookie is Secure, so the client has to speak https or it is never
    # stored — which is exactly what a browser on http would do too.
    test_client = TestClient(app, base_url="https://testserver")
    test_client.post("/api/drishti/login", json={"username": "mani", "password": "pw"})
    assert test_client.get("/api/drishti/state").status_code == 200


def test_device_types_carries_the_relay_channels(client):
    test_client, _ = client
    body = test_client.get("/api/drishti/device-types").json()
    assert body["channels"] == [1, 2, 3]


def test_device_types_never_exposes_a_bcm_pin(client):
    test_client, _ = client
    # A client that could see the pin map could offer a pin, and a wrong one
    # drives whatever the Hailo HAT, camera or I2C bus is using.
    assert "17" not in test_client.get("/api/drishti/device-types").text
