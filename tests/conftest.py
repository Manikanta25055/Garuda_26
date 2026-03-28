"""
Test configuration for Garuda Web API.
Mocks all hardware dependencies (GStreamer, Hailo, GPIO) so tests run
on any machine without the RPi5 / AI accelerator attached.
"""
import sys
import os
import json
import collections
import threading
import time
from unittest.mock import MagicMock, patch
import pytest

# ── Mock hardware modules BEFORE importing Garuda_web ─────────────────────────
# Must be at module level so they intercept import-time code.

# GObject introspection / GStreamer
_gi_mock = MagicMock()
_gi_mock.require_version = MagicMock()
sys.modules.setdefault('gi', _gi_mock)
sys.modules.setdefault('gi.repository', MagicMock())
sys.modules.setdefault('gi.repository.Gst', MagicMock())
sys.modules.setdefault('gi.repository.GLib', MagicMock())

# Hailo AI runtime
sys.modules.setdefault('hailo', MagicMock())

# hailo_rpi_common — provide just enough structure for Garuda_web to import
class _MockCallbackClass:
    def __init__(self):
        self.new_variable = None
    def increment(self): pass
    def get_count(self): return 0
    def set_frame(self, f): pass
    def get_frame(self): return None

class _MockGStreamerApp:
    def __init__(self, args, user_data): pass
    def run(self): pass

_hrpc = MagicMock()
_hrpc.GStreamerApp = _MockGStreamerApp
_hrpc.app_callback_class = _MockCallbackClass
_hrpc.get_default_parser = MagicMock(return_value=MagicMock())
_hrpc.QUEUE = MagicMock(return_value="queue ! ")
_hrpc.get_caps_from_pad = MagicMock(return_value=None)
_hrpc.get_numpy_from_buffer = MagicMock(return_value=None)
sys.modules.setdefault('hailo_rpi_common', _hrpc)

# Audio / speech
sys.modules.setdefault('speech_recognition', MagicMock())
sys.modules.setdefault('setproctitle', MagicMock())

# WebRTC (optional — disable cleanly)
_aiortc = MagicMock()
sys.modules.setdefault('aiortc', _aiortc)
sys.modules.setdefault('aiortc.mediastreams', MagicMock())
sys.modules.setdefault('av', MagicMock())

# ── Import the app module ─────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'basic_pipelines'))
import Garuda_web as gw
from starlette.testclient import TestClient

# ── Helpers ───────────────────────────────────────────────────────────────────
_DEFAULT_USERS = {
    "user": {
        "password": "user",
        "role": "user",
        "display_name": "User",
        "box_color": "#1565c0",
        "history": {"logins": [], "narada_activity": []},
    },
    "admin": {
        "password": "root",
        "role": "admin",
        "display_name": "Admin",
        "box_color": "#e65100",
        "history": {"logins": [], "narada_activity": []},
    },
}


@pytest.fixture(scope="function")
def tmp_data(tmp_path):
    (tmp_path / "system_logs").mkdir()
    return tmp_path


