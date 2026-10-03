"""The service base (garuda_core) and how Garuda_web uses it."""
import json
import os
import tarfile
import threading
import time

import pytest

import conftest  # noqa: F401
import Garuda_web as gw
from basic_pipelines.garuda_core import API_VERSION
from basic_pipelines.garuda_core.backup import BackupManager
from basic_pipelines.garuda_core.settings import Settings
from basic_pipelines.garuda_core.workers import Supervisor


# ── settings ─────────────────────────────────────────────────────────────────

def test_settings_report_what_is_missing_without_leaking_values(monkeypatch):
    for name in ("EMAIL_SENDER", "EMAIL_SENDER_PASS", "EMAIL_RECIPIENTS", "NIM_API_KEY"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("GARUDA_EVAL_OTP_BYPASS", "1")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk-secret-value")
    monkeypatch.setenv("ELEVENLABS_SPEECH_ENGINE_ID", "")
    s = Settings.load()
    text = json.dumps(s.problems()) + json.dumps(s.public())
    assert any(sev == "error" and "OTP_BYPASS" in msg for sev, msg in s.problems())
    assert any("email" in msg.lower() for _, msg in s.problems())
    assert any("half configured" in msg for _, msg in s.problems())
    assert "sk-secret-value" not in text


def test_bad_numbers_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("GARUDA_PORT", "eighty")
    monkeypatch.setenv("GARUDA_BACKUP_KEEP", "0")
    s = Settings.load()
    assert s.port == 8080 and s.backup_keep == 1


# ── supervised workers ───────────────────────────────────────────────────────

def test_a_worker_that_raises_is_restarted_and_reported():
    sup = Supervisor(sleep=lambda s: None)
    runs, done = [], threading.Event()

    def flaky():
        runs.append(1)
        if len(runs) < 3:
            raise RuntimeError("boom")
        done.set()

    sup.spawn("flaky", flaky)
    assert done.wait(5)
    time.sleep(0.05)
    status = sup.status()[0]
    assert status["restarts"] == 2 and status["state"] == "finished"
    assert "boom" in status["last_error"]


def test_a_dead_critical_worker_makes_the_service_unhealthy():
    sup = Supervisor(sleep=lambda s: None)

    def broken():
        raise RuntimeError("no")

    thread = sup.spawn("pipeline", broken, restart=False, critical=True)
    thread.join(5)
    assert sup.healthy() is False
    assert sup.status()[0]["state"] == "failed"


# ── backups ──────────────────────────────────────────────────────────────────

def test_backup_archives_state_and_keeps_only_the_newest(tmp_path):
    (tmp_path / "users.json").write_text('{"a": 1}')
    (tmp_path / "config.json").write_text('{}')
    (tmp_path / "perm_system_log.txt").write_text("not state")
    mgr = BackupManager(tmp_path, keep=2)
    for label in ("20260101-000000", "20260102-000000", "20260103-000000"):
        made = mgr.create(label=label)
    assert sorted(made["files"]) == ["config.json", "users.json"]
    names = [b["name"] for b in mgr.list()]
    assert names == ["garuda-state-20260103-000000.tar.gz", "garuda-state-20260102-000000.tar.gz"]
    archive = mgr.dir / names[0]
    assert oct(archive.stat().st_mode & 0o777) == "0o600"
    with tarfile.open(archive) as tar:
        assert sorted(tar.getnames()) == ["config.json", "users.json"]


def test_backup_with_no_state_writes_nothing(tmp_path):
    mgr = BackupManager(tmp_path)
    assert mgr.create() is None and mgr.list() == []


def test_backup_endpoints_are_admin_only(app_client, user_headers, admin_headers, tmp_path, monkeypatch):
    (tmp_path / "users.json").write_text("{}")
    monkeypatch.setattr(gw.BACKUPS, "data_dir", tmp_path)
    monkeypatch.setattr(gw.BACKUPS, "dir", tmp_path / "backups" / "auto")
    assert app_client.post('/api/system/backups', headers=user_headers).status_code == 403
    r = app_client.post('/api/system/backups', headers=admin_headers)
    assert r.status_code == 200 and r.json()["backup"]["files"] == ["users.json"]
    assert len(app_client.get('/api/system/backups', headers=admin_headers).json()["backups"]) == 1


# ── HTTP layer ───────────────────────────────────────────────────────────────

def test_every_api_route_also_answers_under_the_version_prefix(app_client, user_headers):
    assert app_client.get(f'/api/v{API_VERSION}/session', headers=user_headers).status_code == 200
    # (the shared client keeps cookies; a bad header outranks them)
    assert app_client.get(f'/api/v{API_VERSION}/session', headers={'X-Garuda-Token': 'bad'}).status_code == 401
    assert app_client.get('/api/v99/session').status_code == 404


def test_responses_carry_a_request_id_and_the_api_version(app_client):
    r = app_client.get('/api/health')
    assert len(r.headers['x-request-id']) >= 8 and r.headers['x-garuda-api'] == API_VERSION
    r = app_client.get('/api/health', headers={'X-Request-ID': 'client-trace-0001'})
    assert r.headers['x-request-id'] == 'client-trace-0001'
    r = app_client.get('/api/health', headers={'X-Request-ID': 'bad id\twith spaces'})
    assert r.headers['x-request-id'] != 'bad id\twith spaces'


def test_errors_keep_detail_and_gain_one_shape(app_client):
    r = app_client.get('/api/session')
    body = r.json()
    assert body['detail'] == 'Not authenticated'
    assert body['error'] == {'code': 'not_authenticated', 'status': 401,
                             'message': 'Not authenticated',
                             'request_id': r.headers['x-request-id']}
    r = app_client.post('/api/login', json={'username': 'user'})
    body = r.json()
    assert r.status_code == 422 and isinstance(body['detail'], list)
    assert body['error']['code'] == 'validation_error' and 'password' in body['error']['message']


def test_api_reference_is_not_public(app_client, admin_headers, user_headers):
    for path in ('/docs', '/redoc', '/openapi.json'):
        assert app_client.get(path).status_code == 404
    assert app_client.get('/api/openapi.json', headers={'X-Garuda-Token': 'bad'}).status_code == 401
    assert app_client.get('/api/openapi.json', headers=user_headers).status_code == 403
    spec = app_client.get('/api/openapi.json', headers=admin_headers).json()
    assert spec['paths']['/api/login']['post']['tags'] == ['Auth']
    assert spec['paths']['/api/health']['get']['tags'] == ['System']


# ── health, readiness, meta ──────────────────────────────────────────────────

def test_health_is_public_and_cheap(app_client):
    body = app_client.get('/api/health').json()
    assert body['status'] == 'ok' and body['api_version'] == API_VERSION


def test_ready_reports_a_dead_camera_as_not_ready(app_client, monkeypatch):
    monkeypatch.setattr(gw.STATE.camera, 'frame_ts', 0.0)
    r = app_client.get('/api/ready')
    assert r.status_code == 503 and r.json()['checks']['camera'] is False
    monkeypatch.setattr(gw.STATE.camera, 'frame_ts', time.time())
    r = app_client.get('/api/ready')
    assert r.json()['checks']['camera'] is True


def test_meta_tells_a_client_which_product_and_features(app_client):
    body = app_client.get('/api/meta', headers={'Host': 'garuda.veeramanikanta.in'}).json()
    assert body['product'] == 'security' and body['features']['home_automation'] is False
    body = app_client.get('/api/meta', headers={'Host': 'drishti.veeramanikanta.in'}).json()
    assert body['product'] == 'home' and body['auth']['header'] == 'X-Garuda-Token'


def test_system_info_is_admin_only_and_holds_no_secret(app_client, user_headers, admin_headers):
    assert app_client.get('/api/system/info', headers=user_headers).status_code == 403
    r = app_client.get('/api/system/info', headers=admin_headers)
    info = r.json()
    assert {'build', 'checks', 'workers', 'settings', 'problems', 'backups', 'sessions'} <= set(info)
    assert 'log-flush' in [w['name'] for w in info['workers']]
    for secret in (os.environ.get('EMAIL_SENDER_PASS'), os.environ.get('ELEVENLABS_API_KEY')):
        if secret:
            assert secret not in r.text


# ── compatibility ────────────────────────────────────────────────────────────

def test_native_app_mode_route_is_served(app_client, user_headers):
    r = app_client.post('/api/set-mode', json={'mode': 'dnd', 'value': True}, headers=user_headers)
    assert r.status_code == 200 and gw.STATE.modes.dnd is True


# ── refresh tokens across a restart ──────────────────────────────────────────

def test_refresh_token_survives_a_restart_and_is_stored_as_a_digest(app_client):
    r = app_client.post('/api/login', json={'username': 'user', 'password': 'user'},
                        headers={'Origin': 'https://garuda-26.vercel.app'})
    token = r.json()['refresh_token']
    gw._save_refresh_tokens()
    on_disk = open(gw.REFRESH_TOKENS_FILE).read()
    assert token not in on_disk and gw._rt_digest(token) in on_disk
    assert oct(os.stat(gw.REFRESH_TOKENS_FILE).st_mode & 0o777) == '0o600'
    # "Restart": memory is empty, the file is read back.
    gw.STATE.auth.refresh_tokens.clear()
    gw.STATE.auth.sessions.clear()
    gw._load_refresh_tokens()
    assert gw._user_signed_in('user')
    r2 = app_client.post('/api/refresh', headers={'X-Garuda-Refresh': token})
    assert r2.status_code == 200 and r2.json()['username'] == 'user'


def test_logout_revokes_a_token_known_only_from_disk(app_client):
    r = app_client.post('/api/login', json={'username': 'user', 'password': 'user'},
                        headers={'Origin': 'https://garuda-26.vercel.app'})
    token = r.json()['refresh_token']
    gw._save_refresh_tokens()
    gw.STATE.auth.refresh_tokens.clear()
    gw._load_refresh_tokens()
    app_client.post('/api/logout', headers={'X-Garuda-Refresh': token})
    assert app_client.post('/api/refresh', headers={'X-Garuda-Refresh': token}).status_code == 401
    assert not gw._user_signed_in('user')


def test_tokens_of_deleted_users_are_not_loaded(app_client):
    gw.STATE.auth.refresh_tokens['t'] = {'username': 'ghost', 'role': 'user',
                               'expires': time.time() + 60, 'created_at': time.time()}
    gw._save_refresh_tokens()
    gw.STATE.auth.refresh_tokens.clear()
    gw._load_refresh_tokens()
    assert gw.STATE.auth.persisted_refresh == {}


# ── events database ──────────────────────────────────────────────────────────

def test_events_db_uses_wal_and_records_its_schema_version(app_client):
    import sqlite3
    conn = sqlite3.connect(gw.EVENTS_DB)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == gw._EVENT_DB_MIGRATIONS[-1][0]
    finally:
        conn.close()
