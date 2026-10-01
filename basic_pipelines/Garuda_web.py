##############################################################################
# GARUDA WEB — FastAPI web interface for the Garuda Security System
#
# SETUP (one-time):
#   pip install fastapi uvicorn[standard]
#   # For voice assistant:
#   curl -fsSL https://ollama.com/install.sh | sh
#   ollama pull phi3:latest
#
# RUN:
#   source setup_env.sh
#   python3 Garuda_web.py --input rpi
#   # Open http://localhost:8080
##############################################################################

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

import os
import argparse
import numpy as np
import setproctitle
import cv2
import time
import hailo
import sys
import datetime
import smtplib
from email.mime.text import MIMEText
import random
import string
import json
import requests
import secrets
import socket
import ipaddress
import subprocess
import asyncio
import threading
import traceback
import hashlib
import hmac
import math
import signal
import tempfile
import re
import anyio.to_thread
from pathlib import Path
from collections import defaultdict

from dotenv import load_dotenv
load_dotenv()

import speech_recognition as sr
import ctypes

# Silence ALSA/Jack error spam when PortAudio probes audio devices.
# IMPORTANT: keep callbacks at module level — temporary CFUNCTYPE objects get
# garbage-collected and the dangling C pointer causes a segfault.
_ALSA_ERROR_CB = None
_JACK_ERROR_CB = None
_JACK_INFO_CB  = None

try:
    _asound = ctypes.cdll.LoadLibrary('libasound.so.2')
    _ALSA_ERROR_CB = ctypes.CFUNCTYPE(
        None,
        ctypes.c_char_p, ctypes.c_int,
        ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p
    )(lambda *_: None)
    _asound.snd_lib_error_set_handler(_ALSA_ERROR_CB)
except Exception:
    pass

try:
    _libjack = ctypes.cdll.LoadLibrary('libjack.so.0')
    _JACK_CB_TYPE = ctypes.CFUNCTYPE(None, ctypes.c_char_p)
    _JACK_ERROR_CB = _JACK_CB_TYPE(lambda *_: None)
    _JACK_INFO_CB  = _JACK_CB_TYPE(lambda *_: None)
    _libjack.jack_set_error_function(_JACK_ERROR_CB)
    _libjack.jack_set_info_function(_JACK_INFO_CB)
except Exception:
    pass

import sqlite3

try:
    import psutil
except ImportError:
    psutil = None

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Response, Depends
from fastapi.responses import StreamingResponse, HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List
import uvicorn

try:
    from aiortc import RTCPeerConnection, RTCSessionDescription
    from aiortc.mediastreams import VideoStreamTrack
    import av
    _WEBRTC_AVAILABLE = True
except ImportError:
    _WEBRTC_AVAILABLE = False

from hailo_rpi_common import (
    get_default_parser,
    QUEUE,
    get_caps_from_pad,
    get_numpy_from_buffer,
    GStreamerApp,
    app_callback_class,
)

##############################################################################
# EMAIL CONFIG (secrets from .env, overridable via config.json for non-secrets)
##############################################################################
EMAIL_SENDER = os.environ.get("EMAIL_SENDER", "")
EMAIL_SENDER_PASS = os.environ.get("EMAIL_SENDER_PASS", "")
EMAIL_RECIPIENTS = [r.strip() for r in os.environ.get("EMAIL_RECIPIENTS", "amarmanikantan@gmail.com").split(",") if r.strip()]

# Set SECURE_COOKIES=1 in .env when serving behind HTTPS (Cloudflare tunnel).
# Leave unset for direct http://localhost access — secure=True drops cookies on plain HTTP.
_COOKIE_SECURE = os.environ.get("SECURE_COOKIES", "0").lower() in ("1", "true", "yes")
EMAIL_COOLDOWN = 60
last_email_sent_time = 0
_email_lock = threading.Lock()
_danger_active = False   # True while danger label is continuously detected

DANGER_LABELS: list = ["Knife", "scissors", "Hammer"]   # all non-person model outputs

# ── Encrypted evidence exfiltration (AES-256-GCM + SSH) ─────────────────────
# Set these in .env to enable off-site encrypted clip backup:
#   EXFIL_HOST        — SSH server hostname or IP
#   EXFIL_PORT        — SSH port (default 22)
#   EXFIL_USER        — SSH username
#   EXFIL_KEY_PATH    — path to SSH private key (preferred over password)
#   EXFIL_PASSWORD    — SSH password (fallback if no key)
#   EXFIL_REMOTE_PATH — remote directory (default ~/garuda_evidence/)
#   EXFIL_AES_KEY     — 32-byte key as 64-char hex string (generate with:
#                        python3 -c "import os,binascii; print(binascii.hexlify(os.urandom(32)).decode())")
_EXFIL_HOST     = os.environ.get("EXFIL_HOST", "")
_EXFIL_PORT     = int(os.environ.get("EXFIL_PORT", "22"))
_EXFIL_USER     = os.environ.get("EXFIL_USER", "")
_EXFIL_KEY_PATH = os.environ.get("EXFIL_KEY_PATH", "")
_EXFIL_PASSWORD = os.environ.get("EXFIL_PASSWORD", "")
_EXFIL_REMOTE   = os.environ.get("EXFIL_REMOTE_PATH", "garuda_evidence/")
_exfil_raw_key  = os.environ.get("EXFIL_AES_KEY", "")
_EXFIL_AES_KEY: bytes | None = bytes.fromhex(_exfil_raw_key) if len(_exfil_raw_key) == 64 else None

##############################################################################
# GLOBALS & SETTINGS
##############################################################################
app_gst = None  # GStreamer app instance

_BASE = Path(__file__).parent
SCISSORS_LOG_FILE    = str(_BASE / "danger_sightings.txt")
NIGHT_MODE_LOG_FILE  = str(_BASE / "night_mode_findings.txt")
LLM_LOG_FILE         = str(_BASE / "system_logs" / "llm_reasoning.json")
USERS_FILE           = str(_BASE / "system_logs" / "users.json")
CONFIG_FILE          = str(_BASE / "system_logs" / "config.json")
ALERT_HISTORY_FILE   = str(_BASE / "system_logs" / "alert_history.json")
PRESENCE_LOG_FILE    = str(_BASE / "system_logs" / "presence_log.json")
MASTER_KEYS_FILE     = str(_BASE / "system_logs" / "master_keys.json")
PERM_SYSTEM_LOG      = str(_BASE / "system_logs" / "perm_system_log.txt")
PERM_VOICE_LOG       = str(_BASE / "system_logs" / "perm_voice_log.txt")
PERM_DETECTION_LOG   = str(_BASE / "system_logs" / "perm_detection_log.txt")
FEEDBACK_FILE        = str(_BASE / "system_logs" / "feedback.json")
FEEDBACK_BACKUP_FILE = str(_BASE / "system_logs" / "feedback.backup.json")

# ── Drishti ──────────────────────────────────────────────────────────────────
# The relay channel map and data directory live in drishti_config so a seeding
# or migration script can read them without importing this module, which starts
# GStreamer and claims GPIO pins.
#
# This file is imported two ways: as basic_pipelines.Garuda_web by the tests,
# and as a plain script by scripts/run_garuda_web.sh. Relative imports only work
# in the first case, so fall back to absolute for the second.
try:
    from .drishti_config import CHANNEL_TO_PIN, RELAY_CHANNELS
    from .drishti_config import DATA_DIR as DRISHTI_DATA_DIR
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from basic_pipelines.drishti_config import CHANNEL_TO_PIN, RELAY_CHANNELS
    from basic_pipelines.drishti_config import DATA_DIR as DRISHTI_DATA_DIR

# Both hostnames reach this one app through the one Cloudflare tunnel, so the
# Host header is what decides which bundle / serves. Without it,
# drishti.veeramanikanta.in shows the Garuda dashboard.
# Named so a test harness can point it somewhere else before the lifespan
# runs. It writes live session tokens; it must never be the real file
# during a test run.
DRISHTI_SESSIONS_PATH = os.path.join(DRISHTI_DATA_DIR, "sessions.json")
DRISHTI_HOST = os.environ.get("DRISHTI_HOST", "drishti.veeramanikanta.in")
DRISHTI_DIST = _BASE / "drishti_dist"
# The standalone Drishti app is halted: its features now live in this app's
# own Home / Automations / Insights pages, behind Garuda's sign-in. Set
# DRISHTI_APP_ENABLED=1 to serve the Svelte bundle and /api/drishti again.
DRISHTI_APP_ENABLED = os.environ.get("DRISHTI_APP_ENABLED", "0").lower() in ("1", "true", "yes")

# Two products from one service. Garuda (home security) and Drishti (home
# automation, built on Garuda) share the camera, the Hailo and this process;
# the address decides which one a visitor gets. Hosts listed here get the
# security-only product: no home-automation pages, and /api/home is closed.
SECURITY_ONLY_HOSTS = frozenset(
    h.strip().lower() for h in
    os.environ.get("GARUDA_SECURITY_HOSTS", "garuda.veeramanikanta.in").split(",") if h.strip())


def _product_for_host(host):
    host = (host or "").split(":")[0].lower()
    return "security" if host in SECURITY_ONLY_HOSTS else "home"

# The base every part of the service stands on: typed settings, logging,
# supervised background loops, state backups. See garuda_core/__init__.py.
try:
    from .garuda_routes.master_keys import build_master_keys_router
    from .garuda_routes.users import build_users_router, AddUserRequest, DeleteUserRequest, UpdateUserRequest  # noqa: F401
    from .garuda_routes.config import build_config_router, ConfigUpdateRequest, CustomCommandRequest, DeleteCommandRequest  # noqa: F401
    from .garuda_routes.presence import build_presence_router, DeviceAddRequest, DeviceDeleteRequest  # noqa: F401
    from .garuda_routes.feedback import build_feedback_router, FeedbackRequest  # noqa: F401
    from .garuda_routes.events import build_events_router
    from .garuda_core import API_VERSION, BUILD
    from .garuda_core.settings import Settings
    from .garuda_core.workers import Supervisor
    from .garuda_core.backup import BackupManager
    from .garuda_core import http as _core_http
    from .garuda_core import logging_setup as _core_logging
    from .garuda_core import evidence as _evidence
    from .garuda_core import events as _events
    from .garuda_core import mailer as _mailer
    from .garuda_core.system_api import build_system_router
    from .garuda_core.security import (  # noqa: F401  (re-exported: tests and routes use them from here)
        _PBKDF2_ITERS, _hash_password, _verify_password, _DUMMY_PASSWORD_HASH, _validate_password_strength, _MK_PREFIX, _MK_ITERS, _mk_is_hashed, _mk_hash, _mk_check, _mk_mask, _master_key_matches, generate_otp_code, _rt_digest)
    from .garuda_core.storage import (  # noqa: F401  (re-exported: tests and routes use them from here)
        _atomic_json_write)
    from .garuda_core.validation import (  # noqa: F401  (re-exported: tests and routes use them from here)
        _time_in_range, _HHMM_RE, _clean_labels, _COLOR_RE)
except ImportError:
    from basic_pipelines.garuda_routes.master_keys import build_master_keys_router
    from basic_pipelines.garuda_routes.users import build_users_router, AddUserRequest, DeleteUserRequest, UpdateUserRequest  # noqa: F401
    from basic_pipelines.garuda_routes.config import build_config_router, ConfigUpdateRequest, CustomCommandRequest, DeleteCommandRequest  # noqa: F401
    from basic_pipelines.garuda_routes.presence import build_presence_router, DeviceAddRequest, DeviceDeleteRequest  # noqa: F401
    from basic_pipelines.garuda_routes.feedback import build_feedback_router, FeedbackRequest  # noqa: F401
    from basic_pipelines.garuda_routes.events import build_events_router
    from basic_pipelines.garuda_core import API_VERSION, BUILD
    from basic_pipelines.garuda_core.settings import Settings
    from basic_pipelines.garuda_core.workers import Supervisor
    from basic_pipelines.garuda_core.backup import BackupManager
    from basic_pipelines.garuda_core import http as _core_http
    from basic_pipelines.garuda_core import logging_setup as _core_logging
    from basic_pipelines.garuda_core import evidence as _evidence
    from basic_pipelines.garuda_core import events as _events
    from basic_pipelines.garuda_core import mailer as _mailer
    from basic_pipelines.garuda_core.system_api import build_system_router
    from basic_pipelines.garuda_core.security import (  # noqa: F401  (re-exported: tests and routes use them from here)
        _PBKDF2_ITERS, _hash_password, _verify_password, _DUMMY_PASSWORD_HASH, _validate_password_strength, _MK_PREFIX, _MK_ITERS, _mk_is_hashed, _mk_hash, _mk_check, _mk_mask, _master_key_matches, generate_otp_code, _rt_digest)
    from basic_pipelines.garuda_core.storage import (  # noqa: F401  (re-exported: tests and routes use them from here)
        _atomic_json_write)
    from basic_pipelines.garuda_core.validation import (  # noqa: F401  (re-exported: tests and routes use them from here)
        _time_in_range, _HHMM_RE, _clean_labels, _COLOR_RE)

import logging
SETTINGS = Settings.load()
SUPERVISOR = Supervisor()
BACKUPS = BackupManager(DRISHTI_DATA_DIR, keep=SETTINGS.backup_keep)
_syslog = logging.getLogger("garuda.system")

system_updates_log: List[str] = []
voice_assistant_log: List[str] = []
voice_responses: List[str] = []
_detection_log: List[str] = []   # in-memory recent detection events (danger + watch)
latest_detection_info = ""

ADMIN_OTP = None
_admin_otp_user: str | None = None   # server-side stored username for OTP step 2
_admin_otp_ts: float = 0             # epoch when admin OTP was generated
_admin_otp_attempts: int = 0         # failed verify attempts; cleared on success or expiry
_forgot_otp_store: dict = {}  # username → {otp, ts, attempts}  (per-user, no race condition)
USER_FORGOT_OTP: str | None = None   # test-facing alias: last generated forgot OTP string
# Flat test-facing aliases (conftest monkeypatches these directly)
_forgot_otp_user: str | None = None
_forgot_otp_ts: float = 0.0
_forgot_otp_attempts: int = 0

# Modes
MODE_DND = False
MODE_EMAIL_OFF = False
MODE_IDLE = False
MODE_NIGHT = False
MODE_EMERGENCY = False
MODE_PRIVACY = True

DETECTION_THRESHOLD = 0.3
CUSTOM_MODES = {}
NARADA_WAKE_WORD = "narada"
CUSTOM_VOICE_COMMANDS = {}

_alert_active = False
_alert_end_time     = 0.0   # epoch when current alert expires (3s visual banner)
_danger_trigger_info = ""   # detection text snapshot that fired the alert
_last_danger_conf    = 0.0  # confidence of last danger detection (for logging)
_app_start_time = time.time()
_detections_today = 0
_last_alert_time = None
_mode_lock = threading.Lock()
_alert_lock = threading.Lock()   # guards _alert_active/_alert_end_time/_danger_trigger_info

# ── Dead man's switch ────────────────────────────────────
_last_heartbeat = time.time()      # updated by GET /api/heartbeat
_DEADMAN_TIMEOUT = 180             # seconds without heartbeat before tamper alert
_deadman_alert_sent = False
_heartbeat_ever = False            # True only after a real heartbeat is received
# Opt-in: the dead-man switch is only meaningful when an external monitor
# (e.g. UptimeRobot) is hitting /api/heartbeat. Disabled by default so a
# deployment WITHOUT such a monitor does not spam "missed heartbeat" alerts.
_DEADMAN_ENABLED = os.environ.get("DEADMAN_ENABLED", "0") == "1"
_DEADMAN_REALERT_INTERVAL = 3600   # min seconds between repeat alerts (anti-spam)
_deadman_last_alert = 0.0

# ── Camera blindness detection ───────────────────────────
_blind_frame_count = 0
_blind_alert_sent = False
_last_tamper_email = 0.0            # last camera-tamper email time (anti-spam)
_TAMPER_EMAIL_COOLDOWN = 3600      # min seconds between camera-tamper emails
_class_counts_today = {}   # class_name → count since startup
_total_frames = 0          # total inference frames (for avg FPS)
_watch_last_logged: dict = {}   # label → last log timestamp (30s cooldown)
_perm_lock = threading.Lock()
# ── RAM-buffered log write globals (buffer defined here; functions in HELPERS) ─
_log_buffer: "defaultdict[str, list]" = defaultdict(list)
_log_buffer_lock = threading.Lock()

# ── False positive reduction ──────────────────────────────
_label_consec_frames: dict = {}   # label → consecutive frames seen above threshold

# ── Scheduled modes ───────────────────────────────────────
MODE_SCHEDULE: dict = {}   # {"night": {"start": "22:00", "end": "06:00"}, ...}

# ── Night presence window (yellow alarm when human seen in dead hours) ─────────
NIGHT_PRESENCE_WINDOW: dict = {"start": "01:30", "end": "05:00", "enabled": True}
_night_presence_alert_active = False
_night_presence_alert_end_time = 0.0
_np_lock = threading.Lock()

# ── Clip recording ────────────────────────────────────────
_clip_writer     = None
_clip_lock       = threading.Lock()
_clip_start_time = 0.0
_clip_path       = ""

# ── Phone presence detection ──────────────────────────────
KNOWN_DEVICES: list = []      # [{name, mac}] — loaded from config
_alert_history: dict = {}     # {ISO-date: alert_count} — persisted to disk
_presence_log: list  = []     # [{ts, event, device, mac}] — permanent presence record
MASTER_KEYS: list    = []   # loaded from MASTER_KEYS_FILE at startup
MASTER_KEY_OTP: str | None = None
_owner_present   = False
_owner_last_seen = 0.0
OWNER_AWAY_GRACE = 90         # seconds without seeing device before marking away (3 missed polls)
_last_arp_cache  = ""         # last raw ARP table read (refreshed by _presence_poller)

# ── Detection categories ──────────────────────────────────
WATCH_LABELS: list = ['Person', 'person']   # human — log silently, no alert

# ── Password hashing (PBKDF2-SHA256) ────────────────────

def _invalidate_user_sessions(username: str, except_token: str | None = None,
                              except_refresh: str | None = None) -> int:
    """Remove all active sessions for a user. Returns count removed.

    Refresh tokens go too: a password change that left them alive let anyone
    holding an old refresh cookie keep minting sessions for another week.
    """
    to_delete = [
        t for t, s in list(_sessions.items())
        if s.get("username") == username and t != except_token
    ]
    for t in to_delete:
        _sessions.pop(t, None)
    global _refresh_dirty
    keep_digest = _rt_digest(except_refresh) if except_refresh else None
    for t in [t for t, s in list(_refresh_tokens.items())
              if s.get("username") == username and t != except_refresh]:
        _refresh_tokens.pop(t, None)
        _refresh_dirty = True
    for d in [d for d, s in list(_persisted_refresh.items())
              if s.get("username") == username and d != keep_digest]:
        _persisted_refresh.pop(d, None)
        _refresh_dirty = True
    return len(to_delete)

# ── Atomic JSON write ────────────────────────────────────

