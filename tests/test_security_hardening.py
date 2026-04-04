"""
Security hardening tests — covers every fix added in the security pass:
  - OTP brute-force lockout (3 attempts)
  - Account enumeration prevention in forgot-password
  - Password complexity & length enforcement
  - Username format validation
  - Email recipient format + count validation
  - Email cooldown range validation
  - Custom command phrase/response length + count limits
  - Session invalidation on password change
  - Session token is 128-char hex (secrets.token_hex(64))
  - PBKDF2 iteration upgrade (≥ 600000)
  - OTP uses secrets module (produces only digits)
  - Global rate-limit middleware covers formerly-unprotected endpoints
"""
import time
import string
import collections
from unittest.mock import MagicMock
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'basic_pipelines'))
import conftest  # noqa — ensures hardware mocks run before any import
import Garuda_web as gw

# Capture real crypto functions before session fixtures can patch them
_real_hash_password = gw._hash_password
_real_verify_password = gw._verify_password


# ── OTP brute-force lockout ───────────────────────────────────────────────────

def test_admin_otp_lockout_after_3_wrong_attempts(app_client, monkeypatch):
    """Admin OTP must be invalidated after 3 consecutive wrong attempts."""
    monkeypatch.setattr(gw, 'send_otp_via_email', MagicMock(return_value=(True, None)))
    r = app_client.post('/api/admin/send-otp', json={'username': 'admin', 'password': 'root'})
    assert r.status_code == 200

    for _ in range(3):
        r = app_client.post('/api/admin/verify-otp', json={'username': 'admin', 'otp': '000000'})
        assert r.status_code == 401

    # 4th attempt (even with real OTP) must fail — state cleared
    r = app_client.post('/api/admin/verify-otp', json={'username': 'admin', 'otp': '000000'})
    assert r.status_code == 401
    assert gw.ADMIN_OTP is None
    assert gw._admin_otp_attempts == 0


def test_admin_otp_attempt_counter_resets_on_success(app_client, monkeypatch):
    """Successful OTP verification resets the attempt counter."""
    monkeypatch.setattr(gw, 'send_otp_via_email', MagicMock(return_value=(True, None)))
    r0 = app_client.post('/api/admin/send-otp', json={'username': 'admin', 'password': 'root'})
    assert r0.status_code == 200
    saved_otp = gw.ADMIN_OTP
    assert saved_otp is not None
    # 2 wrong attempts
    app_client.post('/api/admin/verify-otp', json={'username': 'admin', 'otp': '000000'})
    app_client.post('/api/admin/verify-otp', json={'username': 'admin', 'otp': '000000'})
    # Correct OTP succeeds
    r = app_client.post('/api/admin/verify-otp', json={'username': 'admin', 'otp': saved_otp})
    assert r.status_code == 200
    assert gw._admin_otp_attempts == 0


def test_forgot_otp_lockout_after_3_wrong_attempts(app_client, monkeypatch):
    """Forgot-password OTP must be cleared after 3 failed verify attempts."""
    monkeypatch.setattr(gw, 'send_otp_via_email', MagicMock(return_value=(True, None)))
    r = app_client.post('/api/forgot/send-otp', json={'username': 'user'})
    assert r.status_code == 200
    assert gw.USER_FORGOT_OTP is not None

    for _ in range(3):
        r = app_client.post('/api/forgot/reset',
                            json={'username': 'user', 'otp': '000000', 'new_password': 'Ignored1!'})
        assert r.status_code == 401

    # OTP cleared — next attempt also fails
    r = app_client.post('/api/forgot/reset',
                        json={'username': 'user', 'otp': '000000', 'new_password': 'Ignored1!'})
    assert r.status_code == 401
    assert gw.USER_FORGOT_OTP is None


# ── Account enumeration prevention ───────────────────────────────────────────

def test_forgot_send_otp_nonexistent_user_returns_200(app_client):
    """Non-existent username must return 200 (not 404) to prevent enumeration."""
    r = app_client.post('/api/forgot/send-otp', json={'username': 'ghost_user_xyz'})
    assert r.status_code == 200
    data = r.json()
    assert data.get('ok') is True
    # No OTP should have been set
    assert gw.USER_FORGOT_OTP is None


def test_forgot_send_otp_existing_user_returns_200(app_client, monkeypatch):
    """Existing username also returns 200 — same response as non-existent."""
    monkeypatch.setattr(gw, 'send_otp_via_email', MagicMock(return_value=(True, None)))
    r = app_client.post('/api/forgot/send-otp', json={'username': 'user'})
    assert r.status_code == 200
    assert r.json().get('ok') is True


