"""TC-M01 through TC-M08: Mode toggling tests."""
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'basic_pipelines'))
import Garuda_web as gw


def test_toggle_dnd_mode(app_client, user_headers):
    """TC-M01: Toggle DND on → state reflects change."""
    r = app_client.post('/api/modes', json={'mode': 'dnd', 'value': True}, headers=user_headers)
    assert r.status_code == 200
    # Verify via state
    s = app_client.get('/api/state', headers=user_headers).json()
    assert s['modes']['dnd'] is True


def test_modes_requires_auth(app_client):
    """TC-M02: No auth → 401."""
    r = app_client.post('/api/modes', json={'mode': 'dnd', 'value': True})
    assert r.status_code == 401


def test_toggle_dnd_on_off_5x(app_client, user_headers):
    """TC-M03: Toggle DND on/off 5x → state consistent each time."""
    for i in range(5):
        val = bool(i % 2)
        r = app_client.post('/api/modes', json={'mode': 'dnd', 'value': val}, headers=user_headers)
        assert r.status_code == 200
        s = app_client.get('/api/state', headers=user_headers).json()
        assert s['modes']['dnd'] == val


def test_unknown_mode_returns_error(app_client, user_headers):
    """TC-M04: Unknown mode key → 400."""
    r = app_client.post('/api/modes', json={'mode': 'nonexistent', 'value': True}, headers=user_headers)
    assert r.status_code == 400


def test_night_mode_reflected_in_state(app_client, user_headers):
    """TC-M05: Set night mode → mode_night true in state."""
    app_client.post('/api/modes', json={'mode': 'night', 'value': True}, headers=user_headers)
    s = app_client.get('/api/state', headers=user_headers).json()
    assert s['modes']['night'] is True


def test_emergency_stop_requires_auth(app_client):
    """TC-M06: /api/emergency-stop without auth → 401."""
    r = app_client.post('/api/emergency-stop')
    assert r.status_code == 401


def test_privacy_mode_in_state(app_client, user_headers):
    """TC-M08: Toggle privacy mode → reflected in state."""
    app_client.post('/api/modes', json={'mode': 'privacy', 'value': False}, headers=user_headers)
    s = app_client.get('/api/state', headers=user_headers).json()
    assert s['modes']['privacy'] is False


def test_emergency_overrides_dnd(app_client, user_headers):
    """Emergency mode auto-clears DND."""
    app_client.post('/api/modes', json={'mode': 'dnd', 'value': True}, headers=user_headers)
    app_client.post('/api/modes', json={'mode': 'emergency', 'value': True}, headers=user_headers)
    s = app_client.get('/api/state', headers=user_headers).json()
    assert s['modes']['dnd'] is False