def _safe_json_load(filepath: str, default):
    try:
        if os.path.exists(filepath):
            with open(filepath, encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        log_system_update(f"Failed to read {os.path.basename(filepath)}: {e}")
    return default

# ── Rate limiter (in-memory, per-IP) ────────────────────
_rate_store: dict = defaultdict(list)   # IP → [timestamps]
_RATE_LIMIT = 30     # max requests
_RATE_WINDOW = 60    # per N seconds
# A signed-in page is not an attacker: one page load fires several API calls
# and the home pages refresh on their own. Sharing the anonymous budget made
# "Too many requests" pop up during ordinary use.
_RATE_LIMIT_SESSION = 300

# ── Brute-force login lockout ────────────────────────────
_login_failures: dict = {}   # IP → {"count": int, "lockout_until": float}
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_LOCKOUT_SECONDS = 300  # 5 minutes

def _get_client_ip(request) -> str:
    """Return the real client IP, trusting proxy headers only from local proxies.

    Works for a Request or a WebSocket. Cloudflare sets CF-Connecting-IP itself
    and overwrites whatever the caller sent. X-Forwarded-For is appended to, so
    its first entry is whatever the caller typed: taking that one let anyone
    dodge the rate limit and the login lockout with a made-up header. The last
    entry is the one the proxy added.
    """
    client = request.client.host if request.client else "unknown"
    if client in ("127.0.0.1", "::1", "localhost"):
        cf = request.headers.get("CF-Connecting-IP", "").strip()
        if cf:
            return cf
        fwd = request.headers.get("X-Forwarded-For", "").split(",")[-1].strip()
        return fwd or client
    return client

def _check_rate_limit(request, bucket: str = "", limit: Optional[int] = None) -> bool:
    """Return True if request is within rate limit, False if exceeded."""
    limit = _RATE_LIMIT if limit is None else limit
    ip = _get_client_ip(request)
    now = time.time()
    stamps = _rate_store[f"{bucket}:{ip}" if bucket else ip]
    stamps[:] = [t for t in stamps if now - t < _RATE_WINDOW]
    if len(stamps) >= limit:
        return False
    stamps.append(now)
    return True

def _prune_rate_state():
    """Drop rate-limit and lockout records nobody is using any more.

    Both tables are keyed by client address and only ever grew: a scanner
    walking addresses left one entry behind for each, for the life of the
    process.
    """
    now = time.time()
    for key in list(_rate_store.keys()):
        stamps = _rate_store.get(key)
        if not stamps or now - max(stamps) > 3600:
            _rate_store.pop(key, None)
    for ip in list(_login_failures.keys()):
        entry = _login_failures.get(ip) or {}
        until = entry.get("lockout_until", 0.0)
        if (until and now >= until) or (not until and now - entry.get("last", now) > 3600):
            _login_failures.pop(ip, None)

def _is_login_locked(ip: str) -> bool:
    """Return True if the IP is currently locked out from login attempts."""
    entry = _login_failures.get(ip)
    if not entry:
        return False
    if entry["lockout_until"] == 0.0:
        # Failures accumulating but threshold not yet reached
        return False
    if time.time() < entry["lockout_until"]:
        return True
    # Lockout expired — clear it
    _login_failures.pop(ip, None)
    return False

def _record_login_failure(ip: str):
    """Increment failure count; trigger lockout after _LOGIN_MAX_ATTEMPTS."""
    entry = _login_failures.setdefault(ip, {"count": 0, "lockout_until": 0.0})
    entry["count"] += 1
    entry["last"] = time.time()
    if entry["count"] >= _LOGIN_MAX_ATTEMPTS:
        entry["lockout_until"] = time.time() + _LOGIN_LOCKOUT_SECONDS
        log_system_update(
            f"[SECURITY] Login lockout: {ip} after {entry['count']} failed attempts "
            f"({_LOGIN_LOCKOUT_SECONDS}s cooldown)."
        )

def _clear_login_failure(ip: str):
    """Clear failure record after a successful login."""
    _login_failures.pop(ip, None)


USERS: dict = {}  # populated from users.json at startup; no hardcoded defaults

try:
    from .garuda_auto.frame_publisher import FramePublisher
except ImportError:
    from basic_pipelines.garuda_auto.frame_publisher import FramePublisher

# MJPEG / WebRTC frame buffer
_frame_buffer = None
_frame_raw    = None       # raw numpy BGR for WebRTC track
# Pipeline rate in, browser rate out. The clip writer shares this gate, because
# its VideoWriter is built at a fixed 15fps and writing faster is what made
# saved clips play back in slow motion.
_frame_publisher = FramePublisher()
_frame_lock   = threading.Lock()
_frame_seq    = 0          # incremented every new frame; lets MJPEG clients skip duplicates
_frame_ts     = 0.0        # wall clock of the last frame; the only liveness signal we have

# Drishti rebuilds its descriptor at this rate, not at frame rate.
_DRISHTI_OBSERVE_INTERVAL_S = 0.2
_drishti_last_observe = 0.0

# ── Async secondary cascade (MobileNet + MiDaS) ─────────────────────────────
# Non-blocking queue bridges primary YOLO callback to secondary daemon thread.
# maxsize=2: if secondary falls behind, new frames are intentionally dropped.
# Primary GStreamer callback NEVER blocks on secondary inference.
import queue as _queue_mod
SECONDARY_QUEUE_SIZE = 2
_secondary_queue: _queue_mod.Queue = _queue_mod.Queue(maxsize=SECONDARY_QUEUE_SIZE)
_secondary_stop  = threading.Event()

class _WebCascadeMetrics:
    """Thread-safe counters for the web server's async cascade path."""
    def __init__(self):
        self._lock               = threading.Lock()
        self.primary_frames      = 0
        self.secondary_enqueued  = 0
        self.secondary_dropped   = 0
        self.secondary_completed = 0

    def record_primary(self):
        with self._lock:
            self.primary_frames += 1

    def record_secondary_enqueue(self):
        with self._lock:
            self.secondary_enqueued += 1

    def record_secondary_drop(self):
        with self._lock:
            self.secondary_dropped += 1

    def record_secondary_complete(self):
        with self._lock:
            self.secondary_completed += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "primary_frames":      self.primary_frames,
                "secondary_enqueued":  self.secondary_enqueued,
                "secondary_dropped":   self.secondary_dropped,
                "secondary_completed": self.secondary_completed,
            }

_cascade_metrics = _WebCascadeMetrics()


def _secondary_worker_loop():
    """
    Daemon thread: consumes person-detection frames from _secondary_queue,
    runs MobileNet classification + MiDaS depth analysis (when available).
    Decoupled from the primary GStreamer YOLO pipeline — primary never waits.

    VDevice note: on Garuda_web the primary YOLO runs inside the GStreamer
    hailonet element (owns the Hailo device). Secondary models would need
    their own VDevice session or the hailonet would need to release the device.
    In practice, secondary inference is stubbed here until the cascade HEFs
    are loaded alongside the GStreamer pipeline. The architecture (queue,
    daemon thread, drop semantics) is fully production-ready.
    """
    import logging
    _log = logging.getLogger("garuda_web.secondary")
    _log.info("Secondary worker thread started (daemon).")
    while not _secondary_stop.is_set():
        try:
            frame, det_info = _secondary_queue.get(timeout=0.3)
        except _queue_mod.Empty:
            continue

        try:
            # --- Secondary inference placeholder ---
            # When cascade HEFs are loaded alongside the GStreamer pipeline,
            # MobileNet + MiDaS inference runs here on the duplicated frame.
            # For now: log the event, record completion metric.
            label = det_info.get("label", "person")
            conf  = det_info.get("confidence", 0.0)
            _log.debug(f"Secondary analysis: {label} conf={conf:.2f}")
        except Exception as e:
            _log.warning(f"Secondary worker error: {e}")
        finally:
            _cascade_metrics.record_secondary_complete()

    _log.info("Secondary worker thread stopped.")

# Start secondary daemon thread at module load
_secondary_thread = threading.Thread(
    target=_secondary_worker_loop,
    daemon=True,
    name="secondary_cascade",
)
_secondary_thread.start()

# WebRTC peer connections
_pc_set: set = set()
_MAX_PEER_CONNECTIONS = 4

# Event-driven WS broadcaster
_event_loop  = None        # asyncio loop ref (set in lifespan)
_ws_trigger  = None        # asyncio.Event — set to push WS immediately
_ws_broadcaster_task = None

# Session store: token → {username, role, expires}
_sessions = {}

# WebSocket clients (all connected devices): socket -> {username, role}
_ws_clients: dict = {}

# EMA-smoothed system stats (α=0.25 → ~4-tick rolling average)
_cpu_ema       = 0.0
_ram_ema       = 0.0
_temp_ema      = 0.0
_cpu_cores_ema: list = []   # per-core EMA values (populated on first psutil call)
_EMA_A         = 0.25

# Voice stop event
_voice_stop_event = threading.Event()

##############################################################################
# PERSISTENCE
##############################################################################
def load_users():
    global USERS
    # The legacy file holds plaintext passwords from before hashing. It is read
    # only on a machine that has never had a users.json; a users.json that is
    # there but unreadable must not quietly bring those old passwords back.
    candidates = [USERS_FILE] if os.path.exists(USERS_FILE) else [USERS_FILE, "system_logs/users_data.json"]
    for path in candidates:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    data = json.load(f)
                if isinstance(data, dict) and data:
                    default_colors = ["#1565c0","#2e7d32","#6a1b9a","#00838f",
                                      "#f57f17","#4527a0","#ad1457"]
                    idx = 0
                    for uname, udata in data.items():
                        if "display_name" not in udata:
                            udata["display_name"] = uname.capitalize()
                        if "box_color" not in udata:
                            udata["box_color"] = "#e65100" if udata.get("role") == "admin" \
                                else default_colors[idx % len(default_colors)]
                            idx += 1
                        if "history" not in udata:
                            udata["history"] = {"logins": [], "narada_activity": []}
                    USERS = data
                    return
            except Exception as e:
                print(f"Warning: failed to load users from {path}: {e}")

def save_users():
    try:
        _atomic_json_write(USERS_FILE, USERS)
    except Exception as e:
        log_system_update(f"Failed to save users: {e}")

def load_config():
    global CUSTOM_VOICE_COMMANDS, CUSTOM_MODES, EMAIL_RECIPIENTS
    global EMAIL_COOLDOWN, EMAIL_SENDER, DETECTION_THRESHOLD
    global KNOWN_DEVICES, WATCH_LABELS, DANGER_LABELS
    global MODE_DND, MODE_EMAIL_OFF, MODE_IDLE, MODE_NIGHT, MODE_EMERGENCY, MODE_PRIVACY
    global NIGHT_PRESENCE_WINDOW
    # NOTE: EMAIL_SENDER_PASS is NOT loaded from config.json —
    # it lives exclusively in .env / environment variables for security.
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            CUSTOM_VOICE_COMMANDS = cfg.get("custom_voice_commands", CUSTOM_VOICE_COMMANDS)
            CUSTOM_MODES = cfg.get("custom_modes", CUSTOM_MODES)
            EMAIL_RECIPIENTS = cfg.get("email_recipients", EMAIL_RECIPIENTS)
            EMAIL_COOLDOWN = cfg.get("email_cooldown", EMAIL_COOLDOWN)
            EMAIL_SENDER = cfg.get("email_sender", EMAIL_SENDER)
            DETECTION_THRESHOLD = cfg.get("detection_threshold", DETECTION_THRESHOLD)
            KNOWN_DEVICES = cfg.get("known_devices", KNOWN_DEVICES)
            WATCH_LABELS = cfg.get("watch_labels", WATCH_LABELS)
            # Support both legacy "danger_label" (str) and new "danger_labels" (list)
            if "danger_labels" in cfg:
                DANGER_LABELS = cfg["danger_labels"]
            elif "danger_label" in cfg:
                DANGER_LABELS = [cfg["danger_label"]]
            NIGHT_PRESENCE_WINDOW = cfg.get("night_presence_window", NIGHT_PRESENCE_WINDOW)
            # Restore persisted mode states
            modes = cfg.get("modes", {})
            MODE_DND       = bool(modes.get("dnd",       MODE_DND))
            MODE_EMAIL_OFF = bool(modes.get("email_off", MODE_EMAIL_OFF))
            MODE_IDLE      = bool(modes.get("idle",      MODE_IDLE))
            MODE_NIGHT     = bool(modes.get("night",     MODE_NIGHT))
            MODE_EMERGENCY = bool(modes.get("emergency", MODE_EMERGENCY))
            MODE_PRIVACY   = bool(modes.get("privacy",   MODE_PRIVACY))
            global MODE_SCHEDULE
            MODE_SCHEDULE  = cfg.get("mode_schedule", MODE_SCHEDULE)
        except Exception as e:
            print(f"Warning: failed to load config: {e}")

def _load_alert_history():
    """Load alert-activity history from disk into _alert_history."""
    global _alert_history
    try:
        if os.path.exists(ALERT_HISTORY_FILE):
            with open(ALERT_HISTORY_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                _alert_history = data
            elif isinstance(data, list):
                # Legacy list format — migrate to {date: count} by counting entries per day
                migrated: dict = {}
                for entry in data:
                    if isinstance(entry, dict) and "timestamp" in entry:
                        day = entry["timestamp"][:10]
                        migrated[day] = migrated.get(day, 0) + 1
                _alert_history = migrated
                _atomic_json_write(ALERT_HISTORY_FILE, _alert_history)
            else:
                _alert_history = {}
    except Exception:
        _alert_history = {}

def _record_alert_activity():
    """Increment today's alert count and persist to disk."""
    global _alert_history
    today = datetime.date.today().isoformat()
    _alert_history[today] = _alert_history.get(today, 0) + 1
    try:
        _atomic_json_write(ALERT_HISTORY_FILE, _alert_history)
    except Exception:
        pass

_PRESENCE_LOG_MAX = 5000
_USER_HISTORY_MAX = 200

def _remember_user_activity(user_name, kind, entry):
    """Append to a user's history list, keeping only the recent entries."""
    user = USERS.get(user_name) if user_name else None
    if not isinstance(user, dict):
        return
    items = user.setdefault("history", {}).setdefault(kind, [])
    items.append(entry)
    if len(items) > _USER_HISTORY_MAX:
        del items[:-_USER_HISTORY_MAX]

def _load_presence_log():
    global _presence_log
    try:
        if os.path.exists(PRESENCE_LOG_FILE):
            with open(PRESENCE_LOG_FILE) as f:
                data = json.load(f)
            _presence_log = data[-_PRESENCE_LOG_MAX:] if isinstance(data, list) else []
    except Exception:
        _presence_log = []

def _append_presence_log(event: str, device: str, mac: str):
    """Append one presence event, persist to disk, and queue for sync."""
    global _presence_log
    _presence_log.append({
        "ts":     datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "event":  event,
        "device": device,
        "mac":    mac,
    })
    # The whole list is rewritten on every event; without a cap that write
    # (and the file) grew for ever.
    if len(_presence_log) > _PRESENCE_LOG_MAX:
        _presence_log[:] = _presence_log[-_PRESENCE_LOG_MAX:]
    try:
        _atomic_json_write(PRESENCE_LOG_FILE, _presence_log)
    except Exception:
        pass
    queue_event("PRESENCE", device, 0.0, f"{event} (mac={mac})")

def load_master_keys():
    global MASTER_KEYS
    try:
        if os.path.exists(MASTER_KEYS_FILE):
            with open(MASTER_KEYS_FILE) as f:
                data = json.load(f)
            if isinstance(data.get("keys"), list) and data["keys"]:
                keys = [k for k in data["keys"] if isinstance(k, str) and k]
                if any(not _mk_is_hashed(k) for k in keys):
                    # One-time migration of a file written before hashing.
                    keys = [k if _mk_is_hashed(k) else _mk_hash(k) for k in keys]
                    MASTER_KEYS[:] = keys
                    save_master_keys()
                else:
                    MASTER_KEYS[:] = keys
                return
    except Exception:
        pass
    # If no key file, seed from MASTER_KEY env var (set in .env)
    bootstrap = os.environ.get("MASTER_KEY", "").strip()
    if bootstrap:
        MASTER_KEYS[:] = [_mk_hash(bootstrap)]
        save_master_keys()  # persist to file for future runs

def save_master_keys():
    try:
        # Never write a key as typed, whatever put it in the list.
        MASTER_KEYS[:] = [k if _mk_is_hashed(k) else _mk_hash(k) for k in MASTER_KEYS]
        _atomic_json_write(MASTER_KEYS_FILE, {"keys": MASTER_KEYS})
        try:
            os.chmod(MASTER_KEYS_FILE, 0o600)
        except OSError:
            pass
    except Exception as exc:
        log_system_update(f"Failed to save master keys: {type(exc).__name__}")

async def _async_save_config():
    """Run save_config in a thread so it never blocks the async event loop (fsync is slow on RPi SD)."""
    await asyncio.to_thread(save_config)

def save_config():
    # NOTE: EMAIL_SENDER_PASS is intentionally excluded —
    # credentials must not be stored in plaintext JSON on disk.
    try:
        cfg = {
            "custom_voice_commands": CUSTOM_VOICE_COMMANDS,
            "custom_modes": CUSTOM_MODES,
            "email_recipients": EMAIL_RECIPIENTS,
            "email_cooldown": EMAIL_COOLDOWN,
            "email_sender": EMAIL_SENDER,
            "detection_threshold": DETECTION_THRESHOLD,
            "known_devices": KNOWN_DEVICES,
            "watch_labels": WATCH_LABELS,
            "danger_labels": DANGER_LABELS,
            "night_presence_window": NIGHT_PRESENCE_WINDOW,
            "modes": {
                "dnd":       MODE_DND,
                "email_off": MODE_EMAIL_OFF,
                "idle":      MODE_IDLE,
                "night":     MODE_NIGHT,
                "emergency": MODE_EMERGENCY,
                "privacy":   MODE_PRIVACY,
            },
            "mode_schedule": MODE_SCHEDULE,
        }
        _atomic_json_write(CONFIG_FILE, cfg)
    except Exception as e:
        log_system_update(f"Failed to save config: {e}")

def _load_logs_from_disk():
    """Populate in-memory log lists from permanent files on startup (last 500 lines each)."""
    global system_updates_log, voice_assistant_log, _detection_log
    def _tail(path, n=500):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            return [l.rstrip("\n") for l in lines[-n:]]
        except FileNotFoundError:
            return []
        except Exception:
            return []
    system_updates_log[:] = _tail(PERM_SYSTEM_LOG)
    voice_assistant_log[:] = _tail(PERM_VOICE_LOG)
    _detection_log[:] = _tail(PERM_DETECTION_LOG)

load_users()
load_config()
load_master_keys()
_load_logs_from_disk()

##############################################################################
# HELPERS
##############################################################################
# ── RAM-buffered log writes ───────────────────────────────────────────────────
# All text log writes (detection, system, voice, scissors, night-mode) are
# accumulated in-memory and flushed to disk every _LOG_FLUSH_INTERVAL seconds.
# This eliminates per-event fsync calls — the biggest source of SD card wear.
# Critical state (users, config, alert history) still uses _atomic_json_write.
# _log_buffer and _log_buffer_lock are declared in the GLOBALS section (above
# load_users() so startup log calls work correctly).
_LOG_FLUSH_INTERVAL = 60    # flush every 60 seconds
_LOG_MAX_SIZE_BYTES = 10 * 1024 * 1024   # rotate at 10 MB

def _rotate_log(filepath: str):
    """Rename filepath → filepath.1, discarding any previous .1 file."""
    try:
        rotated = filepath + ".1"
        if os.path.exists(rotated):
            os.unlink(rotated)
        os.rename(filepath, rotated)
    except Exception:
        pass

def _do_flush_logs():
    """Write all buffered log lines to disk. Called by the flush thread and on shutdown."""
    with _log_buffer_lock:
        snapshots = {path: buf[:] for path, buf in _log_buffer.items() if buf}
        for path in snapshots:
            _log_buffer[path].clear()
    for path, lines in snapshots.items():
        if not lines:
            continue
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            # Rotate if over size cap
            try:
                if os.path.getsize(path) > _LOG_MAX_SIZE_BYTES:
                    _rotate_log(path)
            except FileNotFoundError:
                pass
            with open(path, "a", encoding="utf-8") as f:
                for line in lines:
                    f.write(line + "\n")
        except Exception:
            pass

def _flush_log_thread():
    """Background daemon thread: flush log buffer on interval."""
    while True:
        time.sleep(_LOG_FLUSH_INTERVAL)
        _do_flush_logs()

def _perm_write(filepath: str, line: str):
    """Buffer a log line in RAM; flushed to disk every _LOG_FLUSH_INTERVAL seconds."""
    with _log_buffer_lock:
        _log_buffer[filepath].append(line)

def _append_detection_perm(event_type: str, label: str, confidence: float, info: str = ""):
    """Append one detection event to in-memory list, permanent file, and SQLite queue."""
    global _detection_log
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] [{event_type.upper()}] {label} conf={confidence:.2f}"
    if info:
        line += f" — {info}"
    _detection_log.append(line)
    if len(_detection_log) > 500:
        _detection_log[:] = _detection_log[-500:]
    _perm_write(PERM_DETECTION_LOG, line)
    queue_event(event_type.upper(), label, confidence, info)

def log_system_update(message):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {message}"
    system_updates_log.append(entry)
    if len(system_updates_log) > 500:
        system_updates_log[:] = system_updates_log[-500:]
    _perm_write(PERM_SYSTEM_LOG, entry)
    # Also into the one service log (garuda.log), next to the request and
    # worker lines, so there is a single file to read when something is wrong.
    _syslog.info("%s", message)

def append_voice_log(message, user_name=None):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {message}"
    voice_assistant_log.append(entry)
    if len(voice_assistant_log) > 500:
        voice_assistant_log[:] = voice_assistant_log[-500:]
    _perm_write(PERM_VOICE_LOG, entry)
    _remember_user_activity(user_name, "narada_activity", entry)

