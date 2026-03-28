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
import hashlib
import tempfile
import re
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
from pydantic import BaseModel
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
EMAIL_RECIPIENTS = ["amarmanikantan@gmail.com"]
EMAIL_COOLDOWN = 60
last_email_sent_time = 0

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL   = "llama-3.3-70b-versatile"

##############################################################################
# GLOBALS & SETTINGS
##############################################################################
app_gst = None  # GStreamer app instance

SCISSORS_LOG_FILE = "danger_sightings.txt"
NIGHT_MODE_LOG_FILE = "night_mode_findings.txt"
LLM_LOG_FILE = "system_logs/llm_reasoning.json"
USERS_FILE = "system_logs/users.json"
CONFIG_FILE          = "system_logs/config.json"
ALERT_HISTORY_FILE   = "system_logs/alert_history.json"
PRESENCE_LOG_FILE    = "system_logs/presence_log.json"
MASTER_KEYS_FILE     = "system_logs/master_keys.json"
PERM_SYSTEM_LOG      = "system_logs/perm_system_log.txt"
PERM_VOICE_LOG       = "system_logs/perm_voice_log.txt"
PERM_DETECTION_LOG   = "system_logs/perm_detection_log.txt"

system_updates_log: List[str] = []
voice_assistant_log: List[str] = []
voice_responses: List[str] = []
_detection_log: List[str] = []   # in-memory recent detection events (danger + watch)
latest_detection_info = ""

ADMIN_OTP = None
USER_FORGOT_OTP = None
_forgot_otp_user = None

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
_alert_flash_count = 0
_alert_end_time     = 0.0   # epoch when current alert expires (3s visual banner)
_alert_cooldown_until = 0.0 # epoch after which the next alert may fire (60s cooldown)
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

# ── Camera blindness detection ───────────────────────────
_blind_frame_count = 0
_blind_alert_sent = False
_class_counts_today = {}   # class_name → count since startup
_total_frames = 0          # total inference frames (for avg FPS)
_watch_last_logged: dict = {}   # label → last log timestamp (30s cooldown)
_perm_lock = threading.Lock()

# ── Phone presence detection ──────────────────────────────
KNOWN_DEVICES: list = []      # [{name, mac}] — loaded from config
_alert_history: dict = {}     # {ISO-date: alert_count} — persisted to disk
_presence_log: list  = []     # [{ts, event, device, mac}] — permanent presence record
MASTER_KEYS: list    = ["cizduz-vudqa6-mynsoK"]   # master key bootstrap value
MASTER_KEY_OTP: str | None = None
_pending_mk: str | None    = None
_owner_present   = False
_owner_last_seen = 0.0
OWNER_AWAY_GRACE = 90         # seconds without seeing device before marking away (3 missed polls)
_last_arp_cache  = ""         # last raw ARP table read (refreshed by _presence_poller)

# ── Detection categories ──────────────────────────────────
WATCH_LABELS: list = ['person', 'backpack', 'suitcase']  # log silently, no alert

# ── Password hashing (PBKDF2-SHA256) ────────────────────
def _hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac('sha256', pw.encode(), salt, 260000)
    return f"pbkdf2:sha256:260000:{salt.hex()}:{dk.hex()}"

def _verify_password(pw: str, stored: str) -> bool:
    if stored.startswith("pbkdf2:"):
        parts = stored.split(":")
        if len(parts) != 5:
            return False
        _, algo, iters, salt_hex, dk_hex = parts
        dk = hashlib.pbkdf2_hmac(algo, pw.encode(), bytes.fromhex(salt_hex), int(iters))
        return dk.hex() == dk_hex
    return pw == stored  # plaintext fallback for migration

# ── Atomic JSON write ────────────────────────────────────
def _atomic_json_write(filepath: str, data):
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(filepath) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, filepath)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

# ── Rate limiter (in-memory, per-IP) ────────────────────
_rate_store: dict = defaultdict(list)   # IP → [timestamps]
_RATE_LIMIT = 30     # max requests
_RATE_WINDOW = 60    # per N seconds

def _check_rate_limit(request) -> bool:
    """Return True if request is within rate limit, False if exceeded."""
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    stamps = _rate_store[ip]
    stamps[:] = [t for t in stamps if now - t < _RATE_WINDOW]
    if len(stamps) >= _RATE_LIMIT:
        return False
    stamps.append(now)
    return True


USERS = {
    "user": {
        "password": "user",
        "role": "user",
        "display_name": "User",
        "box_color": "#1565c0",
        "history": {"logins": [], "narada_activity": []}
    },
    "admin": {
        "password": "root",
        "role": "admin",
        "display_name": "Admin",
        "box_color": "#e65100",
        "history": {"logins": [], "narada_activity": []}
    }
}

# MJPEG / WebRTC frame buffer
_frame_buffer = None
_frame_raw    = None       # raw numpy BGR for WebRTC track
_frame_lock   = threading.Lock()
_frame_seq    = 0          # incremented every new frame; lets MJPEG clients skip duplicates

# WebRTC peer connections
_pc_set: set = set()

# Event-driven WS broadcaster
_event_loop  = None        # asyncio loop ref (set in lifespan)
_ws_trigger  = None        # asyncio.Event — set to push WS immediately

# Session store: token → {username, role, expires}
_sessions = {}

# WebSocket clients (all connected devices)
_ws_clients: set = set()

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
    for path in [USERS_FILE, "system_logs/users_data.json"]:
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
    global KNOWN_DEVICES, WATCH_LABELS
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
        except Exception as e:
            print(f"Warning: failed to load config: {e}")

def _load_alert_history():
    """Load alert-activity history from disk into _alert_history."""
    global _alert_history
    try:
        if os.path.exists(ALERT_HISTORY_FILE):
            with open(ALERT_HISTORY_FILE) as f:
                _alert_history = json.load(f)
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

def _load_presence_log():
    global _presence_log
    try:
        if os.path.exists(PRESENCE_LOG_FILE):
            with open(PRESENCE_LOG_FILE) as f:
                _presence_log = json.load(f)
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
                MASTER_KEYS[:] = data["keys"]
    except Exception:
        pass

def save_master_keys():
    try:
        _atomic_json_write(MASTER_KEYS_FILE, {"keys": MASTER_KEYS})
    except Exception:
        pass

def save_config():
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
        }
        _atomic_json_write(CONFIG_FILE, cfg)
    except Exception as e:
        log_system_update(f"Failed to save config: {e}")

load_users()
load_config()

##############################################################################
# HELPERS
##############################################################################
def _perm_write(filepath: str, line: str):
    """Thread-safe append of one line to a permanent log file on disk."""
    try:
        os.makedirs("system_logs", exist_ok=True)
        with _perm_lock:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
    except Exception:
        pass

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
    _perm_write(PERM_SYSTEM_LOG, entry)

def append_voice_log(message, user_name=None):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {message}"
    voice_assistant_log.append(entry)
    _perm_write(PERM_VOICE_LOG, entry)
    if user_name and user_name in USERS:
        USERS[user_name]["history"]["narada_activity"].append(entry)

def append_voice_response(message, user_name=None):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {message}"
    voice_responses.append(entry)
    _perm_write(PERM_VOICE_LOG, "→ " + entry)
    if user_name and user_name in USERS:
        USERS[user_name]["history"]["narada_activity"].append(entry)

##############################################################################
# OFFLINE EVENT QUEUE (SQLite)
##############################################################################
EVENTS_DB = "system_logs/garuda_events.db"
_eq_lock = threading.Lock()
_net_online = True          # tracked by connectivity monitor

def _init_event_db():
    """Create events table if not exists."""
    os.makedirs(os.path.dirname(EVENTS_DB) or ".", exist_ok=True)
    conn = sqlite3.connect(EVENTS_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            label TEXT,
            confidence REAL DEFAULT 0,
            info TEXT DEFAULT '',
            synced INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_synced ON events(synced)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp)")
    conn.commit()
    conn.close()

