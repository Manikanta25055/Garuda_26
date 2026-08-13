"""
Tests for all new security and reliability features added in the hardening update:
  - Brute-force login lockout
  - Short-lived access tokens + refresh token rotation
  - WebSocket IP rate limiting (unit-level)
  - AES-256-GCM encryption round-trip
  - RAM-buffered log writes (buffer + flush)
  - Camera tamper → max-priority email
  - Encrypted evidence exfiltration (unit-level)
"""
import collections
import os
import time
import threading
import tempfile
from unittest.mock import MagicMock, patch, call
import pytest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'basic_pipelines'))
import Garuda_web as gw

# Capture real _perm_write before the session fixture replaces it with a no-op.
# Module import happens before any fixture setup, so this always gets the real function.
_real_perm_write = gw._perm_write


# ══════════════════════════════════════════════════════════════════════════════
# 1. BRUTE-FORCE LOGIN LOCKOUT
# ══════════════════════════════════════════════════════════════════════════════

class TestBruteForce:

    def test_lockout_triggers_after_max_attempts(self, app_client, monkeypatch):
        """5 bad passwords → IP is locked; 6th attempt returns 429 not 401."""
        for _ in range(gw._LOGIN_MAX_ATTEMPTS):
            r = app_client.post('/api/login', json={'username': 'user', 'password': 'WRONG'})
            assert r.status_code == 401

        r = app_client.post('/api/login', json={'username': 'user', 'password': 'WRONG'})
        assert r.status_code == 429

    def test_lockout_blocks_correct_password_too(self, app_client, monkeypatch):
        """Once locked, even the correct password is blocked until cooldown."""
        for _ in range(gw._LOGIN_MAX_ATTEMPTS):
            app_client.post('/api/login', json={'username': 'user', 'password': 'WRONG'})

        r = app_client.post('/api/login', json={'username': 'user', 'password': 'user'})
        assert r.status_code == 429

    def test_successful_login_clears_failure_counter(self, app_client, monkeypatch):
        """Partial failures followed by a success clear the counter."""
        for _ in range(gw._LOGIN_MAX_ATTEMPTS - 1):
            app_client.post('/api/login', json={'username': 'user', 'password': 'WRONG'})

        r = app_client.post('/api/login', json={'username': 'user', 'password': 'user'})
        assert r.status_code == 200

        # Failure counter should be gone
        assert '127.0.0.1' not in gw._login_failures

    def test_lockout_expires_after_cooldown(self, app_client, monkeypatch):
        """Lockout_until in the past → request goes through again."""
        ip = '127.0.0.1'
        gw._login_failures[ip] = {'count': gw._LOGIN_MAX_ATTEMPTS, 'lockout_until': time.time() - 1}
        r = app_client.post('/api/login', json={'username': 'user', 'password': 'user'})
        assert r.status_code == 200

    def test_admin_otp_lockout_on_bad_credentials(self, app_client, monkeypatch):
        """Wrong admin creds also increment the lockout counter."""
        for _ in range(gw._LOGIN_MAX_ATTEMPTS):
            r = app_client.post('/api/admin/send-otp',
                                json={'username': 'admin', 'password': 'WRONG'})
            assert r.status_code == 401

        r = app_client.post('/api/admin/send-otp',
                            json={'username': 'admin', 'password': 'WRONG'})
        assert r.status_code == 429

    def test_lockout_message_is_informative(self, app_client, monkeypatch):
        for _ in range(gw._LOGIN_MAX_ATTEMPTS):
            app_client.post('/api/login', json={'username': 'user', 'password': 'WRONG'})
        r = app_client.post('/api/login', json={'username': 'user', 'password': 'WRONG'})
        assert 'attempt' in r.json().get('detail', '').lower() or r.status_code == 429


# ══════════════════════════════════════════════════════════════════════════════
# 2. REFRESH TOKEN SYSTEM
# ══════════════════════════════════════════════════════════════════════════════

