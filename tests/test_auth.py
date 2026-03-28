"""TC-A01 through TC-A18: Authentication tests."""
import time
import pytest
from unittest.mock import MagicMock

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'basic_pipelines'))
import Garuda_web as gw


# ── TC-A01: Login with valid user credentials → 200, token returned ──────────
def test_login_valid_user(app_client):
    r = app_client.post('/api/login', json={'username': 'user', 'password': 'user'})
    assert r.status_code == 200
    d = r.json()
    assert 'token' in d
    assert d['role'] == 'user'
    assert d['username'] == 'user'


# ── TC-A02: Login with wrong password → 401 ──────────────────────────────────
def test_login_wrong_password(app_client):
    r = app_client.post('/api/login', json={'username': 'user', 'password': 'wrong'})
    assert r.status_code == 401


# ── TC-A03: Login with admin username via /api/login → 403 ───────────────────
def test_login_admin_via_user_flow(app_client):
    r = app_client.post('/api/login', json={'username': 'admin', 'password': 'root'})
    assert r.status_code == 403


# ── TC-A04: Login with unknown username → 401 ─────────────────────────────────
def test_login_unknown_user(app_client):
    r = app_client.post('/api/login', json={'username': 'nobody', 'password': 'x'})
    assert r.status_code == 401


# ── TC-A05: Admin OTP step-1 with valid creds → 200 ─────────────────────────
def test_admin_send_otp_valid(app_client, monkeypatch):
    monkeypatch.setattr(gw, 'send_otp_via_email', MagicMock(return_value=(True, None)))
    r = app_client.post('/api/admin/send-otp', json={'username': 'admin', 'password': 'root'})
    assert r.status_code == 200
    assert r.json().get('ok') is True


# ── TC-A06: Admin OTP step-1 with wrong password → 401 ──────────────────────
def test_admin_send_otp_wrong_password(app_client):
    r = app_client.post('/api/admin/send-otp', json={'username': 'admin', 'password': 'wrong'})
    assert r.status_code == 401


# ── TC-A07: Admin OTP step-2 with correct OTP → 200, admin token ─────────────
def test_admin_verify_otp_correct(app_client, monkeypatch):
    monkeypatch.setattr(gw, 'send_otp_via_email', MagicMock(return_value=(True, None)))
    app_client.post('/api/admin/send-otp', json={'username': 'admin', 'password': 'root'})
    otp = gw.ADMIN_OTP
    r = app_client.post('/api/admin/verify-otp', json={'username': 'admin', 'otp': otp})
    assert r.status_code == 200
    d = r.json()
    assert d['role'] == 'admin'
    assert 'token' in d


# ── TC-A08: Admin OTP step-2 with wrong OTP → 401 ────────────────────────────
def test_admin_verify_otp_wrong(app_client, monkeypatch):
    monkeypatch.setattr(gw, 'send_otp_via_email', MagicMock(return_value=(True, None)))
    app_client.post('/api/admin/send-otp', json={'username': 'admin', 'password': 'root'})
    r = app_client.post('/api/admin/verify-otp', json={'username': 'admin', 'otp': '000000'})
    assert r.status_code == 401


# ── TC-A09: OTP expires after admin OTP is consumed (reuse → 401) ────────────
def test_admin_otp_single_use(app_client, monkeypatch):
    monkeypatch.setattr(gw, 'send_otp_via_email', MagicMock(return_value=(True, None)))
    app_client.post('/api/admin/send-otp', json={'username': 'admin', 'password': 'root'})
    otp = gw.ADMIN_OTP
    # First use succeeds
    r1 = app_client.post('/api/admin/verify-otp', json={'username': 'admin', 'otp': otp})
    assert r1.status_code == 200
    # Reuse must fail (OTP cleared)
    r2 = app_client.post('/api/admin/verify-otp', json={'username': 'admin', 'otp': otp})
    assert r2.status_code == 401


# ── TC-A10: Rate limiting — 31 rapid requests → 31st returns 429 ─────────────
def test_rate_limiting_login(app_client, monkeypatch):
    # Reset rate store to ensure clean state
    import collections
    monkeypatch.setattr(gw, '_rate_store', collections.defaultdict(list))
    codes = []
    for _ in range(32):
        r = app_client.post('/api/login', json={'username': 'user', 'password': 'wrong'})
        codes.append(r.status_code)
    # At least one 429 must appear
    assert 429 in codes


# ── TC-A11: GET /api/session with valid token → 200 ─────────────────────────
def test_session_with_valid_token(app_client, user_token):
    r = app_client.get('/api/session', headers={'X-Garuda-Token': user_token})
    assert r.status_code == 200
    assert r.json()['role'] == 'user'


# ── TC-A12: GET /api/session with invalid token → 401 ────────────────────────
def test_session_with_invalid_token(app_client):
    r = app_client.get('/api/session', headers={'X-Garuda-Token': 'not-a-real-token'})
    assert r.status_code == 401


# ── TC-A13: GET /api/session with expired token → 401 ────────────────────────
def test_session_expired(app_client):
    # Create session with 1-second duration then wait
    token = gw.create_session('user', duration=1)
    time.sleep(1.1)
    r = app_client.get('/api/session', headers={'X-Garuda-Token': token})
    assert r.status_code == 401


# ── TC-A14: POST /api/logout invalidates token ───────────────────────────────
def test_logout_invalidates_session(app_client, user_token):
    r1 = app_client.post('/api/logout', headers={'X-Garuda-Token': user_token})
    assert r1.status_code == 200
    r2 = app_client.get('/api/session', headers={'X-Garuda-Token': user_token})
    assert r2.status_code == 401


# ── TC-A15: POST /api/forgot/send-otp with valid user → 200 ─────────────────
def test_forgot_send_otp_valid_user(app_client, monkeypatch):
    monkeypatch.setattr(gw, 'send_otp_via_email', MagicMock(return_value=(True, None)))
    r = app_client.post('/api/forgot/send-otp', json={'username': 'user'})
    assert r.status_code == 200
    assert r.json().get('ok') is True


# ── TC-A16: Password reset with correct OTP → 200 ────────────────────────────
def test_forgot_reset_correct_otp(app_client, monkeypatch):
    monkeypatch.setattr(gw, 'send_otp_via_email', MagicMock(return_value=(True, None)))
    app_client.post('/api/forgot/send-otp', json={'username': 'user'})
    otp = gw.USER_FORGOT_OTP
    r = app_client.post('/api/forgot/reset', json={'otp': otp, 'new_password': 'newpass123'})
    assert r.status_code == 200
    # Now login with new password should work
    r2 = app_client.post('/api/login', json={'username': 'user', 'password': 'newpass123'})
    assert r2.status_code == 200


# ── TC-A17: Password reset with wrong OTP → 401 ──────────────────────────────
def test_forgot_reset_wrong_otp(app_client, monkeypatch):
    monkeypatch.setattr(gw, 'send_otp_via_email', MagicMock(return_value=(True, None)))
    app_client.post('/api/forgot/send-otp', json={'username': 'user'})
    r = app_client.post('/api/forgot/reset', json={'otp': '999999', 'new_password': 'x'})
    assert r.status_code == 401


# ── TC-A18: bypass_otp key must NOT appear in any auth response ───────────────
def test_bypass_otp_not_exposed(app_client, monkeypatch):
    """bypass_otp must never leak to the client in production responses."""
    monkeypatch.setattr(gw, 'send_otp_via_email', MagicMock(return_value=(False, 'smtp error')))
    r = app_client.post('/api/admin/send-otp', json={'username': 'admin', 'password': 'root'})
    assert 'bypass_otp' not in r.json()