def append_voice_response(message, user_name=None):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {message}"
    voice_responses.append(entry)
    if len(voice_responses) > 500:
        voice_responses[:] = voice_responses[-500:]
    _perm_write(PERM_VOICE_LOG, "→ " + entry)
    _remember_user_activity(user_name, "narada_activity", entry)

##############################################################################
# OFFLINE EVENT QUEUE (SQLite)
##############################################################################
# Anchored to the repository, not to whatever directory the process happened
# to be started from (same file the service has always used from its unit's
# WorkingDirectory).
EVENTS_DB = str(_BASE.parent / "system_logs" / "garuda_events.db")
_EVENTS_KEEP_DAYS = _events.KEEP_DAYS
# The same objects the queue uses, still reachable under their old names.
_pending_cache = _events._pending_cache
_eq_lock = _events._lock
_net_online = True          # tracked by connectivity monitor

# The queue itself lives in garuda_core/events.py. These keep the names the
# rest of this module, the camera callback and the tests call, and pass the
# database path at call time (EVENTS_DB is repointed by the test suite).
_EVENT_DB_MIGRATIONS = _events.MIGRATIONS

def _init_event_db():
    """Create events table if not exists."""
    _events.init_db(EVENTS_DB)

def queue_event(event_type: str, label: str = "", confidence: float = 0.0, info: str = ""):
    """Insert an event into the SQLite queue. Thread-safe."""
    _events.insert(EVENTS_DB, event_type, label, confidence, info, on_error=log_system_update)

def get_events_since(since_ts: str = "", limit: int = 500) -> list:
    """Return events after the given ISO timestamp, oldest-first."""
    return _events.since(EVENTS_DB, since_ts, limit)

def get_unsynced_events(limit: int = 1000) -> list:
    """Unsynced events, oldest first."""
    return _events.unsynced(EVENTS_DB, limit)

def prune_synced_events(days: int = _EVENTS_KEEP_DAYS) -> int:
    """Delete synced events older than `days`; the table had no upper bound."""
    return _events.prune_synced(EVENTS_DB, days)

def get_pending_count(max_age: float = 0.0) -> int:
    """Return count of unsynced events (see garuda_core.events.pending_count)."""
    return _events.pending_count(EVENTS_DB, max_age)

def mark_events_synced(up_to_id: int):
    """Mark all events up to and including the given ID as synced."""
    _events.mark_synced(EVENTS_DB, up_to_id)

def _check_connectivity() -> bool:
    """Quick connectivity check — try to resolve DNS."""
    import socket
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=3)
        return True
    except OSError:
        return False

def _connectivity_monitor():
    """Background thread: monitor internet connectivity, log transitions."""
    global _net_online
    was_online = True
    while True:
        time.sleep(30)
        online = _check_connectivity()
        if online and not was_online:
            # Just came back online
            _net_online = True
            pending = get_pending_count()
            log_system_update(f"[NETWORK] Internet restored — {pending} queued events ready to sync")
            push_urgent_ws()
        elif not online and was_online:
            # Just went offline
            _net_online = False
            log_system_update("[NETWORK] Internet connection lost — events will be queued locally")
            push_urgent_ws()
        was_online = online

def stop_app():
    log_system_update("Stopping Garuda Web app.")
    _do_flush_logs()   # write buffered logs before exit
    if app_gst is not None:
        try:
            app_gst.pipeline.set_state(Gst.State.NULL)
        except Exception:
            pass
    # This runs on a worker thread, where sys.exit() only ended that thread:
    # the camera stopped but the server stayed up, half alive. SIGTERM lets
    # uvicorn shut down in order (the lifespan flushes logs); the unit is
    # Restart=on-failure, so a clean exit stays stopped.
    os.kill(os.getpid(), signal.SIGTERM)

##############################################################################
# OTP / EMAIL
##############################################################################

def _send_mail(subject, body, to=None):
    """One email from the configured sender; to the alert recipients unless
    `to` names someone else. Raises on failure (see garuda_core.mailer)."""
    _mailer.send(subject, body, sender=EMAIL_SENDER, password=EMAIL_SENDER_PASS,
                 to=EMAIL_RECIPIENTS if to is None else to)

def send_otp_via_email(email, otp_code):
    body = f"Hello,\n\nYour OTP code is: {otp_code}\n\nUse this to complete your login."
    try:
        _send_mail("Your Garuda OTP Code", body, to=email)
        log_system_update(f"OTP email sent to {email}")
        return True, None
    except smtplib.SMTPAuthenticationError:
        err = "SMTP auth failed. Check EMAIL_SENDER_PASS (must be a Gmail App Password)."
        log_system_update(err)
        return False, err
    except Exception as e:
        err = str(e)
        log_system_update(f"Email error: {err}")
        return False, err

##############################################################################
# WEBRTC VIDEO TRACK
##############################################################################
if _WEBRTC_AVAILABLE:
    class GarudaVideoTrack(VideoStreamTrack):
        """Serves the latest BGR frame from the Hailo pipeline as an H.264 track."""
        kind = "video"

        async def recv(self):
            pts, time_base = await self.next_timestamp()
            with _frame_lock:
                raw = _frame_raw
            if raw is not None:
                vf = av.VideoFrame.from_ndarray(raw, format="bgr24")
            else:
                vf = av.VideoFrame(width=1280, height=720, format="yuv420p")
            vf.pts = pts
            vf.time_base = time_base
            return vf

##############################################################################
# EVENT-DRIVEN WS HELPER
##############################################################################
def push_urgent_ws():
    """Signal the WS broadcaster to push state immediately (cross-thread safe)."""
    if _event_loop and _ws_trigger:
        _event_loop.call_soon_threadsafe(_ws_trigger.set)

##############################################################################
# PHONE PRESENCE DETECTION
##############################################################################
def _get_local_subnet() -> str:
    """Return the first local subnet (e.g. '192.168.1.0/24') from ip route."""
    try:
        out = subprocess.check_output(['ip', 'route'], text=True, timeout=3)
        for line in out.splitlines():
            parts = line.split()
            # Lines like: "192.168.1.0/24 dev wlan0 ..."
            if parts and '/' in parts[0] and parts[0][0].isdigit():
                return parts[0]
    except Exception:
        pass
    return ''

def _probe_subnet_for_arp(subnet: str):
    """Send a UDP datagram to every host in subnet to force ARP table population.

    The packets are sent to port 9 (discard service) so remote hosts ignore them,
    but the kernel must resolve each MAC via ARP before sending — populating the
    local ARP cache so /proc/net/arp reflects every reachable device.
    """
    try:
        net = ipaddress.IPv4Network(subnet, strict=False)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        for host in net.hosts():
            try:
                sock.sendto(b'\x00', (str(host), 9))
            except Exception:
                pass
        sock.close()
    except Exception:
        pass

def _device_mac(device) -> str:
    return str((device or {}).get("mac") or "").strip().lower()

def _mac_online(mac: str) -> bool:
    """True when `mac` is in the last ARP read as a complete (0x2) entry.

    A substring test over the raw table also matched stale and incomplete
    rows, and an empty MAC matched everything, which read as "owner is home".
    """
    if not mac:
        return False
    for line in _last_arp_cache.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] == "0x2" and parts[3] == mac:
            return True
    return False

def _present_device():
    """The first registered device seen on the network, or None."""
    return next((d for d in KNOWN_DEVICES if _mac_online(_device_mac(d))), None)

def _check_device_presence() -> bool:
    """Return True if any registered device MAC appears in the kernel ARP table."""
    global _last_arp_cache
    try:
        with open('/proc/net/arp') as f:
            _last_arp_cache = f.read().lower()
        return _present_device() is not None
    except Exception:
        return False

def _presence_poller():
    """Background thread: poll ARP table every 30s to detect owner's phone.

    Before reading /proc/net/arp we send UDP probes to every host in the local
    subnet.  This forces ARP resolution so the table contains all active devices,
    not just those that have recently communicated with the Pi directly.
    """
    global _owner_present, _owner_last_seen
    _subnet = ''
    first = True
    while True:
        if not first:
            time.sleep(30)
        first = False
        if not KNOWN_DEVICES:
            continue
        # One bad cycle (a malformed device entry, a failed probe) must not end
        # the thread: presence would then stay frozen until the next restart.
        try:
            # Discover subnet once (lazy) and reprobe each cycle
            if not _subnet:
                _subnet = _get_local_subnet()
            if _subnet:
                _probe_subnet_for_arp(_subnet)
                time.sleep(2)   # allow ARP responses to arrive
            found = _check_device_presence()
            log_system_update(
                f"[PRESENCE] {'Match' if found else 'No match'} — "
                f"{len([l for l in _last_arp_cache.splitlines() if '0x2' in l])} active ARP entries"
            )
            if found:
                _owner_last_seen = time.time()
                if not _owner_present:
                    _owner_present = True
                    seen = _present_device() or {}
                    dev, mac = seen.get("name", "Unknown"), _device_mac(seen)
                    _append_presence_log("arrived", dev, mac)
                    log_system_update(f"[OWNER] {dev} arrived — device detected on network.")
                    push_urgent_ws()
            elif _owner_present and (time.time() - _owner_last_seen > OWNER_AWAY_GRACE):
                _owner_present = False
                dev = next((d.get("name", "Unknown") for d in KNOWN_DEVICES), "Unknown")
                _append_presence_log("left", dev, "")
                log_system_update(f"[OWNER] {dev} away — device not seen for {OWNER_AWAY_GRACE}s.")
                push_urgent_ws()
        except Exception as exc:
            log_system_update(f"[PRESENCE] poll failed: {type(exc).__name__}: {exc}")