def queue_event(event_type: str, label: str = "", confidence: float = 0.0, info: str = ""):
    """Insert an event into the SQLite queue. Thread-safe."""
    stamp = datetime.datetime.now().isoformat()
    with _eq_lock:
        try:
            conn = sqlite3.connect(EVENTS_DB, timeout=5)
            conn.execute(
                "INSERT INTO events (timestamp, event_type, label, confidence, info) VALUES (?, ?, ?, ?, ?)",
                (stamp, event_type, label, confidence, info))
            conn.commit()
            conn.close()
        except Exception as e:
            log_system_update(f"[QUEUE] DB write error: {e}")

def get_events_since(since_ts: str = "", limit: int = 500) -> list:
    """Return events after the given ISO timestamp, oldest-first."""
    with _eq_lock:
        try:
            conn = sqlite3.connect(EVENTS_DB, timeout=5)
            conn.row_factory = sqlite3.Row
            if since_ts:
                rows = conn.execute(
                    "SELECT * FROM events WHERE timestamp > ? ORDER BY timestamp ASC LIMIT ?",
                    (since_ts, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM events ORDER BY timestamp ASC LIMIT ?",
                    (limit,)).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []

def get_pending_count() -> int:
    """Return count of unsynced events."""
    with _eq_lock:
        try:
            conn = sqlite3.connect(EVENTS_DB, timeout=5)
            count = conn.execute("SELECT COUNT(*) FROM events WHERE synced = 0").fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0

def mark_events_synced(up_to_id: int):
    """Mark all events up to and including the given ID as synced."""
    with _eq_lock:
        try:
            conn = sqlite3.connect(EVENTS_DB, timeout=5)
            conn.execute("UPDATE events SET synced = 1 WHERE id <= ?", (up_to_id,))
            conn.commit()
            conn.close()
        except Exception:
            pass

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
    if app_gst is not None:
        try:
            app_gst.pipeline.set_state(Gst.State.NULL)
        except Exception:
            pass
    sys.exit(0)

##############################################################################
# OTP / EMAIL
##############################################################################
def generate_otp_code(length=6):
    return "".join(random.choice(string.digits) for _ in range(length))

def send_otp_via_email(email, otp_code):
    body = f"Hello,\n\nYour OTP code is: {otp_code}\n\nUse this to complete your login."
    msg = MIMEText(body)
    msg['Subject'] = "Your Garuda OTP Code"
    msg['From'] = EMAIL_SENDER
    msg['To'] = email
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(EMAIL_SENDER, EMAIL_SENDER_PASS)
            server.send_message(msg)
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

def _check_device_presence() -> bool:
    """Return True if any registered device MAC appears in the kernel ARP table."""
    global _last_arp_cache
    try:
        with open('/proc/net/arp') as f:
            _last_arp_cache = f.read().lower()
        return any(d.get('mac', '').lower() in _last_arp_cache for d in KNOWN_DEVICES)
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
                dev  = next((d["name"] for d in KNOWN_DEVICES if d["mac"].lower() in _last_arp_cache), "Unknown")
                mac  = next((d["mac"]  for d in KNOWN_DEVICES if d["mac"].lower() in _last_arp_cache), "")
                _append_presence_log("arrived", dev, mac)
                log_system_update(f"[OWNER] {dev} arrived — device detected on network.")
                push_urgent_ws()
        elif _owner_present and (time.time() - _owner_last_seen > OWNER_AWAY_GRACE):
            _owner_present = False
            dev = next((d["name"] for d in KNOWN_DEVICES), "Unknown")
            _append_presence_log("left", dev, "")
            log_system_update(f"[OWNER] {dev} away — device not seen for {OWNER_AWAY_GRACE}s.")
            push_urgent_ws()

##############################################################################
# ALERTS
##############################################################################
def trigger_software_alert():
    global _alert_active, _alert_flash_count, _last_alert_time
    global _alert_end_time, _alert_cooldown_until
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
        _alert_flash_count = 3
        _alert_end_time = time.time() + 3
    if not was_active:
        # New alert starting: log, record, sound, email
        if night:
            try:
                with open(NIGHT_MODE_LOG_FILE, "a") as f:
                    f.write(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
            except Exception:
                pass
        _last_alert_time = datetime.datetime.now()
        _record_alert_activity()
        log_system_update("Alert triggered.")
        push_urgent_ws()
        try:
            os.system("aplay /usr/share/sounds/alsa/Front_Center.wav &")
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
    current_time = time.time()
    if (current_time - last_email_sent_time) < EMAIL_COOLDOWN:
        return
    last_email_sent_time = current_time
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    subject = "Scissors Detected Alert"
    if emergency:
        subject = "EMERGENCY: " + subject
    elif night:
        subject = "HIGH PRIORITY: " + subject
    body = f"Detected scissors at {now_str}.\nCheck your environment for safety.\n"
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = EMAIL_SENDER
    msg['To'] = ", ".join(EMAIL_RECIPIENTS)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(EMAIL_SENDER, EMAIL_SENDER_PASS)
            server.send_message(msg)
        log_system_update("Email alert sent.")
    except Exception as e:
        log_system_update(f"Failed sending email alert: {e}")

def log_scissors_detection():
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{stamp}] SCISSORS DETECTED\n"
    try:
        with open(SCISSORS_LOG_FILE, "a") as f:
            f.write(entry)
    except Exception as e:
        log_system_update(f"Error logging scissors detection: {e}")

##############################################################################
# GSTREAMER CALLBACK
##############################################################################
class user_app_callback_class(app_callback_class):
    def __init__(self):
        super().__init__()
        self.person_detected = False
        self.danger_label = "scissors"
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


def app_callback(pad, info, user_data):
    global latest_detection_info, DETECTION_THRESHOLD, MODE_PRIVACY
    global _detections_today, _frame_buffer, _total_frames, _class_counts_today
    global _last_danger_conf
    buffer = info.get_buffer()
    if buffer is None:
        return Gst.PadProbeReturn.OK

    user_data.increment()
    _total_frames += 1
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
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        variance = float(np.var(gray))
        if variance < 50:   # nearly uniform → blocked/covered
            _blind_frame_count += 1
            if _blind_frame_count >= 300 and not _blind_alert_sent:   # ~10s at 30fps
                _blind_alert_sent = True
                log_system_update("[TAMPER] Camera blindness detected — lens may be covered!")
                _append_detection_perm("TAMPER", "camera_blind", 0.0, "camera appears blocked")
        else:
            _blind_frame_count = 0
            _blind_alert_sent = False

    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)

    with _mode_lock:
        threshold = DETECTION_THRESHOLD
        privacy = MODE_PRIVACY

    danger_detected = False
    det_count = 0
    for d in detections:
        label = d.get_label()
        confidence = d.get_confidence()
        if confidence >= threshold:
            det_count += 1
            text_info += f"{label} ({confidence:.2f})\n"
            _class_counts_today[label] = _class_counts_today.get(label, 0) + 1
            if privacy and label == "person" and frame is not None:
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
            if label == user_data.danger_label:
                danger_detected = True
                _last_danger_conf = confidence
            elif label in WATCH_LABELS and label != user_data.danger_label:
                # WATCH: log silently with 30s cooldown to avoid per-frame spam
                now_t = time.time()
                if now_t - _watch_last_logged.get(label, 0) >= 30:
                    _watch_last_logged[label] = now_t
                    log_system_update(f"[WATCH] {label} ({confidence:.2f})")
                    _append_detection_perm("WATCH", label, confidence)

    if det_count > 0:
        _detections_today += det_count

    if danger_detected:
        global _danger_trigger_info
        _danger_trigger_info = text_info   # snapshot the frame that triggered
        _captured_conf = _last_danger_conf
        _captured_label = user_data.danger_label
        threading.Thread(target=trigger_software_alert, daemon=True).start()
        threading.Thread(target=send_email_alert, daemon=True).start()
        # Log danger to permanent log at most once per 60s to avoid per-frame spam
        _danger_key = "__danger__"
        _now = time.time()
        if _now - _watch_last_logged.get(_danger_key, 0) >= 60:
            _watch_last_logged[_danger_key] = _now
            threading.Thread(target=log_scissors_detection, daemon=True).start()
            threading.Thread(target=lambda: _append_detection_perm(
                "DANGER", _captured_label, _captured_conf, "alert triggered"), daemon=True).start()

    user_data.person_detected = any(d.get_label() == "person" for d in detections)

    if frame is not None:
        cv2.putText(frame, f"Thr: {threshold:.2f}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
        if privacy:
            cv2.putText(frame, "PRIVACY ON", (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 80, 80), 2)
        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        _, jpeg = cv2.imencode('.jpg', frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 75])
        with _frame_lock:
            global _frame_seq, _frame_raw
            _frame_buffer = jpeg.tobytes()
            _frame_raw    = frame_bgr
            _frame_seq += 1
        user_data.set_frame(frame_bgr)

    latest_detection_info = text_info
    return Gst.PadProbeReturn.OK