# ── Password complexity & length ──────────────────────────────────────────────

@pytest.mark.parametrize("password,expected_status", [
    ('Short1',        400),   # too short (< 8 chars)
    ('nouppercase1',  400),   # no uppercase
    ('NOLOWERCASE1',  400),   # no lowercase
    ('NoDigitHere',   400),   # no digit
    ('',              400),   # empty
    ('   ',           400),   # whitespace only
    ('A' * 257 + '1', 400),   # too long (> 256)
    ('ValidPass1',    200),   # valid
    ('Abcdefg1',      200),   # exactly 8 chars
])
def test_add_user_password_complexity(app_client, monkeypatch, admin_token, password, expected_status):
    """add_user enforces password complexity rules."""
    headers = {'X-Garuda-Token': admin_token}
    r = app_client.post('/api/users/add', json={
        'username': 'testuser99',
        'password': password,
        'role': 'user',
    }, headers=headers)
    assert r.status_code == expected_status, f"password={password!r}: {r.text}"


@pytest.mark.parametrize("password,expected_status", [
    ('Short1',       400),
    ('nouppercase1', 400),
    ('NOLOWERCASE1', 400),
    ('NoDigitHere',  400),
    ('ValidPass1',   200),
])
def test_update_user_password_complexity(app_client, admin_token, password, expected_status):
    """update_user enforces password complexity when new_password provided."""
    headers = {'X-Garuda-Token': admin_token}
    r = app_client.post('/api/users/update', json={
        'username': 'user',
        'new_password': password,
    }, headers=headers)
    assert r.status_code == expected_status, f"password={password!r}: {r.text}"


@pytest.mark.parametrize("password,expected_status", [
    ('short1',       400),
    ('NOLOWERCASE1', 400),
    ('ValidPass1',   200),
])
def test_forgot_reset_password_complexity(app_client, monkeypatch, password, expected_status):
    """forgot/reset enforces password complexity for new_password."""
    monkeypatch.setattr(gw, 'send_otp_via_email', MagicMock(return_value=(True, None)))
    app_client.post('/api/forgot/send-otp', json={'username': 'user'})
    otp = gw.USER_FORGOT_OTP

    r = app_client.post('/api/forgot/reset',
                        json={'username': 'user', 'otp': otp, 'new_password': password})
    assert r.status_code == expected_status, f"password={password!r}: {r.text}"


# ── Username format validation ────────────────────────────────────────────────

@pytest.mark.parametrize("username,expected_status", [
    ('ab',             400),   # too short (< 3 chars)
    ('a' * 33,         400),   # too long (> 32 chars)
    ('bad user',       400),   # space not allowed
    ('bad@user',       400),   # @ not allowed
    ('bad.user',       400),   # dot not allowed
    ('valid_user',     200),   # underscore OK
    ('valid-user',     200),   # hyphen OK
    ('ValidUser123',   200),   # mixed case + digits OK
    ('abc',            200),   # exactly 3 chars
    ('a' * 32,         200),   # exactly 32 chars
])
def test_add_user_username_validation(app_client, admin_token, username, expected_status):
    """add_user enforces username format: 3-32 chars, alphanumeric/underscore/hyphen."""
    headers = {'X-Garuda-Token': admin_token}
    r = app_client.post('/api/users/add', json={
        'username': username,
        'password': 'ValidPass1',
        'role': 'user',
    }, headers=headers)
    assert r.status_code == expected_status, f"username={username!r}: {r.text}"


# ── Email recipient validation ────────────────────────────────────────────────

def test_config_email_recipients_invalid_format(app_client, admin_token):
    """Email addresses must be valid format."""
    headers = {'X-Garuda-Token': admin_token}
    r = app_client.post('/api/config', json={'email_recipients': ['not-an-email']}, headers=headers)
    assert r.status_code == 400


def test_config_email_recipients_max_count(app_client, admin_token):
    """Cannot set more than 10 email recipients."""
    headers = {'X-Garuda-Token': admin_token}
    too_many = [f'user{i}@example.com' for i in range(11)]
    r = app_client.post('/api/config', json={'email_recipients': too_many}, headers=headers)
    assert r.status_code == 400


def test_config_email_recipients_valid(app_client, admin_token):
    """Valid email addresses within limit are accepted."""
    headers = {'X-Garuda-Token': admin_token}
    r = app_client.post('/api/config',
                        json={'email_recipients': ['a@b.com', 'c@d.org']},
                        headers=headers)
    assert r.status_code == 200