##############################################################################
# ALERTS
##############################################################################
def trigger_software_alert():
    global _alert_active, _last_alert_time, _alert_end_time
    with _mode_lock:
        dnd = MODE_DND
        idle = MODE_IDLE
        night = MODE_NIGHT
    if dnd or idle:
        return
    with _alert_lock:
        was_active = _alert_active
        # Extend the 3s window every frame scissors is visible — alert stays on
        # while scissors is in frame and expires 3s after it disappears.
        _alert_active = True
        _alert_end_time = time.time() + 3
    if not was_active:
        # New alert starting: log, record, sound, email
        if night:
            _perm_write(NIGHT_MODE_LOG_FILE,
                        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        _last_alert_time = datetime.datetime.now()
        _record_alert_activity()
        log_system_update("Alert triggered.")
        push_urgent_ws()
        try:
            subprocess.Popen(["aplay", "/usr/share/sounds/alsa/Front_Center.wav"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

def send_email_alert():
    global last_email_sent_time
    with _mode_lock:
        email_off = MODE_EMAIL_OFF
        idle = MODE_IDLE
        emergency = MODE_EMERGENCY
        night = MODE_NIGHT
    if email_off or idle:
        return
    with _email_lock:
        current_time = time.time()
        if (current_time - last_email_sent_time) < EMAIL_COOLDOWN:
            return
        last_email_sent_time = current_time
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    label_str = ", ".join(DANGER_LABELS)
    subject = f"Danger Object Detected — {label_str}"
    if emergency:
        subject = "EMERGENCY: " + subject
    elif night:
        subject = "HIGH PRIORITY: " + subject
    body = f"Danger object detected at {now_str}.\nObject(s): {label_str}\nCheck your environment for safety.\n"
    try:
        _send_mail(subject, body)
        log_system_update("Email alert sent.")
    except Exception as e:
        log_system_update(f"Failed sending email alert: {e}")

def log_scissors_detection(label: str = "danger"):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _perm_write(SCISSORS_LOG_FILE, f"[{stamp}] DANGER DETECTED: {label.upper()}")

def _send_tamper_email():
    """Maximum-priority tamper alert — bypasses DND/idle/email-off modes.

    Rate-limited to one email per _TAMPER_EMAIL_COOLDOWN so a flickering /
    intermittently-dark camera cannot spam the recipient.
    """
    global _last_tamper_email
    if not EMAIL_SENDER or not EMAIL_RECIPIENTS:
        return
    now = time.time()
    if (now - _last_tamper_email) < _TAMPER_EMAIL_COOLDOWN:
        return
    _last_tamper_email = now
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    body = (
        f"CRITICAL: Camera tamper detected at {now_str}.\n"
        "The camera lens appears to be covered or the feed has gone blank.\n"
        "Immediate physical inspection required."
    )
    try:
        _send_mail("CRITICAL TAMPER ALERT — Garuda Camera Covered", body)
        log_system_update("[TAMPER] Alert email sent.")
    except Exception as e:
        log_system_update(f"[TAMPER] Email failed: {e}")

##############################################################################
# ENCRYPTED EVIDENCE EXFILTRATION
##############################################################################
def _encrypt_clip_aes256(src_path: str) -> str | None:
    """AES-256-GCM encrypt src_path → src_path.enc. Returns encrypted path or None on failure."""
    # The work is in garuda_core.evidence; the key and the logger are read
    # here, at call time, so a changed (or test-patched) value is honoured.
    return _evidence.encrypt_file(src_path, _EXFIL_AES_KEY, log_system_update)

def _ssh_upload(local_path: str, remote_filename: str) -> bool:
    """SFTP-upload local_path to the configured SSH server. Returns True on success."""
    return _evidence.sftp_upload(
        local_path, remote_filename, host=_EXFIL_HOST, port=_EXFIL_PORT, user=_EXFIL_USER,
        key_path=_EXFIL_KEY_PATH, password=_EXFIL_PASSWORD, remote_dir=_EXFIL_REMOTE,
        log=log_system_update)

def exfiltrate_clip(clip_path: str):
    """Encrypt a clip and upload the ciphertext off-device via SSH. Runs in a daemon thread."""
    if not _EXFIL_HOST or _EXFIL_AES_KEY is None:
        return   # exfiltration not configured
    enc_path = _encrypt_clip_aes256(clip_path)
    if not enc_path:
        return
    log_system_update(f"[EXFIL] Encrypted → {os.path.basename(enc_path)}")
    if _ssh_upload(enc_path, os.path.basename(enc_path)):
        log_system_update(f"[EXFIL] Uploaded to {_EXFIL_HOST}:{_EXFIL_REMOTE}")
        # Remove plaintext clip — only ciphertext kept locally (briefly)
        try:
            os.unlink(clip_path)
        except Exception:
            pass
    else:
        log_system_update(f"[EXFIL] Upload failed — encrypted clip retained locally: {enc_path}")

##############################################################################
# GSTREAMER CALLBACK
##############################################################################
class user_app_callback_class(app_callback_class):
    def __init__(self):
        super().__init__()
        self.person_detected = False
        self.danger_labels = list(DANGER_LABELS)
        # Override with a threading-safe lock-based store
        # (base class uses multiprocessing.Queue which breaks across threads)
        self._frame = None
        self._flock = threading.Lock()

    def set_frame(self, frame):
        with self._flock:
            self._frame = frame

    def get_frame(self):
        with self._flock:
            return self._frame


try:
    import zoneinfo as _zoneinfo
    _IST = _zoneinfo.ZoneInfo("Asia/Kolkata")
except Exception:
    _IST = None   # no timezone data: fall back to the system clock

_np_last_check = 0.0


def _check_night_presence():
    """Activate yellow night-presence alarm if a person is detected in the configured window (IST)."""
    global _night_presence_alert_active, _night_presence_alert_end_time
    with _np_lock:
        win = dict(NIGHT_PRESENCE_WINDOW)   # snapshot — avoids race with config update
    if not win.get("enabled", True):
        return
    now_ist = datetime.datetime.now(_IST) if _IST is not None else datetime.datetime.now()
    now_hm = now_ist.strftime("%H:%M")
    start, end = win.get("start", "01:30"), win.get("end", "05:00")
    # Handle window that wraps midnight (e.g. 23:00 → 05:00)
    if start <= end:
        in_window = start <= now_hm < end
    else:
        in_window = now_hm >= start or now_hm < end
    if in_window:
        with _np_lock:
            _night_presence_alert_active = True
            _night_presence_alert_end_time = time.time() + 10


def app_callback(pad, info, user_data):
    global latest_detection_info, DETECTION_THRESHOLD, MODE_PRIVACY
    global _detections_today, _frame_buffer, _total_frames, _class_counts_today
    global _last_danger_conf, _label_consec_frames
    global _clip_writer, _clip_start_time   # assigned (set to None) on auto-stop
    buffer = info.get_buffer()
    if buffer is None:
        return Gst.PadProbeReturn.OK

    user_data.increment()
    _total_frames += 1
    _cascade_metrics.record_primary()
    frame_num = user_data.get_count()
    text_info = f"Frame: {frame_num}\n"
    format_, width, height = get_caps_from_pad(pad)

    if user_data.use_frame and format_ and width and height:
        frame = get_numpy_from_buffer(buffer, format_, width, height)
    else:
        frame = None

    # Camera blindness detection — flag if camera is covered/blocked
    global _blind_frame_count, _blind_alert_sent
    if frame is not None:
        # Every 4th pixel each way: a covered lens is uniform at any scale, and
        # this runs on every frame of a 60 fps pipeline.
        gray = cv2.cvtColor(np.ascontiguousarray(frame[::4, ::4]), cv2.COLOR_RGB2GRAY)
        variance = float(np.var(gray))
        if variance < 50:   # nearly uniform → blocked/covered
            _blind_frame_count += 1
            if _blind_frame_count >= 300 and not _blind_alert_sent:   # ~10s at 30fps
                _blind_alert_sent = True
                log_system_update("[TAMPER] Camera blindness detected — lens may be covered!")
                _append_detection_perm("TAMPER", "camera_blind", 0.0, "camera appears blocked")
                push_urgent_ws()
                # Max-priority: bypass DND/idle and send alert email immediately
                threading.Thread(target=_send_tamper_email, daemon=True).start()
        else:
            _blind_frame_count = 0
            _blind_alert_sent = False

    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)

    with _mode_lock:
        threshold = DETECTION_THRESHOLD
        privacy = MODE_PRIVACY

    # Build case-insensitive lookup sets so UI casing mismatches never break detection
    _danger_set = {lbl.lower() for lbl in user_data.danger_labels}
    _watch_set  = {lbl.lower() for lbl in WATCH_LABELS}

    danger_detected = False
    det_count = 0
    for d in detections:
        label = d.get_label()
        confidence = d.get_confidence()
        if confidence >= threshold:
            det_count += 1
            text_info += f"{label} ({confidence:.2f})\n"
            _class_counts_today[label] = _class_counts_today.get(label, 0) + 1
            # Privacy blur: blur any detected person (case-insensitive)
            if privacy and label.lower() == "person" and frame is not None:
                bbox = d.get_bbox()
                x1 = int(bbox.xmin() * width)
                y1 = int(bbox.ymin() * height)
                x2 = int(bbox.xmax() * width)
                y2 = int(bbox.ymax() * height)
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(width, x2), min(height, y2)
                if x2 > x1 and y2 > y1:
                    roi_face = frame[y1:y2, x1:x2]
                    roi_face = cv2.GaussianBlur(roi_face, (51, 51), 30)
                    frame[y1:y2, x1:x2] = roi_face
            if label.lower() in _danger_set:
                _label_consec_frames[label] = _label_consec_frames.get(label, 0) + 1
                if _label_consec_frames[label] >= 2:   # require 2 consecutive frames to fire
                    danger_detected = True
                _last_danger_conf = confidence
            elif label.lower() == "person" or (label.lower() in _watch_set and label.lower() not in _danger_set):
                # WATCH: log silently with 30s cooldown to avoid per-frame spam
                now_t = time.time()
                if now_t - _watch_last_logged.get(label, 0) >= 30:
                    _watch_last_logged[label] = now_t
                    log_system_update(f"[WATCH] {label} ({confidence:.2f})")
                    _append_detection_perm("WATCH", label, confidence)

    # Reset consecutive counts for labels not seen (or below threshold) this frame
    seen_above_thr = {d.get_label() for d in detections if d.get_confidence() >= threshold}
    for k in list(_label_consec_frames):
        if k not in seen_above_thr:
            _label_consec_frames[k] = 0

    if det_count > 0:
        _detections_today += det_count

    global _danger_active
    # Find which danger labels were actually detected this frame (for logging)
    _triggered_labels = [d.get_label() for d in detections
                         if d.get_label().lower() in _danger_set
                         and d.get_confidence() >= threshold]
    if danger_detected:
        global _danger_trigger_info
        _danger_trigger_info = text_info   # snapshot the frame that triggered
        _captured_conf = _last_danger_conf
        _captured_label = _triggered_labels[0] if _triggered_labels else "danger"
        _is_rising_edge = not _danger_active
        if _is_rising_edge:
            _danger_active = True
        # Batch all danger work into ONE daemon thread per frame (not 4-5 separate ones)
        def _danger_work(lbl=_captured_label, conf=_captured_conf, rising=_is_rising_edge):
            trigger_software_alert()
            if rising:
                send_email_alert()
                _danger_key = "__danger__"
                _now = time.time()
                if _now - _watch_last_logged.get(_danger_key, 0) >= 60:
                    _watch_last_logged[_danger_key] = _now
                    log_scissors_detection(lbl)
                    _append_detection_perm("DANGER", lbl, conf, "alert triggered")
        with _alert_lock:
            _already_alerting = _alert_active
        if _already_alerting and not _is_rising_edge:
            # The alert is up: keeping it up is two lock grabs, done here. A
            # new thread for each of the 60 frames a second a knife stays in
            # view was the single largest cost of an alert.
            trigger_software_alert()
        else:
            threading.Thread(target=_danger_work, daemon=True).start()
    elif not _triggered_labels:
        # Only reset when NO danger labels are seen at all this frame.
        # Avoids false reset during the 2-frame ramp-up period.
        _danger_active = False

    # ── Drishti: one descriptor per observation ──────────────────────────────
    # observe() is pure arithmetic. Every piece of I/O a rule causes happens on
    # the rule thread instead, so a slow relay or a dead broker can never back
    # up into the video path. Throttled because the rule loop ticks at 2 Hz and
    # nothing is gained by rebuilding the descriptor thirty times a second.
    global _drishti_last_observe
    _now = time.time()
    if _now - _drishti_last_observe >= _DRISHTI_OBSERVE_INTERVAL_S:
        _drishti_last_observe = _now
        try:
            _dets = []
            for d in detections:
                if d.get_confidence() < threshold:
                    continue
                b = d.get_bbox()
                _dets.append({"label": d.get_label().lower(),
                              "bbox": (b.xmin(), b.ymin(), b.xmax(), b.ymax())})
            # Subsampled: a full mean on every frame is not worth the cycles,
            # and ambient light does not change between adjacent pixels.
            _luma = float(frame[::8, ::8].mean()) if frame is not None else 0.0
            DRISHTI_RUNTIME.observe(_dets, _luma)
        except Exception as exc:
            log_system_update(f"[DRISHTI] observe failed: {exc}")

    user_data.person_detected = any(d.get_label().lower() == "person" for d in detections)
    if user_data.person_detected:
        # Cheap and thread-safe, so it runs here; once a second is plenty for
        # a banner that stays up ten seconds (it used to start a thread on
        # every frame with a person in it).
        global _np_last_check
        if _now - _np_last_check >= 1.0:
            _np_last_check = _now
            try:
                _check_night_presence()
            except Exception as exc:
                log_system_update(f"[NIGHT] presence check failed: {exc}")
        # Async cascade: push frame to secondary queue for MobileNet + MiDaS.
        # Non-blocking — if queue full, drop and record metric. Primary never waits.
        if frame is not None:
            best_person = max(
                (d for d in detections if d.get_label().lower() == "person"),
                key=lambda d: d.get_confidence(),
                default=None,
            )
            if best_person is not None:
                det_info = {
                    "label": best_person.get_label(),
                    "confidence": best_person.get_confidence(),
                }
                try:
                    _secondary_queue.put_nowait((frame.copy(), det_info))
                    _cascade_metrics.record_secondary_enqueue()
                except _queue_mod.Full:
                    _cascade_metrics.record_secondary_drop()

    # Gated: the encode is the most expensive thing in this callback and no
    # browser can use 60fps MJPEG. Nothing is drawn on the frame -- the debug
    # readout that used to be here also reached the WebRTC track and every saved
    # evidence clip, because all three read _frame_raw.
    if frame is not None and _frame_publisher.due():
        frame_bgr, jpeg = FramePublisher.encode(frame)
        with _frame_lock:
            global _frame_seq, _frame_raw, _frame_ts
            _frame_buffer = jpeg
            _frame_raw    = frame_bgr
            _frame_seq += 1
            _frame_ts     = time.time()
        user_data.set_frame(frame_bgr)

        # Clip recording — write current frame if active
        _clip_autostopped = False
        _clip_autostopped_path = ""
        with _clip_lock:
            if _clip_writer is not None:
                try:
                    _clip_writer.write(frame_bgr)
                except Exception:
                    pass
                if time.time() - _clip_start_time > 60:
                    _clip_writer.release()
                    _clip_writer = None
                    _clip_autostopped = True
                    _clip_autostopped_path = _clip_path
        if _clip_autostopped:
            log_system_update("Clip auto-stopped after 60 s.")
            push_urgent_ws()   # notify JS so it can reset the record button
            if _clip_autostopped_path:
                threading.Thread(target=exfiltrate_clip, args=(_clip_autostopped_path,),
                                 daemon=True).start()

    latest_detection_info = text_info
    return Gst.PadProbeReturn.OK


PI_CAMERA_SIZE = (1280, 720)
PI_CAMERA_FPS = 60


class GStreamerDetectionApp(GStreamerApp):
    def __init__(self, args, user_data):
        # Force frame capture for MJPEG stream; suppress display
        args.use_frame = True
        args.show_fps = False
        super().__init__(args, user_data)
        self.batch_size = 1
        self.network_width = 640
        self.network_height = 640
        self.network_format = "RGB"
        nms_score_threshold = 0.25
        nms_iou_threshold = 0.45

        new_postprocess_path = os.path.join(self.current_path, '../resources/libyolo_hailortpp_post.so')
        if os.path.exists(new_postprocess_path):
            self.default_postprocess_so = new_postprocess_path
        else:
            self.default_postprocess_so = os.path.join(self.postprocess_dir, 'libyolo_hailortpp_post.so')

        if args.hef_path is not None:
            self.hef_path = args.hef_path
        elif args.network == "yolov8s":
            self.hef_path = os.path.join(self.current_path, '../resources/yolov8s_h8l.hef')
        elif args.network == "yolov6n":
            self.hef_path = os.path.join(self.current_path, '../resources/yolov6n.hef')
        elif args.network == "yolox_s_leaky":
            self.hef_path = os.path.join(self.current_path, '../resources/yolox_s_leaky_h8l_mz.hef')
        else:
            raise ValueError("Invalid network type")

        if args.labels_json:
            self.labels_config = f' config-path={args.labels_json} '
            if not os.path.exists(new_postprocess_path):
                print("New postprocess .so file is missing. Required for custom labels.")
                sys.exit(1)
        else:
            self.labels_config = ''

        self.app_callback = app_callback
        # When using libyolo_hailortpp_post.so with a custom labels config, the HEF
        # runs NMS internally (HailortPP mode). Adding output-format-type=FLOAT32
        # conflicts with that and silently drops all detections (Knife, Hammer, etc.).
        # Only set FLOAT32 for standard models that rely on hailofilter for NMS.
        if args.labels_json:
            self.thresholds_str = (
                f"nms-score-threshold={nms_score_threshold} "
                f"nms-iou-threshold={nms_iou_threshold}"
            )
        else:
            self.thresholds_str = (
                f"nms-score-threshold={nms_score_threshold} "
                f"nms-iou-threshold={nms_iou_threshold} "
                f"output-format-type=HAILO_FORMAT_TYPE_FLOAT32"
            )
        setproctitle.setproctitle("Garuda Web App")
        # Use fakesink — no display needed, frames captured via MJPEG callback
        self.video_sink = "fakesink"
        self.create_pipeline()

    def run(self):
        """Override base run() to skip cv2 display subprocess (web mode uses MJPEG)."""
        from hailo_rpi_common import disable_qos
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.bus_call, self.loop)

        identity = self.pipeline.get_by_name("identity_callback")
        if identity:
            identity_pad = identity.get_static_pad("src")
            identity_pad.add_probe(Gst.PadProbeType.BUFFER, self.app_callback, self.user_data)

        disable_qos(self.pipeline)
        self.pipeline.set_state(Gst.State.PLAYING)

        camera_stop, camera_thread = threading.Event(), None
        appsrc = self.pipeline.get_by_name("app_source")
        if appsrc is not None:
            camera_thread = threading.Thread(target=self._feed_pi_camera,
                                             args=(appsrc, camera_stop), daemon=True)
            camera_thread.start()

        if self.options_menu.dump_dot:
            GLib.timeout_add_seconds(3, self.dump_dot_file)

        try:
            self.loop.run()
        except Exception:
            pass

        self.user_data.running = False
        # The camera must be closed before the next pipeline opens it again.
        camera_stop.set()
        if camera_thread is not None:
            camera_thread.join(timeout=8)
        self.pipeline.set_state(Gst.State.NULL)

    def _feed_pi_camera(self, appsrc, stop):
        """Capture from the Pi camera with picamera2 and push frames into appsrc.

        Until 2026-10 the pipeline used libcamerasrc. That GStreamer plugin
        (libcamera 0.5.2) aborts the whole process on an internal assertion
        (a request completes while its queue is empty) when the Pi is busy,
        taking the web server down with it: seven times on 2026-10-01 alone.
        picamera2 talks to the same camera without that plugin, and a failure
        here is an ordinary Python exception: the pipeline loop is asked to
        quit and _run_pipeline starts a fresh one.
        """
        picam2 = None
        try:
            from picamera2 import Picamera2
            # appsrc has to announce what it sends; the capsfilter after it
            # only checks, it does not describe the buffers.
            appsrc.set_property("caps", Gst.Caps.from_string(
                f"video/x-raw, format={self.network_format}, width={PI_CAMERA_SIZE[0]}, "
                f"height={PI_CAMERA_SIZE[1]}, framerate={PI_CAMERA_FPS}/1, pixel-aspect-ratio=1/1"))
            picam2 = Picamera2()
            # libcamera names formats back to front: "BGR888" is R,G,B in memory,
            # which is what the pipeline's video/x-raw format=RGB expects.
            picam2.configure(picam2.create_video_configuration(
                main={"size": PI_CAMERA_SIZE, "format": "BGR888"},
                controls={"FrameRate": PI_CAMERA_FPS}, buffer_count=4, queue=False))
            picam2.start()
            while not stop.is_set():
                frame = picam2.capture_array("main")
                ret = appsrc.emit("push-buffer", Gst.Buffer.new_wrapped(frame.tobytes()))
                if ret not in (Gst.FlowReturn.OK, Gst.FlowReturn.FLUSHING):
                    raise RuntimeError(f"appsrc refused a frame: {ret.value_nick}")
        except Exception as exc:
            if not stop.is_set():
                log_system_update(f"[CAMERA] Pi camera feed failed: {type(exc).__name__}: {exc}")
                traceback.print_exc()
                GLib.idle_add(self.loop.quit)
        finally:
            if picam2 is not None:
                for close in (picam2.stop, picam2.close):
                    try:
                        close()
                    except Exception:
                        pass

    def get_pipeline_string(self):
        if self.source_type == "rpi":
            # 1280x720 @ 60fps — IMX708 supports up to 120fps at 720p vs 30fps at 1536x864
            # Frames arrive from _feed_pi_camera (picamera2) already RGB at this
            # size; skip the common videoscale+videoconvert to avoid redundant
            # processing on the Pi 5. Leaky: a late frame is dropped, never queued.
            source_element = (
                "appsrc name=app_source is-live=true do-timestamp=true format=time "
                "max-buffers=2 leaky-type=downstream ! "
                f"video/x-raw, format={self.network_format}, width={PI_CAMERA_SIZE[0]}, "
                f"height={PI_CAMERA_SIZE[1]}, framerate={PI_CAMERA_FPS}/1 ! "
                + QUEUE("queue_src_scale")
                + "videoscale n-threads=2 ! "
                f"video/x-raw, format={self.network_format}, width={self.network_width}, height={self.network_height}, "
                "pixel-aspect-ratio=1/1 ! "
            )
        elif self.source_type == "usb":
            source_element = (
                f"v4l2src device={self.video_source} name=src_0 ! "
                "video/x-raw, width=640, height=480, framerate=30/1 ! "
            )
        else:
            source_element = (
                f"filesrc location={self.video_source} name=src_0 ! "
                + QUEUE("queue_dec264")
                + "qtdemux ! h264parse ! avdec_h264 max-threads=2 ! "
                "video/x-raw, format=I420 ! "
            )

        if self.source_type != "rpi":
            # USB and file sources need scale + format conversion to network dims
            source_element += QUEUE("queue_scale")
            source_element += "videoscale n-threads=2 ! "
            source_element += QUEUE("queue_src_convert")
            source_element += "videoconvert n-threads=3 name=src_convert qos=false ! "
            source_element += (
                f"video/x-raw, format={self.network_format}, "
                f"width={self.network_width}, height={self.network_height}, "
                "pixel-aspect-ratio=1/1 ! "
            )

        pipeline_string = (
            "hailomuxer name=hmux "
            + source_element
            + "tee name=t ! "
            + QUEUE("bypass_queue", max_size_buffers=20)
            + "hmux.sink_0 "
            + "t. ! "
            + QUEUE("queue_hailonet")
            + "videoconvert n-threads=3 ! "
            f"hailonet hef-path={self.hef_path} batch-size={self.batch_size} "
            f"{self.thresholds_str} force-writable=true ! "
            + QUEUE("queue_hailofilter")
            + f"hailofilter so-path={self.default_postprocess_so} {self.labels_config} qos=false ! "
            + QUEUE("queue_hmuc")
            + "hmux.sink_1 "
            + "hmux. ! "
            + QUEUE("queue_hailo_python")
            + QUEUE("queue_user_callback")
            + "identity name=identity_callback ! "
            # Nothing downstream of the probe. hailooverlay drew detection
            # boxes and a three-threaded videoconvert converted them, both
            # into a fakesink that discards the result -- the frames the app
            # serves are taken at identity_callback, upstream of all of it.
            # On a board that had already tripped its soft temperature limit
            # that was worth reclaiming.
            + QUEUE("queue_hailo_display")
            + "fakesink name=hailo_display sync=false "
        )
        return pipeline_string

##############################################################################
# NARADA VOICE ASSISTANT
##############################################################################
BUILT_IN_COMMANDS = {
    "activate dnd"              : "Enables Do Not Disturb mode",
    "deactivate dnd"            : "Disables DND mode",
    "activate email off"        : "Turns off email notifications",
    "deactivate email off"      : "Turns on email notifications",
    "activate idle"             : "Disables all alerts",
    "deactivate idle"           : "Re-enables all alerts",
    "activate night mode"       : "High priority alerts",
    "deactivate night mode"     : "Return to normal alerts",
    "activate emergency mode"   : "Maximum alerts",
    "deactivate emergency mode" : "Stops emergency mode",
    "hi / hello"                : "Greets the user",
    "how are you"               : "Narada status update",
    "time"                      : "Tells the current time",
}


# None until the voice thread has tried the microphone; then True or False.
# The web app shows one clear notice instead of a pile of repeated errors.
_voice_mic_ok = None
_voice_mic_detail = ""


def voice_assistant_loop(stop_event, current_user=None):
    global MODE_DND, MODE_EMAIL_OFF, MODE_IDLE, MODE_NIGHT, MODE_EMERGENCY, MODE_PRIVACY
    global DETECTION_THRESHOLD, _voice_mic_ok, _voice_mic_detail

    recognizer = sr.Recognizer()
    try:
        mic = sr.Microphone()
        _voice_mic_ok, _voice_mic_detail = True, ""
        append_voice_log("Microphone connected.", user_name=current_user)
    except Exception as e:
        _voice_mic_ok, _voice_mic_detail = False, str(e)
        append_voice_log(f"Error accessing microphone: {e}", user_name=current_user)
        return

    with mic as source:
        recognizer.adjust_for_ambient_noise(source)
        append_voice_log("Calibrated for ambient noise.", user_name=current_user)

    while not stop_event.is_set():
        with mic as source:
            append_voice_log("Listening...", user_name=current_user)
            try:
                audio = recognizer.listen(source, timeout=10, phrase_time_limit=10)
            except sr.WaitTimeoutError:
                continue

        try:
            user_input = recognizer.recognize_google(audio)
            append_voice_log(f"You said: {user_input}", user_name=current_user)
        except sr.UnknownValueError:
            append_voice_log("Could not understand audio.", user_name=current_user)
            continue
        except sr.RequestError as e:
            append_voice_log(f"Speech recognition error: {e}", user_name=current_user)
            continue

        response = _assistant_reply(user_input, current_user or "voice", "user")["reply"]

        append_voice_response(response, user_name=current_user)
        time.sleep(0.5)

##############################################################################
# SESSION MANAGEMENT
##############################################################################
_ACCESS_DURATION  = 900           # 15 minutes — short-lived access token
_REFRESH_DURATION = 7 * 24 * 3600  # 7 days — refresh token

# Refresh token store: token → {username, role, expires, created_at}
_refresh_tokens: dict = {}

# Every restart used to sign the whole house out: the store above lived only
# in memory. It is now mirrored to disk, as SHA-256 digests (the file is no
# use to someone who reads it), and a token that arrives after a restart is
# recognised by its digest and adopted back into the store above.
REFRESH_TOKENS_FILE = str(_BASE / "system_logs" / "refresh_tokens.json")
_persisted_refresh: dict = {}      # sha256(token) → record, loaded at start-up
_refresh_dirty = False

def _load_refresh_tokens():
    global _persisted_refresh
    data = {}
    try:
        if os.path.exists(REFRESH_TOKENS_FILE):
            with open(REFRESH_TOKENS_FILE, encoding="utf-8") as f:
                data = json.load(f)
    except Exception:
        data = {}
    now = time.time()
    _persisted_refresh = {
        d: rec for d, rec in (data.items() if isinstance(data, dict) else [])
        if isinstance(rec, dict) and rec.get("expires", 0) > now and rec.get("username") in USERS
    }

def _save_refresh_tokens():
    """Write the digests of every live refresh token. Blocking (fsync)."""
    global _refresh_dirty
    _refresh_dirty = False
    now = time.time()
    snapshot = {d: rec for d, rec in list(_persisted_refresh.items()) if rec.get("expires", 0) > now}
    for token, rec in list(_refresh_tokens.items()):
        if rec.get("expires", 0) > now:
            snapshot[_rt_digest(token)] = rec
    try:
        _atomic_json_write(REFRESH_TOKENS_FILE, snapshot)
        os.chmod(REFRESH_TOKENS_FILE, 0o600)
    except Exception as exc:
        _syslog.warning("could not persist refresh tokens: %s", exc)

def _revoke_refresh(token) -> bool:
    global _refresh_dirty
    if not token:
        return False
    hit = _refresh_tokens.pop(token, None) is not None
    hit = (_persisted_refresh.pop(_rt_digest(token), None) is not None) or hit
    if hit:
        _refresh_dirty = True
    return hit

def create_refresh_token(username: str) -> str:
    token = secrets.token_hex(64)
    now = time.time()
    _refresh_tokens[token] = {
        "username": username,
        "role": USERS[username]["role"],
        "expires": now + _REFRESH_DURATION,
        "created_at": now,
    }
    global _refresh_dirty
    _refresh_dirty = True
    return token

def _prune_expired_refresh_tokens():
    global _refresh_dirty
    now = time.time()
    for t in [t for t, s in list(_refresh_tokens.items()) if s.get("expires", 0) <= now]:
        _refresh_tokens.pop(t, None)
        _refresh_dirty = True
    for d in [d for d, s in list(_persisted_refresh.items()) if s.get("expires", 0) <= now]:
        _persisted_refresh.pop(d, None)
        _refresh_dirty = True

def _user_signed_in(username: str) -> bool:
    """True while `username` still holds a live session or refresh token.

    Long-lived connections (the state socket, the camera streams) are checked
    against this, not against the token they opened with: access tokens rotate
    every 15 minutes, but sign-out, a password change and deleting the account
    all leave the user with nothing, and the connection must end with it.
    """
    now = time.time()
    if any(s.get("username") == username and s.get("expires", 0) > now
           for s in list(_sessions.values())):
        return True
    return any(s.get("username") == username and s.get("expires", 0) > now
               for s in list(_refresh_tokens.values()) + list(_persisted_refresh.values()))

def _is_cross_site(request) -> bool:
    """True when the page calling the API lives on another site (the Vercel copy).

    Such a page never gets our SameSite cookies back, so it is handed the
    refresh token in the body and returns it in X-Garuda-Refresh.
    """
    origin = request.headers.get("origin") or ""
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if not origin or not host:
        return False
    origin_host = origin.split("://", 1)[-1].split("/", 1)[0].split(":")[0].lower()
    return origin_host != host

def _cookie_secure(request) -> bool:
    """Secure cookies whenever the visitor reached us over HTTPS.

    SECURE_COOKIES in .env forces it; otherwise the proxy says which scheme the
    browser used, so a forgotten setting cannot send session cookies in clear.
    """
    if _COOKIE_SECURE:
        return True
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return proto == "https" or '"scheme":"https"' in request.headers.get("cf-visitor", "").replace(" ", "")

def _set_session_cookies(request, response, access_token, refresh_token=None, persistent=True):
    """Set the session cookie and, when given, the refresh cookie.

    `persistent=False` is "Remember me" left unticked: the refresh cookie is
    then a browser-session cookie and goes when the browser closes.
    """
    secure = _cookie_secure(request)
    response.set_cookie("garuda_session", access_token, httponly=True, samesite="lax",
                        secure=secure, max_age=_ACCESS_DURATION)
    if refresh_token:
        response.set_cookie("garuda_refresh", refresh_token, httponly=True, samesite="lax",
                            secure=secure, path="/api/refresh",
                            max_age=_REFRESH_DURATION if persistent else None)

def get_refresh_token(token: str) -> dict | None:
    s = _refresh_tokens.get(token)
    if not s and token:
        # Issued before the last restart: known only by its digest.
        s = _persisted_refresh.pop(_rt_digest(token), None)
        if s:
            _refresh_tokens[token] = s
    if not s:
        return None
    if s["expires"] <= time.time():
        _revoke_refresh(token)
        return None
    return s

def create_session(username, duration=None):
    if duration is None:
        duration = _ACCESS_DURATION
    token = secrets.token_hex(64)
    now = time.time()
    _sessions[token] = {
        "username": username,
        "role": USERS[username]["role"],
        "expires": now + duration,
        "created_at": now,
        "max_lifetime": now + 86400,  # absolute 24-hour hard limit
        "logs_unlocked": False,
    }
    return token

def create_master_session(duration=3600):
    """Create an admin session via master key — logs unlocked immediately."""
    token = secrets.token_hex(64)
    now = time.time()
    _sessions[token] = {
        "username": "admin",
        "role": "admin",
        "expires": now + duration,
        "created_at": now,
        "max_lifetime": now + 28800,  # absolute 8-hour hard limit for master sessions
        "logs_unlocked": True,
    }
    return token

def get_session(token):
    if not token:
        return None
    s = _sessions.get(token)
    if not s:
        return None
    now = time.time()
    if s["expires"] <= now or now >= s.get("max_lifetime", now + 1):
        _sessions.pop(token, None)   # pop: another thread may have pruned it already
        return None
    return s

def _prune_expired_sessions():
    """Remove sessions that have expired or exceeded their absolute lifetime."""
    now = time.time()
    dead = [t for t, s in list(_sessions.items())
            if s["expires"] <= now or now >= s.get("max_lifetime", now + 1)]
    for t in dead:
        _sessions.pop(t, None)
    _prune_expired_refresh_tokens()

def require_session(request: Request):
    # X-Garuda-Token header takes priority (cross-origin API); cookie is browser fallback
    token = request.headers.get("X-Garuda-Token") or request.cookies.get("garuda_session")
    session = get_session(token)
    if not session:
        raise HTTPException(401, "Not authenticated")
    # No sliding window — access tokens are short-lived (15 min); use /api/refresh to renew.
    # Inject token so endpoints can exclude the current session during invalidation.
    session["token"] = token
    return session

def require_admin(request: Request):
    session = require_session(request)
    if session["role"] != "admin":
        raise HTTPException(403, "Admin access required")
    return session

def require_logs(request: Request):
    """Admin session AND master key must have been entered this session."""
    session = require_admin(request)
    if not session.get("logs_unlocked", False):
        raise HTTPException(403, "Master key required to view logs.")
    return session

##############################################################################
# STATE HELPER
##############################################################################
def _home_state_summary():
    """Devices on, notices and presence for the dashboard; never raises."""
    try:
        return HOME.summary()
    except Exception as exc:
        return {"error": type(exc).__name__}


def _recent_alert_history(days: int = 120) -> dict:
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    return {day: n for day, n in _alert_history.items() if str(day) >= cutoff}


def get_state_dict():
    global _alert_active, _danger_trigger_info, _alert_end_time
    # Expire alert once the wall-clock timer runs out
    _just_cleared = False
    with _alert_lock:
        if _alert_active and _alert_end_time > 0 and time.time() >= _alert_end_time:
            _alert_active = False
            _alert_end_time = 0.0
            _danger_trigger_info = ""
            _just_cleared = True
    if _just_cleared:
        push_urgent_ws()   # push cleared state outside lock to avoid deadlock

    uptime = int(time.time() - _app_start_time)
    hours, rem = divmod(uptime, 3600)
    mins, secs = divmod(rem, 60)
    uptime_str = f"{hours:02d}:{mins:02d}:{secs:02d}"

    # System health (psutil) — EMA-smoothed to avoid jitter
    global _cpu_ema, _ram_ema, _temp_ema, _cpu_cores_ema
    cpu_pct = None
    ram_pct = None
    cpu_temp = None
    cpu_cores = []
    ram_used_gb = None
    ram_total_gb = None
    if psutil:
        raw_cpu = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        raw_ram = vm.percent
        _cpu_ema = _EMA_A * raw_cpu + (1 - _EMA_A) * _cpu_ema
        _ram_ema = _EMA_A * raw_ram + (1 - _EMA_A) * _ram_ema
        cpu_pct = round(_cpu_ema, 1)
        ram_pct = round(_ram_ema, 1)
        ram_used_gb  = round(vm.used  / (1024 ** 3), 1)
        ram_total_gb = round(vm.total / (1024 ** 3), 1)
        # Per-core EMA
        raw_cores = psutil.cpu_percent(percpu=True, interval=None)
        if not _cpu_cores_ema:
            _cpu_cores_ema.extend(raw_cores)
        else:
            for i, v in enumerate(raw_cores):
                if i < len(_cpu_cores_ema):
                    _cpu_cores_ema[i] = _EMA_A * v + (1 - _EMA_A) * _cpu_cores_ema[i]
        cpu_cores = [round(v, 1) for v in _cpu_cores_ema]
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                for sensor_name in ('cpu_thermal', 'coretemp', 'k10temp', 'acpitz'):
                    if sensor_name in temps and temps[sensor_name]:
                        raw_temp = temps[sensor_name][0].current
                        _temp_ema = _EMA_A * raw_temp + (1 - _EMA_A) * _temp_ema
                        cpu_temp = round(_temp_ema, 1)
                        break
        except Exception:
            pass

    inference_fps = round(_total_frames / max(1, uptime), 1) if uptime > 0 else 0.0

    # ── Disk usage ──
    disk_pct = None
    disk_used_gb = None
    disk_total_gb = None
    if psutil:
        try:
            du = psutil.disk_usage('/')
            disk_pct = round(du.percent, 1)
            disk_used_gb = round(du.used / (1024 ** 3), 1)
            disk_total_gb = round(du.total / (1024 ** 3), 1)
        except Exception:
            pass

    # ── Network status ──
    net_connected = False
    net_iface = None
    if psutil:
        try:
            stats = psutil.net_if_stats()
            for iface in ('wlan0', 'eth0', 'end0'):
                if iface in stats and stats[iface].isup:
                    net_connected = True
                    net_iface = iface
                    break
        except Exception:
            pass

    # ── Thermal throttling (RPi5: >80°C = throttled) ──
    throttled = False
    if cpu_temp and cpu_temp >= 80:
        throttled = True

    # ── Security health ──
    # If no HEARTBEAT_KEY is configured, watchdog is N/A (always OK)
    _hb_key = os.environ.get("HEARTBEAT_KEY", "")
    watchdog_ok = True if not _hb_key else (time.time() - _last_heartbeat) < _DEADMAN_TIMEOUT
    camera_blind = _blind_alert_sent

    # Expire night presence alert if window ended
    global _night_presence_alert_active
    with _np_lock:
        if _night_presence_alert_active and time.time() > _night_presence_alert_end_time:
            _night_presence_alert_active = False
    np_alert = _night_presence_alert_active

    return {
        "modes": {
            "dnd": MODE_DND,
            "email_off": MODE_EMAIL_OFF,
            "idle": MODE_IDLE,
            "night": MODE_NIGHT,
            "emergency": MODE_EMERGENCY,
            "privacy": MODE_PRIVACY,
        },
        "alert_active": _alert_active,
        "night_presence_alert": np_alert,
        "danger_info": _danger_trigger_info,   # only non-empty during a danger alert
        "last_alert": _last_alert_time.isoformat() if _last_alert_time else None,
        "uptime": uptime_str,
        "uptime_seconds": uptime,
        "system_log": system_updates_log[-50:],
        "voice_log": voice_assistant_log[-30:],
        "voice_mic": {"ok": _voice_mic_ok, "detail": _voice_mic_detail},
        "voice_responses": voice_responses[-30:],
        "detection_threshold": DETECTION_THRESHOLD,
        "cpu_percent": cpu_pct,
        "cpu_cores": cpu_cores,
        "ram_percent": ram_pct,
        "ram_used_gb": ram_used_gb,
        "ram_total_gb": ram_total_gb,
        "cpu_temp": cpu_temp,
        "inference_fps": inference_fps,
        "owner_present": _owner_present,
        "home": _home_state_summary(),
        "owner_name": (_present_device() or {}).get("name"),
        "known_devices": [
            {"name": d.get("name", ""), "mac": _device_mac(d),
             "online": _mac_online(_device_mac(d))}
            for d in KNOWN_DEVICES
        ],
        # The heatmap shows 13 weeks; the full history (one key per day, for
        # ever) was being sent to every client every two seconds.
        "alert_history": _recent_alert_history(),
        # Security health
        "watchdog_ok": watchdog_ok,
        "camera_blind": camera_blind,
        "throttled": throttled,
        # Extended hardware
        "disk_percent": disk_pct,
        "disk_used_gb": disk_used_gb,
        "disk_total_gb": disk_total_gb,
        "net_connected": net_connected,
        "net_iface": net_iface,
        # Log counts for badge display (avoid sending full arrays over WS)
        "detection_log_count": len(_detection_log),
        "presence_log_count": len(_presence_log),
        # Offline queue
        "net_online": _net_online,
        "pending_sync": get_pending_count(max_age=10.0),
        # Clip recording state (lets JS reset button when server auto-stops)
        "clip_recording": _clip_writer is not None,
    }

##############################################################################
# DEAD MAN'S SWITCH MONITOR
##############################################################################
def _deadman_monitor():
    """Background thread: if no /api/heartbeat in _DEADMAN_TIMEOUT seconds, send tamper alert.

    Opt-in via DEADMAN_ENABLED=1. Only meaningful with an external monitor
    hitting /api/heartbeat. Anti-spam guards: never alarms unless at least one
    real heartbeat has been received (otherwise there is simply no heartbeat
    source), and repeat alerts are rate-limited to _DEADMAN_REALERT_INTERVAL.
    """
    global _deadman_alert_sent, _deadman_last_alert
    if not _DEADMAN_ENABLED:
        log_system_update("[TAMPER] Dead-man switch disabled (set DEADMAN_ENABLED=1 to enable).")
        return
    while True:
        time.sleep(60)
        # No heartbeat has ever arrived → no monitor configured, not tampering.
        if not _heartbeat_ever:
            continue
        elapsed = time.time() - _last_heartbeat
        now = time.time()
        if elapsed > _DEADMAN_TIMEOUT and not _deadman_alert_sent \
                and (now - _deadman_last_alert) > _DEADMAN_REALERT_INTERVAL:
            _deadman_alert_sent = True
            _deadman_last_alert = now
            log_system_update(f"[TAMPER] No heartbeat in {int(elapsed)}s — possible system tampering!")
            # Send tamper alert email
            try:
                body = (f"Garuda dead man's switch triggered.\n"
                        f"No heartbeat received in {int(elapsed)} seconds.\n"
                        f"Possible system tampering or network failure.")
                _send_mail("TAMPER ALERT: Garuda heartbeat missed", body)
            except Exception as e:
                log_system_update(f"[TAMPER] Failed to send alert email: {e}")

##############################################################################
# SCHEDULED MODES
##############################################################################

def _schedule_monitor():
    """Background thread: enforce scheduled mode transitions.

    Checks every 30 s and immediately on first run so startup catches the
    correct state without a 60-s blind window.  Takes a dict snapshot before
    iterating so a concurrent update_config() call can't cause a RuntimeError.
    """
    mode_map = {
        "dnd": "MODE_DND", "email_off": "MODE_EMAIL_OFF",
        "idle": "MODE_IDLE", "night": "MODE_NIGHT",
    }
    # What each schedule last asked for. A mode is only written when that
    # changes (the window opens or closes, or the schedule is edited): writing
    # it on every pass undid, within 30 s, any switch a person flipped by hand
    # inside the window.
    applied: dict = {}
    while True:
        try:
            sched_snap = dict(MODE_SCHEDULE)   # snapshot outside lock — avoids racing with update_config
            for gone in [m for m in applied if m not in sched_snap]:
                applied.pop(gone, None)
            if sched_snap:
                now_str = datetime.datetime.now().strftime("%H:%M")
                changed = False
                with _mode_lock:
                    for mode_name, sched in sched_snap.items():
                        if not isinstance(sched, dict):
                            continue
                        start = sched.get("start", "")
                        end   = sched.get("end", "")
                        if not start or not end or mode_name not in mode_map:
                            continue
                        in_range = _time_in_range(start, end, now_str)
                        key = (start, end, in_range)
                        if applied.get(mode_name) == key:
                            continue
                        applied[mode_name] = key
                        gkey = mode_map[mode_name]
                        if globals().get(gkey) != in_range:
                            globals()[gkey] = in_range
                            changed = True
                            log_system_update(
                                f"[MODE] {mode_name} {'on' if in_range else 'off'} by schedule ({start}-{end})")
                if changed:
                    push_urgent_ws()
        except Exception as exc:
            log_system_update(f"[MODE] schedule check failed: {type(exc).__name__}: {exc}")
        time.sleep(30)   # sleep AFTER check so first run is immediate; 30 s ≤ worst-case lag

##############################################################################
# FASTAPI APP
##############################################################################
from contextlib import asynccontextmanager

@asynccontextmanager
async def _lifespan(app):
    global _event_loop, _ws_trigger, _ws_broadcaster_task
    _event_loop = asyncio.get_running_loop()
    _ws_trigger = asyncio.Event()
    _init_event_db()
    _load_alert_history()
    _load_presence_log()
    load_master_keys()
    # Proposals and sessions from the previous run are stale by definition.
    DRISHTI_CTX.pending.purge()
    # Backed by a file, so deploying a change does not sign the house out.
    _drishti_auth.configure(DRISHTI_SESSIONS_PATH)
    _drishti_auth.prune_expired()
    DRISHTI_RUNTIME.start()
    HOME.start()
    log_system_update(
        f"[DRISHTI] rule loop started — {len(DRISHTI_CTX.store.rules)} rules, "
        f"{len(DRISHTI_CTX.registry.devices)} devices")
    _ws_broadcaster_task = asyncio.create_task(_ws_broadcaster())
    _load_refresh_tokens()
    # Supervised: a loop that raises is logged, restarted with a pause, and
    # shows on /api/system/info instead of vanishing until the next restart.
    SUPERVISOR.spawn("presence", _presence_poller)
    SUPERVISOR.spawn("deadman", _deadman_monitor)
    SUPERVISOR.spawn("connectivity", _connectivity_monitor)
    SUPERVISOR.spawn("mode-schedule", _schedule_monitor)
    SUPERVISOR.spawn("log-flush", _flush_log_thread, critical=True)
    yield
    HOME.stop()
    DRISHTI_RUNTIME.stop()
    # Flush any remaining buffered log lines before exit
    _do_flush_logs()
    _save_refresh_tokens()
    if _ws_broadcaster_task is not None:
        _ws_broadcaster_task.cancel()
        await asyncio.gather(_ws_broadcaster_task, return_exceptions=True)
        _ws_broadcaster_task = None
    # Close any open WebRTC peer connections on shutdown
    if _pc_set:
        await asyncio.gather(*[pc.close() for pc in list(_pc_set)], return_exceptions=True)
        _pc_set.clear()

# /docs, /redoc and /openapi.json were served to anyone who asked, over the
# tunnel: a map of every route for whoever is probing. The reference is still
# there for an admin, at /api/openapi.json.
fastapi_app = FastAPI(title="Garuda Security System", version=API_VERSION, lifespan=_lifespan,
                      docs_url=None, redoc_url=None, openapi_url=None)

_VERCEL_PROJECT_RE = re.escape(os.environ.get("GARUDA_VERCEL_PROJECT", "garuda-26").strip().lower() or "garuda-26")

# CORS — restrict to known origins
fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://garuda.veeramanikanta.in",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
    ],
    # Credentials are allowed, so the Vercel rule names this project's own
    # deployments (garuda-26, garuda-26-git-<branch>-..., garuda-26-<hash>-...)
    # rather than every site anyone hosts on vercel.app.
    allow_origin_regex=(r"^https://([a-z0-9-]+\.)*veeramanikanta\.in$"
                        r"|^https://" + _VERCEL_PROJECT_RE + r"(-[a-z0-9-]+)?\.vercel\.app$"
                        r"|^http://(localhost|127\.0\.0\.1)(:\d+)?$"),
    allow_credentials=True,
    # PATCH and DELETE are used by /api/home (devices, scenes, schedules,
    # rules). The Vercel front end is cross-origin, so without them the
    # browser's preflight fails and those buttons silently do nothing.
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-Garuda-Token", "X-Garuda-Refresh"],
)