class GStreamerDetectionApp(GStreamerApp):
    def __init__(self, args, user_data):
        # Force frame capture for MJPEG stream; suppress display
        args.use_frame = True
        args.show_fps = False
        super().__init__(args, user_data)
        self.batch_size = 2
        self.network_width = 640
        self.network_height = 640
        self.network_format = "RGB"
        nms_score_threshold = 0.3
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

        if self.options_menu.dump_dot:
            GLib.timeout_add_seconds(3, self.dump_dot_file)

        try:
            self.loop.run()
        except Exception:
            pass

        self.user_data.running = False
        self.pipeline.set_state(Gst.State.NULL)

    def get_pipeline_string(self):
        if self.source_type == "rpi":
            # 1280x720 @ 60fps — IMX708 supports up to 120fps at 720p vs 30fps at 1536x864
            source_element = (
                "libcamerasrc name=src_0 ! "
                f"video/x-raw, format={self.network_format}, width=1280, height=720, framerate=60/1 ! "
                + QUEUE("queue_src_scale")
                + "videoscale ! "
                f"video/x-raw, format={self.network_format}, width={self.network_width}, height={self.network_height}, framerate=60/1 ! "
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
            + QUEUE("queue_hailooverlay")
            + "hailooverlay ! "
            + QUEUE("queue_videoconvert")
            + "videoconvert n-threads=3 qos=false ! "
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


def apply_rule_based_command(user_input_lower):
    global MODE_DND, MODE_EMAIL_OFF, MODE_IDLE, MODE_NIGHT, MODE_EMERGENCY, MODE_PRIVACY
    for phrase, resp in CUSTOM_VOICE_COMMANDS.items():
        if phrase in user_input_lower:
            return resp

    response = None
    with _mode_lock:
        if "activate dnd" in user_input_lower:
            MODE_DND = True; response = "Do Not Disturb activated."
        elif "deactivate dnd" in user_input_lower:
            MODE_DND = False; response = "Do Not Disturb deactivated."
        elif "activate email off" in user_input_lower:
            MODE_EMAIL_OFF = True; response = "Email alerts disabled."
        elif "deactivate email off" in user_input_lower:
            MODE_EMAIL_OFF = False; response = "Email alerts enabled."
        elif "activate idle" in user_input_lower:
            MODE_IDLE = True; response = "Idle mode activated."
        elif "deactivate idle" in user_input_lower:
            MODE_IDLE = False; response = "Idle mode deactivated."
        elif "activate night mode" in user_input_lower or "night mode on" in user_input_lower:
            MODE_NIGHT = True; response = "Night mode activated."
        elif "deactivate night mode" in user_input_lower or "night mode off" in user_input_lower:
            MODE_NIGHT = False; response = "Night mode deactivated."
        elif "activate emergency" in user_input_lower or "emergency on" in user_input_lower:
            MODE_EMERGENCY = True; MODE_DND = False; response = "EMERGENCY MODE activated."
        elif "deactivate emergency" in user_input_lower or "emergency off" in user_input_lower:
            MODE_EMERGENCY = False; response = "Emergency mode deactivated."
        elif "privacy on" in user_input_lower or "enable privacy" in user_input_lower:
            MODE_PRIVACY = True; response = "Privacy masking enabled."
        elif "privacy off" in user_input_lower or "disable privacy" in user_input_lower:
            MODE_PRIVACY = False; response = "Privacy masking disabled."

    if response:
        return response
    if "time" in user_input_lower or "clock" in user_input_lower:
        return f"The time is {datetime.datetime.now().strftime('%I:%M %p')}."
    if any(w in user_input_lower for w in ["hi", "hello", "hey narada"]):
        return "Hello! I'm Narada, your AI security assistant."
    if "how are you" in user_input_lower:
        return "All systems operational. Standing by to assist."
    if "your name" in user_input_lower:
        return "I am Narada, voice assistant of the Garuda Security System."
    if "status" in user_input_lower:
        with _mode_lock:
            parts = [m for m, v in [("DND", MODE_DND), ("Night", MODE_NIGHT),
                                     ("EMERGENCY", MODE_EMERGENCY), ("Idle", MODE_IDLE)] if v]
        active = ", ".join(parts) if parts else "none"
        return f"Active modes: {active}. Threshold: {DETECTION_THRESHOLD:.2f}."
    return "I heard you, but I'm not sure what to do. Try a command like 'activate dnd'."


def query_local_llm(user_input, model=None):
    """Query Groq cloud API — rich project context, live state, natural language commands."""
    if not GROQ_API_KEY:
        return None  # No key configured, fall back to rule-based
    if model is None:
        model = GROQ_MODEL

    # Build live state snapshot (read under lock)
    with _mode_lock:
        active_modes = [name for name, val in [
            ("DND", MODE_DND), ("Email-Off", MODE_EMAIL_OFF),
            ("Idle", MODE_IDLE), ("Night", MODE_NIGHT),
            ("Emergency", MODE_EMERGENCY), ("Privacy", MODE_PRIVACY),
        ] if val]
    uptime_s = int(time.time() - _app_start_time)
    uptime_str = f"{uptime_s // 3600}h {(uptime_s % 3600) // 60}m" if uptime_s >= 60 else f"{uptime_s}s"
    state_str = (
        f"Active modes: {', '.join(active_modes) if active_modes else 'none'}. "
        f"Detection threshold: {DETECTION_THRESHOLD:.2f}. "
        f"Detections today: {_detections_today}. "
        f"Alert active: {_alert_active}. Uptime: {uptime_str}."
    )

    system_prompt = f"""You are Narada, the AI assistant embedded in Garuda — a smart AI home security system built by Manikanta, running on Raspberry Pi 5 with a Hailo-8L AI accelerator and Sony IMX708 camera.

PROJECT OVERVIEW:
Garuda performs real-time AI object detection to monitor the environment. It can detect threats, send email alerts, control operation modes, and respond to natural language commands through you (Narada).

HARDWARE:
- Raspberry Pi 5 (8GB RAM)
- Hailo-8L NPU (13 TOPS) — runs YOLOv6n detection at up to 60fps
- Sony IMX708 camera (1280×720 @ 60fps)
- GPIO: LED indicator (lights up on alerts), HC-SR04 ultrasonic distance sensor

DETECTION SYSTEM:
- Model: YOLOv6n trained on 80 COCO classes
- Danger label: scissors (requires 15 consecutive frames at confidence ≥ 0.55 to trigger — avoids false alarms)
- Detection threshold: adjustable 0.05–0.95 (lower = more sensitive, higher = stricter)
- On threat: plays alarm sound, sends email alert with detection details and timestamp

OPERATION MODES (you can enable/disable these):
- **DND** (Do Not Disturb): Silences all audio alarms. Email alerts still work.
- **Email-Off**: Stops all email alert sending. Local alarms still sound.
- **Idle**: Disables ALL alerts (audio + email). Use when you know someone trusted is present.
- **Night**: High-sensitivity mode — logs all detections, stricter alert criteria.
- **Emergency**: Maximum alert mode — overrides DND, triggers immediate emails.
- **Privacy**: Masks detected objects on the camera feed (blur/box) for privacy.

CURRENT LIVE STATE:
{state_str}
Current time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.

NATURAL LANGUAGE COMMANDS (understand intent, not just exact phrasing):
- "quiet mode" / "turn on dnd" / "don't disturb me" / "mute alerts" → activate DND
- "unmute" / "alerts on" / "disable dnd" → deactivate DND
- "no emails" / "stop email alerts" / "turn off notifications" → activate Email-Off
- "send emails again" / "enable email alerts" → deactivate Email-Off
- "idle" / "I'm home" / "pause monitoring" / "stand down" → activate Idle
- "resume" / "start monitoring" / "watch again" / "back on duty" → deactivate Idle
- "night mode" / "night watch" / "high alert" → activate Night
- "day mode" / "normal mode" / "lower alert" → deactivate Night
- "emergency" / "intruder!" / "maximum alert" → activate Emergency
- "all clear" / "cancel emergency" / "stand down emergency" → deactivate Emergency
- "privacy on" / "hide objects" / "blur camera" / "mask detections" → activate Privacy
- "privacy off" / "show everything" → deactivate Privacy
- "set threshold to 0.5" / "make it less sensitive" / "confidence 0.6" → change DETECTION_THRESHOLD
- "status" / "what's running?" / "system check" → describe current state
- "what can you do?" / "help" / "commands" → list capabilities
- "how does Garuda work?" / "explain the system" → explain the project
- "what was detected?" / "any alerts today?" → report detection stats

RESPONSE FORMAT — you MUST respond ONLY with this exact JSON (no markdown wrapper, no backticks, no prose outside the JSON):
{{"modes":{{"MODE_DND":null,"MODE_EMAIL_OFF":null,"MODE_IDLE":null,"MODE_NIGHT":null,"MODE_EMERGENCY":null,"MODE_PRIVACY":null}},"settings":{{"DETECTION_THRESHOLD":null}},"response":"your reply here"}}

RULES:
- Set mode values to true (activate), false (deactivate), or null (no change).
- Set DETECTION_THRESHOLD to a float 0.05–0.95 if requested, else null.
- "response" must be conversational and helpful. Use **bold**, `code`, and bullet lists where they add clarity.
- When changing a mode, confirm it and explain what it does in 1-2 sentences.
- When answering project questions, use the context above — be accurate.
- Never invent capabilities not described above."""

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_input},
        ],
        "temperature": 0.3,
        "max_tokens": 600,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        res = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json=payload, headers=headers, timeout=8
        )
        res.raise_for_status()
        content = res.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout):
        return None  # Network unavailable — fall back to rule-based
    except Exception:
        return None


