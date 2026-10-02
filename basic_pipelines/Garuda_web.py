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

# Set SECURE_COOKIES=1 in .env when serving behind HTTPS (Cloudflare tunnel).
# Leave unset for direct http://localhost access — secure=True drops cookies on plain HTTP.
_COOKIE_SECURE = os.environ.get("SECURE_COOKIES", "0").lower() in ("1", "true", "yes")

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
# This file is loaded as a top-level module: as a script by
# scripts/run_garuda_web.sh and as `Garuda_web` by the tests. Relative imports
# only work when it is imported as part of the basic_pipelines package, so fall
# back to absolute.
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
    from .garuda_services import assistant as _svc_assistant
    from .garuda_services.assistant import (  # noqa: F401
        voice_assistant_loop, _voice_turn_logged, _assistant_reply, _ai_configure, _ai_test)
    from .garuda_services import home as _svc_home
    from .garuda_services.home import (  # noqa: F401
        _drishti_authenticate, _drishti_system_state, _drishti_set_privacy, _home_presence, _home_security, _home_email, _home_modes, _home_set_mode, _home_security_summary)
    from .garuda_services import middleware as _svc_middleware
    from .garuda_services.middleware import (  # noqa: F401
        global_rate_limit, product_scope, security_headers)
    from .garuda_services import support as _svc_support
    from .garuda_services.support import (  # noqa: F401
        mjpeg_frames, _prune_old_clips, _combined_log_text, _load_feedback, _save_feedback, stop_app, send_otp_via_email, _require_eval_token, _safe_json_load, _product_for_host, narada_voice_ws)
    from .garuda_core import API_VERSION, BUILD
    from .garuda_core.settings import Settings
    from .garuda_core.workers import Supervisor
    from .garuda_core.backup import BackupManager
    from .garuda_core import http as _core_http
    from .garuda_core import state as _core_state
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
    from basic_pipelines.garuda_services import assistant as _svc_assistant
    from basic_pipelines.garuda_services.assistant import (  # noqa: F401
        voice_assistant_loop, _voice_turn_logged, _assistant_reply, _ai_configure, _ai_test)
    from basic_pipelines.garuda_services import home as _svc_home
    from basic_pipelines.garuda_services.home import (  # noqa: F401
        _drishti_authenticate, _drishti_system_state, _drishti_set_privacy, _home_presence, _home_security, _home_email, _home_modes, _home_set_mode, _home_security_summary)
    from basic_pipelines.garuda_services import middleware as _svc_middleware
    from basic_pipelines.garuda_services.middleware import (  # noqa: F401
        global_rate_limit, product_scope, security_headers)
    from basic_pipelines.garuda_services import support as _svc_support
    from basic_pipelines.garuda_services.support import (  # noqa: F401
        mjpeg_frames, _prune_old_clips, _combined_log_text, _load_feedback, _save_feedback, stop_app, send_otp_via_email, _require_eval_token, _safe_json_load, _product_for_host, narada_voice_ws)
    from basic_pipelines.garuda_core import API_VERSION, BUILD
    from basic_pipelines.garuda_core.settings import Settings
    from basic_pipelines.garuda_core.workers import Supervisor
    from basic_pipelines.garuda_core.backup import BackupManager
    from basic_pipelines.garuda_core import http as _core_http
    from basic_pipelines.garuda_core import state as _core_state
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
_svc_support.bind(sys.modules[__name__])
_svc_middleware.bind(sys.modules[__name__])
_svc_home.bind(sys.modules[__name__])
_svc_assistant.bind(sys.modules[__name__])
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

# Live state, grouped by concern (garuda_core/state.py). The flat names the
# tests still use are forwarded to it; code in this file uses STATE directly.
STATE = _core_state.State()
_core_state.forward(sys.modules[__name__], {
    "MODE_DND": ("modes", "dnd"), "MODE_EMAIL_OFF": ("modes", "email_off"),
    "MODE_IDLE": ("modes", "idle"), "MODE_NIGHT": ("modes", "night"),
    "MODE_EMERGENCY": ("modes", "emergency"), "MODE_PRIVACY": ("modes", "privacy"),
    "MODE_SCHEDULE": ("modes", "schedule"), "CUSTOM_MODES": ("modes", "custom"),
    "_mode_lock": ("modes", "lock"),
    # Email settings: the secrets come from .env, the rest from config.json.
    "EMAIL_SENDER": ("config", "email_sender"), "EMAIL_SENDER_PASS": ("config", "email_sender_pass"),
    "EMAIL_RECIPIENTS": ("config", "email_recipients"), "EMAIL_COOLDOWN": ("config", "email_cooldown"),
    "DETECTION_THRESHOLD": ("config", "detection_threshold"),
    "DANGER_LABELS": ("config", "danger_labels"), "WATCH_LABELS": ("config", "watch_labels"),
    "KNOWN_DEVICES": ("config", "known_devices"),
    "NIGHT_PRESENCE_WINDOW": ("config", "night_presence_window"),
    "CUSTOM_VOICE_COMMANDS": ("config", "custom_voice_commands"),
})