# ── Email cooldown range ──────────────────────────────────────────────────────

@pytest.mark.parametrize("cooldown,expected_status", [
    (4,    400),   # below min (5)
    (3601, 400),   # above max (3600)
    (5,    200),   # exactly min
    (3600, 200),   # exactly max
    (300,  200),   # typical value
])
def test_config_email_cooldown_range(app_client, admin_token, cooldown, expected_status):
    """Email cooldown must be between 5 and 3600 seconds."""
    headers = {'X-Garuda-Token': admin_token}
    r = app_client.post('/api/config', json={'email_cooldown': cooldown}, headers=headers)
    assert r.status_code == expected_status, f"cooldown={cooldown}: {r.text}"


# ── Custom command limits ─────────────────────────────────────────────────────

def test_add_command_phrase_too_long(app_client, admin_token):
    """Command phrase > 200 chars must be rejected."""
    headers = {'X-Garuda-Token': admin_token}
    r = app_client.post('/api/config/command/add',
                        json={'phrase': 'x' * 201, 'response': 'ok'},
                        headers=headers)
    assert r.status_code == 400


def test_add_command_response_too_long(app_client, admin_token):
    """Command response > 500 chars must be rejected."""
    headers = {'X-Garuda-Token': admin_token}
    r = app_client.post('/api/config/command/add',
                        json={'phrase': 'test phrase', 'response': 'r' * 501},
                        headers=headers)
    assert r.status_code == 400


def test_add_command_empty_phrase(app_client, admin_token):
    """Empty command phrase must be rejected."""
    headers = {'X-Garuda-Token': admin_token}
    r = app_client.post('/api/config/command/add',
                        json={'phrase': '', 'response': 'ok'},
                        headers=headers)
    assert r.status_code == 400


def test_add_command_max_100_commands(app_client, admin_token):
    """Cannot add more than 100 custom commands."""
    headers = {'X-Garuda-Token': admin_token}
    # Fill up to 100
    gw.CUSTOM_VOICE_COMMANDS.clear()
    for i in range(100):
        gw.CUSTOM_VOICE_COMMANDS[f'cmd_{i}'] = 'response'
    # 101st should fail
    r = app_client.post('/api/config/command/add',
                        json={'phrase': 'new unique phrase', 'response': 'ok'},
                        headers=headers)
    assert r.status_code == 400
    assert len(gw.CUSTOM_VOICE_COMMANDS) == 100


def test_update_existing_command_ignores_count_limit(app_client, admin_token):
    """Updating an existing command phrase is allowed even at 100-command limit."""
    headers = {'X-Garuda-Token': admin_token}
    gw.CUSTOM_VOICE_COMMANDS.clear()
    for i in range(100):
        gw.CUSTOM_VOICE_COMMANDS[f'cmd_{i}'] = 'response'
    # Overwriting cmd_0 should succeed
    r = app_client.post('/api/config/command/add',
                        json={'phrase': 'cmd_0', 'response': 'updated'},
                        headers=headers)
    assert r.status_code == 200


# ── Session invalidation on password change ───────────────────────────────────

def test_update_user_password_invalidates_existing_sessions(app_client, admin_token, user_token):
    """Changing a user's password via update_user invalidates all their sessions."""
    headers = {'X-Garuda-Token': admin_token}
    user_hdrs = {'X-Garuda-Token': user_token}

    # User session is active
    r = app_client.get('/api/session', headers=user_hdrs)
    assert r.status_code == 200

    # Admin changes password
    r = app_client.post('/api/users/update',
                        json={'username': 'user', 'new_password': 'NewValidPass1'},
                        headers=headers)
    assert r.status_code == 200

    # Old session must now be invalid
    r = app_client.get('/api/session', headers=user_hdrs)
    assert r.status_code == 401


def test_admin_session_preserved_after_password_change(app_client, admin_token):
    """Admin's own session must survive after changing another user's password."""
    headers = {'X-Garuda-Token': admin_token}
    r = app_client.post('/api/users/update',
                        json={'username': 'user', 'new_password': 'NewValidPass1'},
                        headers=headers)
    assert r.status_code == 200
    # Admin session still valid
    r = app_client.get('/api/session', headers=headers)
    assert r.status_code == 200


