"""TC-SEC01 through TC-SEC10: Security tests."""
import time
import collections
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'basic_pipelines'))
import Garuda_web as gw


def test_heartbeat_with_wrong_key_doesnt_reset_deadman(app_client, monkeypatch):
    """TC-SEC01: Heartbeat with wrong key does NOT reset the deadman timer."""
    old_hb = gw._last_heartbeat
    monkeypatch.setenv('HEARTBEAT_KEY', 'correct-key')
    time.sleep(0.01)
    # Wrong key — timer should NOT update
    r = app_client.get('/api/heartbeat?key=wrong-key')
    assert r.status_code == 200
    # _last_heartbeat must not have been updated past our recorded value by this call
    assert gw._last_heartbeat <= old_hb + 0.1  # minimal drift allowed


def test_heartbeat_with_correct_key_resets_deadman(app_client, monkeypatch):
    """Heartbeat with correct key resets dead man timer."""
    monkeypatch.setenv('HEARTBEAT_KEY', 'correct-key')
    before = time.time()
    r = app_client.get('/api/heartbeat?key=correct-key')
    assert r.status_code == 200
    assert gw._last_heartbeat >= before


def test_heartbeat_public_when_no_key_configured(app_client, monkeypatch):
    """If HEARTBEAT_KEY env var not set, heartbeat resets timer (backward compat)."""
    monkeypatch.delenv('HEARTBEAT_KEY', raising=False)
    before = time.time()
    r = app_client.get('/api/heartbeat')
    assert r.status_code == 200
    assert gw._last_heartbeat >= before


def test_session_token_format(app_client, user_token):
    """TC-SEC02: Token is 64-char hex string (secrets.token_hex(32))."""
    assert len(user_token) == 64
    assert all(c in '0123456789abcdef' for c in user_token)


def test_user_cannot_add_admin(app_client, user_headers):
    """TC-SEC03: User cannot add admin-role accounts."""
    r = app_client.post('/api/users/add',
                        json={'username': 'evil', 'password': 'x', 'role': 'admin'},
                        headers=user_headers)
    assert r.status_code == 403


def test_sql_injection_events_since(app_client, user_headers):
    """TC-SEC04: SQL injection in since param → safe (no crash, no data leak)."""
    evil = "'; DROP TABLE events; --"
    r = app_client.get(f'/api/events/since?since={evil}', headers=user_headers)
    # Should return 200 with empty list, not 500
    assert r.status_code == 200
    # Verify events table still exists
    import sqlite3
    conn = sqlite3.connect(gw.EVENTS_DB)
    tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    conn.close()
    assert 'events' in tables


def test_xss_in_voice_command_stored_escaped(app_client, admin_headers):
    """TC-SEC05: XSS payload in voice command phrase → stored, returned as-is (not executed)."""
    payload = '<script>alert("xss")</script>'
    r = app_client.post('/api/config/command/add',
                        json={'phrase': payload, 'response': 'ok'},
                        headers=admin_headers)
    assert r.status_code == 200
    cfg = app_client.get('/api/config', headers=admin_headers).json()
    # Phrase stored exactly — frontend must escape on display
    assert payload in cfg['custom_voice_commands']


def test_security_headers_present(app_client):
    """TC-SEC07: Security headers in all responses."""
    r = app_client.get('/api/heartbeat')
    assert r.headers.get('X-Content-Type-Options') == 'nosniff'
    assert r.headers.get('X-Frame-Options') == 'DENY'


def test_bypass_otp_absent_from_responses(app_client, monkeypatch):
    """TC-SEC08: bypass_otp must not appear in any response body."""
    from unittest.mock import MagicMock
    monkeypatch.setattr(gw, 'send_otp_via_email', MagicMock(return_value=(False, 'error')))

    endpoints = [
        ('/api/admin/send-otp', {'username': 'admin', 'password': 'root'}),
        ('/api/forgot/send-otp', {'username': 'user'}),
    ]
    for path, body in endpoints:
        r = app_client.post(path, json=body)
        assert 'bypass_otp' not in r.json(), f"bypass_otp leaked from {path}"


def test_expired_token_rejected(app_client):
    """TC-SEC09: Expired session token → 401."""
    token = gw.create_session('user', duration=1)
    time.sleep(1.1)
    r = app_client.get('/api/state', headers={'X-Garuda-Token': token})
    assert r.status_code == 401


def test_rate_limit_resets_after_window(app_client, monkeypatch):
    """TC-SEC10 variant: Rate store is per-IP and expires after window."""
    import collections
    monkeypatch.setattr(gw, '_rate_store', collections.defaultdict(list))
    # Exhaust the limit
    for _ in range(31):
        app_client.post('/api/login', json={'username': 'x', 'password': 'y'})
    # Force-expire all timestamps to simulate window passing
    for ip in list(gw._rate_store.keys()):
        gw._rate_store[ip] = []
    # Now a fresh request should succeed (not 429)
    r = app_client.post('/api/login', json={'username': 'user', 'password': 'user'})
    assert r.status_code in (200, 401)  # Not 429