_core_state.forward(sys.modules[__name__], {
    "_alert_active": ("alerts", "active"), "_alert_end_time": ("alerts", "end_time"),
    "_danger_trigger_info": ("alerts", "danger_trigger_info"),
    "_last_danger_conf": ("alerts", "last_danger_conf"),
    "_danger_active": ("alerts", "danger_active"),
    "_last_alert_time": ("alerts", "last_alert_time"), "_alert_history": ("alerts", "history"),
    "_alert_lock": ("alerts", "lock"), "last_email_sent_time": ("alerts", "last_email_sent_time"),
    "_email_lock": ("alerts", "email_lock"), "_last_tamper_email": ("alerts", "last_tamper_email"),
    "_night_presence_alert_active": ("alerts", "night_presence_active"),
    "_night_presence_alert_end_time": ("alerts", "night_presence_end_time"),
    "_np_last_check": ("alerts", "night_presence_last_check"),
    "_np_lock": ("alerts", "night_presence_lock"),
    "_blind_frame_count": ("alerts", "blind_frame_count"),
    "_blind_alert_sent": ("alerts", "blind_alert_sent"),
})

_core_state.forward(sys.modules[__name__], {
    "_owner_present": ("presence", "owner_present"),
    "_owner_last_seen": ("presence", "owner_last_seen"),
    "_last_arp_cache": ("presence", "last_arp_cache"), "_presence_log": ("presence", "log"),
})

_core_state.forward(sys.modules[__name__], {
    "_net_online": ("system", "net_online"), "_last_heartbeat": ("system", "last_heartbeat"),
    "_heartbeat_ever": ("system", "heartbeat_ever"),
    "_deadman_alert_sent": ("system", "deadman_alert_sent"),
    "_deadman_last_alert": ("system", "deadman_last_alert"), "_cpu_ema": ("system", "cpu_ema"),
    "_ram_ema": ("system", "ram_ema"), "_temp_ema": ("system", "temp_ema"),
    "_cpu_cores_ema": ("system", "cpu_cores_ema"), "_voice_mic_ok": ("system", "voice_mic_ok"),
    "_voice_mic_detail": ("system", "voice_mic_detail"), "_event_loop": ("system", "event_loop"),
    "_ws_trigger": ("system", "ws_trigger"),
    "_ws_broadcaster_task": ("system", "ws_broadcaster_task"),
})

_core_state.forward(sys.modules[__name__], {
    "_frame_buffer": ("camera", "frame_buffer"), "_frame_raw": ("camera", "frame_raw"),
    "_frame_seq": ("camera", "frame_seq"), "_frame_ts": ("camera", "frame_ts"),
    "_frame_lock": ("camera", "frame_lock"), "_total_frames": ("camera", "total_frames"),
    "_detections_today": ("camera", "detections_today"),
    "latest_detection_info": ("camera", "latest_detection_info"),
    "_class_counts_today": ("camera", "class_counts_today"),
    "_watch_last_logged": ("camera", "watch_last_logged"),
    "_label_consec_frames": ("camera", "label_consec_frames"),
    "_drishti_last_observe": ("camera", "drishti_last_observe"),
    "_clip_writer": ("camera", "clip_writer"), "_clip_lock": ("camera", "clip_lock"),
    "_clip_start_time": ("camera", "clip_start_time"), "_clip_path": ("camera", "clip_path"),
    "app_gst": ("camera", "app_gst"),
})

NARADA_WAKE_WORD = "narada"

_app_start_time = time.time()