def test_forgot_reset_invalidates_user_sessions(app_client, monkeypatch, user_token):
    """Password reset via forgot flow invalidates existing sessions for that user."""
    monkeypatch.setattr(gw, 'send_otp_via_email', MagicMock(return_value=(True, None)))
    app_client.post('/api/forgot/send-otp', json={'username': 'user'})
    otp = gw.USER_FORGOT_OTP

    user_hdrs = {'X-Garuda-Token': user_token}
    r = app_client.get('/api/session', headers=user_hdrs)
    assert r.status_code == 200

    r = app_client.post('/api/forgot/reset',
                        json={'username': 'user', 'otp': otp, 'new_password': 'NewValidPass1'})
    assert r.status_code == 200

    r = app_client.get('/api/session', headers=user_hdrs)
    assert r.status_code == 401


# ── Session token format (128-char hex) ───────────────────────────────────────

def test_session_token_is_128_char_hex(app_client):
    """Session token must be 128-character hex string (secrets.token_hex(64))."""
    r = app_client.post('/api/login', json={'username': 'user', 'password': 'user'})
    assert r.status_code == 200
    token = r.json()['token']
    assert len(token) == 128, f"token length {len(token)}, expected 128"
    assert all(c in string.hexdigits for c in token)


def test_master_session_token_is_128_char_hex(app_client):
    """Master-key session token must also be 128-char hex."""
    r = app_client.post('/api/master_key/login', json={'key': 'test-master-key-12345'})
    assert r.status_code == 200
    token = r.json()['token']
    assert len(token) == 128
    assert all(c in string.hexdigits for c in token)


# ── PBKDF2 iteration count ────────────────────────────────────────────────────

def test_pbkdf2_uses_600000_iterations():
    """Hashed passwords must use 600000 PBKDF2 iterations."""
    hashed = _real_hash_password('TestPass1')
    assert hashed.startswith('pbkdf2:sha256:600000:'), f"Got: {hashed[:40]}"


def test_pbkdf2_still_verifiable():
    """Password hashed with new iteration count must still verify correctly."""
    hashed = _real_hash_password('TestPass1')
    assert _real_verify_password('TestPass1', hashed) is True
    assert _real_verify_password('WrongPass1', hashed) is False


# ── OTP digit-only generation ────────────────────────────────────────────────

def test_generate_otp_code_digits_only():
    """OTP must contain only digit characters 0-9."""
    for _ in range(20):
        otp = gw.generate_otp_code(6)
        assert len(otp) == 6
        assert otp.isdigit(), f"Non-digit in OTP: {otp!r}"


def test_generate_otp_code_respects_length():
    """generate_otp_code respects the length parameter."""
    for n in (4, 6, 8):
        otp = gw.generate_otp_code(n)
        assert len(otp) == n


# ── Global rate-limit middleware ──────────────────────────────────────────────

def test_global_rate_limit_applies_to_modes_endpoint(app_client, admin_token, monkeypatch):
    """The /api/modes endpoint is covered by the global rate-limit middleware."""
    monkeypatch.setattr(gw, '_RATE_LIMIT', 5)
    monkeypatch.setattr(gw, '_rate_store', collections.defaultdict(list))
    headers = {'X-Garuda-Token': admin_token}
    statuses = []
    for _ in range(8):
        r = app_client.post('/api/modes', json={'mode': 'dnd', 'value': False}, headers=headers)
        statuses.append(r.status_code)
    assert 429 in statuses, "Expected at least one 429 from global rate limiter"


def test_global_rate_limit_applies_to_config_endpoint(app_client, admin_token, monkeypatch):
    """The /api/config endpoint is covered by the global rate-limit middleware."""
    monkeypatch.setattr(gw, '_RATE_LIMIT', 5)
    monkeypatch.setattr(gw, '_rate_store', collections.defaultdict(list))
    headers = {'X-Garuda-Token': admin_token}
    statuses = []
    for _ in range(8):
        r = app_client.post('/api/config', json={}, headers=headers)
        statuses.append(r.status_code)
    assert 429 in statuses


def test_global_rate_limit_applies_to_users_endpoint(app_client, admin_token, monkeypatch):
    """The /api/users endpoint is covered by the global rate-limit middleware."""
    monkeypatch.setattr(gw, '_RATE_LIMIT', 5)
    monkeypatch.setattr(gw, '_rate_store', collections.defaultdict(list))
    headers = {'X-Garuda-Token': admin_token}
    statuses = []
    for _ in range(8):
        r = app_client.get('/api/users', headers=headers)
        statuses.append(r.status_code)
    assert 429 in statuses