# Global rate-limit middleware — applied to all API endpoints
# Endpoints that do their own per-action rate limiting (login, OTP) keep their
# individual checks; this catches everything else.
# Health and readiness are polled by monitors; they must not eat the budget.
_RATE_EXEMPT_PREFIXES = ("/static/", "/drishti/", "/ws", "/stream", "/api/eval/",
                         "/api/health", "/api/ready")

@fastapi_app.middleware("http")
async def global_rate_limit(request: Request, call_next):
    path = request.url.path
    _eval_tok = os.environ.get("GARUDA_EVAL_TOKEN", "")
    _tok_hdr = request.headers.get("X-Eval-Token", "")
    _eval_bypass = bool(_eval_tok) and hmac.compare_digest(_tok_hdr.encode(), _eval_tok.encode())
    if not _eval_bypass and not any(path.startswith(p) for p in _RATE_EXEMPT_PREFIXES):
        token = request.headers.get("X-Garuda-Token") or request.cookies.get("garuda_session")
        signed_in = bool(token) and get_session(token) is not None
        allowed = (_check_rate_limit(request, "session", _RATE_LIMIT_SESSION) if signed_in
                   else _check_rate_limit(request))
        if not allowed:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Too many requests. Try again later."}, status_code=429)
    return await call_next(request)

@fastapi_app.middleware("http")
async def product_scope(request: Request, call_next):
    """The security-only product has no home automation, on the server too."""
    if (request.url.path.startswith("/api/home")
            and _product_for_host(request.headers.get("host")) == "security"):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return await call_next(request)

# Security headers middleware
@fastapi_app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        # blob:/data: scripts are the voice client's audio worklets; the
        # ElevenLabs hosts carry Narada's WebRTC voice session.
        "script-src 'self' 'unsafe-inline' blob: data:; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' blob: data:; "
        "media-src 'self' blob: mediastream:; "
        "font-src 'self'; "
        "connect-src 'self' wss: ws: https://*.elevenlabs.io; "
        "frame-ancestors 'none'"
    )
    # Answers from the API describe this moment and this user; nothing between
    # the Pi and the browser should keep a copy.
    if request.url.path.startswith("/api/") and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
    return response

# Request ids, the /api/v1 alias, one error shape and the access log. Added
# after the middleware above so it wraps them: a 429 from the rate limiter
# carries a request id too.
_core_http.install(fastapi_app, api_version=API_VERSION, client_ip=_get_client_ip)

# Serve static files from garuda_web/
_static_dir = Path(__file__).parent / "garuda_web"
if _static_dir.exists():
    fastapi_app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# Vite builds with base=/drishti/, so the SPA's own asset URLs are absolute and
# index.html can be served from / without rewriting anything.
if DRISHTI_APP_ENABLED and DRISHTI_DIST.is_dir():
    fastapi_app.mount("/drishti", StaticFiles(directory=str(DRISHTI_DIST)), name="drishti")

