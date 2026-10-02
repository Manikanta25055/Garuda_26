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
    from .garuda_routes.logs import build_logs_router
    from .garuda_routes.auth import build_auth_router, LoginRequest, OTPRequest, VerifyOTPRequest, ForgotPasswordRequest, SendForgotOTPRequest  # noqa: F401
    from .garuda_routes.camera import build_camera_router, WebRTCOfferRequest  # noqa: F401
    from .garuda_routes.narada import build_narada_router, ChatRequest  # noqa: F401
    from .garuda_routes.control import build_control_router, ModeRequest  # noqa: F401
    from .garuda_routes.evaluation import build_evaluation_router, EvalInjectRequest, EvalTagRequest  # noqa: F401
    from .garuda_routes.pages import build_pages_router
    from .garuda_routes.sockets import build_sockets_router
    from .garuda_routes.feedback import build_feedback_router, FeedbackRequest  # noqa: F401
    from .garuda_routes.events import build_events_router
    from .garuda_services import presence as _svc_presence
    from .garuda_services.presence import (  # noqa: F401
        _get_local_subnet, _probe_subnet_for_arp, _device_mac, _mac_online, _present_device, _check_device_presence, _presence_poller, _do_presence_check)
    from .garuda_services import monitors as _svc_monitors
    from .garuda_services.monitors import (  # noqa: F401
        _check_connectivity, _connectivity_monitor, _deadman_monitor, _schedule_monitor)
    from .garuda_services import alerts as _svc_alerts
    from .garuda_services.alerts import (  # noqa: F401
        trigger_software_alert, send_email_alert, log_scissors_detection, _send_tamper_email, exfiltrate_clip)
    from .garuda_services import persistence as _svc_persistence
    from .garuda_services.persistence import (  # noqa: F401
        load_users, save_users, load_config, _load_alert_history, _record_alert_activity, _remember_user_activity, _load_presence_log, _append_presence_log, load_master_keys, save_master_keys, _async_save_config, save_config)
    from .garuda_services import logs as _svc_logs
    from .garuda_services.logs import (  # noqa: F401
        _load_logs_from_disk, _rotate_log, _do_flush_logs, _flush_log_thread, _perm_write, _append_detection_perm, log_system_update, append_voice_log, append_voice_response)
    from .garuda_services import detection as _svc_detection
    from .garuda_services.detection import (  # noqa: F401
        _secondary_worker_loop, _check_night_presence, app_callback, _resolve_camera)
    from .garuda_services import pipeline as _svc_pipeline
    from .garuda_services.pipeline import (  # noqa: F401
        _WebCascadeMetrics, user_app_callback_class, GStreamerDetectionApp)
    from .garuda_services import sessions as _svc_sessions
    from .garuda_services.sessions import (  # noqa: F401
        _invalidate_user_sessions, _get_client_ip, _check_rate_limit, _prune_rate_state, _is_login_locked, _record_login_failure, _clear_login_failure, _load_refresh_tokens, _save_refresh_tokens, _revoke_refresh, create_refresh_token, _prune_expired_refresh_tokens, _user_signed_in, _is_cross_site, _cookie_secure, _set_session_cookies, get_refresh_token, create_session, create_master_session, get_session, _prune_expired_sessions, require_session, require_admin, require_logs)
    from .garuda_services import state as _svc_state
    from .garuda_services.state import (  # noqa: F401
        _home_state_summary, _recent_alert_history, get_state_dict, _state_for_role, push_urgent_ws, _ws_connect_allowed, _ws_send, _ws_broadcaster, _client_meta, _system_extra, _probe_camera, _probe_events_db, _probe_disk, _probe_workers, _probe_rules)
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
    from basic_pipelines.garuda_routes.logs import build_logs_router
    from basic_pipelines.garuda_routes.auth import build_auth_router, LoginRequest, OTPRequest, VerifyOTPRequest, ForgotPasswordRequest, SendForgotOTPRequest  # noqa: F401
    from basic_pipelines.garuda_routes.camera import build_camera_router, WebRTCOfferRequest  # noqa: F401
    from basic_pipelines.garuda_routes.narada import build_narada_router, ChatRequest  # noqa: F401
    from basic_pipelines.garuda_routes.control import build_control_router, ModeRequest  # noqa: F401
    from basic_pipelines.garuda_routes.evaluation import build_evaluation_router, EvalInjectRequest, EvalTagRequest  # noqa: F401
    from basic_pipelines.garuda_routes.pages import build_pages_router
    from basic_pipelines.garuda_routes.sockets import build_sockets_router
    from basic_pipelines.garuda_routes.feedback import build_feedback_router, FeedbackRequest  # noqa: F401
    from basic_pipelines.garuda_routes.events import build_events_router
    from basic_pipelines.garuda_services import presence as _svc_presence
    from basic_pipelines.garuda_services.presence import (  # noqa: F401
        _get_local_subnet, _probe_subnet_for_arp, _device_mac, _mac_online, _present_device, _check_device_presence, _presence_poller, _do_presence_check)
    from basic_pipelines.garuda_services import monitors as _svc_monitors
    from basic_pipelines.garuda_services.monitors import (  # noqa: F401
        _check_connectivity, _connectivity_monitor, _deadman_monitor, _schedule_monitor)
    from basic_pipelines.garuda_services import alerts as _svc_alerts
    from basic_pipelines.garuda_services.alerts import (  # noqa: F401
        trigger_software_alert, send_email_alert, log_scissors_detection, _send_tamper_email, exfiltrate_clip)
    from basic_pipelines.garuda_services import persistence as _svc_persistence
    from basic_pipelines.garuda_services.persistence import (  # noqa: F401
        load_users, save_users, load_config, _load_alert_history, _record_alert_activity, _remember_user_activity, _load_presence_log, _append_presence_log, load_master_keys, save_master_keys, _async_save_config, save_config)
    from basic_pipelines.garuda_services import logs as _svc_logs
    from basic_pipelines.garuda_services.logs import (  # noqa: F401
        _load_logs_from_disk, _rotate_log, _do_flush_logs, _flush_log_thread, _perm_write, _append_detection_perm, log_system_update, append_voice_log, append_voice_response)
    from basic_pipelines.garuda_services import detection as _svc_detection
    from basic_pipelines.garuda_services.detection import (  # noqa: F401
        _secondary_worker_loop, _check_night_presence, app_callback, _resolve_camera)
    from basic_pipelines.garuda_services import pipeline as _svc_pipeline
    from basic_pipelines.garuda_services.pipeline import (  # noqa: F401
        _WebCascadeMetrics, user_app_callback_class, GStreamerDetectionApp)
    from basic_pipelines.garuda_services import sessions as _svc_sessions
    from basic_pipelines.garuda_services.sessions import (  # noqa: F401
        _invalidate_user_sessions, _get_client_ip, _check_rate_limit, _prune_rate_state, _is_login_locked, _record_login_failure, _clear_login_failure, _load_refresh_tokens, _save_refresh_tokens, _revoke_refresh, create_refresh_token, _prune_expired_refresh_tokens, _user_signed_in, _is_cross_site, _cookie_secure, _set_session_cookies, get_refresh_token, create_session, create_master_session, get_session, _prune_expired_sessions, require_session, require_admin, require_logs)
    from basic_pipelines.garuda_services import state as _svc_state
    from basic_pipelines.garuda_services.state import (  # noqa: F401
        _home_state_summary, _recent_alert_history, get_state_dict, _state_for_role, push_urgent_ws, _ws_connect_allowed, _ws_send, _ws_broadcaster, _client_meta, _system_extra, _probe_camera, _probe_events_db, _probe_disk, _probe_workers, _probe_rules)
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