def _apply_llm_result(llm_result):
    """Apply mode/settings changes from an LLM JSON response and return the reply text."""
    global DETECTION_THRESHOLD
    def _to_bool(v):
        if isinstance(v, bool): return v
        if isinstance(v, int):  return v != 0
        if isinstance(v, str):
            return v.lower() in ("true", "active", "on", "yes", "1", "enabled")
        return None
    modes_to_change    = llm_result.get("modes", {}) or {}
    settings_to_change = llm_result.get("settings", {}) or {}
    with _mode_lock:
        for key in ["MODE_DND","MODE_EMAIL_OFF","MODE_IDLE","MODE_NIGHT","MODE_EMERGENCY","MODE_PRIVACY"]:
            raw = modes_to_change.get(key)
            if raw is not None:
                val = _to_bool(raw)
                if val is not None:
                    globals()[key] = val
        raw_thr = settings_to_change.get("DETECTION_THRESHOLD")
        if raw_thr is not None:
            try:
                DETECTION_THRESHOLD = max(0.05, min(0.95, float(raw_thr)))
            except (ValueError, TypeError):
                pass
        if MODE_EMERGENCY:
            globals()["MODE_DND"] = False
        if MODE_NIGHT:
            globals()["MODE_DND"] = False
    push_urgent_ws()
    return llm_result.get("response") or "Done."


def voice_assistant_loop(stop_event, current_user=None):
    global MODE_DND, MODE_EMAIL_OFF, MODE_IDLE, MODE_NIGHT, MODE_EMERGENCY, MODE_PRIVACY
    global DETECTION_THRESHOLD

    recognizer = sr.Recognizer()
    try:
        mic = sr.Microphone()
        append_voice_log("Microphone connected.", user_name=current_user)
    except Exception as e:
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

        user_input_lower = user_input.lower()
        llm_result = query_local_llm(user_input)

        if llm_result is not None:
            response = _apply_llm_result(llm_result)
        else:
            response = apply_rule_based_command(user_input_lower)

        append_voice_response(response, user_name=current_user)
        time.sleep(0.5)

##############################################################################
# SESSION MANAGEMENT
##############################################################################
def create_session(username, duration=3600):
    token = secrets.token_hex(32)
    _sessions[token] = {
        "username": username,
        "role": USERS[username]["role"],
        "expires": time.time() + duration,
        "logs_unlocked": False,
    }
    return token

def create_master_session(duration=3600):
    """Create an admin session via master key — logs unlocked immediately."""
    token = secrets.token_hex(32)
    _sessions[token] = {
        "username": "admin",
        "role": "admin",
        "expires": time.time() + duration,
        "logs_unlocked": True,
    }
    return token

def get_session(token):
    if not token:
        return None
    s = _sessions.get(token)
    if s and s["expires"] > time.time():
        return s
    return None

def require_session(request: Request):
    # X-Garuda-Token header takes priority (cross-origin API); cookie is browser fallback
    token = request.headers.get("X-Garuda-Token") or request.cookies.get("garuda_session")
    session = get_session(token)
    if not session:
        raise HTTPException(401, "Not authenticated")
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
def get_state_dict():
    global _alert_active, _alert_flash_count, _danger_trigger_info, _alert_end_time
    # Expire alert once the wall-clock timer runs out
    _just_cleared = False
    with _alert_lock:
        if _alert_active and _alert_end_time > 0 and time.time() >= _alert_end_time:
            _alert_active = False
            _alert_end_time = 0.0
            _alert_flash_count = 0
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
        "danger_info": _danger_trigger_info,   # only non-empty during a scissors alert
        "last_alert": _last_alert_time.isoformat() if _last_alert_time else None,
        "uptime": uptime_str,
        "uptime_seconds": uptime,
        "system_log": system_updates_log[-50:],
        "voice_log": voice_assistant_log[-30:],
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
        "owner_name": next(
            (d["name"] for d in KNOWN_DEVICES if d["mac"].lower() in _last_arp_cache), None
        ),
        "known_devices": [
            {"name": d["name"], "mac": d["mac"],
             "online": d["mac"].lower() in _last_arp_cache}
            for d in KNOWN_DEVICES
        ],
        "alert_history": _alert_history,
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
        "pending_sync": get_pending_count(),
    }

##############################################################################
# DEAD MAN'S SWITCH MONITOR
##############################################################################
def _deadman_monitor():
    """Background thread: if no /api/heartbeat in _DEADMAN_TIMEOUT seconds, send tamper alert."""
    global _deadman_alert_sent
    while True:
        time.sleep(60)
        elapsed = time.time() - _last_heartbeat
        if elapsed > _DEADMAN_TIMEOUT and not _deadman_alert_sent:
            _deadman_alert_sent = True
            log_system_update(f"[TAMPER] No heartbeat in {int(elapsed)}s — possible system tampering!")
            # Send tamper alert email
            try:
                body = (f"Garuda dead man's switch triggered.\n"
                        f"No heartbeat received in {int(elapsed)} seconds.\n"
                        f"Possible system tampering or network failure.")
                msg = MIMEText(body)
                msg['Subject'] = "TAMPER ALERT: Garuda heartbeat missed"
                msg['From'] = EMAIL_SENDER
                msg['To'] = ", ".join(EMAIL_RECIPIENTS)
                with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
                    server.login(EMAIL_SENDER, EMAIL_SENDER_PASS)
                    server.send_message(msg)
            except Exception as e:
                log_system_update(f"[TAMPER] Failed to send alert email: {e}")

##############################################################################
# FASTAPI APP
##############################################################################
from contextlib import asynccontextmanager

