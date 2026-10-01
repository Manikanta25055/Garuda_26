"""Regression tests for the 2026-10 backend audit.

Each test pins one defect that was found and fixed, so it cannot come back
unnoticed.
"""
import sqlite3
import time
from unittest.mock import MagicMock

import conftest  # noqa: F401  (mocks the hardware modules before the import below)
import Garuda_web as gw


class _FakeRequest:
    def __init__(self, headers, host="127.0.0.1"):
        self.headers = headers
        self.client = MagicMock(host=host)


# ── client address ───────────────────────────────────────────────────────────

def test_client_ip_ignores_the_spoofable_end_of_forwarded_for():
    req = _FakeRequest({"X-Forwarded-For": "6.6.6.6, 203.0.113.9"})
    assert gw._get_client_ip(req) == "203.0.113.9"


def test_client_ip_prefers_cloudflare_header():
    req = _FakeRequest({"CF-Connecting-IP": "198.51.100.7", "X-Forwarded-For": "6.6.6.6"})
    assert gw._get_client_ip(req) == "198.51.100.7"


def test_forwarded_headers_are_not_trusted_from_a_remote_peer():
    req = _FakeRequest({"CF-Connecting-IP": "198.51.100.7"}, host="192.168.1.50")
    assert gw._get_client_ip(req) == "192.168.1.50"


# ── passwords ────────────────────────────────────────────────────────────────

def test_non_ascii_password_against_plaintext_record_does_not_raise():
    assert gw._verify_password("pässwörd", "user") is False
    assert gw._verify_password("pässwörd", "pässwörd") is True


def test_damaged_hash_fails_closed():
    assert gw._verify_password("x", "pbkdf2:sha256:notanumber:zz:zz") is False


def test_admin_name_is_not_revealed_without_the_password(app_client):
    r = app_client.post('/api/login', json={'username': 'admin', 'password': 'wrong'})
    assert r.status_code == 401
    r = app_client.post('/api/login', json={'username': 'admin', 'password': 'root'})
    assert r.status_code == 403


# ── sessions ─────────────────────────────────────────────────────────────────

def test_password_change_revokes_refresh_tokens(app_client, admin_headers):
    app_client.post('/api/login', json={'username': 'user', 'password': 'user'})
    assert any(s['username'] == 'user' for s in gw._refresh_tokens.values())
    r = app_client.post('/api/users/update',
                        json={'username': 'user', 'new_password': 'NewPass123'},
                        headers=admin_headers)
    assert r.status_code == 200
    assert not any(s['username'] == 'user' for s in gw._refresh_tokens.values())
    assert not any(s['username'] == 'user' for s in gw._sessions.values())


def test_deleting_a_user_ends_their_sessions(app_client, admin_headers, user_headers):
    assert app_client.get('/api/session', headers=user_headers).status_code == 200
    r = app_client.post('/api/users/delete', json={'username': 'user'}, headers=admin_headers)
    assert r.status_code == 200
    assert app_client.get('/api/session', headers=user_headers).status_code == 401
    assert not gw._user_signed_in('user')


def test_cross_site_login_gets_a_refresh_token_it_can_send_back(app_client):
    r = app_client.post('/api/login', json={'username': 'user', 'password': 'user'},
                        headers={'Origin': 'https://garuda-26.vercel.app'})
    body = r.json()
    assert body.get('refresh_token')
    r2 = app_client.post('/api/refresh', headers={'X-Garuda-Refresh': body['refresh_token']})
    assert r2.status_code == 200 and r2.json()['token']


def test_same_site_login_keeps_the_refresh_token_out_of_the_body(app_client):
    r = app_client.post('/api/login', json={'username': 'user', 'password': 'user'})
    assert 'refresh_token' not in r.json()


def test_remember_me_decides_whether_the_refresh_cookie_persists(app_client):
    r = app_client.post('/api/login', json={'username': 'user', 'password': 'user'})
    cookie = next(c for c in r.headers.get_list('set-cookie') if c.startswith('garuda_refresh='))
    assert 'max-age' not in cookie.lower()
    r = app_client.post('/api/login',
                        json={'username': 'user', 'password': 'user', 'remember_me': True})
    cookie = next(c for c in r.headers.get_list('set-cookie') if c.startswith('garuda_refresh='))
    assert 'max-age' in cookie.lower()


def test_cookies_are_secure_when_the_proxy_reports_https(app_client, monkeypatch):
    monkeypatch.setattr(gw, '_COOKIE_SECURE', False)
    r = app_client.post('/api/login', json={'username': 'user', 'password': 'user'},
                        headers={'X-Forwarded-Proto': 'https'})
    assert all('secure' in c.lower() for c in r.headers.get_list('set-cookie'))