# ── Dead man's switch ────────────────────────────────────
_DEADMAN_TIMEOUT = 180             # seconds without heartbeat before tamper alert
# Opt-in: the dead-man switch is only meaningful when an external monitor
# (e.g. UptimeRobot) is hitting /api/heartbeat. Disabled by default so a
# deployment WITHOUT such a monitor does not spam "missed heartbeat" alerts.
_DEADMAN_ENABLED = os.environ.get("DEADMAN_ENABLED", "0") == "1"
_DEADMAN_REALERT_INTERVAL = 3600   # min seconds between repeat alerts (anti-spam)

# ── Camera blindness detection ───────────────────────────
_TAMPER_EMAIL_COOLDOWN = 3600      # min seconds between camera-tamper emails
_perm_lock = threading.Lock()
# ── RAM-buffered log write globals (buffer defined here; functions in HELPERS) ─
_log_buffer: "defaultdict[str, list]" = defaultdict(list)
_log_buffer_lock = threading.Lock()

# ── False positive reduction ──────────────────────────────

# ── Night presence alarm (the window itself is STATE.config.night_presence_window) ──

# ── Clip recording ────────────────────────────────────────

# ── Phone presence detection ──────────────────────────────
MASTER_KEYS: list    = []   # loaded from MASTER_KEYS_FILE at startup
MASTER_KEY_OTP: str | None = None
OWNER_AWAY_GRACE = 90         # seconds without seeing device before marking away (3 missed polls)

# ── Password hashing (PBKDF2-SHA256) ────────────────────

# ── Atomic JSON write ────────────────────────────────────

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
# Pipeline rate in, browser rate out. The clip writer shares this gate, because
# its VideoWriter is built at a fixed 15fps and writing faster is what made
# saved clips play back in slow motion.
_frame_publisher = FramePublisher()

# Drishti rebuilds its descriptor at this rate, not at frame rate.
_DRISHTI_OBSERVE_INTERVAL_S = 0.2

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

# Session store: token → {username, role, expires}
_sessions = {}

# WebSocket clients (all connected devices): socket -> {username, role}
_ws_clients: dict = {}

# EMA-smoothed system stats (α=0.25 → ~4-tick rolling average)
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

# ── RAM-buffered log writes (the functions are in garuda_services/logs.py) ────
# All text log writes (detection, system, voice, scissors, night-mode) are
# accumulated in-memory and flushed to disk every _LOG_FLUSH_INTERVAL seconds.
# This eliminates per-event fsync calls — the biggest source of SD card wear.
# Critical state (users, config, alert history) still uses _atomic_json_write.
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

##############################################################################
# EMAIL
##############################################################################

def _send_mail(subject, body, to=None):
    """One email from the configured sender; to the alert recipients unless
    `to` names someone else. Raises on failure (see garuda_core.mailer)."""
    cfg = STATE.config
    _mailer.send(subject, body, sender=cfg.email_sender, password=cfg.email_sender_pass,
                 to=cfg.email_recipients if to is None else to)

##############################################################################
# WEBRTC VIDEO TRACK
##############################################################################
if _WEBRTC_AVAILABLE:
    class GarudaVideoTrack(VideoStreamTrack):
        """Serves the latest BGR frame from the Hailo pipeline as an H.264 track."""
        kind = "video"

        async def recv(self):
            pts, time_base = await self.next_timestamp()
            with STATE.camera.frame_lock:
                raw = STATE.camera.frame_raw
            if raw is not None:
                vf = av.VideoFrame.from_ndarray(raw, format="bgr24")
            else:
                vf = av.VideoFrame(width=1280, height=720, format="yuv420p")
            vf.pts = pts
            vf.time_base = time_base
            return vf

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
# CAMERA PIPELINE SETTINGS (the pipeline is in garuda_services/pipeline.py)
##############################################################################
try:
    import zoneinfo as _zoneinfo
    _IST = _zoneinfo.ZoneInfo("Asia/Kolkata")
except Exception:
    _IST = None   # no timezone data: fall back to the system clock



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
# FASTAPI APP
##############################################################################
from contextlib import asynccontextmanager

@asynccontextmanager
async def _lifespan(app):
    STATE.system.event_loop = asyncio.get_running_loop()
    STATE.system.ws_trigger = asyncio.Event()
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
    STATE.system.ws_broadcaster_task = asyncio.create_task(_ws_broadcaster())
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
    if STATE.system.ws_broadcaster_task is not None:
        STATE.system.ws_broadcaster_task.cancel()
        await asyncio.gather(STATE.system.ws_broadcaster_task, return_exceptions=True)
        STATE.system.ws_broadcaster_task = None
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