@asynccontextmanager
async def _lifespan(app):
    global _event_loop, _ws_trigger
    _event_loop = asyncio.get_event_loop()
    _ws_trigger = asyncio.Event()
    _init_event_db()
    _load_alert_history()
    _load_presence_log()
    load_master_keys()
    asyncio.ensure_future(_ws_broadcaster())
    threading.Thread(target=_presence_poller, daemon=True).start()
    threading.Thread(target=_deadman_monitor, daemon=True).start()
    threading.Thread(target=_connectivity_monitor, daemon=True).start()
    yield
    # Close any open WebRTC peer connections on shutdown
    if _pc_set:
        await asyncio.gather(*[pc.close() for pc in list(_pc_set)], return_exceptions=True)
        _pc_set.clear()

fastapi_app = FastAPI(title="Garuda Security System", lifespan=_lifespan)

# CORS — restrict to known origins
fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://garuda.veeramanikanta.in",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Garuda-Token"],
)

# Security headers middleware
@fastapi_app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' blob: data:; "
        "connect-src 'self' wss: ws:; "
        "frame-ancestors 'none'"
    )
    return response

# Serve static files from garuda_web/
_static_dir = Path(__file__).parent / "garuda_web"
if _static_dir.exists():
    fastapi_app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# ── Pydantic models ──────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str
    remember_me: bool = False

class ModeRequest(BaseModel):
    mode: str   # "dnd","email_off","idle","night","emergency","privacy"
    value: bool

class AddUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"
    display_name: str = ""
    box_color: str = "#1565c0"

class DeleteUserRequest(BaseModel):
    username: str

class UpdateUserRequest(BaseModel):
    username: str
    new_password: Optional[str] = None
    display_name: Optional[str] = None
    box_color: Optional[str] = None

class ConfigUpdateRequest(BaseModel):
    detection_threshold: Optional[float] = None
    email_sender: Optional[str] = None
    email_recipients: Optional[List[str]] = None
    email_cooldown: Optional[int] = None
    danger_label: Optional[str] = None
    privacy: Optional[bool] = None
    watch_labels: Optional[List[str]] = None

class DeviceAddRequest(BaseModel):
    name: str
    mac: str

class DeviceDeleteRequest(BaseModel):
    mac: str

class CustomCommandRequest(BaseModel):
    phrase: str
    response: str

class DeleteCommandRequest(BaseModel):
    phrase: str

class OTPRequest(BaseModel):
    username: str
    password: str

class VerifyOTPRequest(BaseModel):
    username: str
    otp: str

class ForgotPasswordRequest(BaseModel):
    otp: str
    new_password: str

class SendForgotOTPRequest(BaseModel):
    username: str

class WebRTCOfferRequest(BaseModel):
    sdp: str
    type: str

class ChatRequest(BaseModel):
    message: str

# ── Routes ───────────────────────────────────────────────────────────────────

@fastapi_app.get("/", response_class=HTMLResponse)
async def index():
    html_path = _static_dir / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return HTMLResponse("<h1>Garuda Web</h1><p>garuda_web/index.html not found.</p>")

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
    if not _check_rate_limit(request):
        raise HTTPException(429, "Too many requests. Try again later.")
    u = data.username.strip()
    p = data.password.strip()
    if u in USERS and USERS[u].get("role") == "admin":
        raise HTTPException(403, "Admin accounts must sign in via the Admin Access flow.")
    if u in USERS and _verify_password(p, USERS[u]["password"]):
        # Auto-migrate plaintext passwords to hashed
        if not USERS[u]["password"].startswith("pbkdf2:"):
            USERS[u]["password"] = _hash_password(p)
            save_users()
        duration = 5 * 24 * 3600 if data.remember_me else 3600
        token = create_session(u, duration)
        response.set_cookie("garuda_session", token, httponly=True, samesite="lax", max_age=duration)
        log_system_update(f"Login: {u}")
        USERS[u]["history"]["logins"].append(
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        save_users()
        return {
            "role": USERS[u]["role"],
            "username": u,
            "display_name": USERS[u].get("display_name", u),
            "box_color": USERS[u].get("box_color", "#1565c0"),
            "token": token,   # for cross-origin clients that can't use cookies
        }
    raise HTTPException(401, "Invalid username or password.")

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
    token = request.cookies.get("garuda_session")
    if token and token in _sessions:
        del _sessions[token]
    response.delete_cookie("garuda_session")
    return {"ok": True}

@fastapi_app.post("/api/admin/send-otp")
async def admin_send_otp(data: OTPRequest, request: Request, response: Response):
    """Admin login step 1: verify credentials, send OTP."""
    if not _check_rate_limit(request):
        raise HTTPException(429, "Too many requests. Try again later.")
    global ADMIN_OTP
    u = data.username.strip()
    p = data.password.strip()
    if u not in USERS or not _verify_password(p, USERS[u]["password"]) or USERS[u]["role"] != "admin":
        raise HTTPException(401, "Invalid admin credentials.")
    # Auto-migrate plaintext passwords to hashed
    if not USERS[u]["password"].startswith("pbkdf2:"):
        USERS[u]["password"] = _hash_password(p)
        save_users()
    ADMIN_OTP = generate_otp_code(6)
    dest = EMAIL_RECIPIENTS[0] if EMAIL_RECIPIENTS else EMAIL_SENDER
    ok, err = send_otp_via_email(dest, ADMIN_OTP)
    if not ok:
        return {"ok": False, "error": err}
    return {"ok": True}

@fastapi_app.post("/api/admin/verify-otp")
async def admin_verify_otp(data: VerifyOTPRequest, request: Request, response: Response):
    """Admin login step 2: verify OTP, issue session."""
    if not _check_rate_limit(request):
        raise HTTPException(429, "Too many requests. Try again later.")
    global ADMIN_OTP
    if data.otp.strip() != ADMIN_OTP:
        raise HTTPException(401, "Invalid OTP.")
    ADMIN_OTP = None
    token = create_session(data.username)
    response.set_cookie("garuda_session", token, httponly=True, samesite="lax", max_age=3600)
    log_system_update(f"Admin login: {data.username}")
    return {
        "role": "admin",
        "username": data.username,
        "display_name": USERS[data.username].get("display_name", data.username),
        "token": token,   # for cross-origin clients
    }

@fastapi_app.post("/api/forgot/send-otp")
async def forgot_send_otp(data: SendForgotOTPRequest, request: Request):
    if not _check_rate_limit(request):
        raise HTTPException(429, "Too many requests. Try again later.")
    global USER_FORGOT_OTP, _forgot_otp_user
    u = data.username.strip()
    if u not in USERS:
        raise HTTPException(404, "User not found.")
    USER_FORGOT_OTP = generate_otp_code(6)
    _forgot_otp_user = u
    dest = EMAIL_RECIPIENTS[0] if EMAIL_RECIPIENTS else EMAIL_SENDER
    ok, err = send_otp_via_email(dest, USER_FORGOT_OTP)
    if not ok:
        return {"ok": False, "error": err}
    return {"ok": True}

@fastapi_app.post("/api/forgot/reset")
async def forgot_reset(data: ForgotPasswordRequest, request: Request):
    if not _check_rate_limit(request):
        raise HTTPException(429, "Too many requests. Try again later.")
    global USER_FORGOT_OTP, _forgot_otp_user
    if data.otp.strip() != USER_FORGOT_OTP or not _forgot_otp_user:
        raise HTTPException(401, "Invalid OTP.")
    if not data.new_password.strip():
        raise HTTPException(400, "Password cannot be empty.")
    USERS[_forgot_otp_user]["password"] = _hash_password(data.new_password.strip())
    save_users()
    log_system_update(f"Password reset for {_forgot_otp_user}.")
    USER_FORGOT_OTP = None
    _forgot_otp_user = None
    return {"ok": True}

@fastapi_app.get("/api/state")
async def get_state(session=Depends(require_session)):
    return get_state_dict()

@fastapi_app.post("/api/chat")
async def chat(data: ChatRequest, session=Depends(require_session)):
    msg = data.message.strip()
    if not msg:
        raise HTTPException(400, "Empty message")
    if not GROQ_API_KEY:
        reply = ("Narada is not configured yet. "
                 "Please go to Admin → Settings → Narada and enter your Groq API key, then click Save Settings.")
        return {"response": reply}
    loop = asyncio.get_event_loop()
    llm_result = await loop.run_in_executor(None, query_local_llm, msg, GROQ_MODEL)
    if llm_result is not None:
        reply = _apply_llm_result(llm_result)
    else:
        reply = apply_rule_based_command(msg.lower())
    return {"response": reply}

def _groq_stream_text(user_input):
    """Sync generator: yields text tokens from Groq streaming API."""
    if not GROQ_API_KEY:
        yield apply_rule_based_command(user_input.lower())
        return
    system_prompt = (
        "You are Narada, the AI assistant embedded in Garuda — an AI home security system "
        "running on Raspberry Pi 5 with Hailo-8L AI accelerator and IMX708 camera (1280×720 @ 60fps).\n"
        "System details: YOLOv6n object detection, danger label = scissors (single-frame trigger, "
        "60s cooldown between alerts), modes: DND / Night / Emergency / Idle / Privacy, detection threshold "
        "(0.05–0.95 default 0.35), email alerts via Gmail SMTP, WebRTC + WS binary JPEG + MJPEG "
        "camera streaming, Groq LLM (llama-3.3-70b-versatile) for this chat.\n"
        "When the user requests a mode or setting change, confirm what you're doing. "
        "Be concise and direct. Use markdown (bold, code blocks, lists) where it adds clarity."
    )
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_input},
        ],
        "temperature": 0.7,
        "max_tokens": 600,
        "stream": True,
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        with requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json=payload, headers=headers, stream=True, timeout=30
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                if line.startswith(b"data: "):
                    chunk_raw = line[6:]
                    if chunk_raw == b"[DONE]":
                        return
                    try:
                        chunk = json.loads(chunk_raw)
                        token = chunk["choices"][0]["delta"].get("content", "")
                        if token:
                            yield token
                    except Exception:
                        pass
    except Exception:
        yield apply_rule_based_command(user_input.lower())