@pytest.fixture(scope="function")
def app_client(tmp_data, monkeypatch):
    """
    Reset all global state, redirect file paths to tmp_data, mock SMTP,
    and yield a TestClient for the FastAPI app.
    """
    import smtplib

    # ── File path redirects ──
    monkeypatch.setattr(gw, 'USERS_FILE',          str(tmp_data / 'system_logs/users.json'))
    monkeypatch.setattr(gw, 'CONFIG_FILE',         str(tmp_data / 'system_logs/config.json'))
    monkeypatch.setattr(gw, 'ALERT_HISTORY_FILE',  str(tmp_data / 'system_logs/alert_history.json'))
    monkeypatch.setattr(gw, 'PRESENCE_LOG_FILE',   str(tmp_data / 'system_logs/presence_log.json'))
    monkeypatch.setattr(gw, 'MASTER_KEYS_FILE',    str(tmp_data / 'system_logs/master_keys.json'))
    monkeypatch.setattr(gw, 'EVENTS_DB',           str(tmp_data / 'system_logs/garuda_events.db'))
    monkeypatch.setattr(gw, 'PERM_SYSTEM_LOG',     str(tmp_data / 'system_logs/perm_system_log.txt'))
    monkeypatch.setattr(gw, 'PERM_VOICE_LOG',      str(tmp_data / 'system_logs/perm_voice_log.txt'))
    monkeypatch.setattr(gw, 'PERM_DETECTION_LOG',  str(tmp_data / 'system_logs/perm_detection_log.txt'))
    monkeypatch.setattr(gw, 'SCISSORS_LOG_FILE',   str(tmp_data / 'danger_sightings.txt'))
    monkeypatch.setattr(gw, 'NIGHT_MODE_LOG_FILE', str(tmp_data / 'night_mode_findings.txt'))

    # ── In-memory state reset ──
    monkeypatch.setattr(gw, '_sessions',    {})
    monkeypatch.setattr(gw, '_rate_store',  collections.defaultdict(list))
    monkeypatch.setattr(gw, 'USERS',        {k: dict(v) for k, v in _DEFAULT_USERS.items()})
    monkeypatch.setattr(gw, 'MASTER_KEYS',  ["test-master-key-12345"])
    monkeypatch.setattr(gw, 'ADMIN_OTP',    None)
    monkeypatch.setattr(gw, 'USER_FORGOT_OTP', None)
    monkeypatch.setattr(gw, '_forgot_otp_user', None)
    monkeypatch.setattr(gw, 'MASTER_KEY_OTP', None)
    monkeypatch.setattr(gw, '_pending_mk', None)
    monkeypatch.setattr(gw, 'system_updates_log', [])
    monkeypatch.setattr(gw, 'voice_assistant_log', [])
    monkeypatch.setattr(gw, 'voice_responses', [])
    monkeypatch.setattr(gw, '_detection_log', [])
    monkeypatch.setattr(gw, 'latest_detection_info', '')
    monkeypatch.setattr(gw, 'MODE_DND',       False)
    monkeypatch.setattr(gw, 'MODE_EMAIL_OFF', False)
    monkeypatch.setattr(gw, 'MODE_IDLE',      False)
    monkeypatch.setattr(gw, 'MODE_NIGHT',     False)
    monkeypatch.setattr(gw, 'MODE_EMERGENCY', False)
    monkeypatch.setattr(gw, 'MODE_PRIVACY',   True)
    monkeypatch.setattr(gw, '_alert_active',  False)
    monkeypatch.setattr(gw, '_alert_end_time', 0.0)
    monkeypatch.setattr(gw, '_alert_flash_count', 0)
    monkeypatch.setattr(gw, '_danger_trigger_info', '')
    monkeypatch.setattr(gw, '_alert_history', {})
    monkeypatch.setattr(gw, '_presence_log',  [])
    monkeypatch.setattr(gw, '_net_online',    True)
    monkeypatch.setattr(gw, '_total_frames',  0)
    monkeypatch.setattr(gw, '_detections_today', 0)
    monkeypatch.setattr(gw, 'last_email_sent_time', 0)
    monkeypatch.setattr(gw, 'DETECTION_THRESHOLD', 0.3)
    monkeypatch.setattr(gw, 'CUSTOM_VOICE_COMMANDS', {})
    monkeypatch.setattr(gw, 'KNOWN_DEVICES', [])
    monkeypatch.setattr(gw, '_app_start_time', time.time())

    # ── Stub background threads (they start in lifespan) ──
    monkeypatch.setattr(gw, '_presence_poller',     lambda: None)
    monkeypatch.setattr(gw, '_deadman_monitor',     lambda: None)
    monkeypatch.setattr(gw, '_connectivity_monitor', lambda: None)

    # ── Mock SMTP so no real emails are sent ──
    smtp_mock = MagicMock()
    smtp_mock.__enter__ = MagicMock(return_value=smtp_mock)
    smtp_mock.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(gw.smtplib, 'SMTP_SSL', MagicMock(return_value=smtp_mock))

    with TestClient(gw.fastapi_app, raise_server_exceptions=True) as client:
        gw._init_event_db()   # ensure SQLite DB exists for each test
        yield client


@pytest.fixture
def user_token(app_client):
    """Return a valid session token for the regular user."""
    r = app_client.post('/api/login', json={'username': 'user', 'password': 'user'})
    assert r.status_code == 200, r.text
    return r.json()['token']


@pytest.fixture
def admin_token(app_client, monkeypatch):
    """Return a valid admin session token (via OTP flow)."""
    # Patch send_otp_via_email to succeed without hitting SMTP
    monkeypatch.setattr(gw, 'send_otp_via_email', MagicMock(return_value=(True, None)))
    r = app_client.post('/api/admin/send-otp', json={'username': 'admin', 'password': 'root'})
    assert r.status_code == 200, r.text
    otp = gw.ADMIN_OTP
    assert otp is not None
    r2 = app_client.post('/api/admin/verify-otp', json={'username': 'admin', 'otp': otp})
    assert r2.status_code == 200, r2.text
    return r2.json()['token']


@pytest.fixture
def admin_headers(admin_token):
    return {'X-Garuda-Token': admin_token}


@pytest.fixture
def user_headers(user_token):
    return {'X-Garuda-Token': user_token}