# garuda_services modules read this module's state through `core`.
_svc_state.bind(sys.modules[__name__])
_svc_sessions.bind(sys.modules[__name__])
_svc_pipeline.bind(sys.modules[__name__])
_svc_detection.bind(sys.modules[__name__])
_svc_logs.bind(sys.modules[__name__])
_svc_persistence.bind(sys.modules[__name__])
_svc_alerts.bind(sys.modules[__name__])
_svc_monitors.bind(sys.modules[__name__])
_svc_presence.bind(sys.modules[__name__])

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

_cascade_metrics = _WebCascadeMetrics()


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

_PRESENCE_LOG_MAX = 5000
_USER_HISTORY_MAX = 200

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

##############################################################################
# PHONE PRESENCE DETECTION
##############################################################################

##############################################################################
# ALERTS
##############################################################################

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

##############################################################################
# GSTREAMER CALLBACK
##############################################################################


try:
    import zoneinfo as _zoneinfo
    _IST = _zoneinfo.ZoneInfo("Asia/Kolkata")
except Exception:
    _IST = None   # no timezone data: fall back to the system clock

_np_last_check = 0.0


PI_CAMERA_SIZE = (1280, 720)
PI_CAMERA_FPS = 60


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

##############################################################################
# STATE HELPER
##############################################################################


