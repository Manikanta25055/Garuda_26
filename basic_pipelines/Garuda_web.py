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
import asyncio
import threading
from pathlib import Path

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

try:
    import psutil
except ImportError:
    psutil = None

from fastapi import FastAPI, WebSocket, HTTPException, Request, Response, Depends
from fastapi.responses import StreamingResponse, HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import uvicorn

from hailo_rpi_common import (
    get_default_parser,
    QUEUE,
    get_caps_from_pad,
    get_numpy_from_buffer,
    GStreamerApp,
    app_callback_class,
)

##############################################################################
# EMAIL CONFIG
##############################################################################
EMAIL_SENDER = "mgonugondlamanikanta@gmail.com"
EMAIL_SENDER_PASS = "grhy ipzy hedi xprp"
EMAIL_RECIPIENTS = ["amarmanikantan@gmail.com"]
EMAIL_COOLDOWN = 60
last_email_sent_time = 0

##############################################################################
# GLOBALS & SETTINGS
##############################################################################
app_gst = None  # GStreamer app instance

SCISSORS_LOG_FILE = "danger_sightings.txt"
NIGHT_MODE_LOG_FILE = "night_mode_findings.txt"
LLM_LOG_FILE = "system_logs/llm_reasoning.json"
USERS_FILE = "system_logs/users.json"
CONFIG_FILE = "system_logs/config.json"

system_updates_log: List[str] = []
voice_assistant_log: List[str] = []
voice_responses: List[str] = []
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
_danger_trigger_info = ""   # detection text from the SPECIFIC frame that triggered scissors
_app_start_time = time.time()
_detections_today = 0
_last_alert_time = None
_mode_lock = threading.Lock()
_class_counts_today = {}   # class_name → count since startup
_total_frames = 0          # total inference frames (for avg FPS)

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

# MJPEG frame buffer
_frame_buffer = None
_frame_lock   = threading.Lock()
_frame_seq    = 0          # incremented every new frame; lets MJPEG clients skip duplicates

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
        os.makedirs("system_logs", exist_ok=True)
        with open(USERS_FILE, "w") as f:
            json.dump(USERS, f, indent=2)
    except Exception as e:
        log_system_update(f"Failed to save users: {e}")

def load_config():
    global CUSTOM_VOICE_COMMANDS, CUSTOM_MODES, EMAIL_RECIPIENTS
    global EMAIL_COOLDOWN, EMAIL_SENDER, EMAIL_SENDER_PASS, DETECTION_THRESHOLD
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            CUSTOM_VOICE_COMMANDS = cfg.get("custom_voice_commands", CUSTOM_VOICE_COMMANDS)
            CUSTOM_MODES = cfg.get("custom_modes", CUSTOM_MODES)
            EMAIL_RECIPIENTS = cfg.get("email_recipients", EMAIL_RECIPIENTS)
            EMAIL_COOLDOWN = cfg.get("email_cooldown", EMAIL_COOLDOWN)
            EMAIL_SENDER = cfg.get("email_sender", EMAIL_SENDER)
            EMAIL_SENDER_PASS = cfg.get("email_sender_pass", EMAIL_SENDER_PASS)
            DETECTION_THRESHOLD = cfg.get("detection_threshold", DETECTION_THRESHOLD)
        except Exception as e:
            print(f"Warning: failed to load config: {e}")

def save_config():
    try:
        os.makedirs("system_logs", exist_ok=True)
        cfg = {
            "custom_voice_commands": CUSTOM_VOICE_COMMANDS,
            "custom_modes": CUSTOM_MODES,
            "email_recipients": EMAIL_RECIPIENTS,
            "email_cooldown": EMAIL_COOLDOWN,
            "email_sender": EMAIL_SENDER,
            "email_sender_pass": EMAIL_SENDER_PASS,
            "detection_threshold": DETECTION_THRESHOLD,
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        log_system_update(f"Failed to save config: {e}")

load_users()
load_config()

##############################################################################
# HELPERS
##############################################################################
def log_system_update(message):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    system_updates_log.append(f"[{timestamp}] {message}")

def append_voice_log(message, user_name=None):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {message}"
    voice_assistant_log.append(entry)
    if user_name and user_name in USERS:
        USERS[user_name]["history"]["narada_activity"].append(entry)

def append_voice_response(message, user_name=None):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {message}"
    voice_responses.append(entry)
    if user_name and user_name in USERS:
        USERS[user_name]["history"]["narada_activity"].append(entry)

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
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10)
        server.login(EMAIL_SENDER, EMAIL_SENDER_PASS)
        server.send_message(msg)
        server.quit()
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
# ALERTS
##############################################################################
def trigger_software_alert():
    global _alert_active, _alert_flash_count, _last_alert_time
    with _mode_lock:
        dnd = MODE_DND
        idle = MODE_IDLE
        night = MODE_NIGHT
    if dnd or idle:
        log_system_update("Alert skipped (DND/Idle active).")
        return
    if night:
        try:
            with open(NIGHT_MODE_LOG_FILE, "a") as f:
                f.write(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
        except Exception:
            pass
    _alert_active = True
    _alert_flash_count = 10
    _last_alert_time = datetime.datetime.now()
    log_system_update("Alert triggered.")
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
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10)
        server.login(EMAIL_SENDER, EMAIL_SENDER_PASS)
        server.send_message(msg)
        server.quit()
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

    if det_count > 0:
        _detections_today += det_count

    if danger_detected:
        global _danger_trigger_info
        _danger_trigger_info = text_info   # snapshot the scissors-trigger frame
        threading.Thread(target=trigger_software_alert, daemon=True).start()
        threading.Thread(target=log_scissors_detection, daemon=True).start()
        threading.Thread(target=send_email_alert, daemon=True).start()

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
            global _frame_seq
            _frame_buffer = jpeg.tobytes()
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