# ── Drishti router ───────────────────────────────────────────────────────────
# Same two import paths as the constants above; sys.path is already fixed by
# the time this runs in script mode.
try:
    from .drishti_api import build_context as _build_drishti_context
    from .drishti_api import build_router as _build_drishti_router
    from . import drishti_auth as _drishti_auth
    from .garuda_auto.runtime import DrishtiRuntime as _DrishtiRuntime
except ImportError:
    from basic_pipelines.drishti_api import build_context as _build_drishti_context
    from basic_pipelines.drishti_api import build_router as _build_drishti_router
    from basic_pipelines import drishti_auth as _drishti_auth
    from basic_pipelines.garuda_auto.runtime import DrishtiRuntime as _DrishtiRuntime

DRISHTI_CTX = _build_drishti_context(
    data_dir=DRISHTI_DATA_DIR,
    relay_channels=RELAY_CHANNELS,
    channel_to_pin=CHANNEL_TO_PIN,
    mqtt_host=os.environ.get("DRISHTI_MQTT_HOST", "localhost"),
    nim_key=os.environ.get("NIM_API_KEY", ""),
    nim_model=os.environ.get("NIM_MODEL", ""),
    matcher_backend=os.environ.get("DRISHTI_MATCHER", "fuzzy"),
)


def _drishti_authenticate(username, password):
    """Drishti login against Garuda's own user table. Returns a role or None.

    drishti_api cannot import this module to reach USERS: Garuda_web runs as a
    script, so the live globals are __main__'s, and an import would produce a
    second copy whose USERS is still the empty dict it starts as. Passing the
    function in keeps the one live table.
    """
    user = USERS.get(username)
    if user is None or not _verify_password(password, user["password"]):
        return None
    return user.get("role", "user")


def _drishti_system_state():
    """Mode flags, uptime and camera liveness for the Drishti Home screen."""
    return {
        "modes": {
            "dnd": MODE_DND, "night": MODE_NIGHT, "idle": MODE_IDLE,
            "emergency": MODE_EMERGENCY, "privacy": MODE_PRIVACY,
            "email_off": MODE_EMAIL_OFF,
        },
        "uptime_s": int(time.time() - _app_start_time),
        # There is no pipeline liveness flag, and app_gst is set before the
        # pipeline produces anything. Frame freshness is the honest signal:
        # what the screen wants to know is whether the camera is delivering.
        "pipeline": "running" if (time.time() - _frame_ts) < 5.0 else "stopped",
        "rule_loop": DRISHTI_RUNTIME.health(),
    }


def _drishti_set_privacy(on):
    """Turn the camera off from the app.

    MODE_PRIVACY was reachable only through the voice assistant, so the web app
    could read the flag and never change it.
    """
    global MODE_PRIVACY
    MODE_PRIVACY = bool(on)
    log_system_update(f"[DRISHTI] privacy {'on' if MODE_PRIVACY else 'off'}")


DRISHTI_CTX.authenticate = _drishti_authenticate
DRISHTI_CTX.system_state = _drishti_system_state
DRISHTI_CTX.set_privacy = _drishti_set_privacy

# The loop that makes rules actually run. Narada-RS built SceneBuilder and
# RuleEngine, tested them, and connected them to nothing: until now the
# descriptor was always empty and no rule had ever fired.
DRISHTI_RUNTIME = _DrishtiRuntime(DRISHTI_CTX)
DRISHTI_CTX.on_registry_change = DRISHTI_RUNTIME.rebind

if DRISHTI_APP_ENABLED:
    fastapi_app.include_router(_build_drishti_router(DRISHTI_CTX))

# ── Home automation (Drishti, merged into Garuda) ────────────────────────────
# One NIM client for everything that needs a model: the rule compiler, the
# Narada agent and the daily digest. Models are tried in order, so a model NIM
# retires (as nemotron-3-nano was on 2026-09-01) falls through to the next.
try:
    from .garuda_auto.llm import NimChat, NimUnavailable, parse_models
    from .garuda_auto.home import HomeServices
    from .garuda_auto.decision import DecisionEngine, LocalBackend, JevBackend
    from .garuda_auto.agent import HomeAgent
    from .garuda_auto.digest import Digest
    from .garuda_auto.narada_voice import NaradaVoice
    from .garuda_auto.envfile import set_vars as _set_env_vars
    from .home_api import build_home_router
except ImportError:
    from basic_pipelines.garuda_auto.llm import NimChat, NimUnavailable, parse_models
    from basic_pipelines.garuda_auto.home import HomeServices
    from basic_pipelines.garuda_auto.decision import DecisionEngine, LocalBackend, JevBackend
    from basic_pipelines.garuda_auto.agent import HomeAgent
    from basic_pipelines.garuda_auto.digest import Digest
    from basic_pipelines.garuda_auto.narada_voice import NaradaVoice
    from basic_pipelines.garuda_auto.envfile import set_vars as _set_env_vars
    from basic_pipelines.home_api import build_home_router

# Where admin-entered AI keys are persisted. A module global so the tests can
# point it at a temp file: the suite must never rewrite the real .env.
HOME_ENV_PATH = str(Path(__file__).resolve().parent.parent / ".env")

NIM_CHAT = NimChat(
    os.environ.get("NIM_API_KEY", ""),
    parse_models(os.environ.get("NIM_MODEL", ""), os.environ.get("NIM_FALLBACK_MODELS", "")),
)
DRISHTI_CTX.nim.chat = NIM_CHAT

HOME = HomeServices(DRISHTI_CTX, DRISHTI_RUNTIME, DRISHTI_DATA_DIR)
DRISHTI_RUNTIME.context_provider = HOME.context


def _home_presence():
    """True home / False away / None when no phone is registered to watch."""
    return _owner_present if KNOWN_DEVICES else None


def _home_security():
    if _alert_active:
        return "danger"
    if _night_presence_alert_active:
        return "night_presence"
    return "clear"


def _home_email(subject, body):
    """Home notices go to the alert recipients, unless email alerts are off."""
    if MODE_EMAIL_OFF or not (EMAIL_SENDER and EMAIL_SENDER_PASS and EMAIL_RECIPIENTS):
        return
    _send_mail(subject, body)


def _home_modes():
    return {"dnd": MODE_DND, "night": MODE_NIGHT, "idle": MODE_IDLE,
            "emergency": MODE_EMERGENCY, "privacy": MODE_PRIVACY, "email_off": MODE_EMAIL_OFF}


def _home_set_mode(mode, value, actor):
    """The assistant's way into the same switch as POST /api/modes."""
    global MODE_DND
    names = {"dnd": "MODE_DND", "email_off": "MODE_EMAIL_OFF", "idle": "MODE_IDLE",
             "night": "MODE_NIGHT", "emergency": "MODE_EMERGENCY", "privacy": "MODE_PRIVACY"}
    if mode not in names:
        raise ValueError(f"unknown mode: {mode!r}")
    if mode in ADMIN_ONLY_MODES and value and USERS.get(actor, {}).get("role") != "admin":
        raise PermissionError("only an admin can turn this mode on: it silences alerts")
    with _mode_lock:
        globals()[names[mode]] = bool(value)
        if mode == "emergency" and value:
            MODE_DND = False
    save_config()
    log_system_update(f"Mode {mode} set to {bool(value)} by {actor or 'assistant'} (Narada)")
    push_urgent_ws()
    return f"{mode} {'on' if value else 'off'}"


def _home_security_summary():
    return {"alert_active": _alert_active, "night_presence_alert": _night_presence_alert_active,
            "alerts_today": _alert_history.get(datetime.date.today().isoformat(), 0),
            "camera_live": (time.time() - _frame_ts) < 5.0}


HOME.presence_fn = _home_presence
HOME.security_fn = _home_security
HOME.notify_fn = _home_email
HOME.on_change = lambda: push_urgent_ws()

DECISION = DecisionEngine(
    LocalBackend(lambda: [d for d in DRISHTI_CTX.registry.devices if d.get("enabled", True)],
                 lambda: HOME.scenes.scenes),
    JevBackend(os.environ.get("JEV_API_KEY", ""),
               os.environ.get("JEV_BASE_URL", "https://api.typesafe.ai")),
    threshold=float(os.environ.get("DECISION_THRESHOLD", "0.85")),
)
AGENT = HomeAgent(DRISHTI_CTX, HOME, NIM_CHAT, DECISION, modes_fn=_home_modes,
                  set_mode_fn=_home_set_mode, security_fn=_home_security_summary)
def _voice_turn_logged(user, heard, said):
    append_voice_log(f"You said: {heard}", user_name=user)
    append_voice_response(said, user_name=user)


# ElevenLabs does the listening and speaking; _assistant_reply (NIM) decides.
NARADA_VOICE = NaradaVoice(
    os.environ.get("ELEVENLABS_API_KEY", ""),
    os.environ.get("ELEVENLABS_SPEECH_ENGINE_ID", ""),
    reply_fn=lambda text, user, role, scope: _assistant_reply(text, user, role, scope, voice=True),
    on_turn=_voice_turn_logged,
    voice_id=os.environ.get("ELEVENLABS_VOICE_ID", ""),
)
DIGEST = Digest(HOME, NIM_CHAT,
                alerts_fn=lambda: _alert_history.get(datetime.date.today().isoformat(), 0))
HOME.digest_fn = DIGEST.text


def _ai_configure(fields, actor):
    """Apply AI settings now and persist them to .env for the next start."""
    persist = {}
    if fields.get("nim_api_key"):
        NIM_CHAT.configure(api_key=fields["nim_api_key"])
        persist["NIM_API_KEY"] = NIM_CHAT.api_key
    if fields.get("nim_model") is not None or fields.get("nim_fallback_models") is not None:
        primary = fields.get("nim_model") or os.environ.get("NIM_MODEL", "")
        fallbacks = fields.get("nim_fallback_models")
        if fallbacks is None:
            fallbacks = os.environ.get("NIM_FALLBACK_MODELS", "")
        NIM_CHAT.configure(models=parse_models(primary, fallbacks))
        persist["NIM_MODEL"] = primary
        persist["NIM_FALLBACK_MODELS"] = fallbacks
    if fields.get("jev_api_key") is not None:
        DECISION.jev.api_key = fields["jev_api_key"].strip()
        persist["JEV_API_KEY"] = DECISION.jev.api_key
    if fields.get("jev_base_url"):
        DECISION.jev.base_url = fields["jev_base_url"].strip().rstrip("/")
        persist["JEV_BASE_URL"] = DECISION.jev.base_url
    if fields.get("decision_threshold") is not None:
        DECISION.threshold = float(fields["decision_threshold"])
        persist["DECISION_THRESHOLD"] = str(DECISION.threshold)
    for name, value in persist.items():
        os.environ[name] = value
    if persist:
        try:
            _set_env_vars(HOME_ENV_PATH, persist)
        except OSError as exc:
            log_system_update(f"[HOME] AI settings applied but not saved: {exc}")
    log_system_update(f"[HOME] AI settings changed by {actor}: {', '.join(sorted(persist)) or 'none'}")


def _ai_test():
    """One tiny request, so the settings page can say whether the key works."""
    started = time.time()
    try:
        message = NIM_CHAT.chat([{"role": "user", "content": "Reply with the single word: ready"}],
                                max_tokens=200, temperature=0, timeout=30)
    except NimUnavailable as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "model": NIM_CHAT.last_model,
            "latency_s": round(time.time() - started, 2),
            "reply": (message.get("content") or "").strip()[:80]}


fastapi_app.include_router(build_home_router(
    DRISHTI_CTX, HOME, AGENT, DIGEST, session_dep=require_session, admin_dep=require_admin,
    ai_configure=_ai_configure, ai_test=_ai_test))

# ── Pydantic models ──────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str
    remember_me: bool = False

class ModeRequest(BaseModel):
    mode: str   # "dnd","email_off","idle","night","emergency","privacy"
    value: bool

class OTPRequest(BaseModel):
    username: str
    password: str

class VerifyOTPRequest(BaseModel):
    username: str
    otp: str

class ForgotPasswordRequest(BaseModel):
    username: Optional[str] = None   # omittable — endpoint resolves from OTP store
    otp: str
    new_password: str

class SendForgotOTPRequest(BaseModel):
    username: str

class WebRTCOfferRequest(BaseModel):
    sdp: str
    type: str

class ChatRequest(BaseModel):
    message: str = Field(max_length=2000)

# ── Routes ───────────────────────────────────────────────────────────────────

@fastapi_app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if DRISHTI_APP_ENABLED and host == DRISHTI_HOST:
        drishti_index = DRISHTI_DIST / "index.html"
        if drishti_index.is_file():
            return HTMLResponse(drishti_index.read_text())
    html_path = _static_dir / "index.html"
    if html_path.exists():
        product = _product_for_host(host)
        html = html_path.read_text().replace(
            '<html lang="en" data-theme="light">',
            f'<html lang="en" data-theme="light" data-product="{product}">', 1)
        if product == "home":
            html = html.replace("<title>Garuda</title>", "<title>Drishti</title>", 1)
        # Always revalidated: the page names the versioned scripts, so a stale
        # copy of it pins a browser to an old build.
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})
    return HTMLResponse("<h1>Garuda Web</h1><p>garuda_web/index.html not found.</p>")

@fastapi_app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    p = _static_dir / "favicon.ico"
    return FileResponse(str(p), media_type="image/x-icon") if p.exists() else Response(status_code=204)

@fastapi_app.get("/manifest.json")
async def pwa_manifest():
    p = _static_dir / "manifest.json"
    return FileResponse(str(p), media_type="application/manifest+json") if p.exists() else JSONResponse({})