# ── one-time codes ───────────────────────────────────────────────────────────

def test_master_key_otp_expires(app_client, admin_headers, monkeypatch):
    monkeypatch.setattr(gw, 'MASTER_KEY_OTP', '123456')
    monkeypatch.setattr(gw, '_master_otp_ts', time.time() - 400)
    r = app_client.post('/api/master_key/add',
                        json={'otp': '123456', 'new_key': 'Zx9!kQ2#vLm8@pTr'},
                        headers=admin_headers)
    assert r.status_code == 401 and 'expired' in r.json()['detail'].lower()


def test_master_key_otp_allows_three_guesses(app_client, admin_headers, monkeypatch):
    monkeypatch.setattr(gw, 'MASTER_KEY_OTP', '123456')
    monkeypatch.setattr(gw, '_master_otp_ts', time.time())
    monkeypatch.setattr(gw, '_master_otp_attempts', 0)
    for _ in range(3):
        r = app_client.post('/api/master_key/add',
                            json={'otp': '000000', 'new_key': 'Zx9!kQ2#vLm8@pTr'},
                            headers=admin_headers)
        assert r.status_code == 401
    assert gw.MASTER_KEY_OTP is None


def test_master_key_guessing_locks_the_caller_out(app_client):
    for _ in range(gw._LOGIN_MAX_ATTEMPTS):
        assert app_client.post('/api/master_key/login', json={'key': 'nope'}).status_code == 401
    r = app_client.post('/api/master_key/login', json={'key': 'test-master-key-12345'})
    assert r.status_code == 429


def test_a_new_admin_otp_gets_fresh_attempts(app_client, monkeypatch):
    monkeypatch.setattr(gw, 'send_otp_via_email', MagicMock(return_value=(True, None)))
    monkeypatch.setattr(gw, '_admin_otp_attempts', 2)
    app_client.post('/api/admin/send-otp', json={'username': 'admin', 'password': 'root'})
    assert gw._admin_otp_attempts == 0


# ── events ───────────────────────────────────────────────────────────────────

def test_pending_events_are_found_behind_a_thousand_synced_rows(app_client, user_headers):
    conn = sqlite3.connect(gw.EVENTS_DB)
    conn.executemany(
        "INSERT INTO events (timestamp, event_type, label, synced) VALUES (?, 'WATCH', 'old', 1)",
        [(f"2026-01-01T00:00:{i % 60:02d}.{i:06d}",) for i in range(1100)])
    conn.commit()
    conn.close()
    gw.queue_event("DANGER", "Knife", 0.9, "new")
    r = app_client.get('/api/events/pending', headers=user_headers)
    labels = [e['label'] for e in r.json()['events']]
    assert labels == ['Knife']
    assert gw.get_pending_count() == 0


def test_negative_limit_does_not_mean_unlimited(app_client, user_headers):
    for i in range(5):
        gw.queue_event("WATCH", f"p{i}")
    r = app_client.get('/api/events/since?limit=-1', headers=user_headers)
    assert r.json()['count'] == 1


# ── state push ───────────────────────────────────────────────────────────────

def test_non_admin_state_hides_device_macs_and_security_lines():
    payload = {"known_devices": [{"mac": "aa:bb:cc:dd:ee:ff"}], "cpu_cores": [1.0],
               "voice_log": ["x"], "voice_responses": ["y"], "modes": {},
               "system_log": ["[t] Login: user", "[t] [SECURITY] Login lockout: 1.2.3.4",
                              "[t] Alert triggered."]}
    slim = gw._state_for_role(payload, "user")
    assert "known_devices" not in slim and "voice_log" not in slim
    assert slim["system_log"] == ["[t] Alert triggered."]
    assert gw._state_for_role(payload, "admin") is payload


def test_state_endpoint_applies_the_role_filter(app_client, user_headers, admin_headers):
    assert 'known_devices' not in app_client.get('/api/state', headers=user_headers).json()
    assert 'known_devices' in app_client.get('/api/state', headers=admin_headers).json()


# ── presence ─────────────────────────────────────────────────────────────────

def test_empty_or_stale_mac_is_not_presence(monkeypatch):
    monkeypatch.setattr(gw, '_last_arp_cache',
                        "ip address hw type flags hw address mask device\n"
                        "192.168.1.5 0x1 0x0 aa:bb:cc:dd:ee:ff * wlan0\n"
                        "192.168.1.6 0x1 0x2 11:22:33:44:55:66 * wlan0\n")
    assert gw._mac_online("") is False
    assert gw._mac_online("aa:bb:cc:dd:ee:ff") is False     # incomplete entry
    assert gw._mac_online("11:22:33:44:55:66") is True


