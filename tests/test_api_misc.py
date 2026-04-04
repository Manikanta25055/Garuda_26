"""Additional API coverage for uncovered Garuda web endpoints."""

from unittest.mock import MagicMock

import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'basic_pipelines'))
import Garuda_web as gw


def test_users_public_lists_only_non_admin_profiles(app_client):
    r = app_client.get('/api/users-public')
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert any(user['username'] == 'user' for user in data)
    assert all(user['username'] != 'admin' for user in data)


def test_chat_without_groq_key_returns_configuration_message(app_client, user_headers, monkeypatch):
    monkeypatch.setattr(gw, 'GROQ_API_KEY', '')
    r = app_client.post('/api/chat', json={'message': 'hello'}, headers=user_headers)
    assert r.status_code == 200
    assert 'Narada is not configured yet' in r.json()['response']


def test_chat_rejects_empty_message(app_client, user_headers):
    r = app_client.post('/api/chat', json={'message': '   '}, headers=user_headers)
    assert r.status_code == 400


def test_chat_stream_without_groq_key_emits_sse_tokens(app_client, user_headers, monkeypatch):
    monkeypatch.setattr(gw, 'GROQ_API_KEY', '')
    r = app_client.post('/api/chat/stream', json={'message': 'status'}, headers=user_headers)
    assert r.status_code == 200
    assert r.headers['content-type'].startswith('text/event-stream')
    body = r.text
    assert '"type": "start"' in body
    assert '"type": "token"' in body
    assert '"type": "done"' in body


def test_presence_refresh_reports_result(app_client, admin_headers, monkeypatch):
    def fake_refresh():
        gw._owner_present = True

    monkeypatch.setattr(gw, '_do_presence_check', fake_refresh)
    r = app_client.post('/api/presence_refresh', headers=admin_headers)
    assert r.status_code == 200
    assert r.json() == {'owner_present': True}


def test_email_test_returns_error_payload_on_send_failure(app_client, admin_headers, monkeypatch):
    monkeypatch.setattr(gw, 'send_otp_via_email', MagicMock(return_value=(False, 'smtp down')))
    r = app_client.post('/api/email/test', headers=admin_headers)
    assert r.status_code == 200
    assert r.json() == {'ok': False, 'error': 'smtp down'}


def test_master_key_login_unlocks_logs(app_client):
    r = app_client.post('/api/master_key/login', json={'key': 'test-master-key-12345'})
    assert r.status_code == 200
    data = r.json()
    assert data['role'] == 'admin'
    assert data['logs_unlocked'] is True
    token = data['token']
    session = gw.get_session(token)
    assert session is not None
    assert session['logs_unlocked'] is True


def test_master_key_login_rejects_invalid_key(app_client):
    r = app_client.post('/api/master_key/login', json={'key': 'wrong-key'})
    assert r.status_code == 401


def test_master_keys_are_masked_for_admin(app_client, admin_headers):
    original = list(gw.MASTER_KEYS)
    gw.MASTER_KEYS[:] = ['Abcd-1234-Secret!']
    try:
        r = app_client.get('/api/master_keys', headers=admin_headers)
        assert r.status_code == 200
        data = r.json()
        assert data['count'] == 1
        assert data['keys'][0].endswith('ret!')
        assert 'Abcd' not in data['keys'][0]
    finally:
        gw.MASTER_KEYS[:] = original


def test_master_key_delete_rejects_last_remaining_key(app_client, admin_headers):
    gw.MASTER_KEYS[:] = ['Only-Key-123!']
    r = app_client.post('/api/master_key/delete', json={'index': 0}, headers=admin_headers)
    assert r.status_code == 400


def test_master_key_delete_removes_requested_index(app_client, admin_headers):
    gw.MASTER_KEYS[:] = ['First-Key-123!', 'Second-Key-456!']
    r = app_client.post('/api/master_key/delete', json={'index': 0}, headers=admin_headers)
    assert r.status_code == 200
    assert gw.MASTER_KEYS == ['Second-Key-456!']


def test_stream_requires_authenticated_session(app_client):
    r = app_client.get('/stream')
    assert r.status_code == 401


def test_webrtc_offer_returns_501_when_webrtc_disabled(app_client, user_headers, monkeypatch):
    monkeypatch.setattr(gw, '_WEBRTC_AVAILABLE', False)
    r = app_client.post('/webrtc/offer', json={'sdp': 'offer', 'type': 'offer'}, headers=user_headers)
    assert r.status_code == 501


def test_feedback_is_persisted_to_primary_and_backup_files(app_client):
    r = app_client.post('/api/feedback', json={
        'message': 'Keep this permanently',
        'category': 'general',
        'rating': 5,
        'name': 'Tester',
    })
    assert r.status_code == 200
    assert r.json() == {'ok': True}

    primary = gw._safe_json_load(gw.FEEDBACK_FILE, [])
    backup = gw._safe_json_load(gw.FEEDBACK_BACKUP_FILE, [])
    assert len(primary) == 1
    assert len(backup) == 1
    assert primary[0]['message'] == 'Keep this permanently'
    assert backup[0]['message'] == 'Keep this permanently'


def test_feedback_load_recovers_from_backup_when_primary_is_invalid(app_client):
    entries = [{
        'id': 1,
        'timestamp': '2026-04-01 12:00:00',
        'category': 'general',
        'rating': 4,
        'name': 'Tester',
        'message': 'Recovered from backup',
        'ip': '127.0.0.1',
    }]
    gw._atomic_json_write(gw.FEEDBACK_BACKUP_FILE, entries)
    with open(gw.FEEDBACK_FILE, 'w', encoding='utf-8') as f:
        f.write('{broken json')

    loaded = gw._load_feedback()
    restored_primary = gw._safe_json_load(gw.FEEDBACK_FILE, [])

    assert loaded == entries
    assert restored_primary == entries
