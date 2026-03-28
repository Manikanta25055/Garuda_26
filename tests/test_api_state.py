"""TC-S01 through TC-S10: /api/state tests."""
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'basic_pipelines'))
import Garuda_web as gw

REQUIRED_KEYS = {
    'inference_fps', 'cpu_percent', 'ram_percent', 'cpu_temp',
    'disk_percent', 'uptime_seconds', 'alert_active',
    'modes', 'net_online', 'pending_sync',
}


def test_state_requires_auth(app_client):
    """TC-S02: No auth → 401."""
    r = app_client.get('/api/state')
    assert r.status_code == 401


def test_state_with_user_token(app_client, user_headers):
    """TC-S01: Valid user token → 200."""
    r = app_client.get('/api/state', headers=user_headers)
    assert r.status_code == 200


def test_state_has_required_keys(app_client, user_headers):
    """TC-S03: All required top-level keys present."""
    r = app_client.get('/api/state', headers=user_headers)
    d = r.json()
    missing = REQUIRED_KEYS - set(d.keys())
    assert not missing, f"Missing keys: {missing}"


def test_fps_valid_range(app_client, user_headers):
    """TC-S04: inference_fps is float 0–60."""
    r = app_client.get('/api/state', headers=user_headers)
    fps = r.json()['inference_fps']
    assert isinstance(fps, (int, float))
    assert 0.0 <= fps <= 60.0


def test_cpu_percent_range(app_client, user_headers):
    """TC-S05: cpu_percent is 0–100 or None (if psutil unavailable)."""
    r = app_client.get('/api/state', headers=user_headers)
    cpu = r.json()['cpu_percent']
    if cpu is not None:
        assert 0.0 <= cpu <= 100.0


def test_disk_percent_range(app_client, user_headers):
    """TC-S06: disk_percent is 0–100 or None."""
    r = app_client.get('/api/state', headers=user_headers)
    dp = r.json()['disk_percent']
    if dp is not None:
        assert 0.0 <= dp <= 100.0


def test_uptime_non_negative(app_client, user_headers):
    """TC-S07: uptime_seconds is non-negative integer."""
    r = app_client.get('/api/state', headers=user_headers)
    ut = r.json()['uptime_seconds']
    assert isinstance(ut, int)
    assert ut >= 0


def test_detection_log_count(app_client, user_headers):
    """TC-S08: detection_log_count matches in-memory log length."""
    gw._detection_log.append('test entry')
    r = app_client.get('/api/state', headers=user_headers)
    d = r.json()
    # detection_log_count should equal current log length
    assert d.get('detection_log_count', 0) == len(gw._detection_log)
    gw._detection_log.clear()


def test_net_online_is_bool(app_client, user_headers):
    """TC-S09: net_online is a bool."""
    r = app_client.get('/api/state', headers=user_headers)
    no = r.json()['net_online']
    assert isinstance(no, bool)


def test_state_stable_repeated_calls(app_client, user_headers):
    """TC-S10: 10 consecutive calls — no exceptions, consistent types."""
    for _ in range(10):
        r = app_client.get('/api/state', headers=user_headers)
        assert r.status_code == 200
        d = r.json()
        assert isinstance(d, dict)
        assert 'uptime_seconds' in d