def query_local_llm(user_input):
    system_prompt = f"""You are Narada, an AI security assistant for the Garuda Security System.
Current time: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}.
Available modes: MODE_DND, MODE_EMAIL_OFF, MODE_IDLE, MODE_NIGHT, MODE_EMERGENCY, MODE_PRIVACY.
DETECTION_THRESHOLD: 0.05-0.95. Default 0.3.
Respond ONLY in this JSON (no markdown, no backticks):
{{"modes":{{"MODE_DND":null,"MODE_EMAIL_OFF":null,"MODE_IDLE":null,"MODE_NIGHT":null,"MODE_EMERGENCY":null,"MODE_PRIVACY":null}},"settings":{{"DETECTION_THRESHOLD":null}},"response":"your response here"}}
Set mode values to true/false to change, null to leave unchanged."""
    payload = {
        "model": "phi3:latest",
        "prompt": f"{system_prompt}\n\nUser says: {user_input}",
        "stream": False,
        "format": "json"
    }
    try:
        res = requests.post("http://localhost:11434/api/generate", json=payload, timeout=5)
        res.raise_for_status()
        llm_response = res.json().get("response", "{}")
        try:
            parsed = json.loads(llm_response)
        except json.JSONDecodeError:
            cleaned = llm_response.strip().strip("```json").strip("```").strip()
            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError:
                return None
        return parsed
    except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout):
        return None  # Ollama unavailable — silent fallback to rule-based
    except Exception:
        return None


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
            def _to_bool(v):
                if isinstance(v, bool): return v
                if isinstance(v, int):  return v != 0
                if isinstance(v, str):
                    return v.lower() in ("true", "active", "on", "yes", "1", "enabled")
                return None

            modes_to_change = llm_result.get("modes", {}) or {}
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
                    MODE_DND = False
                if MODE_NIGHT:
                    MODE_DND = False
            response = llm_result.get("response") or "Done."
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
        "expires": time.time() + duration
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
    # Accept token from cookie (same-origin) or X-Garuda-Token header (cross-origin)
    token = request.cookies.get("garuda_session") or request.headers.get("X-Garuda-Token")
    session = get_session(token)
    if not session:
        raise HTTPException(401, "Not authenticated")
    return session

def require_admin(request: Request):
    session = require_session(request)
    if session["role"] != "admin":
        raise HTTPException(403, "Admin access required")
    return session

##############################################################################
# STATE HELPER
##############################################################################
def get_state_dict():
    global _alert_active, _alert_flash_count, _danger_trigger_info
    # Decrement flash count
    if _alert_flash_count > 0:
        _alert_flash_count -= 1
        if _alert_flash_count == 0:
            _alert_active = False
            _danger_trigger_info = ""

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
    }

##############################################################################
# FASTAPI APP
##############################################################################
from contextlib import asynccontextmanager

@asynccontextmanager
async def _lifespan(app):
    asyncio.ensure_future(_ws_broadcaster())
    yield

fastapi_app = FastAPI(title="Garuda Security System", lifespan=_lifespan)

# CORS — allow any origin so Vercel-hosted frontend can reach the RPi5 backend
fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    email_sender_pass: Optional[str] = None
    email_recipients: Optional[List[str]] = None
    email_cooldown: Optional[int] = None
    danger_label: Optional[str] = None
    privacy: Optional[bool] = None

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
async def login(data: LoginRequest, response: Response):
    u = data.username.strip()
    p = data.password.strip()
    if u in USERS and USERS[u].get("role") == "admin":
        raise HTTPException(403, "Admin accounts must sign in via the Admin Access flow.")
    if u in USERS and USERS[u]["password"] == p:
        duration = 5 * 24 * 3600 if data.remember_me else 3600
        token = create_session(u, duration)
        response.set_cookie("garuda_session", token, httponly=True, samesite="lax", max_age=duration)
        log_system_update(f"Login: {u}")
        if u in USERS:
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
    }

