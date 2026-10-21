"""Fixes for the security-scan findings: one test (or a few) per fix."""
import ssl
import sys
import types
from unittest.mock import MagicMock

import pytest

import conftest  # noqa: F401
import Garuda_web as gw
from basic_pipelines.garuda_core import evidence, mailer


# ── mail: the server certificate is checked ──────────────────────────────────

def test_mail_uses_a_verifying_tls_context(monkeypatch):
    smtp = MagicMock()
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", smtp)
    mailer.send("s", "b", sender="a@x", password="p", to="b@x")
    ctx = smtp.call_args.kwargs["context"]
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


# ── evidence upload: an unknown SSH host key is refused ──────────────────────

def test_sftp_refuses_unknown_host_keys(monkeypatch, tmp_path):
    client = MagicMock()
    fake = types.SimpleNamespace(SSHClient=MagicMock(return_value=client),
                                 RejectPolicy=MagicMock(name="Reject"),
                                 AutoAddPolicy=MagicMock(name="AutoAdd"))
    monkeypatch.setitem(sys.modules, "paramiko", fake)
    known = tmp_path / "known_hosts"
    known.write_text("")
    evidence.sftp_upload(str(known), "f", host="h", port=22, user="u", key_path="",
                         password="p", remote_dir="d", log=lambda m: None,
                         known_hosts=str(known))
    client.load_system_host_keys.assert_called_once()
    client.load_host_keys.assert_called_once_with(str(known))
    client.set_missing_host_key_policy.assert_called_once_with(fake.RejectPolicy())
    fake.AutoAddPolicy.assert_not_called()


# ── password reset: guesses without a username still use up each code ───────

def test_usernameless_reset_guesses_count_against_pending_codes(app_client, monkeypatch):
    monkeypatch.setattr(gw, 'send_otp_via_email', MagicMock(return_value=(True, None)))
    assert app_client.post('/api/forgot/send-otp', json={'username': 'user'}).status_code == 200
    real = gw.STATE.auth.forgot_otp_store['user']['otp']
    wrong = '000000' if real != '000000' else '111111'
    for _ in range(3):
        r = app_client.post('/api/forgot/reset', json={'otp': wrong, 'new_password': 'NewPass123!'})
        assert r.status_code in (401, 422)   # 422: this branch requires the username outright
    r = app_client.post('/api/forgot/reset', json={'otp': real, 'new_password': 'NewPass123!'})
    assert r.status_code in (401, 422)


# ── logout: the refresh token goes too, even when its cookie is not sent ─────

def test_logout_revokes_the_sessions_refresh_token(app_client):
    r = app_client.post('/api/login', json={'username': 'user', 'password': 'user'})
    token = r.json()['token']
    refresh = gw.STATE.auth.sessions[token]['refresh']
    assert refresh in gw.STATE.auth.refresh_tokens
    app_client._client.cookies.clear()   # the /api/refresh-scoped cookie never reaches /api/logout
    assert app_client.post('/api/logout', headers={'X-Garuda-Token': token}).status_code == 200
    assert refresh not in gw.STATE.auth.refresh_tokens
    r = app_client.post('/api/refresh', headers={'X-Garuda-Refresh': refresh})
    assert r.status_code == 401


def test_refreshed_session_remembers_its_refresh_token(app_client):
    app_client.post('/api/login', json={'username': 'user', 'password': 'user'})
    refresh = next(iter(gw.STATE.auth.refresh_tokens))
    app_client._client.cookies.clear()
    r = app_client.post('/api/refresh', headers={'X-Garuda-Refresh': refresh})
    assert gw.STATE.auth.sessions[r.json()['token']]['refresh'] == refresh


# ── event queue: presence (phone MACs) is for admins only ────────────────────

def test_presence_events_are_hidden_from_non_admins(app_client, user_headers, admin_headers):
    gw.queue_event("PRESENCE", "Phone", 0.0, "arrived (mac=aa:bb:cc:dd:ee:ff)")
    gw.queue_event("WATCH", "person", 0.5, "")
    r = app_client.get('/api/events/since', headers=user_headers)
    assert [e['event_type'] for e in r.json()['events']] == ['WATCH']
    r = app_client.get('/api/events/pending', headers=user_headers)
    assert 'aa:bb' not in r.text
    r = app_client.get('/api/events/since', headers=admin_headers)
    assert 'aa:bb:cc:dd:ee:ff' in r.text


# ── .env writer: no character that splitlines() breaks on ────────────────────

@pytest.mark.parametrize("sep", ["\r", "\n", "\x0b", "\x0c", "\x1c", "\x85", " ", " "])
def test_env_writer_refuses_every_line_break(tmp_path, sep):
    from basic_pipelines.garuda_auto import envfile
    env = tmp_path / ".env"
    with pytest.raises(ValueError):
        envfile.set_vars(env, {"NIM_API_KEY": f"k{sep}GARUDA_EVAL_OTP_BYPASS=1"})
    assert not env.exists()
    envfile.set_vars(env, {"NIM_API_KEY": "nvapi-ok"})
    assert env.read_text() == "NIM_API_KEY=nvapi-ok\n"