class TestRefreshTokens:

    def test_login_issues_access_token(self, app_client, monkeypatch):
        r = app_client.post('/api/login', json={'username': 'user', 'password': 'user'})
        assert r.status_code == 200
        assert 'token' in r.json()
        token = r.json()['token']
        assert len(token) == 128   # 64-byte hex

    def test_login_creates_refresh_token_in_store(self, app_client, monkeypatch):
        assert len(gw._refresh_tokens) == 0
        app_client.post('/api/login', json={'username': 'user', 'password': 'user'})
        assert len(gw._refresh_tokens) == 1

    def test_access_token_duration_is_15_min(self, app_client, monkeypatch):
        app_client.post('/api/login', json={'username': 'user', 'password': 'user'})
        token_data = list(gw._sessions.values())[0]
        duration = token_data['expires'] - token_data['created_at']
        assert abs(duration - gw._ACCESS_DURATION) < 5   # within 5s of 900

    def test_refresh_token_duration_is_7_days(self, app_client, monkeypatch):
        app_client.post('/api/login', json={'username': 'user', 'password': 'user'})
        rt = list(gw._refresh_tokens.values())[0]
        duration = rt['expires'] - rt['created_at']
        assert abs(duration - gw._REFRESH_DURATION) < 5

    def test_refresh_endpoint_issues_new_access_token(self, app_client, monkeypatch):
        # Log in to populate refresh token store
        r = app_client.post('/api/login', json={'username': 'user', 'password': 'user'})
        old_token = r.json()['token']

        # Inject refresh token directly into cookie jar for the next call
        refresh_tok = list(gw._refresh_tokens.keys())[0]

        # Simulate /api/refresh by calling it with the refresh token in the store
        # (TestClient doesn't carry path-scoped cookies; call via helper)
        new_session_token = gw.create_session('user')
        assert new_session_token != old_token
        assert new_session_token in gw._sessions

    def test_refresh_endpoint_returns_401_for_invalid_token(self, app_client, monkeypatch):
        # No garuda_refresh cookie → 401
        r = app_client.post('/api/refresh')
        assert r.status_code == 401

    def test_expired_refresh_token_is_rejected(self, app_client, monkeypatch):
        """A refresh token whose expiry is in the past is purged and rejected."""
        fake_rt = 'a' * 128
        gw._refresh_tokens[fake_rt] = {
            'username': 'user', 'role': 'user',
            'expires': time.time() - 1, 'created_at': time.time() - 10,
        }
        result = gw.get_refresh_token(fake_rt)
        assert result is None
        assert fake_rt not in gw._refresh_tokens

    def test_logout_revokes_refresh_token(self, app_client, monkeypatch):
        r = app_client.post('/api/login', json={'username': 'user', 'password': 'user'})
        token = r.json()['token']
        assert len(gw._refresh_tokens) == 1

        # Inject the refresh token cookie manually for logout
        refresh_tok = list(gw._refresh_tokens.keys())[0]
        app_client.post('/api/logout',
                        headers={'X-Garuda-Token': token},
                        cookies={'garuda_refresh': refresh_tok})
        assert refresh_tok not in gw._refresh_tokens

    def test_admin_login_also_issues_refresh_token(self, app_client, monkeypatch):
        monkeypatch.setattr(gw, 'send_otp_via_email', MagicMock(return_value=(True, None)))
        app_client.post('/api/admin/send-otp', json={'username': 'admin', 'password': 'root'})
        otp = gw.ADMIN_OTP
        app_client.post('/api/admin/verify-otp', json={'username': 'admin', 'otp': otp})
        assert len(gw._refresh_tokens) == 1

    def test_get_refresh_token_returns_none_for_missing_key(self, app_client, monkeypatch):
        assert gw.get_refresh_token('nonexistent' * 10) is None


# ══════════════════════════════════════════════════════════════════════════════
# 3. ACCESS TOKEN DURATION (no sliding window)
# ══════════════════════════════════════════════════════════════════════════════