@fastapi_app.post("/api/chat/stream")
async def chat_stream(data: ChatRequest, session=Depends(require_session)):
    """SSE streaming chat — tokens arrive in real-time; commands applied after full response."""
    msg = data.message.strip()
    if not msg:
        raise HTTPException(400, "Empty message")

    loop  = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def _worker():
        full_tokens = []
        for token in _groq_stream_text(msg):
            full_tokens.append(token)
            loop.call_soon_threadsafe(queue.put_nowait, ("token", token))
        # After full response, run structured JSON call in background to apply commands
        full_text = "".join(full_tokens)
        llm_result = query_local_llm(msg)
        if llm_result is not None:
            _apply_llm_result(llm_result)
        loop.call_soon_threadsafe(queue.put_nowait, ("done", None))

    threading.Thread(target=_worker, daemon=True).start()

    async def generate():
        yield f"data: {json.dumps({'type': 'start'})}\n\n"
        while True:
            try:
                kind, payload_val = await asyncio.wait_for(queue.get(), timeout=35)
            except asyncio.TimeoutError:
                break
            if kind == "done":
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                break
            yield f"data: {json.dumps({'type': 'token', 'text': payload_val})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

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
    with _mode_lock:
        globals()[mode_map[data.mode]] = data.value
        if MODE_EMERGENCY:
            MODE_DND = False
        if MODE_NIGHT:
            MODE_DND = False
    log_system_update(f"Mode {data.mode} set to {data.value} by {session['username']}")
    push_urgent_ws()
    return {"ok": True, "modes": get_state_dict()["modes"]}

@fastapi_app.get("/api/users")
async def list_users(session=Depends(require_admin)):
    result = {}
    for uname, udata in USERS.items():
        result[uname] = {
            "role": udata.get("role"),
            "display_name": udata.get("display_name", uname),
            "box_color": udata.get("box_color", "#1565c0"),
        }
    return result

@fastapi_app.post("/api/users/add")
async def add_user(data: AddUserRequest, session=Depends(require_admin)):
    if data.username in USERS:
        raise HTTPException(400, "Username already exists.")
    if not data.username.strip() or not data.password.strip():
        raise HTTPException(400, "Username and password required.")
    USERS[data.username] = {
        "password": _hash_password(data.password),
        "role": data.role,
        "display_name": data.display_name or data.username.capitalize(),
        "box_color": data.box_color,
        "history": {"logins": [], "narada_activity": []},
    }
    save_users()
    log_system_update(f"User added: {data.username}")
    return {"ok": True}

@fastapi_app.post("/api/users/delete")
async def delete_user(data: DeleteUserRequest, session=Depends(require_admin)):
    if data.username == "admin":
        raise HTTPException(400, "Cannot delete the admin account.")
    if data.username not in USERS:
        raise HTTPException(404, "User not found.")
    del USERS[data.username]
    save_users()
    log_system_update(f"User deleted: {data.username}")
    return {"ok": True}

@fastapi_app.post("/api/users/update")
async def update_user(data: UpdateUserRequest, session=Depends(require_admin)):
    if data.username not in USERS:
        raise HTTPException(404, "User not found.")
    if data.new_password:
        USERS[data.username]["password"] = _hash_password(data.new_password)
    if data.display_name is not None:
        USERS[data.username]["display_name"] = data.display_name
    if data.box_color is not None:
        USERS[data.username]["box_color"] = data.box_color
    save_users()
    log_system_update(f"User updated: {data.username}")
    return {"ok": True}

@fastapi_app.get("/api/config")
async def get_config(session=Depends(require_admin)):
    return {
        "detection_threshold": DETECTION_THRESHOLD,
        "email_sender": EMAIL_SENDER,
        "email_recipients": EMAIL_RECIPIENTS,
        "email_cooldown": EMAIL_COOLDOWN,
        "privacy": MODE_PRIVACY,
        "custom_voice_commands": CUSTOM_VOICE_COMMANDS,
        "custom_modes": CUSTOM_MODES,
        "watch_labels": WATCH_LABELS,
        "groq_configured": bool(GROQ_API_KEY),
    }

@fastapi_app.post("/api/config")
async def update_config(data: ConfigUpdateRequest, session=Depends(require_admin)):
    global DETECTION_THRESHOLD, EMAIL_SENDER
    global EMAIL_RECIPIENTS, EMAIL_COOLDOWN, MODE_PRIVACY
    if data.detection_threshold is not None:
        DETECTION_THRESHOLD = max(0.05, min(0.95, data.detection_threshold))
    if data.email_sender is not None:
        EMAIL_SENDER = data.email_sender
    if data.email_recipients is not None:
        EMAIL_RECIPIENTS = data.email_recipients
    if data.email_cooldown is not None:
        EMAIL_COOLDOWN = data.email_cooldown
    if data.privacy is not None:
        with _mode_lock:
            MODE_PRIVACY = data.privacy
    if data.danger_label is not None:
        # Update danger label in user_data if available
        if app_gst and hasattr(app_gst, 'user_data'):
            app_gst.user_data.danger_label = data.danger_label
    if data.watch_labels is not None:
        global WATCH_LABELS
        WATCH_LABELS = [l.strip() for l in data.watch_labels if l.strip()]
    save_config()
    log_system_update("Config updated.")
    return {"ok": True}

@fastapi_app.post("/api/config/command/add")
async def add_command(data: CustomCommandRequest, session=Depends(require_admin)):
    CUSTOM_VOICE_COMMANDS[data.phrase.lower()] = data.response
    save_config()
    return {"ok": True}

@fastapi_app.post("/api/config/command/delete")
async def delete_command(data: DeleteCommandRequest, session=Depends(require_admin)):
    CUSTOM_VOICE_COMMANDS.pop(data.phrase.lower(), None)
    save_config()
    return {"ok": True}

@fastapi_app.get("/api/devices")
async def get_devices(session=Depends(require_admin)):
    return {"devices": KNOWN_DEVICES, "owner_present": _owner_present}