# ── config ───────────────────────────────────────────────────────────────────

def test_impossible_schedule_times_are_dropped(app_client, admin_headers):
    app_client.post('/api/config', json={'mode_schedule': {
        'night': {'start': '99:99', 'end': '06:00'},
        'dnd': {'start': '22:00', 'end': '06:30'}}}, headers=admin_headers)
    assert gw.MODE_SCHEDULE == {'dnd': {'start': '22:00', 'end': '06:30'}}


def test_empty_danger_labels_are_refused(app_client, admin_headers):
    before = list(gw.DANGER_LABELS)
    r = app_client.post('/api/config', json={'danger_labels': []}, headers=admin_headers)
    assert r.status_code == 400
    assert gw.DANGER_LABELS == before


def test_email_password_is_saved_to_the_env_file(app_client, admin_headers):
    saved = gw.EMAIL_SENDER_PASS
    try:
        app_client.post('/api/config', json={'email_sender_pass': 'abcd efgh ijkl mnop'},
                        headers=admin_headers)
        assert 'EMAIL_SENDER_PASS=abcd efgh ijkl mnop' in open(gw.HOME_ENV_PATH).read()
    finally:
        gw.EMAIL_SENDER_PASS = saved


def test_admin_accounts_cannot_be_deleted(app_client, admin_headers):
    gw.USERS['second'] = {'password': 'x', 'role': 'admin', 'history': {}}
    r = app_client.post('/api/users/delete', json={'username': 'second'}, headers=admin_headers)
    assert r.status_code == 400 and 'second' in gw.USERS


# ── housekeeping ─────────────────────────────────────────────────────────────

def test_rate_and_lockout_tables_are_pruned(monkeypatch):
    old = time.time() - 7200
    monkeypatch.setattr(gw, '_rate_store', gw.defaultdict(list, {"1.1.1.1": [old], "2.2.2.2": [time.time()]}))
    monkeypatch.setattr(gw, '_login_failures', {"1.1.1.1": {"count": 9, "lockout_until": old},
                                                "2.2.2.2": {"count": 1, "lockout_until": 0.0, "last": time.time()}})
    gw._prune_rate_state()
    assert list(gw._rate_store) == ["2.2.2.2"]
    assert list(gw._login_failures) == ["2.2.2.2"]


def test_voice_responses_are_bounded(monkeypatch):
    monkeypatch.setattr(gw, 'voice_responses', [])
    for i in range(520):
        gw.append_voice_response(f"r{i}")
    assert len(gw.voice_responses) == 500


def test_api_answers_are_not_cacheable(app_client):
    assert app_client.get('/api/users-public').headers.get('cache-control') == 'no-store'
    assert app_client.get('/').headers.get('cache-control') == 'no-cache'


# ── master keys at rest, and who may silence the alarm ───────────────────────

def test_master_keys_file_is_migrated_to_hashes(tmp_path, monkeypatch):
    import json
    path = tmp_path / 'master_keys.json'
    path.write_text(json.dumps({"keys": ["Plain-Key-9876!"]}))
    monkeypatch.setattr(gw, 'MASTER_KEYS_FILE', str(path))
    monkeypatch.setattr(gw, 'MASTER_KEYS', [])
    gw.load_master_keys()
    on_disk = json.loads(path.read_text())["keys"]
    assert all(k.startswith('mk1$') for k in on_disk) and 'Plain-Key-9876!' not in path.read_text()
    assert gw._master_key_matches('Plain-Key-9876!', gw.MASTER_KEYS)
    assert gw._mk_mask(on_disk[0]).endswith('876!')


def test_only_an_admin_can_switch_on_a_mode_that_silences_alerts(app_client, user_headers, admin_headers):
    for mode in ('idle', 'email_off'):
        r = app_client.post('/api/modes', json={'mode': mode, 'value': True}, headers=user_headers)
        assert r.status_code == 403
        r = app_client.post('/api/modes', json={'mode': mode, 'value': True}, headers=admin_headers)
        assert r.status_code == 200
        # Switching it back off is the safe direction: anyone may.
        r = app_client.post('/api/modes', json={'mode': mode, 'value': False}, headers=user_headers)
        assert r.status_code == 200
    r = app_client.post('/api/modes', json={'mode': 'dnd', 'value': True}, headers=user_headers)
    assert r.status_code == 200