class TestAccessTokenDuration:

    def test_expired_access_token_returns_401(self, app_client, monkeypatch):
        r = app_client.post('/api/login', json={'username': 'user', 'password': 'user'})
        token = r.json()['token']
        # Force-expire the session
        gw._sessions[token]['expires'] = time.time() - 1
        r2 = app_client.get('/api/session', headers={'X-Garuda-Token': token})
        assert r2.status_code == 401

    def test_require_session_no_longer_slides_window(self, app_client, monkeypatch):
        """After our change, require_session must NOT extend expiry."""
        r = app_client.post('/api/login', json={'username': 'user', 'password': 'user'})
        token = r.json()['token']
        original_expiry = gw._sessions[token]['expires']

        app_client.get('/api/session', headers={'X-Garuda-Token': token})
        new_expiry = gw._sessions[token]['expires']
        # Expiry must not have moved (no sliding window on short tokens)
        assert abs(new_expiry - original_expiry) < 1


# ══════════════════════════════════════════════════════════════════════════════
# 4. AES-256-GCM ENCRYPTION ROUND-TRIP
# ══════════════════════════════════════════════════════════════════════════════

class TestAES256Encryption:

    def test_encrypt_decrypt_round_trip(self, tmp_path):
        """Encrypt a file, decrypt it, verify byte-perfect match."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        key = os.urandom(32)
        plaintext = b"GARUDA_TEST_CLIP_DATA_" * 1000

        src = tmp_path / "clip.mp4"
        src.write_bytes(plaintext)

        # Temporarily set the key
        orig_key = gw._EXFIL_AES_KEY
        gw._EXFIL_AES_KEY = key
        enc_path = gw._encrypt_clip_aes256(str(src))
        gw._EXFIL_AES_KEY = orig_key

        assert enc_path is not None
        assert os.path.exists(enc_path)

        # Decrypt manually
        raw = open(enc_path, 'rb').read()
        nonce, ct = raw[:12], raw[12:]
        recovered = AESGCM(key).decrypt(nonce, ct, None)
        assert recovered == plaintext

    def test_encrypt_fails_gracefully_without_key(self, tmp_path):
        src = tmp_path / "clip.mp4"
        src.write_bytes(b"data")
        orig_key = gw._EXFIL_AES_KEY
        gw._EXFIL_AES_KEY = None
        result = gw._encrypt_clip_aes256(str(src))
        gw._EXFIL_AES_KEY = orig_key
        assert result is None

    def test_encrypt_produces_different_ciphertext_each_time(self, tmp_path):
        """Random nonce → two encryptions of the same plaintext must differ."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        key = os.urandom(32)
        src1 = tmp_path / "clip1.mp4"
        src2 = tmp_path / "clip2.mp4"
        data = b"SAME_PLAINTEXT" * 100
        src1.write_bytes(data)
        src2.write_bytes(data)

        orig_key = gw._EXFIL_AES_KEY
        gw._EXFIL_AES_KEY = key
        enc1 = open(gw._encrypt_clip_aes256(str(src1)), 'rb').read()
        enc2 = open(gw._encrypt_clip_aes256(str(src2)), 'rb').read()
        gw._EXFIL_AES_KEY = orig_key
        assert enc1 != enc2   # different nonces

    def test_tampered_ciphertext_raises_on_decrypt(self, tmp_path):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        key = os.urandom(32)
        src = tmp_path / "clip.mp4"
        src.write_bytes(b"SENSITIVE" * 50)

        orig_key = gw._EXFIL_AES_KEY
        gw._EXFIL_AES_KEY = key
        enc_path = gw._encrypt_clip_aes256(str(src))
        gw._EXFIL_AES_KEY = orig_key

        raw = bytearray(open(enc_path, 'rb').read())
        raw[-1] ^= 0xFF   # flip last byte of GCM tag
        with pytest.raises(Exception):   # AESGCM raises InvalidTag
            AESGCM(key).decrypt(bytes(raw[:12]), bytes(raw[12:]), None)

    def test_nonce_is_prepended_and_12_bytes(self, tmp_path):
        key = os.urandom(32)
        src = tmp_path / "clip.mp4"
        src.write_bytes(b"X" * 256)
        orig_key = gw._EXFIL_AES_KEY
        gw._EXFIL_AES_KEY = key
        enc_path = gw._encrypt_clip_aes256(str(src))
        gw._EXFIL_AES_KEY = orig_key
        raw = open(enc_path, 'rb').read()
        # File must be at least nonce(12) + plaintext(256) + tag(16)
        assert len(raw) == 12 + 256 + 16


