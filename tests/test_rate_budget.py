"""The global rate limit: whose budget a request spends, and the record kept
of every refusal (garuda_services/middleware.py, garuda_core/busy.py)."""
import pytest

import Garuda_web as gw
from basic_pipelines.garuda_core.busy import Busy


def _anon(app_client):
    app_client._client.cookies.clear()


def test_a_signed_in_budget_is_per_account_not_per_address(app_client, user_token, monkeypatch):
    monkeypatch.setattr(gw, "_RATE_LIMIT_SESSION", 5)
    h = {"X-Garuda-Token": user_token}
    # Two phones on one public address: before, they shared one allowance.
    for i in range(5):
        ip = "203.0.113.7" if i % 2 else "198.51.100.9"
        assert app_client.get("/api/session", headers={**h, "X-Forwarded-For": ip}).status_code == 200
    r = app_client.get("/api/session", headers={**h, "X-Forwarded-For": "192.0.2.1"})
    assert r.status_code == 429                     # same person, a new address: same budget
    assert r.headers.get("Retry-After")
    assert "user:user" in gw.STATE.auth.rate_store


def test_a_token_in_the_link_counts_as_signed_in(app_client, user_token, monkeypatch):
    # The picture and video links of a page served from another site carry
    # the session as ?token=, and they were counted against the small
    # signed-out allowance.
    _anon(app_client)
    monkeypatch.setattr(gw, "_RATE_LIMIT", 2)
    for _ in range(4):
        r = app_client.get(f"/api/session?token={user_token}")
        assert r.status_code != 429


def test_opening_the_page_costs_nothing_and_sign_in_has_its_own_budget(app_client, monkeypatch):
    _anon(app_client)
    monkeypatch.setattr(gw, "_RATE_LIMIT", 3)
    for _ in range(10):
        for path in ("/", "/sw.js", "/manifest.json", "/favicon.ico"):
            assert app_client.get(path).status_code != 429, path
    # The signed-out allowance is still whole for the sign-in.
    r = app_client.post("/api/login", json={"username": "user", "password": "user"})
    assert r.status_code == 200, r.text


def test_sign_in_is_still_limited(app_client, monkeypatch):
    _anon(app_client)
    monkeypatch.setattr(gw, "_RATE_LIMIT_SIGNIN", 3)
    codes = [app_client.post("/api/login", json={"username": "nobody", "password": "x"},
                             headers={"X-Forwarded-For": "198.51.100.1"}).status_code
             for _ in range(3)]
    assert 429 not in codes
    r = app_client.post("/api/login", json={"username": "user", "password": "user"},
                        headers={"X-Forwarded-For": "198.51.100.1"})
    assert r.status_code == 429


def test_signed_out_requests_are_limited_per_address(app_client, monkeypatch):
    _anon(app_client)
    monkeypatch.setattr(gw, "_RATE_LIMIT", 3)
    codes = [app_client.get("/api/session", headers={"X-Forwarded-For": "198.51.100.5"}).status_code
             for _ in range(4)]
    assert codes[-1] == 429
    assert app_client.get("/api/session", headers={"X-Forwarded-For": "198.51.100.6"}).status_code != 429


def test_a_refusal_is_recorded_with_what_used_the_budget(app_client, user_token, monkeypatch):
    busy = Busy()
    monkeypatch.setattr(gw, "BUSY", busy)
    monkeypatch.setattr(gw, "_RATE_LIMIT_SESSION", 3)
    h = {"X-Garuda-Token": user_token}
    for _ in range(3):
        app_client.get("/api/captures/abc123def456/file", headers=h)
    assert app_client.get("/api/session", headers=h).status_code == 429
    (refusal,) = busy.refusals
    assert refusal["budget"] == "user:user" and refusal["path"] == "/api/session"
    assert refusal["used_by"] == [{"path": "/api/captures/<id>/file", "count": 3}]


# ── busy.py on its own ────────────────────────────────────────────────────────

def test_top_paths_only_look_at_the_last_minute():
    now = [1000.0]
    busy = Busy(clock=lambda: now[0])
    busy.counted("ip:a", "/api/x")
    now[0] += 61
    busy.counted("ip:a", "/api/y")
    busy.counted("ip:b", "/api/z")
    assert busy.top_paths("ip:a") == [("/api/y", 1)]


def test_a_stall_names_the_requests_in_flight():
    busy = Busy()
    n = busy.enter("GET", "/api/slow")
    entry = busy.stalled(2.5)
    busy.leave(n)
    assert entry["seconds"] == 2.5
    assert [r["path"] for r in entry["in_flight"]] == ["/api/slow"]
    assert busy.in_flight() == []


def test_a_thread_that_holds_a_core_is_named():
    import threading
    busy = Busy()
    me = threading.current_thread().native_id
    rows = busy.sample_threads({me: 10.0, 1: 5.0}, {me: 37.0, 1: 5.3}, 30.0)
    assert rows[0]["tid"] == me and rows[0]["core_share"] == 0.9
    assert rows[0]["thread"] == threading.current_thread().name
    assert len(busy.hogs) == 1 and busy.hogs[0]["tid"] == me


@pytest.mark.asyncio
async def test_the_loop_watch_sees_a_blocked_loop(monkeypatch):
    import asyncio, time
    from basic_pipelines.garuda_core import busy as busy_mod
    monkeypatch.setattr(busy_mod, "STALL_S", 0.2)
    busy = Busy()
    task = asyncio.create_task(busy.watch_loop(period=0.05))
    await asyncio.sleep(0.1)
    time.sleep(0.4)                     # a handler doing blocking work on the loop
    await asyncio.sleep(0.15)
    task.cancel()
    assert busy.stalls and busy.stalls[0]["seconds"] >= 0.2


def test_system_info_reports_it(app_client, admin_headers):
    r = app_client.get("/api/system/info", headers=admin_headers)
    assert r.status_code == 200
    assert set(r.json()["busy"]) >= {"refused_requests", "event_loop_stalls", "busy_threads_now"}