@fastapi_app.get("/sw.js")
async def service_worker():
    p = _static_dir / "sw.js"
    return FileResponse(str(p), media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"}) if p.exists() else Response("", media_type="application/javascript")

@fastapi_app.get("/api/users-public")
async def users_public():
    """Return non-sensitive user info for login screen profile cards."""
    result = []
    for uname, udata in USERS.items():
        if udata.get("role") == "user":
            result.append({
                "username": uname,
                "display_name": udata.get("display_name", uname),
                "box_color": udata.get("box_color", "#1565c0"),
            })
    return result

@fastapi_app.post("/api/login")
async def login(data: LoginRequest, request: Request, response: Response):
    ip = _get_client_ip(request)
    if _is_login_locked(ip):
        raise HTTPException(429, "Too many failed attempts. Try again later.")
    if not _check_rate_limit(request):
        raise HTTPException(429, "Too many requests. Try again later.")
    u = data.username.strip()[:64]
    p = data.password.strip()[:256]
    user = USERS.get(u)
    # PBKDF2 at 600k rounds takes a noticeable fraction of a second on the Pi;
    # on the event loop it froze the state socket and every camera stream for
    # that long. The same work is done for an unknown name, so the answer
    # takes as long either way.
    stored = user["password"] if user else _DUMMY_PASSWORD_HASH
    password_ok = await asyncio.to_thread(_verify_password, p, stored)
    if user is None or not password_ok:
        _record_login_failure(ip)
        raise HTTPException(401, "Invalid username or password.")
    if user.get("role") == "admin":
        # Only said once the password has been proved: before, any name could
        # be tested for "is this an admin?" without knowing anything.
        # Test-only bypass for the P1-4 evaluation harness. Set GARUDA_EVAL_OTP_BYPASS=1
        # in the environment before starting the server to allow a named service admin
        # (GARUDA_EVAL_SERVICE_ADMIN) to sign in via /api/login without the email OTP.
        _bypass = os.environ.get("GARUDA_EVAL_OTP_BYPASS", "") == "1"
        _allowed = os.environ.get("GARUDA_EVAL_SERVICE_ADMIN", "")
        if not (_bypass and _allowed and u == _allowed):
            raise HTTPException(403, "Admin accounts must sign in via the Admin Access flow.")
    # Auto-migrate plaintext passwords to hashed
    if not user["password"].startswith("pbkdf2:"):
        user["password"] = await asyncio.to_thread(_hash_password, p)
    _clear_login_failure(ip)
    access_token = create_session(u)
    refresh_token = create_refresh_token(u)
    _set_session_cookies(request, response, access_token, refresh_token,
                         persistent=bool(data.remember_me))
    log_system_update(f"Login: {u}")
    _remember_user_activity(u, "logins", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    await asyncio.to_thread(save_users)
    body = {
        "role": user["role"],
        "username": u,
        "display_name": user.get("display_name", u),
        "box_color": user.get("box_color", "#1565c0"),
        "token": access_token,   # for cross-origin clients that can't use cookies
    }
    if _is_cross_site(request):
        body["refresh_token"] = refresh_token
    return body

@fastapi_app.get("/api/session")
async def session_info(session=Depends(require_session)):
    """Return current session user info — used to restore session on page refresh."""
    u = session["username"]
    return {
        "role": session["role"],
        "username": u,
        "display_name": USERS.get(u, {}).get("display_name", u),
        "box_color": USERS.get(u, {}).get("box_color", "#1565c0"),
        "logs_unlocked": session.get("logs_unlocked", False),
    }

@fastapi_app.post("/api/logout")
async def logout(request: Request, response: Response):
    token = request.headers.get("X-Garuda-Token") or request.cookies.get("garuda_session")
    if token:
        _sessions.pop(token, None)
    # Also revoke the refresh token so stolen refresh tokens can't mint new sessions
    for refresh in (request.cookies.get("garuda_refresh"), request.headers.get("X-Garuda-Refresh")):
        if refresh:
            _revoke_refresh(refresh)
    response.delete_cookie("garuda_session")
    response.delete_cookie("garuda_refresh", path="/api/refresh")
    return {"ok": True}

@fastapi_app.post("/api/refresh")
async def refresh_session(request: Request, response: Response):
    """Exchange a valid refresh token for a new 15-minute access token."""
    # The header is for a front end hosted on another site (Vercel): its
    # browser never sends our SameSite cookie back, so it was signed out every
    # 15 minutes when the access token ran out.
    refresh = request.cookies.get("garuda_refresh") or request.headers.get("X-Garuda-Refresh")
    if not refresh:
        raise HTTPException(401, "No refresh token.")
    rs = get_refresh_token(refresh)
    if not rs:
        raise HTTPException(401, "Refresh token expired or invalid. Please log in again.")
    u = rs["username"]
    if u not in USERS:
        _revoke_refresh(refresh)
        raise HTTPException(401, "User no longer exists.")
    access_token = create_session(u)
    _set_session_cookies(request, response, access_token)
    return {
        "token": access_token,
        "role": USERS[u]["role"],
        "username": u,
    }

@fastapi_app.post("/api/admin/send-otp")
async def admin_send_otp(data: OTPRequest, request: Request, response: Response):
    """Admin login step 1: verify credentials, send OTP."""
    ip = _get_client_ip(request)
    if _is_login_locked(ip):
        raise HTTPException(429, "Too many failed attempts. Try again later.")
    if not _check_rate_limit(request):
        raise HTTPException(429, "Too many requests. Try again later.")
    global ADMIN_OTP, _admin_otp_user, _admin_otp_ts, _admin_otp_attempts
    u = data.username.strip()[:64]
    p = data.password.strip()[:256]
    user = USERS.get(u)
    stored = user["password"] if user else _DUMMY_PASSWORD_HASH
    password_ok = await asyncio.to_thread(_verify_password, p, stored)
    if user is None or not password_ok or user.get("role") != "admin":
        _record_login_failure(ip)
        raise HTTPException(401, "Invalid admin credentials.")
    # Auto-migrate plaintext passwords to hashed
    if not user["password"].startswith("pbkdf2:"):
        user["password"] = await asyncio.to_thread(_hash_password, p)
        await asyncio.to_thread(save_users)
    ADMIN_OTP = generate_otp_code(6)
    _admin_otp_user = u          # store server-side so step 2 cannot be hijacked
    _admin_otp_ts = time.time()  # for expiry check
    _admin_otp_attempts = 0      # a new code gets its own three tries
    dest = EMAIL_RECIPIENTS[0] if EMAIL_RECIPIENTS else EMAIL_SENDER
    # SMTP can take ten seconds; off the event loop so nothing else waits on it.
    ok, err = await asyncio.to_thread(send_otp_via_email, dest, ADMIN_OTP)
    if not ok:
        return {"ok": False, "error": err}
    return {"ok": True}

@fastapi_app.post("/api/admin/verify-otp")
async def admin_verify_otp(data: VerifyOTPRequest, request: Request, response: Response):
    """Admin login step 2: verify OTP, issue session."""
    if not _check_rate_limit(request):
        raise HTTPException(429, "Too many requests. Try again later.")
    global ADMIN_OTP, _admin_otp_user, _admin_otp_ts, _admin_otp_attempts
    if not ADMIN_OTP or not _admin_otp_user:
        raise HTTPException(401, "No OTP pending. Please restart login.")
    if time.time() - _admin_otp_ts > 300:
        ADMIN_OTP = None; _admin_otp_user = None; _admin_otp_attempts = 0
        raise HTTPException(401, "OTP expired. Please request a new one.")
    if _admin_otp_attempts >= 3:
        ADMIN_OTP = None; _admin_otp_user = None; _admin_otp_attempts = 0
        raise HTTPException(401, "Too many incorrect attempts. Please restart login.")
    if not hmac.compare_digest(data.otp.strip().encode(), str(ADMIN_OTP).encode()):
        _admin_otp_attempts += 1
        raise HTTPException(401, "Invalid OTP.")
    u = _admin_otp_user   # use server-stored username, not client-supplied
    ADMIN_OTP = None; _admin_otp_user = None; _admin_otp_ts = 0; _admin_otp_attempts = 0
    if u not in USERS or USERS[u]["role"] != "admin":
        raise HTTPException(401, "Account not authorised.")
    ip = _get_client_ip(request)
    _clear_login_failure(ip)
    access_token = create_session(u)
    refresh_token = create_refresh_token(u)
    _set_session_cookies(request, response, access_token, refresh_token)
    log_system_update(f"Admin login: {u}")
    body = {
        "role": "admin",
        "username": u,
        "display_name": USERS[u].get("display_name", u),
        "token": access_token,   # for cross-origin clients
    }
    if _is_cross_site(request):
        body["refresh_token"] = refresh_token
    return body

@fastapi_app.post("/api/forgot/send-otp")
async def forgot_send_otp(data: SendForgotOTPRequest, request: Request):
    if not _check_rate_limit(request):
        raise HTTPException(429, "Too many requests. Try again later.")
    u = data.username.strip()
    # Always return the same response regardless of whether user exists (anti-enumeration)
    if u not in USERS:
        return {"ok": True}
    otp = generate_otp_code(6)
    _forgot_otp_store[u] = {"otp": otp, "ts": time.time(), "attempts": 0}
    global USER_FORGOT_OTP; USER_FORGOT_OTP = otp   # test-facing alias
    # Send to the user's own email if stored, else fall back to admin recipient
    dest = USERS[u].get("email") or (EMAIL_RECIPIENTS[0] if EMAIL_RECIPIENTS else EMAIL_SENDER)
    ok, err = await asyncio.to_thread(send_otp_via_email, dest, otp)
    if not ok:
        _forgot_otp_store.pop(u, None)
        return {"ok": False, "error": err}
    return {"ok": True}

@fastapi_app.post("/api/forgot/reset")
async def forgot_reset(data: ForgotPasswordRequest, request: Request):
    global USER_FORGOT_OTP
    ip = _get_client_ip(request)
    if _is_login_locked(ip):
        raise HTTPException(429, "Too many failed attempts. Try again later.")
    if not _check_rate_limit(request):
        raise HTTPException(429, "Too many requests. Try again later.")
    # username optional: if omitted, find user by matching OTP across store
    if data.username:
        u = data.username.strip()
    else:
        guess = data.otp.strip()
        u = next((k for k, v in list(_forgot_otp_store.items())
                  if hmac.compare_digest(str(v.get("otp", "")).encode(), guess.encode())), None)
        if not u:
            # A guess with no username used to cost nothing: no attempt was
            # counted against anyone. It now counts against the caller.
            if _forgot_otp_store:
                _record_login_failure(ip)
            raise HTTPException(401, "No OTP pending.")
    state = _forgot_otp_store.get(u)
    if not state:
        USER_FORGOT_OTP = None
        raise HTTPException(401, "No OTP pending.")
    if time.time() - state["ts"] > 300:
        _forgot_otp_store.pop(u, None)
        USER_FORGOT_OTP = None
        raise HTTPException(401, "OTP expired. Please request a new one.")
    if state["attempts"] >= 3:
        _forgot_otp_store.pop(u, None)
        USER_FORGOT_OTP = None
        raise HTTPException(401, "Too many incorrect attempts. Please request a new OTP.")
    if not hmac.compare_digest(data.otp.strip().encode(), str(state["otp"]).encode()):
        state["attempts"] += 1
        _record_login_failure(ip)
        if state["attempts"] >= 3:
            _forgot_otp_store.pop(u, None)
            USER_FORGOT_OTP = None
        raise HTTPException(401, "Invalid OTP.")
    err = _validate_password_strength(data.new_password)
    if err:
        raise HTTPException(400, err)
    if u not in USERS:
        raise HTTPException(404, "User not found.")
    USERS[u]["password"] = await asyncio.to_thread(_hash_password, data.new_password.strip())
    _invalidate_user_sessions(u)
    await asyncio.to_thread(save_users)
    log_system_update(f"Password reset for {u}.")
    _forgot_otp_store.pop(u, None)
    USER_FORGOT_OTP = None
    return {"ok": True}

@fastapi_app.get("/api/state")
async def get_state(session=Depends(require_session)):
    payload = await asyncio.to_thread(get_state_dict)
    return _state_for_role(payload, session["role"])

@fastapi_app.get("/api/cascade_metrics")
async def get_cascade_metrics(session=Depends(require_session)):
    return _cascade_metrics.snapshot()

def _require_eval_token(request: Request):
    """Token-gated access for the P1-4 evaluation harness."""
    expected = os.environ.get("GARUDA_EVAL_TOKEN", "")
    if not expected:
        raise HTTPException(404, "Not found")
    got = request.headers.get("X-Eval-Token", "")
    if not hmac.compare_digest(got.encode(), expected.encode()):
        raise HTTPException(403, "Bad eval token")

class EvalInjectRequest(BaseModel):
    label: str = "Knife"
    confidence: float = 0.92
    email: bool = False

@fastapi_app.post("/api/eval/inject_danger")
async def eval_inject_danger(data: EvalInjectRequest, request: Request):
    _require_eval_token(request)
    t_req = time.time()
    log_scissors_detection(data.label)
    log_system_update(f"[EVAL_INJECT] {data.label} conf={data.confidence:.2f}")
    trigger_software_alert()
    if data.email:
        try:
            await asyncio.to_thread(send_email_alert)
        except Exception as e:
            log_system_update(f"[EVAL_INJECT] email failed: {e}")
    return {"ok": True, "t_request": t_req, "t_alert": time.time(),
            "latency_ms": round((time.time() - t_req) * 1000, 2),
            "label": data.label, "confidence": data.confidence}

class EvalTagRequest(BaseModel):
    tag: str
    note: str = ""

@fastapi_app.post("/api/eval/tag")
async def eval_tag(data: EvalTagRequest, request: Request):
    _require_eval_token(request)
    msg = f"[EVAL_TAG] {data.tag}"
    if data.note:
        msg += f" — {data.note}"
    log_system_update(msg)
    return {"ok": True, "t": time.time(), "tag": data.tag, "note": data.note}

@fastapi_app.get("/api/eval/fps_probe")
async def eval_fps_probe(request: Request):
    _require_eval_token(request)
    with _mode_lock:
        modes = {
            "dnd": MODE_DND, "email_off": MODE_EMAIL_OFF,
            "idle": MODE_IDLE, "night": MODE_NIGHT,
            "emergency": MODE_EMERGENCY, "privacy": MODE_PRIVACY,
        }
    cm = _cascade_metrics.snapshot() if _cascade_metrics else {}
    return {
        "t": time.time(),
        "uptime": time.time() - _app_start_time,
        "total_frames": _total_frames,
        "modes": modes,
        "cascade": cm,
        "alert_active": _alert_active,
    }

@fastapi_app.post("/api/chat")
async def chat(data: ChatRequest, request: Request, session=Depends(require_session)):
    msg = data.message.strip()
    if not msg:
        raise HTTPException(400, "Empty message")
    scope = _product_for_host(request.headers.get("host"))
    result = await anyio.to_thread.run_sync(
        lambda: _assistant_reply(msg, session["username"], session["role"], scope))
    return {"response": result["reply"], "lane": result.get("lane"),
            "actions": result.get("actions", []), "proposal": result.get("proposal"),
            "route": result.get("route"), "model": result.get("model")}


def _assistant_reply(msg, user="", role="user", scope="home", voice=False):
    """Narada's one brain for chat and voice.

    Phrases the owner taught on the Commands page return their fixed reply
    (they never change anything). Everything else goes to the NIM agent;
    without NIM nothing is changed and Narada says why.
    """
    lower = msg.lower()
    for phrase, resp in CUSTOM_VOICE_COMMANDS.items():
        if phrase in lower:
            return {"reply": resp, "lane": "custom", "actions": []}
    return AGENT.handle(msg, user=user, role=role, scope=scope, voice=voice)


@fastapi_app.post("/api/chat/stream")
async def chat_stream(data: ChatRequest, request: Request, session=Depends(require_session)):
    """SSE chat: the NIM agent's reply, replayed word by word."""
    msg = data.message.strip()
    if not msg:
        raise HTTPException(400, "Empty message")

    loop  = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()
    user, role = session["username"], session["role"]
    scope = _product_for_host(request.headers.get("host"))

    def _agent_worker():
        # The agent answers in one piece after its tool calls, so the reply is
        # replayed word by word to keep the chat's typing feel.
        try:
            result = _assistant_reply(msg, user, role, scope)
        except Exception as exc:
            result = {"reply": f"Something went wrong: {type(exc).__name__}", "actions": []}
        meta = {k: result.get(k) for k in ("lane", "actions", "proposal", "model")}
        loop.call_soon_threadsafe(queue.put_nowait, ("meta", meta))
        for word in re.findall(r"\S+\s*", result["reply"]):
            loop.call_soon_threadsafe(queue.put_nowait, ("token", word))
        loop.call_soon_threadsafe(queue.put_nowait, ("done", None))

    threading.Thread(target=_agent_worker, daemon=True).start()

    async def generate():
        yield f"data: {json.dumps({'type': 'start'})}\n\n"
        while True:
            try:
                kind, payload_val = await asyncio.wait_for(queue.get(), timeout=90)
            except asyncio.TimeoutError:
                break
            if kind == "done":
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                break
            if kind == "meta":
                yield f"data: {json.dumps({'type': 'meta', **payload_val})}\n\n"
                continue
            yield f"data: {json.dumps({'type': 'token', 'text': payload_val})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

ADMIN_ONLY_MODES = frozenset({"idle", "email_off"})

@fastapi_app.post("/api/modes")
async def set_mode(data: ModeRequest, session=Depends(require_session)):
    global MODE_DND, MODE_EMAIL_OFF, MODE_IDLE, MODE_NIGHT, MODE_EMERGENCY, MODE_PRIVACY
    mode_map = {
        "dnd": "MODE_DND", "email_off": "MODE_EMAIL_OFF",
        "idle": "MODE_IDLE", "night": "MODE_NIGHT",
        "emergency": "MODE_EMERGENCY", "privacy": "MODE_PRIVACY",
    }
    if data.mode not in mode_map:
        raise HTTPException(400, f"Unknown mode: {data.mode}")
    if data.mode in ADMIN_ONLY_MODES and data.value and session["role"] != "admin":
        # Idle and Email Off stop the system telling anyone about a threat.
        # Any signed-in profile could switch them on; switching them back off
        # (the safe direction) stays open to everyone.
        raise HTTPException(403, "Only an admin can turn this mode on: it silences alerts.")
    with _mode_lock:
        globals()[mode_map[data.mode]] = data.value
        if data.mode == "emergency" and data.value:
            MODE_DND = False
    await _async_save_config()
    log_system_update(f"Mode {data.mode} set to {data.value} by {session['username']}")
    push_urgent_ws()
    return {"ok": True, "modes": get_state_dict()["modes"]}

# The native (Swift) app posts to /api/set-mode, which never existed here: its
# mode switches failed with 404. Same handler, both names.
fastapi_app.post("/api/set-mode", include_in_schema=False)(set_mode)

fastapi_app.include_router(build_users_router(sys.modules[__name__]))

fastapi_app.include_router(build_config_router(sys.modules[__name__]))

def _do_presence_check():
    """Blocking presence check — run in thread executor from async endpoints."""
    global _owner_present, _owner_last_seen
    subnet = _get_local_subnet()
    if subnet:
        _probe_subnet_for_arp(subnet)
        time.sleep(2)
    found = _check_device_presence()
    if found:
        _owner_last_seen = time.time()
        if not _owner_present:
            _owner_present = True
            seen = _present_device() or {}
            dev, mac = seen.get("name", "Unknown"), _device_mac(seen)
            _append_presence_log("arrived", dev, mac)
            log_system_update(f"[OWNER] {dev} arrived (manual refresh).")
    elif _owner_present and (time.time() - _owner_last_seen > OWNER_AWAY_GRACE):
        _owner_present = False
        dev = next((d.get("name", "Unknown") for d in KNOWN_DEVICES), "Unknown")
        _append_presence_log("left", dev, "")
        log_system_update(f"[OWNER] {dev} away (manual refresh — device not found).")

fastapi_app.include_router(build_presence_router(sys.modules[__name__]))

@fastapi_app.get("/api/logs")
async def get_logs(session=Depends(require_logs)):
    return {
        "system_log": system_updates_log,
        "voice_log": voice_assistant_log,
        "voice_responses": voice_responses,
        "presence_log": _presence_log[-200:],
        "detection_log": _detection_log[-200:],
    }

@fastapi_app.get("/api/logs/download")
async def download_logs(session=Depends(require_logs)):
    """Return all permanent logs as a single combined text file for download."""
    # Up to 30 MB of files are read here: on a worker thread, not the loop.
    content = await asyncio.to_thread(_combined_log_text)
    fname = f"garuda-full-log-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


def _combined_log_text() -> str:
    _do_flush_logs()   # include lines still waiting in the write buffer
    parts = []
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts.append(f"# Garuda Security System — Full Log Export")
    parts.append(f"# Generated: {stamp}")
    parts.append("")

    for title, filepath in [
        ("SYSTEM LOG", PERM_SYSTEM_LOG),
        ("VOICE LOG",  PERM_VOICE_LOG),
        ("DETECTION LOG", PERM_DETECTION_LOG),
        ("PRESENCE LOG", PRESENCE_LOG_FILE),
    ]:
        parts.append(f"{'='*60}")
        parts.append(f"  {title}")
        parts.append(f"{'='*60}")
        try:
            if filepath.endswith(".json"):
                # presence_log is JSON array
                if os.path.exists(filepath):
                    with open(filepath, encoding="utf-8") as f:
                        data = json.load(f)
                    for e in data:
                        parts.append(f"[{e.get('ts','')}] {e.get('event','').upper():8s} {e.get('device','')} ({e.get('mac','')})")
                else:
                    parts.append("(no entries)")
            else:
                if os.path.exists(filepath):
                    with open(filepath, encoding="utf-8", errors="replace") as f:
                        content = f.read().strip()
                    parts.append(content if content else "(no entries)")
                else:
                    parts.append("(no entries)")
        except Exception as ex:
            parts.append(f"(error reading log: {ex})")
        parts.append("")

    return "\n".join(parts)

##############################################################################
# MASTER KEY ENDPOINTS
##############################################################################

_MASTER_OTP_TTL = 300
_master_otp_ts = 0.0
_master_otp_attempts = 0

fastapi_app.include_router(build_master_keys_router(sys.modules[__name__]))

@fastapi_app.get("/api/heartbeat")
async def heartbeat(request: Request, key: Optional[str] = None):
    """Health check for external monitors (UptimeRobot etc.).
    Accepts an optional ?key= query param or X-Heartbeat-Key header to guard
    the dead-man reset. Without a key the endpoint still returns health data
    but does NOT reset the deadman timer (prevents unauthenticated suppression).
    """
    global _last_heartbeat, _deadman_alert_sent, _heartbeat_ever
    _HEARTBEAT_KEY = os.environ.get("HEARTBEAT_KEY", "")
    provided = key or request.headers.get("X-Heartbeat-Key", "")
    # Only reset dead-man's switch if key matches (or no key configured)
    if not _HEARTBEAT_KEY or hmac.compare_digest(str(provided).encode(), _HEARTBEAT_KEY.encode()):
        _last_heartbeat = time.time()
        _deadman_alert_sent = False
        _heartbeat_ever = True
    return {"ok": True, "uptime": int(time.time() - _app_start_time)}

@fastapi_app.post("/api/emergency-stop")
async def emergency_stop(session=Depends(require_admin)):
    log_system_update(f"Emergency stop by {session['username']}.")
    threading.Thread(target=stop_app, daemon=True).start()
    return {"ok": True}

# ── Offline event queue endpoints ─────────────────────────────────────────────
fastapi_app.include_router(build_events_router(sys.modules[__name__]))

# ── Feedback ─────────────────────────────────────────────────────────────────
_feedback_lock = threading.Lock()
_FEEDBACK_MAX = 2000

def _load_feedback() -> list:
    entries = _safe_json_load(FEEDBACK_FILE, None)
    if isinstance(entries, list):
        return entries
    backup_entries = _safe_json_load(FEEDBACK_BACKUP_FILE, [])
    if isinstance(backup_entries, list):
        if backup_entries:
            try:
                _atomic_json_write(FEEDBACK_FILE, backup_entries)
            except Exception as e:
                log_system_update(f"Failed to restore feedback from backup: {e}")
        return backup_entries
    return []

def _save_feedback(entries: list):
    try:
        _atomic_json_write(FEEDBACK_FILE, entries)
        _atomic_json_write(FEEDBACK_BACKUP_FILE, entries)
    except Exception as e:
        log_system_update(f"Failed to save feedback: {e}")

fastapi_app.include_router(build_feedback_router(sys.modules[__name__]))

# ── MJPEG stream ─────────────────────────────────────────────────────────────
# Uses _frame_seq to detect new frames only — avoids re-sending duplicate
# frames and keeps per-client CPU near zero when the pipeline is idle.
_STREAM_RECHECK_S = 5.0


async def mjpeg_frames(request: Request):
    """The MJPEG body, shared by Garuda's /stream and Drishti's.

    Authentication is the caller's job — the two endpoints check different
    cookies. This only produces frames.
    """
    # The pipeline already encodes each published frame once (FramePublisher).
    # This used to encode the raw frame again, per viewer, on the event loop:
    # 15 JPEG encodes a second for every open camera view, each one stalling
    # the state socket and every other request while it ran.
    last_seq = -1
    still_valid = getattr(request.state, "stream_valid", None)
    next_check = time.time() + _STREAM_RECHECK_S
    while True:
        if await request.is_disconnected():
            break
        if still_valid is not None and time.time() >= next_check:
            if not still_valid():
                break                      # signed out, or the account is gone
            next_check = time.time() + _STREAM_RECHECK_S
        with _frame_lock:
            seq = _frame_seq
            jpeg = _frame_buffer if seq != last_seq else None
        if jpeg is not None:
            last_seq = seq
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n")
        else:
            await asyncio.sleep(0.02)


DRISHTI_CTX.frame_source = mjpeg_frames


@fastapi_app.get("/stream")
async def mjpeg_stream(request: Request, token: Optional[str] = None):
    # Authenticate via cookie or ?token= query param
    session_token = request.cookies.get("garuda_session") or token
    session = get_session(session_token)
    if not session:
        raise HTTPException(401, "Not authenticated")
    # The stream outlives the 15-minute token it opened with; it ends when the
    # person has no live session left (signed out, password changed, deleted).
    username = session["username"]
    request.state.stream_valid = lambda: _user_signed_in(username)
    return StreamingResponse(
        mjpeg_frames(request),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )

# ── Snapshot ──────────────────────────────────────────────────────────────────
@fastapi_app.get("/api/snapshot")
async def snapshot(request: Request, token: Optional[str] = None):
    session_token = request.cookies.get("garuda_session") or token
    if not get_session(session_token):
        raise HTTPException(401, "Not authenticated")
    with _frame_lock:
        raw = _frame_raw
    if raw is None:
        raise HTTPException(503, "No frame available yet")
    ok, jpeg = await asyncio.to_thread(cv2.imencode, '.jpg', raw, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        raise HTTPException(500, "Could not encode the frame")
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return Response(
        content=jpeg.tobytes(), media_type="image/jpeg",
        headers={"Content-Disposition": f'attachment; filename="garuda_{ts}.jpg"'}
    )

# ── Clip recording ────────────────────────────────────────────────────────────
_CLIPS_KEEP = 50

def _prune_old_clips(keep: int = _CLIPS_KEEP):
    """Keep the newest `keep` clips; nothing ever removed them before."""
    try:
        clips = sorted((_BASE / "system_logs").glob("clip_*.mp4*"), key=lambda p: p.stat().st_mtime)
        for old in clips[:-keep]:
            old.unlink(missing_ok=True)
    except Exception as exc:
        log_system_update(f"Clip cleanup failed: {exc}")

@fastapi_app.post("/api/clip/start")
async def clip_start(session=Depends(require_session)):
    global _clip_writer, _clip_start_time, _clip_path
    # Fast check — avoid I/O if already recording
    with _clip_lock:
        if _clip_writer is not None:
            return {"ok": True, "already_recording": True, "path": _clip_path}
    # Read frame dims and create VideoWriter OUTSIDE the lock (file I/O must not block event loop)
    with _frame_lock:
        raw = _frame_raw
    if raw is None:
        raise HTTPException(503, "No frame available yet")
    h, w = raw.shape[:2]
    ts = int(time.time())
    new_path = str(_BASE / "system_logs" / f"clip_{ts}.mp4")
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    writer = await asyncio.to_thread(cv2.VideoWriter, new_path, fourcc, 15.0, (w, h))
    if not writer.isOpened():
        writer.release()
        raise HTTPException(500, "Could not start recording (disk full or codec missing).")
    await asyncio.to_thread(_prune_old_clips)
    # Assign atomically — re-check in case a concurrent request beat us
    with _clip_lock:
        if _clip_writer is not None:
            writer.release()
            return {"ok": True, "already_recording": True, "path": _clip_path}
        _clip_writer = writer
        _clip_path = new_path
        _clip_start_time = time.time()
    log_system_update(f"Clip recording started by {session['username']}.")
    return {"ok": True, "path": _clip_path}

@fastapi_app.post("/api/clip/stop")
async def clip_stop(session=Depends(require_session)):
    global _clip_writer, _clip_path
    with _clip_lock:
        if _clip_writer is None:
            return {"ok": True, "was_recording": False}
        _clip_writer.release()
        _clip_writer = None
        path = _clip_path
    log_system_update(f"Clip saved: {path}")
    threading.Thread(target=exfiltrate_clip, args=(path,), daemon=True).start()
    return {"ok": True, "path": path}

# ── WebRTC offer/answer ───────────────────────────────────────────────────────
@fastapi_app.post("/webrtc/offer")
async def webrtc_offer(data: WebRTCOfferRequest, session=Depends(require_session)):
    if not _WEBRTC_AVAILABLE:
        raise HTTPException(501, "aiortc not installed")
    if data.type != "offer" or len(data.sdp) > 20000:
        raise HTTPException(400, "Invalid offer")
    # Each connection runs its own H.264 encoder; without a ceiling a signed-in
    # client could open them until the Pi had nothing left for detection.
    if len(_pc_set) >= _MAX_PEER_CONNECTIONS:
        raise HTTPException(503, "Too many live video connections. Close one and try again.")
    pc = RTCPeerConnection()
    _pc_set.add(pc)

    @pc.on("connectionstatechange")
    async def _on_state():
        if pc.connectionState in ("failed", "closed", "disconnected"):
            await pc.close()
            _pc_set.discard(pc)

    try:
        pc.addTrack(GarudaVideoTrack())
        offer = RTCSessionDescription(sdp=data.sdp, type=data.type)
        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        # Wait for ICE gathering to complete, but not for ever: with no route
        # out this loop never ended and the request (and the connection) hung.
        deadline = time.time() + 10
        while pc.iceGatheringState != "complete":
            if time.time() > deadline:
                raise HTTPException(504, "WebRTC negotiation timed out")
            await asyncio.sleep(0.1)
    except Exception as exc:
        # A bad offer used to leave the half-built connection in _pc_set for good.
        _pc_set.discard(pc)
        try:
            await pc.close()
        except Exception:
            pass
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(400, f"Could not negotiate video: {type(exc).__name__}")

    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}

# ── Narada voice (ElevenLabs Speech Engine) ──────────────────────────────────
@fastapi_app.post("/api/narada/voice/token")
async def narada_voice_token(request: Request, session=Depends(require_session)):
    """A one-conversation token for the browser; binds it to this user."""
    if not NARADA_VOICE.configured:
        raise HTTPException(503, "Voice is not set up yet: an admin needs to run "
                                 "scripts/setup_narada_voice.py.")
    scope = _product_for_host(request.headers.get("host"))
    try:
        return await anyio.to_thread.run_sync(
            lambda: NARADA_VOICE.issue_token(session["username"], session["role"], scope))
    except Exception as exc:
        log_system_update(f"Narada voice token failed: {type(exc).__name__}")
        raise HTTPException(502, "Could not reach the voice service. Try again in a moment.")


@fastapi_app.get("/api/narada/info")
async def narada_info(session=Depends(require_session)):
    """What the "i" panel on the Narada page shows: models and usage."""
    nim = NIM_CHAT.status()
    voice = await anyio.to_thread.run_sync(NARADA_VOICE.info)
    return {"nim": {k: nim.get(k) for k in ("configured", "models", "last_model",
                                            "last_latency_s", "calls", "tokens_used")},
            "voice": voice}


@fastapi_app.websocket("/ws/narada-voice")
async def narada_voice_ws(websocket: WebSocket):
    """ElevenLabs connects here with each conversation's transcripts."""
    if not NARADA_VOICE.verify(websocket.headers):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        await NARADA_VOICE.serve(websocket)
    except WebSocketDisconnect:
        pass


_WS_CONNECT_LIMIT = 60   # socket opens per client address per rate window

def _ws_connect_allowed(websocket) -> bool:
    """Per-client limit on opening sockets.

    This used websocket.client.host, which behind the tunnel is 127.0.0.1 for
    everybody, and the same 30-a-minute bucket as anonymous HTTP: every
    phone and laptop in the house shared one small allowance, and a browser
    reconnecting in a loop could lock all of them out (close code 4029).
    """
    ip = _get_client_ip(websocket)
    now = time.time()
    stamps = _rate_store[f"ws:{ip}"]
    stamps[:] = [t for t in stamps if now - t < _RATE_WINDOW]
    if len(stamps) >= _WS_CONNECT_LIMIT:
        return False
    stamps.append(now)
    return True


# ── WebSocket binary JPEG stream (CF Tunnel fallback) ────────────────────────
@fastapi_app.websocket("/ws/stream")
async def ws_stream(websocket: WebSocket, token: Optional[str] = None):
    """Streams JPEG frames as binary WebSocket messages (~same as MJPEG but WS).
    Works through Cloudflare Tunnel (unlike raw UDP WebRTC)."""
    # Rate-limit WebSocket connections per IP (re-use the global _rate_store)
    if not _ws_connect_allowed(websocket):
        await websocket.close(code=4029)
        return
    token = websocket.cookies.get("garuda_session") or token
    session = get_session(token)
    if not session:
        await websocket.close(code=4001)
        return
    username = session["username"]
    await websocket.accept()
    last_seq = -1
    next_check = time.time() + _STREAM_RECHECK_S
    try:
        while True:
            if time.time() >= next_check:
                if not _user_signed_in(username):
                    await websocket.close(code=4001)
                    return
                next_check = time.time() + _STREAM_RECHECK_S
            with _frame_lock:
                seq   = _frame_seq
                frame = _frame_buffer if seq != last_seq else None
            if frame is not None:
                last_seq = seq
                await websocket.send_bytes(frame)
            else:
                await asyncio.sleep(0.02)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log_system_update(f"[STREAM] WS stream error: {type(e).__name__}")

# ── WebSocket broadcaster (event-driven) ─────────────────────────────────────
# Waits on _ws_trigger asyncio.Event with a 2s timeout (heartbeat).
# push_urgent_ws() sets the event from any thread → immediate broadcast.
# Compute state ONCE per tick and fan-out via asyncio.gather — O(1) in CPU.

# What a non-admin does not need pushed to their browser every two seconds.
_ADMIN_ONLY_STATE = ("known_devices", "cpu_cores", "voice_log", "voice_responses")
_ADMIN_ONLY_LOG_TAGS = ("[SECURITY]", "Login", "login", "Master key", "master key",
                        "Password", "User added", "User deleted", "User updated")

def _state_for_role(payload: dict, role: str) -> dict:
    """The state push, trimmed for a non-admin viewer.

    Everyone got the admin's view: registered phone MAC addresses, who signed
    in from where, lockout notices with client addresses. The dashboard for a
    'user' shows none of that, so it is not sent.
    """
    if role == "admin":
        return payload
    slim = {k: v for k, v in payload.items() if k not in _ADMIN_ONLY_STATE}
    slim["system_log"] = [line for line in payload.get("system_log", [])
                          if not any(tag in line for tag in _ADMIN_ONLY_LOG_TAGS)]
    return slim


async def _ws_send(ws, payload):
    # One stalled phone must not hold the push to everyone else.
    await asyncio.wait_for(ws.send_json(payload), timeout=5.0)


async def _ws_broadcaster():
    """Background task: push state immediately on events, or every 2s as heartbeat."""
    _prune_counter = 0
    _maintenance_counter = 0
    while True:
        try:
            await asyncio.wait_for(_ws_trigger.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass
        _ws_trigger.clear()
        # Anything raised in here used to end this task for good: no client
        # got another update until the service was restarted. One bad tick is
        # now logged and the next one runs.
        try:
            if _refresh_dirty:
                await asyncio.to_thread(_save_refresh_tokens)
            # Prune expired sessions every ~5 minutes (150 ticks × 2s)
            _prune_counter += 1
            if _prune_counter >= 150:
                _prune_counter = 0
                _prune_expired_sessions()
                _prune_rate_state()
                _maintenance_counter += 1
                if _maintenance_counter >= 12:        # about hourly
                    _maintenance_counter = 0
                    await asyncio.to_thread(prune_synced_events)
            # psutil, the event database and the home summary all touch the
            # disk: built on a worker thread so the loop keeps serving.
            payload = await asyncio.to_thread(get_state_dict)   # always run — handles alert expiry even without clients
            if not _ws_clients:
                continue
            clients = list(_ws_clients.items())
            # Connections whose owner has signed out everywhere are closed.
            stale = [ws for ws, meta in clients if not _user_signed_in(meta["username"])]
            for ws in stale:
                _ws_clients.pop(ws, None)
                try:
                    await ws.close(code=4001)
                except Exception:
                    pass
            clients = [(ws, meta) for ws, meta in clients if ws not in stale]
            user_payload = None
            sends = []
            for ws, meta in clients:
                if meta["role"] == "admin":
                    sends.append(_ws_send(ws, payload))
                else:
                    if user_payload is None:
                        user_payload = _state_for_role(payload, "user")
                    sends.append(_ws_send(ws, user_payload))
            results = await asyncio.gather(*sends, return_exceptions=True)
            for (ws, _meta), result in zip(clients, results):
                if isinstance(result, Exception):
                    _ws_clients.pop(ws, None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_system_update(f"[WS] broadcast failed: {type(exc).__name__}: {exc}")


@fastapi_app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: Optional[str] = None):
    # Rate-limit WebSocket connections per IP
    if not _ws_connect_allowed(websocket):
        await websocket.close(code=4029)
        return
    # Accept token from cookie (same-origin) or query param (cross-origin)
    token = websocket.cookies.get("garuda_session") or token
    session = get_session(token)
    if not session:
        await websocket.close(code=4001)
        return
    await websocket.accept()
    _ws_clients[websocket] = {"username": session["username"], "role": session["role"]}
    try:
        # Keep the connection alive; broadcaster pushes state.
        # Drain any client messages; the frontend does not send data, so we
        # just wait indefinitely — WebSocketDisconnect fires on close/error.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _ws_clients.pop(websocket, None)

##############################################################################
# SYSTEM: HEALTH, READINESS, DIAGNOSTICS
##############################################################################
def _probe_camera():
    age = time.time() - _frame_ts
    return (age < 5.0, "delivering frames" if age < 5.0 else
            ("no frame yet" if not _frame_ts else f"no frame for {int(age)} s"))

def _probe_events_db():
    conn = sqlite3.connect(EVENTS_DB, timeout=2)
    try:
        conn.execute("SELECT 1 FROM events LIMIT 1").fetchall()
    finally:
        conn.close()
    return True, "ok"

def _probe_disk():
    usage = __import__("shutil").disk_usage(str(_BASE))
    free_mb = usage.free // (1024 * 1024)
    return free_mb >= 200, f"{free_mb} MB free"

def _probe_workers():
    bad = [w["name"] for w in SUPERVISOR.status() if w["critical"] and not w["alive"]]
    return (not bad, "all running" if not bad else "stopped: " + ", ".join(bad))

def _probe_rules():
    health = DRISHTI_RUNTIME.health()
    return bool(health.get("running")), health.get("last_error") or "running"

def _client_meta(request: Request) -> dict:
    """What a client needs to adapt itself: which product this address is,
    and which optional parts this server actually has."""
    product = _product_for_host(request.headers.get("host"))
    return {
        "product": product,
        "name": "Garuda" if product == "security" else "Drishti",
        "features": {
            "home_automation": product != "security",
            "voice": bool(NARADA_VOICE.configured),
            "assistant": bool(NIM_CHAT.configured),
            "webrtc": bool(_WEBRTC_AVAILABLE),
            "clips": True,
            "email_alerts": bool(EMAIL_SENDER and EMAIL_SENDER_PASS and EMAIL_RECIPIENTS),
        },
        "auth": {"access_token_s": _ACCESS_DURATION, "refresh_token_s": _REFRESH_DURATION,
                 "header": "X-Garuda-Token", "refresh_header": "X-Garuda-Refresh"},
    }

def _system_extra() -> dict:
    return {"sessions": {"active": len(_sessions),
                         "refresh_tokens": len(_refresh_tokens) + len(_persisted_refresh),
                         "websockets": len(_ws_clients), "video_peers": len(_pc_set)},
            "log_file": getattr(_core_logging.configure, "path", None)}

fastapi_app.include_router(build_system_router(
    settings=SETTINGS, supervisor=SUPERVISOR, backups=BACKUPS, admin_dep=require_admin,
    probes={"camera": (_probe_camera, True), "events_db": (_probe_events_db, True),
            "disk": (_probe_disk, True), "workers": (_probe_workers, True),
            "rule_loop": (_probe_rules, False)},
    meta_fn=_client_meta, openapi_fn=fastapi_app.openapi,
    started_at=lambda: _app_start_time, extra_info=_system_extra))

_core_http.tag_routes(fastapi_app, [
    ("/api/login", "Auth"), ("/api/logout", "Auth"), ("/api/refresh", "Auth"),
    ("/api/session", "Auth"), ("/api/admin/", "Auth"), ("/api/forgot/", "Auth"),
    ("/api/master_key", "Master keys"), ("/api/users", "Users"),
    ("/api/config", "Configuration"), ("/api/modes", "Modes"), ("/api/devices", "Presence"),
    ("/api/arp", "Presence"), ("/api/presence_refresh", "Presence"),
    ("/api/logs", "Logs"), ("/api/events", "Events"), ("/api/feedback", "Feedback"),
    ("/api/chat", "Narada"), ("/api/narada", "Narada"), ("/api/home", "Home automation"),
    ("/api/clip", "Camera"), ("/api/snapshot", "Camera"), ("/stream", "Camera"),
    ("/webrtc", "Camera"), ("/api/eval", "Evaluation harness"), ("/api/", "Core"),
])

##############################################################################
# CAMERA AUTO-DETECT
##############################################################################
def _resolve_camera(input_src: str) -> str:
    """
    Resolve the camera source for GStreamer.
    - If input is not a /dev/videoN device, return as-is (file or 'rpi').
    - If it IS a /dev/videoN, check via v4l2-ctl whether it is a Pi-internal
      device (rp1-cfe / pispbe). If so, find a real USB camera or fall back
      to 'rpi' (libcamera via GStreamer, which works here unlike OpenCV).
    """
    if not input_src.startswith("/dev/video"):
        return input_src

    try:
        result = subprocess.run(
            ["v4l2-ctl", "--list-devices"],
            capture_output=True, text=True, timeout=3
        )
        # Build map: device_path → category_name
        dev_category: dict = {}
        current = ""
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped and not line.startswith("\t"):
                current = stripped
            elif stripped.startswith("/dev/video"):
                dev_category[stripped] = current

        def _is_pi_internal(dev: str) -> bool:
            return "platform:" in dev_category.get(dev, "")

        if _is_pi_internal(input_src):
            # Look for a real USB camera (no "platform:" in name)
            for dev, cat in dev_category.items():
                if "platform:" not in cat:
                    log_system_update(f"[CAMERA] {input_src} is Pi-internal → using USB: {dev}")
                    return dev
            # No USB camera found; libcamera (GStreamer) works for Pi camera
            log_system_update("[CAMERA] No USB camera found → using Pi camera (rpi/libcamera)")
            return "rpi"
    except Exception as e:
        log_system_update(f"[CAMERA] v4l2-ctl probe failed: {e}")

    return input_src  # unable to determine — use as-is


##############################################################################
# MAIN
##############################################################################
def run_web_app(args):
    global app_gst

    # ── Resolve camera input ───────────────────────────────────────────────
    args.input = _resolve_camera(args.input)
    log_system_update(f"[CAMERA] Input resolved to: {args.input}")

    # ── Auto-select best_v5.hef + best_labels.json when no HEF specified ──
    _base = Path(__file__).parent.parent / "resources"
    _best_hef    = _base / "best_v5.hef"
    _best_labels = _base / "best_labels.json"
    if args.hef_path is None and _best_hef.exists():
        args.hef_path = str(_best_hef)
        if not hasattr(args, "labels_json") or args.labels_json is None:
            if _best_labels.exists():
                args.labels_json = str(_best_labels)
        log_system_update(
            f"[MODEL] Auto-selected {_best_hef.name}"
            + (f" + {_best_labels.name}" if args.labels_json else "")
        )

    # ── Start uvicorn on the MAIN thread via its own event loop ──────────────
    # The server must outlive any pipeline restarts, so we spin the GStreamer
    # pipeline in a background thread and keep the main thread for the server.
    def _run_pipeline():
        global app_gst
        retry_delay = 5
        while True:
            try:
                user_data = user_app_callback_class()
                app_gst = GStreamerDetectionApp(args, user_data)
                log_system_update("Pipeline started.")
                app_gst.run()
                log_system_update("Pipeline stopped. Restarting in 5s...")
            except Exception as e:
                log_system_update(f"Pipeline error: {e}. Restarting in {retry_delay}s...")
            time.sleep(retry_delay)

    # One log file for the whole service, and what the configuration lacks,
    # said once at start-up instead of discovered feature by feature.
    log_path = _core_logging.configure(str(_BASE / "system_logs"), level=SETTINGS.log_level)
    log_system_update(f"Garuda starting: commit {BUILD['commit']} ({BUILD['branch']}), "
                      f"API v{API_VERSION}, log {log_path or 'stderr only'}")
    for severity, message in SETTINGS.problems():
        if severity != "info":
            log_system_update(f"[CONFIG] {severity}: {message}")

    # Start voice assistant thread (returns at once when there is no microphone)
    SUPERVISOR.spawn("voice-assistant", voice_assistant_loop, args=(_voice_stop_event,), restart=False)

    # Start pipeline thread (restarts automatically on failure)
    SUPERVISOR.spawn("camera-pipeline", _run_pipeline, critical=True)

    # A daily archive of accounts, settings, keys, devices and rules.
    SUPERVISOR.spawn("state-backup", BACKUPS.run_forever)

    print("\n" + "="*60)
    print("  Garuda Web UI is running at http://localhost:8080")
    print("  (Cloudflare tunnel handles external access)")
    print("="*60 + "\n")

    # Bind to 127.0.0.1 only — external access goes via Cloudflare tunnel,
    # which already terminates TLS. Binding to 0.0.0.0 would expose the HTTP
    # port on all network interfaces including LAN.
    uvicorn.run(fastapi_app, host=SETTINGS.host, port=SETTINGS.port, log_level="warning",
                # Open camera streams never finish by themselves; without a
                # limit a restart waited on them until systemd killed the process.
                timeout_graceful_shutdown=8)


if __name__ == "__main__":
    parser = get_default_parser()
    parser.add_argument("--network", default="yolov8s",
                        choices=["yolov8s", "yolov6n", "yolox_s_leaky"],
                        help="Detection network to use")
    parser.add_argument("--hef-path", dest="hef_path", default=None,
                        help="Path to custom HEF file")
    parser.add_argument("--labels-json", dest="labels_json", default=None,
                        help="Path to custom labels JSON")
    args = parser.parse_args()
    run_web_app(args)