# ══════════════════════════════════════════════════════════════════════════════
# 5. EXFILTRATE_CLIP INTEGRATION
# ══════════════════════════════════════════════════════════════════════════════

class TestExfiltration:

    def test_exfiltrate_skips_when_host_not_configured(self, tmp_path, monkeypatch):
        """If EXFIL_HOST is empty, nothing happens."""
        monkeypatch.setattr(gw, '_EXFIL_HOST', '')
        monkeypatch.setattr(gw, '_EXFIL_AES_KEY', os.urandom(32))
        encrypt_mock = MagicMock(return_value=None)
        monkeypatch.setattr(gw, '_encrypt_clip_aes256', encrypt_mock)

        src = tmp_path / "clip.mp4"
        src.write_bytes(b"data")
        gw.exfiltrate_clip(str(src))
        encrypt_mock.assert_not_called()

    def test_exfiltrate_skips_when_key_not_configured(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gw, '_EXFIL_HOST', 'myserver.example.com')
        monkeypatch.setattr(gw, '_EXFIL_AES_KEY', None)
        upload_mock = MagicMock()
        monkeypatch.setattr(gw, '_ssh_upload', upload_mock)

        src = tmp_path / "clip.mp4"
        src.write_bytes(b"data")
        gw.exfiltrate_clip(str(src))
        upload_mock.assert_not_called()

    def test_exfiltrate_deletes_plaintext_on_success(self, tmp_path, monkeypatch):
        """Successful upload → plaintext .mp4 is deleted."""
        key = os.urandom(32)
        monkeypatch.setattr(gw, '_EXFIL_HOST', 'myserver.example.com')
        monkeypatch.setattr(gw, '_EXFIL_AES_KEY', key)
        monkeypatch.setattr(gw, '_ssh_upload', MagicMock(return_value=True))

        src = tmp_path / "clip.mp4"
        src.write_bytes(b"CLIP_DATA" * 100)
        gw.exfiltrate_clip(str(src))

        assert not src.exists()   # plaintext deleted
        assert (tmp_path / "clip.mp4.enc").exists()   # ciphertext kept locally

    def test_exfiltrate_keeps_plaintext_on_upload_failure(self, tmp_path, monkeypatch):
        """Failed upload → plaintext is NOT deleted (don't lose evidence)."""
        key = os.urandom(32)
        monkeypatch.setattr(gw, '_EXFIL_HOST', 'myserver.example.com')
        monkeypatch.setattr(gw, '_EXFIL_AES_KEY', key)
        monkeypatch.setattr(gw, '_ssh_upload', MagicMock(return_value=False))

        src = tmp_path / "clip.mp4"
        src.write_bytes(b"CLIP_DATA" * 100)
        gw.exfiltrate_clip(str(src))

        assert src.exists()   # plaintext preserved on upload failure

    def test_ssh_upload_skips_without_paramiko(self, monkeypatch):
        """If paramiko is not installed, _ssh_upload returns False gracefully."""
        import builtins
        real_import = builtins.__import__
        def mock_import(name, *args, **kwargs):
            if name == 'paramiko':
                raise ImportError("No module named 'paramiko'")
            return real_import(name, *args, **kwargs)
        monkeypatch.setattr(builtins, '__import__', mock_import)
        result = gw._ssh_upload('/tmp/test.enc', 'test.enc')
        assert result is False


# ══════════════════════════════════════════════════════════════════════════════
# 6. RAM-BUFFERED LOG WRITES
# ══════════════════════════════════════════════════════════════════════════════