# Middleware (the functions are in garuda_services/middleware.py; the order
# of registration here is the order they were declared in).
# The global rate limit applies to all API endpoints. Endpoints that do their
# own per-action rate limiting (login, OTP) keep their individual checks; this
# catches everything else.
# Health and readiness are polled by monitors; they must not eat the budget.
_RATE_EXEMPT_PREFIXES = ("/static/", "/drishti/", "/ws", "/stream", "/api/eval/",
                         "/api/health", "/api/ready")

fastapi_app.middleware("http")(global_rate_limit)
fastapi_app.middleware("http")(product_scope)
fastapi_app.middleware("http")(security_headers)

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


# ElevenLabs does the listening and speaking; _assistant_reply (NIM) decides.
NARADA_VOICE = NaradaVoice(
    os.environ.get("ELEVENLABS_API_KEY", ""),
    os.environ.get("ELEVENLABS_SPEECH_ENGINE_ID", ""),
    reply_fn=lambda text, user, role, scope: _assistant_reply(text, user, role, scope, voice=True),
    on_turn=_voice_turn_logged,
    voice_id=os.environ.get("ELEVENLABS_VOICE_ID", ""),
)
DIGEST = Digest(HOME, NIM_CHAT,
                alerts_fn=lambda: STATE.alerts.history.get(datetime.date.today().isoformat(), 0))
HOME.digest_fn = DIGEST.text


fastapi_app.include_router(build_home_router(
    DRISHTI_CTX, HOME, AGENT, DIGEST, session_dep=require_session, admin_dep=require_admin,
    ai_configure=_ai_configure, ai_test=_ai_test))

# ── Routes (one module per area in garuda_routes/) ───────────────────────────

fastapi_app.include_router(build_pages_router(sys.modules[__name__]))

fastapi_app.include_router(build_auth_router(sys.modules[__name__]))

fastapi_app.include_router(build_control_router(sys.modules[__name__]))

fastapi_app.include_router(build_evaluation_router(sys.modules[__name__]))

fastapi_app.include_router(build_narada_router(sys.modules[__name__]))

ADMIN_ONLY_MODES = frozenset({"idle", "email_off"})

fastapi_app.include_router(build_users_router(sys.modules[__name__]))

fastapi_app.include_router(build_config_router(sys.modules[__name__]))

fastapi_app.include_router(build_presence_router(sys.modules[__name__]))

fastapi_app.include_router(build_logs_router(sys.modules[__name__]))

# ── Master key OTP state ─────────────────────────────────────────────────────
_MASTER_OTP_TTL = 300
_master_otp_ts = 0.0
_master_otp_attempts = 0

fastapi_app.include_router(build_master_keys_router(sys.modules[__name__]))

# ── Offline event queue endpoints ─────────────────────────────────────────────
fastapi_app.include_router(build_events_router(sys.modules[__name__]))

# ── Feedback ─────────────────────────────────────────────────────────────────
_feedback_lock = threading.Lock()
_FEEDBACK_MAX = 2000

fastapi_app.include_router(build_feedback_router(sys.modules[__name__]))

# ── MJPEG stream (mjpeg_frames is in garuda_services/support.py) ─────────────
_STREAM_RECHECK_S = 5.0


DRISHTI_CTX.frame_source = mjpeg_frames


# ── Clip recording ────────────────────────────────────────────────────────────
_CLIPS_KEEP = 50

fastapi_app.include_router(build_camera_router(sys.modules[__name__]))

# ── Narada voice (ElevenLabs Speech Engine) ──────────────────────────────────
fastapi_app.websocket("/ws/narada-voice")(narada_voice_ws)


_WS_CONNECT_LIMIT = 60   # socket opens per client address per rate window

# ── Browser sockets (the broadcaster is in garuda_services/state.py) ─────────
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
# MAIN
##############################################################################
def run_web_app(args):
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
        retry_delay = 5
        while True:
            try:
                user_data = user_app_callback_class()
                STATE.camera.app_gst = GStreamerDetectionApp(args, user_data)
                log_system_update("Pipeline started.")
                STATE.camera.app_gst.run()
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