@fastapi_app.post("/api/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get("garuda_session")
    if token and token in _sessions:
        del _sessions[token]
    response.delete_cookie("garuda_session")
    return {"ok": True}

@fastapi_app.post("/api/admin/send-otp")
async def admin_send_otp(data: OTPRequest, response: Response):
    """Admin login step 1: verify credentials, send OTP."""
    global ADMIN_OTP
    u = data.username.strip()
    p = data.password.strip()
    if u not in USERS or USERS[u]["password"] != p or USERS[u]["role"] != "admin":
        raise HTTPException(401, "Invalid admin credentials.")
    ADMIN_OTP = generate_otp_code(6)
    dest = EMAIL_RECIPIENTS[0] if EMAIL_RECIPIENTS else EMAIL_SENDER
    ok, err = send_otp_via_email(dest, ADMIN_OTP)
    if not ok:
        # Allow bypass with the OTP embedded in the error response for dev
        return {"ok": False, "error": err, "bypass_otp": ADMIN_OTP}
    return {"ok": True}

@fastapi_app.post("/api/admin/verify-otp")
async def admin_verify_otp(data: VerifyOTPRequest, response: Response):
    """Admin login step 2: verify OTP, issue session."""
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
async def forgot_send_otp(data: SendForgotOTPRequest):
    global USER_FORGOT_OTP, _forgot_otp_user
    u = data.username.strip()
    if u not in USERS:
        raise HTTPException(404, "User not found.")
    USER_FORGOT_OTP = generate_otp_code(6)
    _forgot_otp_user = u
    dest = EMAIL_RECIPIENTS[0] if EMAIL_RECIPIENTS else EMAIL_SENDER
    ok, err = send_otp_via_email(dest, USER_FORGOT_OTP)
    if not ok:
        return {"ok": False, "error": err, "bypass_otp": USER_FORGOT_OTP}
    return {"ok": True}

@fastapi_app.post("/api/forgot/reset")
async def forgot_reset(data: ForgotPasswordRequest):
    global USER_FORGOT_OTP, _forgot_otp_user
    if data.otp.strip() != USER_FORGOT_OTP or not _forgot_otp_user:
        raise HTTPException(401, "Invalid OTP.")
    if not data.new_password.strip():
        raise HTTPException(400, "Password cannot be empty.")
    USERS[_forgot_otp_user]["password"] = data.new_password.strip()
    save_users()
    log_system_update(f"Password reset for {_forgot_otp_user}.")
    USER_FORGOT_OTP = None
    _forgot_otp_user = None
    return {"ok": True}

@fastapi_app.get("/api/state")
async def get_state(session=Depends(require_session)):
    return get_state_dict()

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
        "password": data.password,
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
        USERS[data.username]["password"] = data.new_password
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
    }

@fastapi_app.post("/api/config")
async def update_config(data: ConfigUpdateRequest, session=Depends(require_admin)):
    global DETECTION_THRESHOLD, EMAIL_SENDER, EMAIL_SENDER_PASS
    global EMAIL_RECIPIENTS, EMAIL_COOLDOWN, MODE_PRIVACY
    if data.detection_threshold is not None:
        DETECTION_THRESHOLD = max(0.05, min(0.95, data.detection_threshold))
    if data.email_sender is not None:
        EMAIL_SENDER = data.email_sender
    if data.email_sender_pass is not None:
        EMAIL_SENDER_PASS = data.email_sender_pass
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

@fastapi_app.post("/api/email/test")
async def test_email(session=Depends(require_admin)):
    dest = EMAIL_RECIPIENTS[0] if EMAIL_RECIPIENTS else EMAIL_SENDER
    ok, err = send_otp_via_email(dest, "TEST-123")
    if not ok:
        return {"ok": False, "error": err}
    return {"ok": True}

@fastapi_app.get("/api/logs")
async def get_logs(session=Depends(require_session)):
    return {
        "system_log": system_updates_log,
        "voice_log": voice_assistant_log,
        "voice_responses": voice_responses,
    }

@fastapi_app.post("/api/emergency-stop")
async def emergency_stop(session=Depends(require_session)):
    log_system_update(f"Emergency stop by {session['username']}.")
    threading.Thread(target=stop_app, daemon=True).start()
    return {"ok": True}

# ── MJPEG stream ─────────────────────────────────────────────────────────────
# Uses _frame_seq to detect new frames only — avoids re-sending duplicate
# frames and keeps per-client CPU near zero when the pipeline is idle.
@fastapi_app.get("/stream")
async def mjpeg_stream(request: Request):
    async def generate():
        last_seq = -1
        while True:
            if await request.is_disconnected():
                break
            with _frame_lock:
                seq = _frame_seq
                frame = _frame_buffer if seq != last_seq else None
            if frame is not None:
                last_seq = seq
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
            else:
                # No new frame yet — yield control briefly without busy-spinning
                await asyncio.sleep(0.005)
    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

# ── WebSocket broadcaster (single task → all clients) ────────────────────────
# Compute state ONCE per tick and fan it out to every connected client in
# parallel via asyncio.gather.  This is O(1) in CPU regardless of how many
# clients are connected (vs the old per-client loop which was O(N)).
_ws_state_queue: asyncio.Queue = None  # created in startup event

async def _ws_broadcaster():
    """Background task: compute state once per 500 ms and push to all clients."""
    while True:
        await asyncio.sleep(0.5)
        if not _ws_clients:
            continue
        payload = get_state_dict()
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
                await asyncio.wait_for(websocket.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                # Send a lightweight ping to detect dead connections
                await websocket.send_json({"ping": True})
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