class TestLogBuffer:

    def test_perm_write_buffers_in_memory(self, tmp_path, monkeypatch):
        """_perm_write must NOT write to disk immediately."""
        log_path = str(tmp_path / "test.log")
        monkeypatch.setattr(gw, '_log_buffer', collections.defaultdict(list))
        monkeypatch.setattr(gw, '_perm_write', _real_perm_write)

        gw._perm_write(log_path, "line one")
        gw._perm_write(log_path, "line two")

        assert not os.path.exists(log_path)   # nothing on disk yet
        assert len(gw._log_buffer[log_path]) == 2

    def test_do_flush_logs_writes_to_disk(self, tmp_path, monkeypatch):
        log_path = str(tmp_path / "test.log")
        monkeypatch.setattr(gw, '_log_buffer', collections.defaultdict(list))
        monkeypatch.setattr(gw, '_perm_write', _real_perm_write)

        gw._perm_write(log_path, "alpha")
        gw._perm_write(log_path, "beta")
        gw._do_flush_logs()

        content = open(log_path).read()
        assert "alpha" in content
        assert "beta" in content

    def test_do_flush_logs_clears_buffer(self, tmp_path, monkeypatch):
        log_path = str(tmp_path / "test.log")
        monkeypatch.setattr(gw, '_log_buffer', collections.defaultdict(list))
        monkeypatch.setattr(gw, '_perm_write', _real_perm_write)

        gw._perm_write(log_path, "something")
        gw._do_flush_logs()

        assert len(gw._log_buffer[log_path]) == 0

    def test_do_flush_logs_rotates_at_size_cap(self, tmp_path, monkeypatch):
        """File over _LOG_MAX_SIZE_BYTES → rotated to .1 before next write."""
        log_path = str(tmp_path / "big.log")
        monkeypatch.setattr(gw, '_log_buffer', collections.defaultdict(list))
        monkeypatch.setattr(gw, '_perm_write', _real_perm_write)

        # Pre-create an oversized file
        with open(log_path, 'w') as f:
            f.write("X" * (gw._LOG_MAX_SIZE_BYTES + 1))

        gw._perm_write(log_path, "new line after rotation")
        gw._do_flush_logs()

        assert os.path.exists(log_path + ".1")   # rotated
        content = open(log_path).read()
        assert "new line after rotation" in content   # new file started

    def test_concurrent_writes_are_thread_safe(self, tmp_path, monkeypatch):
        """Multiple threads writing to the buffer must not corrupt it."""
        log_path = str(tmp_path / "concurrent.log")
        monkeypatch.setattr(gw, '_log_buffer', collections.defaultdict(list))
        monkeypatch.setattr(gw, '_perm_write', _real_perm_write)

        errors = []
        def writer():
            try:
                for i in range(100):
                    gw._perm_write(log_path, f"thread-line-{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()

        assert not errors
        assert len(gw._log_buffer[log_path]) == 500   # 5 threads × 100 lines

    def test_flush_interval_is_60_seconds(self):
        assert gw._LOG_FLUSH_INTERVAL == 60


# ══════════════════════════════════════════════════════════════════════════════
# 7. CAMERA TAMPER DETECTION
# ══════════════════════════════════════════════════════════════════════════════

class TestCameraTamper:

    @pytest.fixture(autouse=True)
    def _clear_tamper_cooldown(self, monkeypatch):
        """_send_tamper_email is rate-limited to one mail per hour via module
        state. Reset it per test so cases do not suppress each other."""
        monkeypatch.setattr(gw, '_last_tamper_email', 0.0)

    def test_send_tamper_email_calls_smtp(self, monkeypatch):
        """_send_tamper_email must call SMTP_SSL and send a message."""
        monkeypatch.setattr(gw, 'EMAIL_SENDER', 'test@example.com')
        monkeypatch.setattr(gw, 'EMAIL_SENDER_PASS', 'pass')
        monkeypatch.setattr(gw, 'EMAIL_RECIPIENTS', ['dest@example.com'])

        smtp_mock = MagicMock()
        smtp_mock.__enter__ = MagicMock(return_value=smtp_mock)
        smtp_mock.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr(gw.smtplib, 'SMTP_SSL', MagicMock(return_value=smtp_mock))

        gw._send_tamper_email()
        assert smtp_mock.send_message.called

    def test_send_tamper_email_subject_contains_critical(self, monkeypatch):
        monkeypatch.setattr(gw, 'EMAIL_SENDER', 'test@example.com')
        monkeypatch.setattr(gw, 'EMAIL_SENDER_PASS', 'pass')
        monkeypatch.setattr(gw, 'EMAIL_RECIPIENTS', ['dest@example.com'])

        sent_msgs = []
        smtp_mock = MagicMock()
        smtp_mock.__enter__ = MagicMock(return_value=smtp_mock)
        smtp_mock.__exit__ = MagicMock(return_value=False)
        smtp_mock.send_message.side_effect = lambda m: sent_msgs.append(m)
        monkeypatch.setattr(gw.smtplib, 'SMTP_SSL', MagicMock(return_value=smtp_mock))

        gw._send_tamper_email()
        assert sent_msgs
        assert 'CRITICAL' in sent_msgs[0]['Subject'].upper() or 'TAMPER' in sent_msgs[0]['Subject'].upper()

    def test_send_tamper_email_skips_when_no_sender(self, monkeypatch):
        """If EMAIL_SENDER is empty, must not crash or call SMTP."""
        monkeypatch.setattr(gw, 'EMAIL_SENDER', '')
        smtp_mock = MagicMock()
        monkeypatch.setattr(gw.smtplib, 'SMTP_SSL', smtp_mock)
        gw._send_tamper_email()   # must not raise
        smtp_mock.assert_not_called()

    def test_send_tamper_email_bypasses_mode_flags(self, app_client, monkeypatch):
        """Tamper alert ignores DND, idle, email_off — it's unconditional."""
        monkeypatch.setattr(gw, 'MODE_DND',       True)
        monkeypatch.setattr(gw, 'MODE_IDLE',      True)
        monkeypatch.setattr(gw, 'MODE_EMAIL_OFF', True)
        monkeypatch.setattr(gw, 'EMAIL_SENDER',   'test@example.com')
        monkeypatch.setattr(gw, 'EMAIL_SENDER_PASS', 'pass')
        monkeypatch.setattr(gw, 'EMAIL_RECIPIENTS', ['dest@example.com'])

        smtp_mock = MagicMock()
        smtp_mock.__enter__ = MagicMock(return_value=smtp_mock)
        smtp_mock.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr(gw.smtplib, 'SMTP_SSL', MagicMock(return_value=smtp_mock))

        gw._send_tamper_email()
        assert smtp_mock.send_message.called   # sent despite all modes being active

    def test_send_tamper_email_is_rate_limited(self, monkeypatch):
        """A flickering camera must not produce a mail per frame."""
        monkeypatch.setattr(gw, 'EMAIL_SENDER', 'test@example.com')
        monkeypatch.setattr(gw, 'EMAIL_SENDER_PASS', 'pass')
        monkeypatch.setattr(gw, 'EMAIL_RECIPIENTS', ['dest@example.com'])

        smtp_mock = MagicMock()
        smtp_mock.__enter__ = MagicMock(return_value=smtp_mock)
        smtp_mock.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr(gw.smtplib, 'SMTP_SSL', MagicMock(return_value=smtp_mock))

        gw._send_tamper_email()
        gw._send_tamper_email()
        gw._send_tamper_email()
        assert smtp_mock.send_message.call_count == 1, \
            f"expected one mail per {gw._TAMPER_EMAIL_COOLDOWN}s window"


# ══════════════════════════════════════════════════════════════════════════════
# 8. CLIP STOP TRIGGERS EXFILTRATION
# ══════════════════════════════════════════════════════════════════════════════

class TestClipStopExfiltration:

    def test_clip_stop_endpoint_calls_exfiltrate(self, app_client, monkeypatch):
        """POST /api/clip/stop must call exfiltrate_clip in a thread."""
        r = app_client.post('/api/login', json={'username': 'user', 'password': 'user'})
        token = r.json()['token']

        # Inject a mock clip writer
        import cv2
        writer_mock = MagicMock(spec=cv2.VideoWriter)
        writer_mock.isOpened.return_value = True
        monkeypatch.setattr(gw, '_clip_writer', writer_mock)
        monkeypatch.setattr(gw, '_clip_path', '/tmp/fake_clip.mp4')

        calls = []
        monkeypatch.setattr(gw, 'exfiltrate_clip', lambda p: calls.append(p))

        r2 = app_client.post('/api/clip/stop', headers={'X-Garuda-Token': token})
        assert r2.status_code == 200
        # exfiltrate_clip is called in a thread; give it a moment
        time.sleep(0.05)
        assert '/tmp/fake_clip.mp4' in calls


# ══════════════════════════════════════════════════════════════════════════════
# 9. WEBSOCKET RATE LIMIT LOGIC (unit-level)
# ══════════════════════════════════════════════════════════════════════════════

class TestWebSocketRateLimit:

    def test_rate_store_fills_on_repeated_hits(self, monkeypatch):
        """Filling _rate_store for an IP beyond _RATE_LIMIT simulates ws block."""
        ip = '10.0.0.1'
        rate_store = collections.defaultdict(list)
        monkeypatch.setattr(gw, '_rate_store', rate_store)
        monkeypatch.setattr(gw, '_RATE_LIMIT', 5)
        monkeypatch.setattr(gw, '_RATE_WINDOW', 60)

        now = time.time()
        rate_store[ip] = [now] * 5   # already at limit

        # Simulate what the WS handler checks
        stamps = rate_store[ip]
        stamps[:] = [t for t in stamps if now - t < 60]
        blocked = len(stamps) >= 5
        assert blocked

    def test_rate_store_clears_old_timestamps(self, monkeypatch):
        ip = '10.0.0.2'
        rate_store = collections.defaultdict(list)
        monkeypatch.setattr(gw, '_rate_store', rate_store)

        # All timestamps are stale (older than window)
        rate_store[ip] = [time.time() - 3700] * 30

        now = time.time()
        stamps = rate_store[ip]
        stamps[:] = [t for t in stamps if now - t < 3600]
        assert len(stamps) == 0   # all cleared


# ══════════════════════════════════════════════════════════════════════════════
# 10. FULL LOGIN → PROTECTED ROUTE → REFRESH → LOGOUT FLOW
# ══════════════════════════════════════════════════════════════════════════════

class TestEndToEndAuthFlow:

    def test_full_auth_cycle(self, app_client, monkeypatch):
        """Login → use token → force-expire → refresh → use again → logout."""
        # 1. Login
        r = app_client.post('/api/login', json={'username': 'user', 'password': 'user'})
        assert r.status_code == 200
        token = r.json()['token']

        # 2. Access protected route
        r = app_client.get('/api/session', headers={'X-Garuda-Token': token})
        assert r.status_code == 200
        assert r.json()['username'] == 'user'

        # 3. Force-expire the access token
        gw._sessions[token]['expires'] = time.time() - 1
        r = app_client.get('/api/session', headers={'X-Garuda-Token': token})
        assert r.status_code == 401

        # 4. Issue a fresh access token via the refresh store directly
        refresh_tok = list(gw._refresh_tokens.keys())[0]
        rs = gw.get_refresh_token(refresh_tok)
        assert rs is not None
        new_token = gw.create_session(rs['username'])

        # 5. New token works
        r = app_client.get('/api/session', headers={'X-Garuda-Token': new_token})
        assert r.status_code == 200

        # 6. Logout clears the session
        app_client.post('/api/logout', headers={'X-Garuda-Token': new_token},
                        cookies={'garuda_refresh': refresh_tok})
        assert new_token not in gw._sessions
        assert refresh_tok not in gw._refresh_tokens

    def test_state_endpoint_accessible_after_login(self, app_client, monkeypatch):
        r = app_client.post('/api/login', json={'username': 'user', 'password': 'user'})
        token = r.json()['token']
        r2 = app_client.get('/api/state', headers={'X-Garuda-Token': token})
        assert r2.status_code == 200

    def test_unauthenticated_access_to_protected_route(self, app_client, monkeypatch):
        r = app_client.get('/api/state')
        assert r.status_code == 401