@fastapi_app.get("/api/arp")
async def get_arp_table(session=Depends(require_admin)):
    """Return all active ARP entries so admin can identify device MACs."""
    entries = []
    try:
        with open('/proc/net/arp') as f:
            for line in f.readlines()[1:]:   # skip header
                parts = line.split()
                if len(parts) >= 4 and parts[2] == '0x2':  # 0x2 = complete entry
                    entries.append({"ip": parts[0], "mac": parts[3]})
    except Exception as e:
        raise HTTPException(500, str(e))
    registered_macs = {d["mac"].lower() for d in KNOWN_DEVICES}
    for e in entries:
        e["registered"] = e["mac"].lower() in registered_macs
    return {"entries": entries}

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
            dev = next((d["name"] for d in KNOWN_DEVICES if d["mac"].lower() in _last_arp_cache), "Unknown")
            mac = next((d["mac"]  for d in KNOWN_DEVICES if d["mac"].lower() in _last_arp_cache), "")
            _append_presence_log("arrived", dev, mac)
            log_system_update(f"[OWNER] {dev} arrived (manual refresh).")
    elif _owner_present:
        _owner_present = False
        dev = next((d["name"] for d in KNOWN_DEVICES), "Unknown")
        _append_presence_log("left", dev, "")
        log_system_update(f"[OWNER] {dev} away (manual refresh — device not found).")

