"""TC-C01 through TC-C08: Config CRUD tests."""
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'basic_pipelines'))
import Garuda_web as gw


def test_get_config_admin(app_client, admin_headers):
    """TC-C01: Admin can get config."""
    r = app_client.get('/api/config', headers=admin_headers)
    assert r.status_code == 200
    d = r.json()
    assert 'detection_threshold' in d
    assert 'email_recipients' in d
    assert 'custom_voice_commands' in d


def test_update_detection_threshold(app_client, admin_headers):
    """TC-C02: Update threshold → GET shows new value."""
    r = app_client.post('/api/config', json={'detection_threshold': 0.5}, headers=admin_headers)
    assert r.status_code == 200
    cfg = app_client.get('/api/config', headers=admin_headers).json()
    assert cfg['detection_threshold'] == pytest.approx(0.5)


def test_detection_threshold_clamped_low(app_client, admin_headers):
    """TC-C03: Threshold -1 gets clamped to 0.05 (not rejected)."""
    r = app_client.post('/api/config', json={'detection_threshold': -1}, headers=admin_headers)
    assert r.status_code == 200
    cfg = app_client.get('/api/config', headers=admin_headers).json()
    assert cfg['detection_threshold'] == pytest.approx(0.05)


def test_detection_threshold_clamped_high(app_client, admin_headers):
    """TC-C04: Threshold 1.5 gets clamped to 0.95 (not rejected)."""
    r = app_client.post('/api/config', json={'detection_threshold': 1.5}, headers=admin_headers)
    assert r.status_code == 200
    cfg = app_client.get('/api/config', headers=admin_headers).json()
    assert cfg['detection_threshold'] == pytest.approx(0.95)


def test_add_voice_command(app_client, admin_headers):
    """TC-C05: Add custom voice command → 200, appears in config."""
    r = app_client.post('/api/config/command/add',
                        json={'phrase': 'test phrase', 'response': 'test response'},
                        headers=admin_headers)
    assert r.status_code == 200
    cfg = app_client.get('/api/config', headers=admin_headers).json()
    assert 'test phrase' in cfg['custom_voice_commands']


def test_delete_voice_command(app_client, admin_headers):
    """TC-C06: Delete voice command → no longer in config."""
    app_client.post('/api/config/command/add',
                    json={'phrase': 'to delete', 'response': 'x'},
                    headers=admin_headers)
    r = app_client.post('/api/config/command/delete',
                        json={'phrase': 'to delete'},
                        headers=admin_headers)
    assert r.status_code == 200
    cfg = app_client.get('/api/config', headers=admin_headers).json()
    assert 'to delete' not in cfg['custom_voice_commands']


def test_config_user_forbidden(app_client, user_headers):
    """Config endpoints require admin."""
    assert app_client.get('/api/config', headers=user_headers).status_code == 403
    assert app_client.post('/api/config', json={}, headers=user_headers).status_code == 403


def test_config_persistence(app_client, admin_headers):
    """TC-C08: Config persists across API calls."""
    app_client.post('/api/config', json={'detection_threshold': 0.42}, headers=admin_headers)
    # Read again — must be consistent
    cfg1 = app_client.get('/api/config', headers=admin_headers).json()
    cfg2 = app_client.get('/api/config', headers=admin_headers).json()
    assert cfg1['detection_threshold'] == cfg2['detection_threshold']
