"""TC-L01 through TC-L07: Log access and master key tests."""
import pytest
from unittest.mock import MagicMock

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'basic_pipelines'))
import Garuda_web as gw


def test_logs_require_master_key(app_client, admin_headers):
    """TC-L01: /api/logs without master key unlock → 403."""
    # Admin token without logs_unlocked
    r = app_client.get('/api/logs', headers=admin_headers)
    assert r.status_code == 403


def test_master_key_verify_unlocks_logs(app_client, admin_headers):
    """TC-L02: Verify master key → session logs_unlocked becomes True."""
    r = app_client.post('/api/master_key/verify',
                        json={'key': 'test-master-key-12345'},
                        headers=admin_headers)
    assert r.status_code == 200
    # Now logs should be accessible
    admin_token = admin_headers['X-Garuda-Token']
    sess = gw._sessions.get(admin_token)
    assert sess is not None
    assert sess.get('logs_unlocked') is True


def test_logs_accessible_after_unlock(app_client, admin_headers):
    """TC-L03: /api/logs after unlock → 200, log types present."""
    app_client.post('/api/master_key/verify',
                    json={'key': 'test-master-key-12345'},
                    headers=admin_headers)
    r = app_client.get('/api/logs', headers=admin_headers)
    assert r.status_code == 200
    d = r.json()
    assert 'system_log' in d or 'voice_log' in d or 'detection_log' in d or 'presence_log' in d


def test_logs_download_after_unlock(app_client, admin_headers):
    """TC-L04: /api/logs/download → 200, non-empty text."""
    app_client.post('/api/master_key/verify',
                    json={'key': 'test-master-key-12345'},
                    headers=admin_headers)
    r = app_client.get('/api/logs/download', headers=admin_headers)
    assert r.status_code == 200


def test_logs_user_role_requires_unlock(app_client, user_headers):
    """TC-L05: User cannot access logs (admin + master key required)."""
    r = app_client.get('/api/logs', headers=user_headers)
    assert r.status_code == 403


def test_master_key_request_otp(app_client, admin_headers, monkeypatch):
    """TC-L06: /api/master_key/request_otp → 200."""
    monkeypatch.setattr(gw, 'send_otp_via_email', MagicMock(return_value=(True, None)))
    r = app_client.post('/api/master_key/request_otp',
                        json={'current_key': 'test-master-key-12345'},
                        headers=admin_headers)
    assert r.status_code == 200


def test_master_key_add_new_key(app_client, admin_headers, monkeypatch):
    """TC-L07: Add new master key via OTP flow."""
    monkeypatch.setattr(gw, 'send_otp_via_email', MagicMock(return_value=(True, None)))
    app_client.post('/api/master_key/request_otp',
                    json={'current_key': 'test-master-key-12345'},
                    headers=admin_headers)
    otp = gw.MASTER_KEY_OTP
    assert otp is not None
    r = app_client.post('/api/master_key/add',
                        json={'otp': otp, 'new_key': 'Xy7!Pk2@Qr9#Mn5'},
                        headers=admin_headers)
    assert r.status_code == 200, r.text
    assert 'Xy7!Pk2@Qr9#Mn5' in gw.MASTER_KEYS
