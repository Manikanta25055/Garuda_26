"""
Test configuration for Garuda Web API.
Mocks all hardware dependencies (GStreamer, Hailo, GPIO) so tests run
on any machine without the RPi5 / AI accelerator attached.
"""
import sys
import os
import json
import collections
import asyncio
import threading
import time
from unittest.mock import MagicMock, patch
import pytest
import httpx

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


class SyncASGIClient:
    """Small sync wrapper around httpx.AsyncClient for ASGI app tests.

    Starlette's TestClient currently stalls on this app's startup path in this
    environment. Running a dedicated event loop thread keeps requests
    synchronous for the existing test suite without changing test call sites.
    """

    def __init__(self, app, raise_server_exceptions=True):
        self.app = app
        self.raise_server_exceptions = raise_server_exceptions
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._ready = threading.Event()
        self._thread.start()
        if not self._ready.wait(timeout=10):
            raise RuntimeError("Timed out starting ASGI test client.")
        if getattr(self, "_startup_error", None):
            raise self._startup_error

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._startup())
        self._ready.set()
        self._loop.run_forever()

    async def _startup(self):
        try:
            self._lifespan = gw._lifespan(self.app)
            await self._lifespan.__aenter__()
            self._transport = httpx.ASGITransport(
                app=self.app,
                raise_app_exceptions=self.raise_server_exceptions,
            )
            self._client = httpx.AsyncClient(
                transport=self._transport,
                base_url="http://testserver",
            )
        except Exception as exc:
            self._startup_error = exc

    async def _request(self, method, url, **kwargs):
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("X-Forwarded-For", "127.0.0.1")
        response = await self._client.request(method, url, headers=headers, **kwargs)
        await response.aread()
        return response

    def request(self, method, url, **kwargs):
        future = asyncio.run_coroutine_threadsafe(
            self._request(method, url, **kwargs), self._loop
        )
        return future.result(timeout=10)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def close(self):
        future = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        future.result(timeout=10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    async def _shutdown(self):
        if hasattr(self, "_client"):
            await self._client.aclose()
        if hasattr(self, "_lifespan"):
            await self._lifespan.__aexit__(None, None, None)

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

_SHARED_CLIENT = None


@pytest.fixture(scope="function")
def tmp_data(tmp_path):
    (tmp_path / "system_logs").mkdir()
    return tmp_path


@pytest.fixture(scope="session")
def shared_client():
    global _SHARED_CLIENT
    gw._presence_poller = lambda: None
    gw._deadman_monitor = lambda: None
    gw._connectivity_monitor = lambda: None
    gw._hash_password = lambda pw: f'test-hash::{pw}'
    gw._verify_password = lambda pw, stored: stored == pw or stored == f'test-hash::{pw}'
    gw._perm_write = lambda filepath, line: None
    gw._atomic_json_write = _fast_json_write
    _SHARED_CLIENT = SyncASGIClient(gw.fastapi_app, raise_server_exceptions=True)
    try:
        yield _SHARED_CLIENT
    finally:
        _SHARED_CLIENT.close()


@pytest.fixture(scope="function")
def app_client(tmp_data, monkeypatch, shared_client):
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
    monkeypatch.setattr(gw, '_RATE_WINDOW', 3600)
    monkeypatch.setattr(gw, '_RATE_LIMIT', 30)

    # ── Mock SMTP so no real emails are sent ──
    smtp_mock = MagicMock()
    smtp_mock.__enter__ = MagicMock(return_value=smtp_mock)
    smtp_mock.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(gw.smtplib, 'SMTP_SSL', MagicMock(return_value=smtp_mock))

    gw._init_event_db()   # ensure SQLite DB exists for each test
    yield shared_client


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


def _fast_json_write(filepath: str, data):
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