@fastapi_app.post("/api/presence_refresh")
async def presence_refresh(session=Depends(require_session)):
    """Trigger an immediate ARP presence check without waiting for the 30s poller."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _do_presence_check)
    push_urgent_ws()
    return {"owner_present": _owner_present}

@fastapi_app.post("/api/devices/add")
async def add_device(data: DeviceAddRequest, session=Depends(require_admin)):
    mac = data.mac.strip().lower()
    if not re.match(r'^([0-9a-f]{2}:){5}[0-9a-f]{2}$', mac):
        raise HTTPException(400, "Invalid MAC address format (use aa:bb:cc:dd:ee:ff)")
    if any(d['mac'].lower() == mac for d in KNOWN_DEVICES):
        raise HTTPException(400, "Device with this MAC already registered")
    KNOWN_DEVICES.append({"name": data.name.strip(), "mac": mac})
    save_config()
    log_system_update(f"Known device added: {data.name.strip()} ({mac})")
    return {"ok": True, "devices": KNOWN_DEVICES}

@fastapi_app.post("/api/devices/delete")
async def delete_device(data: DeviceDeleteRequest, session=Depends(require_admin)):
    mac = data.mac.strip().lower()
    before = len(KNOWN_DEVICES)
    KNOWN_DEVICES[:] = [d for d in KNOWN_DEVICES if d['mac'].lower() != mac]
    if len(KNOWN_DEVICES) == before:
        raise HTTPException(404, "Device not found")
    save_config()
    log_system_update(f"Known device removed: {mac}")
    return {"ok": True, "devices": KNOWN_DEVICES}

@fastapi_app.post("/api/email/test")
async def test_email(session=Depends(require_admin)):
    dest = EMAIL_RECIPIENTS[0] if EMAIL_RECIPIENTS else EMAIL_SENDER
    ok, err = send_otp_via_email(dest, "TEST-123")
    if not ok:
        return {"ok": False, "error": err}
    return {"ok": True}

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
                    with open(filepath, encoding="utf-8") as f:
                        content = f.read().strip()
                    parts.append(content if content else "(no entries)")
                else:
                    parts.append("(no entries)")
        except Exception as ex:
            parts.append(f"(error reading log: {ex})")
        parts.append("")

    content = "\n".join(parts)
    fname = f"garuda-full-log-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )

##############################################################################
# MASTER KEY ENDPOINTS
##############################################################################
@fastapi_app.post("/api/master_key/login")
async def master_key_login(data: dict, request: Request, response: Response):
    """Log in with only a master key — issues an admin session with logs unlocked."""
    if not _check_rate_limit(request):
        raise HTTPException(429, "Too many requests. Try again later.")
    key = (data.get("key") or "").strip()
    if not key or key not in MASTER_KEYS:
        raise HTTPException(401, "Invalid master key.")
    token = create_master_session()
    response.set_cookie("garuda_session", token, httponly=True, samesite="lax", max_age=3600)
    log_system_update("Master key login.")
    return {
        "role": "admin",
        "username": "admin",
        "display_name": USERS.get("admin", {}).get("display_name", "Admin"),
        "token": token,
        "logs_unlocked": True,
    }

@fastapi_app.post("/api/master_key/verify")
async def master_key_verify(data: dict, request: Request):
    """Unlock logs on an existing admin session by verifying a master key."""
    require_admin(request)
    key = (data.get("key") or "").strip()
    if not key or key not in MASTER_KEYS:
        raise HTTPException(401, "Invalid master key.")
    token = request.cookies.get("garuda_session") or request.headers.get("X-Garuda-Token")
    if token and token in _sessions:
        _sessions[token]["logs_unlocked"] = True
    return {"ok": True, "logs_unlocked": True}

@fastapi_app.get("/api/master_keys")
async def list_master_keys(session=Depends(require_admin)):
    """Return master keys with all but last 4 chars masked."""
    masked = []
    for k in MASTER_KEYS:
        if len(k) > 4:
            masked.append("\u2022" * (len(k) - 4) + k[-4:])
        else:
            masked.append("\u2022\u2022\u2022\u2022")
    return {"keys": masked, "count": len(MASTER_KEYS)}

@fastapi_app.post("/api/master_key/request_otp")
async def master_key_request_otp(data: dict, session=Depends(require_admin)):
    """Step 1 of adding a master key: verify an existing key, then email OTP."""
    global MASTER_KEY_OTP
    current = (data.get("current_key") or "").strip()
    if not current or current not in MASTER_KEYS:
        raise HTTPException(401, "Current master key is incorrect.")
    MASTER_KEY_OTP = generate_otp_code(6)
    dest = EMAIL_RECIPIENTS[0] if EMAIL_RECIPIENTS else EMAIL_SENDER
    ok, err = send_otp_via_email(dest, MASTER_KEY_OTP)
    if not ok:
        return {"ok": False, "error": err}
    return {"ok": True}

@fastapi_app.post("/api/master_key/add")
async def master_key_add(data: dict, session=Depends(require_admin)):
    """Step 2: verify OTP and persist new master key."""
    global MASTER_KEY_OTP
    otp = (data.get("otp") or "").strip()
    new_key = (data.get("new_key") or "").strip()
    if not otp or otp != MASTER_KEY_OTP:
        raise HTTPException(401, "Invalid OTP.")
    if not new_key or len(new_key) < 12:
        raise HTTPException(400, "Key must be at least 12 characters.")
    if not re.search(r'[A-Z]', new_key):
        raise HTTPException(400, "Key must contain at least one uppercase letter.")
    if not re.search(r'[a-z]', new_key):
        raise HTTPException(400, "Key must contain at least one lowercase letter.")
    if not re.search(r'[0-9]', new_key):
        raise HTTPException(400, "Key must contain at least one number.")
    if not re.search(r'[^A-Za-z0-9]', new_key):
        raise HTTPException(400, "Key must contain at least one symbol (!@#$ etc.).")
    _MK_COMMON = ['password','master','admin','garuda','security','qwerty','asdfgh',
                   'zxcvbn','123456','letmein','welcome','login','access']
    if any(w in new_key.lower() for w in _MK_COMMON):
        raise HTTPException(400, "Key contains a common word or sequence — choose something more random.")
    if new_key in MASTER_KEYS:
        raise HTTPException(400, "Key already exists.")
    # Reject keys too similar to existing ones (shared 6-char substring)
    for existing in MASTER_KEYS:
        for i in range(len(existing) - 5):
            if existing[i:i+6] in new_key:
                raise HTTPException(400, "Key is too similar to an existing master key.")
    MASTER_KEYS.append(new_key)
    save_master_keys()
    MASTER_KEY_OTP = None
    log_system_update("New master key added.")
    return {"ok": True}

@fastapi_app.post("/api/master_key/delete")
async def master_key_delete(data: dict, session=Depends(require_admin)):
    """Delete a master key by index — cannot delete the last key."""
    idx = data.get("index")
    if idx is None or not isinstance(idx, int):
        raise HTTPException(400, "index required.")
    if len(MASTER_KEYS) <= 1:
        raise HTTPException(400, "Cannot delete the last master key.")
    if idx < 0 or idx >= len(MASTER_KEYS):
        raise HTTPException(400, "Index out of range.")
    MASTER_KEYS.pop(idx)
    save_master_keys()
    log_system_update("Master key deleted.")
    return {"ok": True}

@fastapi_app.get("/api/heartbeat")
async def heartbeat(request: Request, key: Optional[str] = None):
    """Health check for external monitors (UptimeRobot etc.).
    Accepts an optional ?key= query param or X-Heartbeat-Key header to guard
    the dead-man reset. Without a key the endpoint still returns health data
    but does NOT reset the deadman timer (prevents unauthenticated suppression).
    """
    global _last_heartbeat, _deadman_alert_sent
    _HEARTBEAT_KEY = os.environ.get("HEARTBEAT_KEY", "")
    provided = key or request.headers.get("X-Heartbeat-Key", "")
    # Only reset dead-man's switch if key matches (or no key configured)
    if not _HEARTBEAT_KEY or provided == _HEARTBEAT_KEY:
        _last_heartbeat = time.time()
        _deadman_alert_sent = False
    return {"ok": True, "uptime": int(time.time() - _app_start_time)}

@fastapi_app.post("/api/emergency-stop")
async def emergency_stop(session=Depends(require_session)):
    log_system_update(f"Emergency stop by {session['username']}.")
    threading.Thread(target=stop_app, daemon=True).start()
    return {"ok": True}

# ── Offline event queue endpoints ─────────────────────────────────────────────
@fastapi_app.get("/api/events/since")
async def events_since(since: str = "", limit: int = 500, session=Depends(require_session)):
    """Return events after the given ISO timestamp, oldest-first."""
    events = get_events_since(since, limit)
    return {"events": events, "count": len(events)}

@fastapi_app.get("/api/events/pending")
async def events_pending(session=Depends(require_session)):
    """Return all unsynced events and mark them as synced."""
    events = get_events_since("", 1000)
    unsynced = [e for e in events if not e.get("synced")]
    if unsynced:
        max_id = max(e["id"] for e in unsynced)
        mark_events_synced(max_id)
    return {"events": unsynced, "count": len(unsynced)}

@fastapi_app.get("/api/events/stats")
async def events_stats(session=Depends(require_session)):
    """Return queue statistics."""
    pending = get_pending_count()
    total = 0
    with _eq_lock:
        try:
            conn = sqlite3.connect(EVENTS_DB, timeout=5)
            total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            conn.close()
        except Exception:
            pass
    return {"pending": pending, "total": total, "online": _net_online}

# ── MJPEG stream ─────────────────────────────────────────────────────────────
# Uses _frame_seq to detect new frames only — avoids re-sending duplicate
# frames and keeps per-client CPU near zero when the pipeline is idle.
@fastapi_app.get("/stream")
async def mjpeg_stream(request: Request, token: Optional[str] = None):
    # Authenticate via cookie or ?token= query param
    session_token = request.cookies.get("garuda_session") or token
    if not get_session(session_token):
        raise HTTPException(401, "Not authenticated")
    async def generate():
        last_seq = -1
        last_sent = 0.0
        while True:
            if await request.is_disconnected():
                break
            now = time.time()
            with _frame_lock:
                seq = _frame_seq
                raw = _frame_raw if seq != last_seq else None
            if raw is not None and (now - last_sent) >= 0.033:
                _, jpeg = cv2.imencode('.jpg', raw, [cv2.IMWRITE_JPEG_QUALITY, 75])
                last_seq = seq
                last_sent = now
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n")
            else:
                await asyncio.sleep(0.005)
    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

# ── WebRTC offer/answer ───────────────────────────────────────────────────────
@fastapi_app.post("/webrtc/offer")
async def webrtc_offer(data: WebRTCOfferRequest, session=Depends(require_session)):
    if not _WEBRTC_AVAILABLE:
        raise HTTPException(501, "aiortc not installed")
    pc = RTCPeerConnection()
    _pc_set.add(pc)

    @pc.on("connectionstatechange")
    async def _on_state():
        if pc.connectionState in ("failed", "closed", "disconnected"):
            await pc.close()
            _pc_set.discard(pc)

    pc.addTrack(GarudaVideoTrack())
    offer = RTCSessionDescription(sdp=data.sdp, type=data.type)
    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    # Wait for ICE gathering to complete
    while pc.iceGatheringState != "complete":
        await asyncio.sleep(0.1)

    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}

# ── WebSocket binary JPEG stream (CF Tunnel fallback) ────────────────────────
@fastapi_app.websocket("/ws/stream")
async def ws_stream(websocket: WebSocket, token: Optional[str] = None):
    """Streams JPEG frames as binary WebSocket messages (~same as MJPEG but WS).
    Works through Cloudflare Tunnel (unlike raw UDP WebRTC)."""
    token = websocket.cookies.get("garuda_session") or token
    if not get_session(token):
        await websocket.close(code=4001)
        return
    await websocket.accept()
    last_seq = -1
    try:
        while True:
            with _frame_lock:
                seq   = _frame_seq
                frame = _frame_buffer if seq != last_seq else None
            if frame is not None:
                last_seq = seq
                await websocket.send_bytes(frame)
            else:
                await asyncio.sleep(0.005)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log_system_update(f"[STREAM] WS stream error: {type(e).__name__}")

# ── WebSocket broadcaster (event-driven) ─────────────────────────────────────
# Waits on _ws_trigger asyncio.Event with a 2s timeout (heartbeat).
# push_urgent_ws() sets the event from any thread → immediate broadcast.
# Compute state ONCE per tick and fan-out via asyncio.gather — O(1) in CPU.

async def _ws_broadcaster():
    """Background task: push state immediately on events, or every 2s as heartbeat."""
    while True:
        try:
            await asyncio.wait_for(_ws_trigger.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass
        _ws_trigger.clear()
        payload = get_state_dict()   # always run — handles alert expiry even without clients
        if not _ws_clients:
            continue
        dead: set = set()
        results = await asyncio.gather(
            *[ws.send_json(payload) for ws in list(_ws_clients)],
            return_exceptions=True
        )
        for ws, result in zip(list(_ws_clients), results):
            if isinstance(result, Exception):
                dead.add(ws)
        _ws_clients.difference_update(dead)


@fastapi_app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: Optional[str] = None):
    # Accept token from cookie (same-origin) or query param (cross-origin)
    token = websocket.cookies.get("garuda_session") or token
    if not get_session(token):
        await websocket.close(code=4001)
        return
    await websocket.accept()
    _ws_clients.add(websocket)
    try:
        # Keep the connection alive; broadcaster pushes state.
        # Also drain any pings/pongs from the client so the socket stays healthy.
        while True:
            try:
                # Drain any client messages (pings/pongs); 60s timeout before
                # declaring the connection dead and exiting the loop.
                await asyncio.wait_for(websocket.receive_text(), timeout=60)
            except asyncio.TimeoutError:
                # No message in 60s → connection likely dead, exit cleanly
                break
    except Exception:
        pass
    finally:
        _ws_clients.discard(websocket)

##############################################################################
# MAIN
##############################################################################
def run_web_app(args):
    global app_gst

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

    # Start voice assistant thread
    threading.Thread(
        target=voice_assistant_loop,
        args=(_voice_stop_event,),
        daemon=True
    ).start()

    # Start pipeline thread (restarts automatically on failure)
    threading.Thread(target=_run_pipeline, daemon=True).start()

    print("\n" + "="*60)
    print("  Garuda Web UI is running at http://localhost:8080")
    print("="*60 + "\n")

    # Run uvicorn on the MAIN thread so the process stays alive
    uvicorn.run(fastapi_app, host="0.0.0.0", port=8080, log_level="warning")


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