##############################################################################
# DEAD MAN'S SWITCH MONITOR
##############################################################################

##############################################################################
# SCHEDULED MODES
##############################################################################

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
# ── Routes ───────────────────────────────────────────────────────────────────

fastapi_app.include_router(build_pages_router(sys.modules[__name__]))

fastapi_app.include_router(build_auth_router(sys.modules[__name__]))

fastapi_app.include_router(build_control_router(sys.modules[__name__]))

def _require_eval_token(request: Request):
    """Token-gated access for the P1-4 evaluation harness."""
    expected = os.environ.get("GARUDA_EVAL_TOKEN", "")
    if not expected:
        raise HTTPException(404, "Not found")
    got = request.headers.get("X-Eval-Token", "")
    if not hmac.compare_digest(got.encode(), expected.encode()):
        raise HTTPException(403, "Bad eval token")

fastapi_app.include_router(build_evaluation_router(sys.modules[__name__]))

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


fastapi_app.include_router(build_narada_router(sys.modules[__name__]))

ADMIN_ONLY_MODES = frozenset({"idle", "email_off"})

def _set_mode_flag(global_name: str, value):
    """Set one MODE_* flag of this module by name.

    A function of its own because it relies on globals(), which always means
    the module the code is written in: a route handler that lives in another
    file must call this, not write to its own globals.
    """
    globals()[global_name] = value


def _get_mode_flag(global_name: str):
    """Read one MODE_* flag of this module by name (see _set_mode_flag)."""
    return globals().get(global_name)

fastapi_app.include_router(build_users_router(sys.modules[__name__]))

fastapi_app.include_router(build_config_router(sys.modules[__name__]))

fastapi_app.include_router(build_presence_router(sys.modules[__name__]))

fastapi_app.include_router(build_logs_router(sys.modules[__name__]))

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

fastapi_app.include_router(build_camera_router(sys.modules[__name__]))

# ── Narada voice (ElevenLabs Speech Engine) ──────────────────────────────────
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

# ── WebSocket binary JPEG stream (CF Tunnel fallback) ────────────────────────
# ── WebSocket broadcaster (event-driven) ─────────────────────────────────────
# Waits on _ws_trigger asyncio.Event with a 2s timeout (heartbeat).
# push_urgent_ws() sets the event from any thread → immediate broadcast.
# Compute state ONCE per tick and fan-out via asyncio.gather — O(1) in CPU.

# What a non-admin does not need pushed to their browser every two seconds.
_ADMIN_ONLY_STATE = ("known_devices", "cpu_cores", "voice_log", "voice_responses")
_ADMIN_ONLY_LOG_TAGS = ("[SECURITY]", "Login", "login", "Master key", "master key",
                        "Password", "User added", "User deleted", "User updated")

fastapi_app.include_router(build_sockets_router(sys.modules[__name__]))

##############################################################################
# SYSTEM: HEALTH, READINESS, DIAGNOSTICS
##############################################################################

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
