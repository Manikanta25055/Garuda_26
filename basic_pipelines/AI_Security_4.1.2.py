##############################################################################
# NARADA SETUP — run these ONCE on Raspberry Pi 5 before using voice commands:
#   curl -fsSL https://ollama.com/install.sh | sh
#   ollama pull phi3:mini        # ~2.3GB, needs 4GB+ free RAM on RPi5
#   # ollama serve starts automatically as a systemd service after install
#   # If RAM is tight, use: ollama pull tinyllama  (700MB, less accurate)
#
# Run this application:
#   source setup_env.sh
#   python3 AI_Security_4.1.2.py --input rpi
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

import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import speech_recognition as sr

# Optional: psutil for hardware usage
try:
    import psutil
except ImportError:
    psutil = None
    print("Warning: psutil not installed. Hardware monitoring will be basic.")

# hailo_rpi_common imports
from hailo_rpi_common import (
    get_default_parser,
    QUEUE,
    get_caps_from_pad,
    get_numpy_from_buffer,
    GStreamerApp,
    app_callback_class,
)

##############################################################################
# EMAIL CONFIG — UPDATE THESE BEFORE RUNNING
# Gmail App Password setup:
#   1. Go to myaccount.google.com -> Security -> 2-Step Verification
#   2. Scroll to "App Passwords" -> create for "Mail" + "Other device"
#   3. Paste the 16-char password (with spaces) into EMAIL_SENDER_PASS
##############################################################################
EMAIL_SENDER = "mgonugondlamanikanta@gmail.com"
EMAIL_SENDER_PASS = "grhy ipzy hedi xprp"
EMAIL_RECIPIENTS = ["amarmanikantan@gmail.com"]
EMAIL_COOLDOWN = 60
last_email_sent_time = 0

##############################################################################
# GLOBALS & SETTINGS
##############################################################################
app = None

SCISSORS_LOG_FILE = "danger_sightings.txt"
NIGHT_MODE_LOG_FILE = "night_mode_findings.txt"
LLM_LOG_FILE = "system_logs/llm_reasoning.json"
USERS_FILE = "system_logs/users.json"
CONFIG_FILE = "system_logs/config.json"

# Logging
system_updates_log = []   # System or admin updates
voice_assistant_log = []  # What Narada hears
voice_responses = []      # Narada's responses to user
latest_detection_info = ""  # Updated by GStreamer callback

# OTP
ADMIN_OTP = None
USER_FORGOT_OTP = None

# Modes
MODE_DND = False
MODE_EMAIL_OFF = False
MODE_IDLE = False
MODE_NIGHT = False
MODE_EMERGENCY = False
MODE_PRIVACY = True  # Blurs faces by default

# Dynamic AI Settings
DETECTION_THRESHOLD = 0.3  # Default sensitivity

# Additional (custom) modes with priorities
CUSTOM_MODES = {}  # e.g., { "strict": {"priority":2, ...}, ... }
# NIGHT=priority 10, EMERGENCY=priority 11

# Wake word
NARADA_WAKE_WORD = "narada"

# For voice assistant updating UI
dashboard_gui = None

# Software alert state (replaces GPIO LED/buzzer)
_alert_active = False
_alert_flash_count = 0

# Tracking stats
_app_start_time = time.time()
_detections_today = 0
_last_alert_time = None

# Threading lock for global mode state
_mode_lock = threading.Lock()

# Users dict (loaded from disk at startup)
# Each user has: password, role, display_name (shown on login screen),
# box_color (hex color for login screen profile box), history
USERS = {
    "user": {
        "password": "user",
        "role": "user",
        "display_name": "User",
        "box_color": "#1565c0",
        "history": {
            "logins": [],
            "narada_activity": []
        }
    },
    "admin": {
        "password": "root",
        "role": "admin",
        "display_name": "Admin",
        "box_color": "#e65100",
        "history": {
            "logins": [],
            "narada_activity": []
        }
    }
}

# Custom voice commands
CUSTOM_VOICE_COMMANDS = {}

##############################################################################
# PERSISTENCE — LOAD / SAVE
##############################################################################
def load_users():
    global USERS
    for path in [USERS_FILE, "system_logs/users_data.json"]:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    data = json.load(f)
                if isinstance(data, dict) and data:
                    # Backfill missing fields for users saved before these were added
                    default_colors = ["#1565c0", "#2e7d32", "#6a1b9a", "#00838f",
                                      "#f57f17", "#4527a0", "#ad1457"]
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
                    print(f"Users loaded from {path} ({len(USERS)} users)")
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
            print(f"Config loaded from {CONFIG_FILE}")
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

# Load persisted data at startup
load_users()
load_config()

##############################################################################
# HELPER & LOGGING FUNCTIONS
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

def update_text_widget(widget, new_text):
    try:
        current_view = widget.yview()
    except Exception:
        current_view = (0.0, 1.0)
    try:
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, new_text)
        if current_view[1] >= 0.99:
            widget.see(tk.END)
        else:
            widget.yview_moveto(current_view[0])
        widget.configure(state="disabled")
    except tk.TclError:
        pass

def stop_and_exit():
    print("Stopping GStreamer pipeline now...")
    log_system_update("Stopping pipeline & exiting the app.")
    if app is not None:
        try:
            app.pipeline.set_state(Gst.State.NULL)
        except Exception:
            pass
    sys.exit(0)

##############################################################################
# OTP / EMAIL
##############################################################################
def generate_otp_code(length=6):
    return "".join(random.choice(string.digits) for _ in range(length))

def send_otp_via_email(email, otp_code):
    """Send OTP email. Returns (success: bool, error_msg: str|None)."""
    body = f"Hello,\n\nYour OTP code is: {otp_code}\n\nUse this to complete your login/forgot flow."
    msg = MIMEText(body)
    msg['Subject'] = "Your OTP Code"
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
# DETECTIONS & ALERTS
##############################################################################
def trigger_software_alert():
    """Software replacement for GPIO LED/buzzer alert."""
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
        except Exception as e:
            log_system_update("Error logging night mode incident: " + str(e))
    _alert_active = True
    _alert_flash_count = 10
    _last_alert_time = datetime.datetime.now()
    log_system_update("Software alert triggered (visual flash).")
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
        log_system_update("Email alert skipped (EmailOff/Idle).")
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
    now = datetime.datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{stamp}] SCISSORS DETECTED\n"
    try:
        with open(SCISSORS_LOG_FILE, "a") as f:
            f.write(entry)
    except Exception as e:
        log_system_update(f"Error logging scissors detection: {e}")
    log_system_update("Scissors detection logged to file.")

##############################################################################
# user_app_callback_class
##############################################################################
class user_app_callback_class(app_callback_class):
    def __init__(self):
        super().__init__()
        self.new_variable = 42
        self.person_detected = False
        self.danger_label = "scissors"
        self.latest_frame = None

    def new_function(self):
        return "The meaning of life is: "

    def set_frame(self, frame):
        self.latest_frame = frame

    def get_frame(self):
        return self.latest_frame

##############################################################################
# GSTREAMER CALLBACK
##############################################################################
def app_callback(pad, info, user_data):
    global latest_detection_info, DETECTION_THRESHOLD, MODE_PRIVACY, _detections_today
    buffer = info.get_buffer()
    if buffer is None:
        return Gst.PadProbeReturn.OK

    user_data.increment()
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
            text_info += f"{label} detected (conf={confidence:.2f})\n"

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
        threading.Thread(target=trigger_software_alert, daemon=True).start()
        threading.Thread(target=log_scissors_detection, daemon=True).start()
        threading.Thread(target=send_email_alert, daemon=True).start()

    user_data.person_detected = any(d.get_label() == "person" for d in detections)

    if frame is not None:
        cv2.putText(frame, f"Threshold: {threshold:.2f}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        if privacy:
            cv2.putText(frame, "PRIVACY MASKING: ACTIVE", (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 0), 2)
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        user_data.set_frame(frame)

    latest_detection_info = text_info
    return Gst.PadProbeReturn.OK

##############################################################################
# GStreamerDetectionApp
##############################################################################
class GStreamerDetectionApp(GStreamerApp):
    def __init__(self, args, user_data):
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
        setproctitle.setproctitle("Hailo Detection App")
        self.create_pipeline()

    def get_pipeline_string(self):
        if self.source_type == "rpi":
            source_element = (
                "libcamerasrc name=src_0 ! "
                f"video/x-raw, format={self.network_format}, width=1536, height=864 ! "
                + QUEUE("queue_src_scale")
                + "videoscale ! "
                f"video/x-raw, format={self.network_format}, width={self.network_width}, height={self.network_height}, framerate=30/1 ! "
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
            + f"fpsdisplaysink video-sink={self.video_sink} name=hailo_display sync={self.sync} "
            f"text-overlay={self.options_menu.show_fps} signal-fps-measurements=true "
        )
        return pipeline_string

##############################################################################
# BUILT-IN VOICE COMMANDS
##############################################################################
BUILT_IN_COMMANDS = {
    "activate dnd"              : "Enables Do Not Disturb mode",
    "deactivate dnd"            : "Disables DND mode",
    "activate email off"        : "Turns off email notifications",
    "deactivate email off"      : "Turns on email notifications",
    "activate idle"             : "Disables all alerts",
    "deactivate idle"           : "Re-enables all alerts",
    "activate night mode"       : "High priority alerts last longer",
    "deactivate night mode"     : "Return to normal alert durations",
    "activate emergency mode"   : "Extra-loud alerts, overrides standard modes",
    "deactivate emergency mode" : "Stops emergency mode",
    "hi / hello"                : "Greets the user",
    "how are you"               : "Narada status update",
    "what's your name"          : "Narada introduction",
    "time"                      : "Tells the current time",
}

##############################################################################
# NARADA VOICE ASSISTANT
##############################################################################
def apply_rule_based_command(user_input_lower):
    """Rule-based command matching when LLM is unavailable. Returns response string."""
    global MODE_DND, MODE_EMAIL_OFF, MODE_IDLE, MODE_NIGHT, MODE_EMERGENCY, MODE_PRIVACY

    # Check custom commands first
    for phrase, resp in CUSTOM_VOICE_COMMANDS.items():
        if phrase in user_input_lower:
            return resp

    response = None
    with _mode_lock:
        if "activate dnd" in user_input_lower:
            MODE_DND = True
            response = "Do Not Disturb activated."
        elif "deactivate dnd" in user_input_lower:
            MODE_DND = False
            response = "Do Not Disturb deactivated."
        elif "activate email off" in user_input_lower:
            MODE_EMAIL_OFF = True
            response = "Email alerts disabled."
        elif "deactivate email off" in user_input_lower:
            MODE_EMAIL_OFF = False
            response = "Email alerts enabled."
        elif "activate idle" in user_input_lower:
            MODE_IDLE = True
            response = "Idle mode activated. All alerts paused."
        elif "deactivate idle" in user_input_lower:
            MODE_IDLE = False
            response = "Idle mode deactivated. Alerts restored."
        elif "activate night mode" in user_input_lower or "night mode on" in user_input_lower:
            MODE_NIGHT = True
            response = "Night mode activated. High sensitivity enabled."
        elif "deactivate night mode" in user_input_lower or "night mode off" in user_input_lower:
            MODE_NIGHT = False
            response = "Night mode deactivated."
        elif "activate emergency" in user_input_lower or "emergency on" in user_input_lower:
            MODE_EMERGENCY = True
            MODE_DND = False
            response = "EMERGENCY MODE activated. All alerts maximized."
        elif "deactivate emergency" in user_input_lower or "emergency off" in user_input_lower:
            MODE_EMERGENCY = False
            response = "Emergency mode deactivated."
        elif "privacy on" in user_input_lower or "enable privacy" in user_input_lower:
            MODE_PRIVACY = True
            response = "Privacy masking enabled."
        elif "privacy off" in user_input_lower or "disable privacy" in user_input_lower:
            MODE_PRIVACY = False
            response = "Privacy masking disabled."

    if response:
        if dashboard_gui:
            try:
                dashboard_gui.root.after(0, dashboard_gui.sync_mode_checkbuttons)
            except Exception:
                pass
        return response

    # General queries
    if "time" in user_input_lower or "clock" in user_input_lower:
        return f"The time is {datetime.datetime.now().strftime('%I:%M %p')}."
    if any(w in user_input_lower for w in ["hi", "hello", "hey narada"]):
        return "Hello! I'm Narada, your AI security assistant. How can I help?"
    if "how are you" in user_input_lower:
        return "All systems operational. Standing by to assist."
    if "your name" in user_input_lower:
        return "I am Narada, the voice assistant of the Garuda Security System."
    if "status" in user_input_lower:
        with _mode_lock:
            parts = []
            if MODE_DND: parts.append("DND")
            if MODE_NIGHT: parts.append("Night")
            if MODE_EMERGENCY: parts.append("EMERGENCY")
            if MODE_IDLE: parts.append("Idle")
        active = ", ".join(parts) if parts else "none"
        return f"Active modes: {active}. Threshold: {DETECTION_THRESHOLD:.2f}."

    return "I heard you, but I'm not sure what to do. Try saying a command like 'activate dnd' or ask Ollama to be set up for smarter responses."


def query_local_llm(user_input):
    """
    Query local Ollama server. Returns parsed dict or None if unavailable/error.
    Install: curl -fsSL https://ollama.com/install.sh | sh && ollama pull phi3:mini
    """
    system_prompt = f"""You are Narada, an advanced AI security assistant for the Garuda Security System.
Current time: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}.

Available modes: MODE_DND, MODE_EMAIL_OFF, MODE_IDLE, MODE_NIGHT, MODE_EMERGENCY, MODE_PRIVACY.
- MODE_PRIVACY: blurs faces. Default true. Set false to see everyone clearly.
- DETECTION_THRESHOLD: 0.05–0.95. Higher = harder to trigger. Default 0.3.

You MUST respond strictly in this JSON format (no markdown, no backticks):
{{
  "modes": {{
    "MODE_DND": null, "MODE_EMAIL_OFF": null, "MODE_IDLE": null,
    "MODE_NIGHT": null, "MODE_EMERGENCY": null, "MODE_PRIVACY": null
  }},
  "settings": {{"DETECTION_THRESHOLD": null}},
  "response": "Your conversational response"
}}
Set values to true/false to change, null to leave unchanged.
"""
    payload = {
        "model": "phi3:latest",
        "prompt": f"{system_prompt}\n\nUser says: {user_input}",
        "stream": False,
        "format": "json"
    }
    try:
        os.makedirs(os.path.dirname(LLM_LOG_FILE), exist_ok=True)
        res = requests.post("http://localhost:11434/api/generate", json=payload, timeout=15)
        res.raise_for_status()
        res_data = res.json()
        llm_response = res_data.get("response", "{}")

        # Try to parse JSON (handle markdown-wrapped responses)
        try:
            parsed = json.loads(llm_response)
        except json.JSONDecodeError:
            cleaned = llm_response.strip().strip("```json").strip("```").strip()
            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError:
                log_system_update(f"LLM returned non-JSON, using rule-based fallback.")
                return None

        # Log for audit
        try:
            with open(LLM_LOG_FILE, "a") as f:
                f.write(json.dumps({
                    "time": str(datetime.datetime.now()),
                    "user": user_input,
                    "narada": parsed
                }) + "\n")
        except Exception:
            pass
        return parsed

    except requests.exceptions.ConnectionError:
        log_system_update("Ollama unavailable. Using rule-based fallback.")
        return None
    except Exception as e:
        log_system_update(f"LLM error: {e}")
        return None


def voice_assistant_loop(stop_event, current_user=None):
    """Voice recognition loop. Uses Ollama LLM if available, else rule-based fallback."""
    global MODE_DND, MODE_EMAIL_OFF, MODE_IDLE, MODE_NIGHT, MODE_EMERGENCY, MODE_PRIVACY
    global DETECTION_THRESHOLD

    recognizer = sr.Recognizer()
    try:
        mic = sr.Microphone()
        append_voice_log("Microphone connected.", user_name=current_user)
    except Exception as e:
        append_voice_log("Error accessing microphone: " + str(e), user_name=current_user)
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
            append_voice_log(f"Speech recognition error: {str(e)}", user_name=current_user)
            continue

        user_input_lower = user_input.lower()
        response = None

        # 1) Try LLM first
        llm_result = query_local_llm(user_input)

        if llm_result is not None:
            # Parse modes with lock
            # phi3 may return "active"/"inactive", true/false, or 1/0 — normalise all
            def _to_bool(v):
                if isinstance(v, bool): return v
                if isinstance(v, int):  return v != 0
                if isinstance(v, str):
                    return v.lower() in ("true", "active", "on", "yes", "1", "enabled")
                return None

            modes_to_change = llm_result.get("modes", {}) or {}
            settings_to_change = llm_result.get("settings", {}) or {}
            with _mode_lock:
                for key, var_name in [
                    ("MODE_DND",       "MODE_DND"),
                    ("MODE_EMAIL_OFF", "MODE_EMAIL_OFF"),
                    ("MODE_IDLE",      "MODE_IDLE"),
                    ("MODE_NIGHT",     "MODE_NIGHT"),
                    ("MODE_EMERGENCY", "MODE_EMERGENCY"),
                    ("MODE_PRIVACY",   "MODE_PRIVACY"),
                ]:
                    raw = modes_to_change.get(key)
                    if raw is not None:
                        val = _to_bool(raw)
                        if val is not None:
                            globals()[var_name] = val

                raw_thr = settings_to_change.get("DETECTION_THRESHOLD")
                if raw_thr is not None:
                    try:
                        thr = float(raw_thr)
                        thr = max(0.05, min(0.95, thr))  # clamp to valid range
                        DETECTION_THRESHOLD = thr
                        log_system_update(f"AI Sensitivity updated to: {DETECTION_THRESHOLD:.2f}")
                    except (ValueError, TypeError):
                        pass
                # Priority overrides
                if MODE_EMERGENCY:
                    MODE_DND = False
                if MODE_NIGHT:
                    MODE_DND = False
            response = llm_result.get("response") or "Done."
        else:
            # Rule-based fallback
            response = apply_rule_based_command(user_input_lower)

        append_voice_response(response, user_name=current_user)

        # Sync UI
        if dashboard_gui:
            try:
                dashboard_gui.root.after(0, dashboard_gui.sync_mode_checkbuttons)
            except Exception:
                pass

        time.sleep(0.5)

##############################################################################
# USER DASHBOARD
##############################################################################
class UserDashboardGUI:
    def __init__(self, root, user_data, username="user"):
        self.root = root
        self.username = username
        self.root.title("Garuda - User Mode")
        self.root.geometry("1100x750")
        self.root.minsize(900, 600)

        style = ttk.Style(self.root)
        style.theme_use("clam")

        self.user_data = user_data
        self.after_id = None
        self.voice_thread = None
        self.voice_stop_event = None
        self.voice_assistant_running = False

        # Store mode indicator labels
        self._mode_indicators = {}

        # Configure grid layout
        self.root.grid_columnconfigure(0, weight=0, minsize=210)  # side panel
        self.root.grid_columnconfigure(1, weight=1)               # main content
        self.root.grid_rowconfigure(0, weight=0)                  # alert banner
        self.root.grid_rowconfigure(1, weight=1)                  # main area
        self.root.grid_rowconfigure(2, weight=0, minsize=160)     # console
        self.root.grid_rowconfigure(3, weight=0)                  # bottom buttons

        # ── Row 0: Alert banner (hidden by default) ──
        self.alert_banner = tk.Label(
            self.root,
            text="⚠  DANGER DETECTED — CHECK CAMERA FEED",
            bg="red", fg="white",
            font=("Helvetica", 13, "bold"),
            pady=5
        )
        self.alert_banner.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.alert_banner.grid_remove()

        # ── Row 1, Col 0: Side Panel ──
        side_panel = tk.Frame(self.root, bg="#2b2b2b", padx=5, pady=5)
        side_panel.grid(row=1, column=0, sticky="nsew")

        tk.Label(side_panel, text="GARUDA", bg="#2b2b2b", fg="white",
                 font=("Helvetica", 14, "bold")).pack(pady=(5, 2))
        tk.Label(side_panel, text=f"User: {username}", bg="#2b2b2b", fg="#aaaaaa",
                 font=("Helvetica", 9)).pack(pady=(0, 8))

        # Mode status cards
        tk.Label(side_panel, text="MODES", bg="#2b2b2b", fg="#aaaaaa",
                 font=("Helvetica", 9, "bold")).pack(anchor="w", padx=5)

        modes_def = [
            ("DND",       "MODE_DND",       self._toggle_dnd_side),
            ("Email Off", "MODE_EMAIL_OFF",  self._toggle_email_side),
            ("Idle",      "MODE_IDLE",       self._toggle_idle_side),
            ("Night",     "MODE_NIGHT",      self._toggle_night_side),
            ("Emergency", "MODE_EMERGENCY",  self._toggle_emergency_side),
        ]
        for label_text, key, cmd in modes_def:
            card = tk.Frame(side_panel, bg="#3c3c3c", padx=5, pady=3)
            card.pack(fill=tk.X, padx=3, pady=2)
            ind = tk.Label(card, text="●", font=("Helvetica", 13), bg="#3c3c3c", fg="green")
            ind.pack(side=tk.LEFT)
            btn = tk.Button(card, text=label_text, bg="#3c3c3c", fg="white",
                            font=("Helvetica", 9), relief="flat", anchor="w",
                            command=cmd, cursor="hand2")
            btn.pack(side=tk.LEFT, padx=4, fill=tk.X, expand=True)
            self._mode_indicators[key] = ind

        # Quick action buttons
        tk.Label(side_panel, text="QUICK ACTIONS", bg="#2b2b2b", fg="#aaaaaa",
                 font=("Helvetica", 9, "bold")).pack(anchor="w", padx=5, pady=(10, 2))

        tk.Button(side_panel, text="Start Narada", bg="#1e88e5", fg="white",
                  font=("Helvetica", 9, "bold"), relief="flat", pady=4,
                  command=self.toggle_voice, cursor="hand2").pack(fill=tk.X, padx=5, pady=2)

        self.btn_narada_side = side_panel.winfo_children()[-1]  # keep ref

        tk.Button(side_panel, text="Emergency Stop", bg="#c62828", fg="white",
                  font=("Helvetica", 9, "bold"), relief="flat", pady=4,
                  command=self.emergency_stop, cursor="hand2").pack(fill=tk.X, padx=5, pady=2)

        # System stats
        tk.Label(side_panel, text="STATS", bg="#2b2b2b", fg="#aaaaaa",
                 font=("Helvetica", 9, "bold")).pack(anchor="w", padx=5, pady=(10, 2))

        self.lbl_uptime = tk.Label(side_panel, text="Uptime: --",
                                   bg="#2b2b2b", fg="#cccccc", font=("Helvetica", 9))
        self.lbl_uptime.pack(anchor="w", padx=8)

        self.lbl_det_count = tk.Label(side_panel, text="Detections: 0",
                                      bg="#2b2b2b", fg="#cccccc", font=("Helvetica", 9))
        self.lbl_det_count.pack(anchor="w", padx=8)

        self.lbl_last_alert = tk.Label(side_panel, text="Last alert: --",
                                       bg="#2b2b2b", fg="#cccccc", font=("Helvetica", 9))
        self.lbl_last_alert.pack(anchor="w", padx=8)

        # ── Row 1, Col 1: Main content (notebook) ──
        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=1, column=1, sticky="nsew", padx=5, pady=5)

        self.detections_tab = ttk.Frame(self.notebook)
        self.build_detections_tab(self.detections_tab)
        self.notebook.add(self.detections_tab, text="AI Detections")

        self.narada_tab = ttk.Frame(self.notebook)
        self.build_narada_tab(self.narada_tab)
        self.notebook.add(self.narada_tab, text="Narada")

        # ── Row 2: Bottom console ──
        console_frame = ttk.LabelFrame(self.root, text="System Console")
        console_frame.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=5, pady=2)

        self.console_text = tk.Text(
            console_frame, height=7, wrap="word",
            bg="black", fg="#00ff00", font=("Courier", 9),
            state="disabled"
        )
        sc_con = tk.Scrollbar(console_frame, command=self.console_text.yview)
        self.console_text.configure(yscrollcommand=sc_con.set)
        self.console_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sc_con.pack(side=tk.LEFT, fill=tk.Y)

        # ── Row 3: Bottom buttons ──
        btn_bar = tk.Frame(self.root, bg="#1a1a1a")
        btn_bar.grid(row=3, column=0, columnspan=2, sticky="ew")

        tk.Button(btn_bar, text="Instructions", bg="#444", fg="white",
                  relief="flat", font=("Helvetica", 9),
                  command=self.show_instructions).pack(side=tk.LEFT, padx=8, pady=4)

        tk.Button(btn_bar, text="Logout", bg="#444", fg="white",
                  relief="flat", font=("Helvetica", 9),
                  command=self.logout).pack(side=tk.RIGHT, padx=8, pady=4)

        # Track login
        if username in USERS:
            USERS[username]["history"]["logins"].append(
                datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )

        self.update_gui()

    def build_detections_tab(self, parent):
        self.detection_text = tk.Text(parent, wrap="word", bg="white", state="disabled")
        sc = tk.Scrollbar(parent, command=self.detection_text.yview)
        self.detection_text.configure(yscrollcommand=sc.set)
        self.detection_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        sc.pack(side=tk.LEFT, fill=tk.Y, pady=5)

    def build_narada_tab(self, parent):
        ctrl_frame = ttk.Frame(parent)
        ctrl_frame.pack(fill=tk.X, padx=10, pady=8)

        self.btn_voice = ttk.Button(ctrl_frame, text="Start Narada", command=self.toggle_voice)
        self.btn_voice.pack(side=tk.LEFT, padx=5)

        self.lbl_status = ttk.Label(ctrl_frame, text="Status: Not Listening")
        self.lbl_status.pack(side=tk.LEFT, padx=10)

        self.listening_indicator = ttk.Label(ctrl_frame, text="●",
                                             font=("Helvetica", 20), foreground="red")
        self.listening_indicator.pack(side=tk.LEFT, padx=5)

        self.btn_clear_log = ttk.Button(ctrl_frame, text="Clear Log",
                                        command=self.clear_narada_log)
        self.btn_clear_log.pack(side=tk.LEFT, padx=5)

        ttk.Label(parent, text="Voice Log:", font=("Helvetica", 10, "bold")).pack(
            anchor="w", padx=8)
        self.voice_text = tk.Text(parent, height=10, wrap="word", bg="lightyellow",
                                  state="disabled")
        sv = tk.Scrollbar(parent, command=self.voice_text.yview)
        self.voice_text.configure(yscrollcommand=sv.set)
        self.voice_text.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=5, pady=2)
        sv.pack_forget()  # scrollbar alongside text
        self.voice_text.pack_forget()

        # redo layout with scrollbar
        vf = ttk.Frame(parent)
        vf.pack(fill=tk.BOTH, expand=True, padx=5, pady=2)
        self.voice_text = tk.Text(vf, height=10, wrap="word", bg="lightyellow",
                                  state="disabled")
        sv2 = tk.Scrollbar(vf, command=self.voice_text.yview)
        self.voice_text.configure(yscrollcommand=sv2.set)
        self.voice_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sv2.pack(side=tk.LEFT, fill=tk.Y)

        ttk.Label(parent, text="Narada Response:", font=("Helvetica", 10, "bold")).pack(
            anchor="w", padx=8)
        rf = ttk.Frame(parent)
        rf.pack(fill=tk.X, padx=5, pady=2)
        self.response_text = tk.Text(rf, height=4, wrap="word", bg="white",
                                     fg="grey", state="disabled")
        sr2 = tk.Scrollbar(rf, command=self.response_text.yview)
        self.response_text.configure(yscrollcommand=sr2.set)
        self.response_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sr2.pack(side=tk.LEFT, fill=tk.Y)

    # ====================== Periodic Update =======================
    def update_gui(self):
        global _alert_active, _alert_flash_count

        # Detection tab
        update_text_widget(self.detection_text, latest_detection_info)

        # Console (bottom)
        update_text_widget(self.console_text, "\n".join(system_updates_log[-50:]))

        # Narada tab
        update_text_widget(self.voice_text, "\n".join(voice_assistant_log[-100:]))
        if self.voice_assistant_running:
            update_text_widget(self.response_text, "\n".join(voice_responses[-20:]))
        else:
            update_text_widget(self.response_text, "Narada is not listening.")

        # Sync mode indicators
        self.sync_mode_checkbuttons()

        # Alert banner flash
        if _alert_active:
            if _alert_flash_count > 0:
                if _alert_flash_count % 2 == 0:
                    self.alert_banner.grid()
                else:
                    self.alert_banner.grid_remove()
                _alert_flash_count -= 1
            else:
                _alert_active = False
                self.alert_banner.grid_remove()

        # Stats
        uptime_sec = int(time.time() - _app_start_time)
        h, r = divmod(uptime_sec, 3600)
        m, s = divmod(r, 60)
        self.lbl_uptime.config(text=f"Uptime: {h:02d}:{m:02d}:{s:02d}")
        self.lbl_det_count.config(text=f"Detections: {_detections_today}")
        last = _last_alert_time.strftime("%H:%M:%S") if _last_alert_time else "--"
        self.lbl_last_alert.config(text=f"Last alert: {last}")

        self.after_id = self.root.after(1000, self.update_gui)

    def sync_mode_checkbuttons(self):
        with _mode_lock:
            states = {
                "MODE_DND": MODE_DND,
                "MODE_EMAIL_OFF": MODE_EMAIL_OFF,
                "MODE_IDLE": MODE_IDLE,
                "MODE_NIGHT": MODE_NIGHT,
                "MODE_EMERGENCY": MODE_EMERGENCY,
            }
        for key, active in states.items():
            ind = self._mode_indicators.get(key)
            if ind:
                ind.config(fg="red" if active else "green")

    # ====================== Side panel mode toggles =======================
    def _toggle_dnd_side(self):
        global MODE_DND
        with _mode_lock:
            MODE_DND = not MODE_DND
        log_system_update(f"User toggled DND => {MODE_DND}")
        self.sync_mode_checkbuttons()

    def _toggle_email_side(self):
        global MODE_EMAIL_OFF
        with _mode_lock:
            MODE_EMAIL_OFF = not MODE_EMAIL_OFF
        log_system_update(f"User toggled EmailOff => {MODE_EMAIL_OFF}")
        self.sync_mode_checkbuttons()

    def _toggle_idle_side(self):
        global MODE_IDLE
        with _mode_lock:
            MODE_IDLE = not MODE_IDLE
        log_system_update(f"User toggled Idle => {MODE_IDLE}")
        self.sync_mode_checkbuttons()

    def _toggle_night_side(self):
        global MODE_NIGHT
        with _mode_lock:
            MODE_NIGHT = not MODE_NIGHT
        log_system_update(f"User toggled Night => {MODE_NIGHT}")
        self.sync_mode_checkbuttons()

    def _toggle_emergency_side(self):
        global MODE_EMERGENCY
        with _mode_lock:
            MODE_EMERGENCY = not MODE_EMERGENCY
        log_system_update(f"User toggled Emergency => {MODE_EMERGENCY}")
        self.sync_mode_checkbuttons()

    # ====================== Emergency Stop ========================
    def emergency_stop(self):
        if messagebox.askyesno("Emergency Stop", "Stop the pipeline and exit?"):
            stop_and_exit()

    # ====================== Narada Control ========================
    def toggle_voice(self):
        if not self.voice_assistant_running:
            self.voice_stop_event = threading.Event()
            self.voice_thread = threading.Thread(
                target=voice_assistant_loop,
                args=(self.voice_stop_event, self.username),
                daemon=True
            )
            self.voice_thread.start()
            self.voice_assistant_running = True
            self.btn_voice.config(text="Stop Narada")
            self.lbl_status.config(text="Status: Listening")
            self.listening_indicator.config(foreground="green")
            append_voice_log("Narada started.", user_name=self.username)
        else:
            if self.voice_stop_event:
                self.voice_stop_event.set()
            self.voice_assistant_running = False
            self.btn_voice.config(text="Start Narada")
            self.lbl_status.config(text="Status: Not Listening")
            self.listening_indicator.config(foreground="red")
            append_voice_log("Narada stopped.", user_name=self.username)

    def clear_narada_log(self):
        global voice_assistant_log, voice_responses
        voice_assistant_log = []
        voice_responses = []
        append_voice_log("Narada log cleared.", user_name=self.username)

    def logout(self):
        if self.after_id:
            self.root.after_cancel(self.after_id)
        self.root.destroy()
        root_main = tk.Tk()
        LoginHomeScreen(root_main)
        root_main.mainloop()

    def show_instructions(self):
        instructions = (
            "Narada Voice Commands:\n\n"
            "Mode Control:\n"
            "  'activate dnd' / 'deactivate dnd'\n"
            "  'activate email off' / 'deactivate email off'\n"
            "  'activate idle' / 'deactivate idle'\n"
            "  'activate night mode' / 'deactivate night mode'\n"
            "  'activate emergency' / 'deactivate emergency'\n"
            "  'privacy on' / 'privacy off'\n\n"
            "General:\n"
            "  'time' — current time\n"
            "  'status' — active modes\n"
            "  'hi', 'hello', 'how are you'\n\n"
            "Tip: Click a mode card in the left panel to toggle it instantly.\n"
            "Note: For smarter responses, install Ollama and run: ollama pull phi3:mini"
        )
        messagebox.showinfo("Narada Instructions", instructions)

##############################################################################
# ADMIN DASHBOARD
##############################################################################
class AdminDashboardGUI:
    def __init__(self, root, user_data, username="admin"):
        self.root = root
        self.username = username
        self.root.title("Garuda - Admin Mode")
        self.root.geometry("1150x780")
        self.root.minsize(950, 650)

        self.user_data = user_data
        self.after_id = None

        if username in USERS:
            USERS[username]["history"]["logins"].append(
                datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )

        style = ttk.Style(self.root)
        style.theme_use("clam")

        self.main_frame = ttk.Frame(self.root)
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        # Left sidebar
        self.sidebar = tk.Frame(self.main_frame, bg="#1a1a2e", width=195)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self.sidebar.pack_propagate(False)

        tk.Label(self.sidebar, text="GARUDA", bg="#1a1a2e", fg="white",
                 font=("Helvetica", 15, "bold")).pack(pady=(15, 2))
        tk.Label(self.sidebar, text="ADMIN PANEL", bg="#1a1a2e", fg="#888",
                 font=("Helvetica", 9)).pack(pady=(0, 10))

        # Content area
        self.content = ttk.Frame(self.main_frame)
        self.content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Sidebar nav buttons
        sidebar_buttons = [
            ("Dashboard",       self.show_dashboard_page),
            ("User Management", self.show_user_mgmt_page),
            ("Logs & Monitoring", self.show_logs_page),
            ("System Settings", self.show_settings_page),
            ("Narada Commands", self.show_narada_page),
            ("Mode Management", self.show_modes_page),
            ("Email Settings",  self.show_email_page),
            ("Instructions",    self.show_instructions),
            ("Logout",          self.logout),
        ]
        for text, cmd in sidebar_buttons:
            bg = "#c62828" if text == "Logout" else "#16213e"
            tk.Button(
                self.sidebar, text=text,
                bg=bg, fg="white",
                activebackground="#0f3460", activeforeground="white",
                relief="flat", font=("Helvetica", 10), pady=8,
                command=cmd, cursor="hand2"
            ).pack(fill=tk.X, padx=8, pady=2)

        # Build all pages
        self.pages = {}
        self.build_dashboard_page()
        self.build_user_mgmt_page()
        self.build_logs_page()
        self.build_settings_page()
        self.build_narada_page()
        self.build_modes_page()
        self.build_email_page()

        self.show_dashboard_page()
        self.update_gui()

    # ==================== PAGE BUILDERS ====================
    def build_dashboard_page(self):
        frame = ttk.Frame(self.content)
        self.pages["dashboard"] = frame

        tk.Label(frame, text="Admin Dashboard", font=("Helvetica", 16, "bold")).pack(pady=8)

        top = ttk.Frame(frame)
        top.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Detection text
        det_frame = ttk.LabelFrame(top, text="AI Detections")
        det_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=3)
        self.det_text = tk.Text(det_frame, height=12, wrap="word", bg="white", state="disabled")
        sc1 = tk.Scrollbar(det_frame, command=self.det_text.yview)
        self.det_text.configure(yscrollcommand=sc1.set)
        self.det_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sc1.pack(side=tk.LEFT, fill=tk.Y)

        # Console text
        con_frame = ttk.LabelFrame(top, text="System Console")
        con_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=3)
        self.console_text = tk.Text(con_frame, height=12, wrap="word", bg="white", state="disabled")
        sc2 = tk.Scrollbar(con_frame, command=self.console_text.yview)
        self.console_text.configure(yscrollcommand=sc2.set)
        self.console_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sc2.pack(side=tk.LEFT, fill=tk.Y)

        # Mode toggles
        mode_frame = ttk.LabelFrame(top, text="Modes")
        mode_frame.pack(side=tk.LEFT, fill=tk.Y, padx=3)

        self.var_dnd = tk.BooleanVar(value=MODE_DND)
        self.var_email_off = tk.BooleanVar(value=MODE_EMAIL_OFF)
        self.var_idle = tk.BooleanVar(value=MODE_IDLE)
        self.var_night = tk.BooleanVar(value=MODE_NIGHT)
        self.var_emergency = tk.BooleanVar(value=MODE_EMERGENCY)

        ttk.Checkbutton(mode_frame, text="DND", variable=self.var_dnd,
                        command=self.toggle_dnd).pack(anchor="w", pady=3)
        ttk.Checkbutton(mode_frame, text="Email Off", variable=self.var_email_off,
                        command=self.toggle_email).pack(anchor="w", pady=3)
        ttk.Checkbutton(mode_frame, text="Idle", variable=self.var_idle,
                        command=self.toggle_idle).pack(anchor="w", pady=3)
        ttk.Checkbutton(mode_frame, text="Night", variable=self.var_night,
                        command=self.toggle_night).pack(anchor="w", pady=3)
        ttk.Checkbutton(mode_frame, text="Emergency", variable=self.var_emergency,
                        command=self.toggle_emergency).pack(anchor="w", pady=3)

    def build_user_mgmt_page(self):
        frame = ttk.Frame(self.content)
        self.pages["user_mgmt"] = frame

        tk.Label(frame, text="User Management", font=("Helvetica", 16, "bold")).pack(pady=8)

        body = ttk.Frame(frame)
        body.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Left: user list
        left = ttk.LabelFrame(body, text="Users")
        left.pack(side=tk.LEFT, fill=tk.Y, padx=5)
        self.user_listbox = tk.Listbox(left, width=20, font=("Helvetica", 11))
        scrl = tk.Scrollbar(left, command=self.user_listbox.yview)
        self.user_listbox.configure(yscrollcommand=scrl.set)
        self.user_listbox.pack(side=tk.LEFT, fill=tk.Y)
        scrl.pack(side=tk.LEFT, fill=tk.Y)
        self.user_listbox.bind("<<ListboxSelect>>", self.on_select_user)
        self.refresh_user_list()

        # Middle: actions
        mid = ttk.LabelFrame(body, text="Create / Edit User")
        mid.pack(side=tk.LEFT, fill=tk.Y, padx=5)

        ttk.Label(mid, text="Username (login):").pack(pady=(8, 1))
        self.entry_uname = ttk.Entry(mid, width=18)
        self.entry_uname.pack(pady=2)

        ttk.Label(mid, text="Password:").pack(pady=(5, 1))
        self.entry_new_pw = ttk.Entry(mid, width=18, show="*")
        self.entry_new_pw.pack(pady=2)

        ttk.Label(mid, text="Display Name (on login screen):").pack(pady=(5, 1))
        self.entry_display_name = ttk.Entry(mid, width=18)
        self.entry_display_name.pack(pady=2)

        ttk.Label(mid, text="Profile Box Color:").pack(pady=(5, 1))
        color_row = ttk.Frame(mid)
        color_row.pack(pady=2)
        self._new_user_color = tk.StringVar(value="#1565c0")
        self.lbl_color_preview = tk.Label(color_row, bg="#1565c0", width=4, height=1,
                                          relief="solid")
        self.lbl_color_preview.pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(color_row, text="Pick Color",
                   command=self._pick_user_color).pack(side=tk.LEFT)

        # Preset color swatches
        swatch_row = ttk.Frame(mid)
        swatch_row.pack(pady=3)
        presets = ["#1565c0", "#2e7d32", "#6a1b9a", "#00838f",
                   "#f57f17", "#c62828", "#4527a0", "#00695c"]
        for col in presets:
            tk.Button(swatch_row, bg=col, width=2, height=1, relief="flat",
                      command=lambda c=col: self._set_color(c)).pack(side=tk.LEFT, padx=1)

        ttk.Separator(mid, orient="horizontal").pack(fill=tk.X, padx=5, pady=8)

        ttk.Button(mid, text="Add User",        command=self.add_user).pack(fill=tk.X, padx=5, pady=3)
        ttk.Button(mid, text="Delete Selected", command=self.delete_user).pack(fill=tk.X, padx=5, pady=3)
        ttk.Button(mid, text="Reset Password",  command=self.reset_password).pack(fill=tk.X, padx=5, pady=3)
        ttk.Button(mid, text="Rename User",     command=self.rename_user).pack(fill=tk.X, padx=5, pady=3)

        # Right: profile
        self.profile_frame = ttk.LabelFrame(body, text="User Profile")
        self.profile_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        ttk.Label(self.profile_frame, text="Select a user to see details.").pack(pady=20)

    def build_logs_page(self):
        frame = ttk.Frame(self.content)
        self.pages["logs"] = frame

        tk.Label(frame, text="Logs & Monitoring", font=("Helvetica", 16, "bold")).pack(pady=8)

        # Search/filter bar
        search_frame = ttk.Frame(frame)
        search_frame.pack(fill=tk.X, padx=8, pady=3)
        ttk.Label(search_frame, text="Filter:").pack(side=tk.LEFT)
        self.entry_log_filter = ttk.Entry(search_frame, width=30)
        self.entry_log_filter.pack(side=tk.LEFT, padx=5)
        ttk.Button(search_frame, text="Apply",  command=self._filter_logs).pack(side=tk.LEFT)
        ttk.Button(search_frame, text="Export", command=self._export_logs).pack(side=tk.LEFT, padx=5)
        ttk.Button(search_frame, text="Clear",  command=self.clear_logs).pack(side=tk.LEFT)

        log_frame = ttk.Frame(frame)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.logs_text = tk.Text(log_frame, wrap="word", bg="white", state="disabled")
        scr = tk.Scrollbar(log_frame, command=self.logs_text.yview)
        self.logs_text.configure(yscrollcommand=scr.set)
        self.logs_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scr.pack(side=tk.LEFT, fill=tk.Y)

    def build_settings_page(self):
        """System Settings — replaces old Hardware page."""
        frame = ttk.Frame(self.content)
        self.pages["settings"] = frame

        tk.Label(frame, text="System Settings", font=("Helvetica", 16, "bold")).pack(pady=8)

        # ── System Resources ──
        res_frame = ttk.LabelFrame(frame, text="System Resources")
        res_frame.pack(fill=tk.X, padx=10, pady=5)
        self.label_cpu  = ttk.Label(res_frame, text="CPU: N/A");  self.label_cpu.pack(anchor="w",  padx=10, pady=2)
        self.label_mem  = ttk.Label(res_frame, text="Mem: N/A");  self.label_mem.pack(anchor="w",  padx=10, pady=2)
        self.label_disk = ttk.Label(res_frame, text="Disk: N/A"); self.label_disk.pack(anchor="w", padx=10, pady=2)

        # ── Detection Threshold ──
        thr_frame = ttk.LabelFrame(frame, text="Detection Sensitivity")
        thr_frame.pack(fill=tk.X, padx=10, pady=5)
        ttk.Label(thr_frame, text="Threshold (lower = more sensitive):").pack(anchor="w", padx=10)
        self.threshold_var = tk.DoubleVar(value=DETECTION_THRESHOLD)
        slider = ttk.Scale(thr_frame, from_=0.05, to=0.95, orient=tk.HORIZONTAL,
                           variable=self.threshold_var,
                           command=self._on_threshold_change)
        slider.pack(fill=tk.X, padx=20, pady=5)
        self.lbl_threshold = ttk.Label(thr_frame, text=f"Current: {DETECTION_THRESHOLD:.2f}")
        self.lbl_threshold.pack(anchor="w", padx=10, pady=(0, 5))

        # ── Privacy Mode ──
        priv_frame = ttk.LabelFrame(frame, text="Privacy")
        priv_frame.pack(fill=tk.X, padx=10, pady=5)
        self.var_privacy = tk.BooleanVar(value=MODE_PRIVACY)
        ttk.Checkbutton(priv_frame, text="Privacy Mode — blur detected persons",
                        variable=self.var_privacy,
                        command=self._toggle_privacy).pack(anchor="w", padx=10, pady=5)

        # ── Danger Label ──
        danger_frame = ttk.LabelFrame(frame, text="Danger Object")
        danger_frame.pack(fill=tk.X, padx=10, pady=5)
        ttk.Label(danger_frame, text="Object label that triggers alerts:").pack(anchor="w", padx=10, pady=2)
        dl_row = ttk.Frame(danger_frame)
        dl_row.pack(anchor="w", padx=10, pady=5)
        self.entry_danger_label = ttk.Entry(dl_row, width=20)
        self.entry_danger_label.insert(0, self.user_data.danger_label if hasattr(self, 'user_data') else "scissors")
        self.entry_danger_label.pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(dl_row, text="Set", command=self._set_danger_label).pack(side=tk.LEFT)

    def build_narada_page(self):
        frame = ttk.Frame(self.content)
        self.pages["narada"] = frame

        tk.Label(frame, text="Narada Commands & Customization",
                 font=("Helvetica", 16, "bold")).pack(pady=8)

        lf_bi = ttk.LabelFrame(frame, text="Built-in Commands (read-only)")
        lf_bi.pack(fill=tk.X, padx=8, pady=3)
        self.list_built_in = tk.Listbox(lf_bi, width=70, height=8)
        self.list_built_in.pack(padx=5, pady=5)
        for phrase, desc in BUILT_IN_COMMANDS.items():
            self.list_built_in.insert(tk.END, f"  {phrase}  =>  {desc}")

        lf_cc = ttk.LabelFrame(frame, text="Custom Commands")
        lf_cc.pack(fill=tk.X, padx=8, pady=3)
        self.list_custom_cmds = tk.Listbox(lf_cc, width=70, height=6)
        self.list_custom_cmds.pack(padx=5, pady=5)
        self.refresh_custom_cmds()

        frm_add = ttk.Frame(lf_cc)
        frm_add.pack(pady=5)
        ttk.Label(frm_add, text="Phrase:").grid(row=0, column=0, padx=5)
        self.entry_cmd_phrase = ttk.Entry(frm_add, width=30)
        self.entry_cmd_phrase.grid(row=0, column=1, padx=5)
        ttk.Label(frm_add, text="Response:").grid(row=1, column=0, padx=5)
        self.entry_cmd_response = ttk.Entry(frm_add, width=30)
        self.entry_cmd_response.grid(row=1, column=1, padx=5)
        ttk.Button(frm_add, text="Add Command",    command=self.add_command).grid(row=2, column=0, pady=5)
        ttk.Button(frm_add, text="Delete Selected", command=self.del_command).grid(row=2, column=1, pady=5)

    def build_modes_page(self):
        frame = ttk.Frame(self.content)
        self.pages["modes"] = frame

        tk.Label(frame, text="Mode Management", font=("Helvetica", 16, "bold")).pack(pady=8)

        body = ttk.Frame(frame)
        body.pack(fill=tk.BOTH, expand=True, padx=5)

        left = ttk.LabelFrame(body, text="Existing Modes")
        left.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        self.custom_modes_listbox = tk.Listbox(left, width=28, height=12)
        self.custom_modes_listbox.pack(padx=5, pady=5)
        self.refresh_custom_modes_list()
        ttk.Button(left, text="Delete Selected Mode", command=self.delete_mode).pack(pady=5)

        right = ttk.LabelFrame(body, text="Create / Edit Custom Mode")
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        frm = ttk.Frame(right)
        frm.pack(pady=10)
        ttk.Label(frm, text="Mode Name:").grid(row=0, column=0, padx=5, pady=4)
        self.entry_mode_name = ttk.Entry(frm, width=20)
        self.entry_mode_name.grid(row=0, column=1, padx=5, pady=4)
        ttk.Label(frm, text="Priority (1-9):").grid(row=1, column=0, padx=5, pady=4)
        self.entry_mode_priority = ttk.Entry(frm, width=8)
        self.entry_mode_priority.grid(row=1, column=1, padx=5, pady=4)
        ttk.Button(right, text="Save Mode", command=self.save_mode).pack(pady=8)
        ttk.Label(right, text="Note: Night=10, Emergency=11 override lower priorities.",
                  foreground="grey").pack()

    def build_email_page(self):
        frame = ttk.Frame(self.content)
        self.pages["email"] = frame

        tk.Label(frame, text="Email Settings", font=("Helvetica", 16, "bold")).pack(pady=8)

        frm_es = ttk.LabelFrame(frame, text="Configuration")
        frm_es.pack(padx=15, pady=5, fill=tk.X)

        # Grid rows
        ttk.Label(frm_es, text="Sender Email:").grid(row=0, column=0, padx=10, pady=6, sticky="e")
        self.entry_sender = ttk.Entry(frm_es, width=40)
        self.entry_sender.grid(row=0, column=1, padx=5, pady=6)
        self.entry_sender.insert(0, EMAIL_SENDER)

        ttk.Label(frm_es, text="App Password:").grid(row=1, column=0, padx=10, pady=6, sticky="e")
        self.entry_sender_pass = ttk.Entry(frm_es, width=40, show="*")
        self.entry_sender_pass.grid(row=1, column=1, padx=5, pady=6)
        self.entry_sender_pass.insert(0, EMAIL_SENDER_PASS)

        ttk.Label(frm_es, text="Recipients (comma):").grid(row=2, column=0, padx=10, pady=6, sticky="e")
        self.entry_recipients = ttk.Entry(frm_es, width=40)
        self.entry_recipients.grid(row=2, column=1, padx=5, pady=6)
        self.entry_recipients.insert(0, ", ".join(EMAIL_RECIPIENTS))

        ttk.Label(frm_es, text="Cooldown (sec):").grid(row=3, column=0, padx=10, pady=6, sticky="e")
        self.entry_cooldown = ttk.Entry(frm_es, width=10)
        self.entry_cooldown.grid(row=3, column=1, padx=5, pady=6, sticky="w")
        self.entry_cooldown.insert(0, str(EMAIL_COOLDOWN))

        info = ttk.Label(frm_es,
            text="Get App Password: myaccount.google.com → Security → 2-Step → App Passwords",
            foreground="#1565c0", font=("Helvetica", 9))
        info.grid(row=4, column=0, columnspan=2, pady=4)

        btn_row = ttk.Frame(frm_es)
        btn_row.grid(row=5, column=0, columnspan=2, pady=8)
        ttk.Button(btn_row, text="Save Settings", command=self.save_email_settings).pack(side=tk.LEFT, padx=10)
        ttk.Button(btn_row, text="Send Test Email", command=self.test_email).pack(side=tk.LEFT, padx=10)

        self.email_status_label = ttk.Label(frame, text="", foreground="green",
                                            font=("Helvetica", 10))
        self.email_status_label.pack(pady=5)

    # ==================== Page Switch ====================
    def show_dashboard_page(self):
        self._hide_all_pages(); self.pages["dashboard"].pack(fill=tk.BOTH, expand=True)

    def show_user_mgmt_page(self):
        self._hide_all_pages(); self.pages["user_mgmt"].pack(fill=tk.BOTH, expand=True)

    def show_logs_page(self):
        self._hide_all_pages(); self.pages["logs"].pack(fill=tk.BOTH, expand=True)

    def show_settings_page(self):
        self._hide_all_pages(); self.pages["settings"].pack(fill=tk.BOTH, expand=True)

    def show_narada_page(self):
        self._hide_all_pages(); self.pages["narada"].pack(fill=tk.BOTH, expand=True)

    def show_modes_page(self):
        self._hide_all_pages(); self.pages["modes"].pack(fill=tk.BOTH, expand=True)

    def show_email_page(self):
        self._hide_all_pages(); self.pages["email"].pack(fill=tk.BOTH, expand=True)

    def show_instructions(self):
        instructions = (
            "Admin Mode Instructions:\n\n"
            "Dashboard       — Real-time detections, console, mode toggles.\n"
            "User Management — Add/delete/rename users; reset passwords. All changes are saved.\n"
            "Logs            — View, filter, export system logs.\n"
            "System Settings — Detection threshold, privacy mode, danger object label.\n"
            "Narada Commands — Add/delete custom voice commands.\n"
            "Mode Management — Create custom modes with priorities.\n"
            "Email Settings  — Update sender, App Password, recipients, test connection.\n\n"
            "Email Setup:\n"
            "  myaccount.google.com → Security → 2-Step Verification → App Passwords\n"
            "  Create one for 'Mail' + 'Other (custom name)', paste 16-char code.\n\n"
            "All changes are persisted to system_logs/users.json and system_logs/config.json."
        )
        messagebox.showinfo("Admin Instructions", instructions)

    def _hide_all_pages(self):
        for p in self.pages.values():
            p.pack_forget()

    # ==================== Periodic Update ====================
    def update_gui(self):
        if self.pages["dashboard"].winfo_ismapped():
            update_text_widget(self.det_text,     latest_detection_info)
            update_text_widget(self.console_text, "\n".join(system_updates_log[-30:]))
            self.sync_mode_checkbuttons()

        if self.pages["logs"].winfo_ismapped():
            query = self.entry_log_filter.get().strip().lower() if hasattr(self, 'entry_log_filter') else ""
            entries = [l for l in system_updates_log if query in l.lower()] if query else system_updates_log
            try:
                self.logs_text.configure(state="normal")
                self.logs_text.delete("1.0", tk.END)
                self.logs_text.insert(tk.END, "\n".join(entries[-200:]))
                self.logs_text.configure(state="disabled")
            except tk.TclError:
                pass

        if psutil and self.pages["settings"].winfo_ismapped():
            cpu  = psutil.cpu_percent()
            mem  = psutil.virtual_memory().percent
            disk = psutil.disk_usage('/').percent
            self.label_cpu.config( text=f"CPU:  {cpu:.1f}%  {self._usage_bar(cpu)}")
            self.label_mem.config( text=f"Mem:  {mem:.1f}%  {self._usage_bar(mem)}")
            self.label_disk.config(text=f"Disk: {disk:.1f}%  {self._usage_bar(disk)}")

        self.after_id = self.root.after(1000, self.update_gui)

    def _usage_bar(self, value, length=20):
        bars = int((value / 100) * length)
        return "[" + "#" * bars + "-" * (length - bars) + "]"

    # ==================== Mode Toggles ====================
    def sync_mode_checkbuttons(self):
        with _mode_lock:
            self.var_dnd.set(MODE_DND)
            self.var_email_off.set(MODE_EMAIL_OFF)
            self.var_idle.set(MODE_IDLE)
            self.var_night.set(MODE_NIGHT)
            self.var_emergency.set(MODE_EMERGENCY)

    def toggle_dnd(self):
        global MODE_DND
        with _mode_lock: MODE_DND = self.var_dnd.get()
        log_system_update(f"Admin toggled DND => {MODE_DND}")

    def toggle_email(self):
        global MODE_EMAIL_OFF
        with _mode_lock: MODE_EMAIL_OFF = self.var_email_off.get()
        log_system_update(f"Admin toggled EmailOff => {MODE_EMAIL_OFF}")

    def toggle_idle(self):
        global MODE_IDLE
        with _mode_lock: MODE_IDLE = self.var_idle.get()
        log_system_update(f"Admin toggled Idle => {MODE_IDLE}")

    def toggle_night(self):
        global MODE_NIGHT
        with _mode_lock: MODE_NIGHT = self.var_night.get()
        log_system_update(f"Admin toggled Night => {MODE_NIGHT}")

    def toggle_emergency(self):
        global MODE_EMERGENCY
        with _mode_lock: MODE_EMERGENCY = self.var_emergency.get()
        log_system_update(f"Admin toggled Emergency => {MODE_EMERGENCY}")

    # ==================== Color helpers ====================
    def _pick_user_color(self):
        from tkinter import colorchooser
        result = colorchooser.askcolor(color=self._new_user_color.get(),
                                       title="Choose Profile Box Color")
        if result and result[1]:
            self._set_color(result[1])

    def _set_color(self, color):
        self._new_user_color.set(color)
        self.lbl_color_preview.config(bg=color)

    # ==================== User Management ====================
    def refresh_user_list(self):
        self.user_listbox.delete(0, tk.END)
        for uname, udata in USERS.items():
            display = udata.get("display_name", uname)
            self.user_listbox.insert(tk.END, f"{uname}  ({display})")

    def add_user(self):
        uname    = self.entry_uname.get().strip()
        upass    = self.entry_new_pw.get().strip()
        dname    = self.entry_display_name.get().strip() or uname.capitalize()
        color    = self._new_user_color.get()
        if not uname or not upass:
            messagebox.showerror("Error", "Enter both username and password.")
            return
        if uname in USERS:
            messagebox.showerror("Error", f"User '{uname}' already exists.")
            return
        USERS[uname] = {
            "password": upass,
            "role": "user",
            "display_name": dname,
            "box_color": color,
            "history": {"logins": [], "narada_activity": []}
        }
        self.refresh_user_list()
        save_users()
        log_system_update(f"Admin added user '{uname}' (display='{dname}', color={color}).")
        messagebox.showinfo("Done", f"User '{uname}' created.\nThey will appear on the login screen.")
        self.entry_uname.delete(0, tk.END)
        self.entry_new_pw.delete(0, tk.END)
        self.entry_display_name.delete(0, tk.END)

    def _listbox_uname(self, idx):
        """Extract the raw username from a listbox entry like 'uname  (Display Name)'."""
        raw = self.user_listbox.get(idx)
        return raw.split("  (")[0].strip()

    def delete_user(self):
        sel = self.user_listbox.curselection()
        if not sel:
            messagebox.showinfo("Info", "Select a user first.")
            return
        uname = self._listbox_uname(sel[0])
        if uname == "admin":
            messagebox.showerror("Error", "Cannot delete the admin user.")
            return
        if not messagebox.askyesno("Confirm Delete", f"Delete user '{uname}'? This cannot be undone."):
            return
        USERS.pop(uname, None)
        self.refresh_user_list()
        save_users()
        log_system_update(f"Admin deleted user '{uname}'.")
        # Clear profile
        for w in self.profile_frame.winfo_children():
            w.destroy()
        ttk.Label(self.profile_frame, text="User deleted.").pack(pady=20)

    def reset_password(self):
        sel = self.user_listbox.curselection()
        if not sel:
            messagebox.showinfo("Info", "Select a user first.")
            return
        uname = self._listbox_uname(sel[0])
        if uname not in USERS:
            return
        # Show dialog for new password
        dlg = tk.Toplevel(self.root)
        dlg.title(f"Reset Password — {uname}")
        dlg.geometry("320x180")
        dlg.grab_set()
        ttk.Label(dlg, text=f"Set new password for '{uname}':").pack(pady=10)
        entry_np = ttk.Entry(dlg, show="*", width=25)
        entry_np.pack(pady=5)
        ttk.Label(dlg, text="Confirm:").pack()
        entry_cp = ttk.Entry(dlg, show="*", width=25)
        entry_cp.pack(pady=5)

        def do_reset():
            np = entry_np.get().strip()
            cp = entry_cp.get().strip()
            if not np:
                messagebox.showerror("Error", "Password cannot be empty.", parent=dlg)
                return
            if np != cp:
                messagebox.showerror("Error", "Passwords do not match.", parent=dlg)
                return
            USERS[uname]["password"] = np
            save_users()
            log_system_update(f"Admin reset password for '{uname}'.")
            dlg.destroy()
            messagebox.showinfo("Done", f"Password for '{uname}' updated.")

        ttk.Button(dlg, text="Set Password", command=do_reset).pack(pady=8)

    def rename_user(self):
        sel = self.user_listbox.curselection()
        if not sel:
            messagebox.showinfo("Info", "Select a user first.")
            return
        oldname = self._listbox_uname(sel[0])
        newname = self.entry_uname.get().strip()
        if not newname:
            messagebox.showerror("Error", "Enter new username in the Username field.")
            return
        if newname in USERS:
            messagebox.showerror("Error", f"'{newname}' already exists.")
            return
        if oldname == "admin":
            messagebox.showerror("Error", "Cannot rename admin user.")
            return
        USERS[newname] = USERS.pop(oldname)
        self.refresh_user_list()
        save_users()
        log_system_update(f"Admin renamed '{oldname}' → '{newname}'.")

    def on_select_user(self, event):
        sel = self.user_listbox.curselection()
        if sel:
            self.show_user_profile(self._listbox_uname(sel[0]))

    def show_user_profile(self, uname):
        for w in self.profile_frame.winfo_children():
            w.destroy()
        if uname not in USERS:
            ttk.Label(self.profile_frame, text="User not found.").pack(pady=20)
            return
        data = USERS[uname]
        ttk.Label(self.profile_frame, text=f"Profile: {uname}",
                  font=("Helvetica", 13, "bold")).pack(pady=5)
        ttk.Label(self.profile_frame, text=f"Role: {data['role']}").pack(anchor="w", padx=10)
        ttk.Label(self.profile_frame, text=f"Display Name: {data.get('display_name', uname)}").pack(anchor="w", padx=10)

        # Color preview
        color_row = ttk.Frame(self.profile_frame)
        color_row.pack(anchor="w", padx=10, pady=2)
        ttk.Label(color_row, text="Box Color:").pack(side=tk.LEFT)
        color = data.get("box_color", "#1565c0")
        tk.Label(color_row, bg=color, width=4, relief="solid").pack(side=tk.LEFT, padx=5)
        ttk.Label(color_row, text=color, foreground="grey").pack(side=tk.LEFT)

        # Change color button
        def change_color():
            from tkinter import colorchooser
            result = colorchooser.askcolor(color=color, title=f"Change color for {uname}")
            if result and result[1]:
                USERS[uname]["box_color"] = result[1]
                save_users()
                self.show_user_profile(uname)  # refresh
        ttk.Button(self.profile_frame, text="Change Box Color",
                   command=change_color).pack(anchor="w", padx=10, pady=2)

        ttk.Label(self.profile_frame, text="Password: ••••••").pack(anchor="w", padx=10)
        ttk.Label(self.profile_frame,
                  text=f"Logins: {len(data['history'].get('logins', []))}").pack(anchor="w", padx=10)

        ttk.Label(self.profile_frame, text="Login History:",
                  font=("Helvetica", 11, "underline")).pack(anchor="w", padx=10, pady=(8, 2))
        hist_frame = ttk.Frame(self.profile_frame)
        hist_frame.pack(fill=tk.X, padx=10)
        for t in data["history"].get("logins", [])[-5:]:
            ttk.Label(hist_frame, text=f"  {t}").pack(anchor="w")

        ttk.Label(self.profile_frame, text="Narada Activity:",
                  font=("Helvetica", 11, "underline")).pack(anchor="w", padx=10, pady=(8, 2))
        act_frame = ttk.Frame(self.profile_frame)
        act_frame.pack(fill=tk.BOTH, expand=True, padx=10)
        act_txt = tk.Text(act_frame, height=6, wrap="word", state="normal")
        act_txt.insert(tk.END, "\n".join(data["history"].get("narada_activity", [])[-20:]))
        act_txt.configure(state="disabled")
        act_txt.pack(fill=tk.BOTH, expand=True)

    # ==================== Logs ====================
    def clear_logs(self):
        global system_updates_log
        if messagebox.askyesno("Clear Logs", "Clear all system logs?"):
            system_updates_log = []
            log_system_update("Admin cleared system logs.")

    def _filter_logs(self):
        query = self.entry_log_filter.get().strip().lower()
        filtered = [l for l in system_updates_log if query in l.lower()] if query else system_updates_log
        try:
            self.logs_text.configure(state="normal")
            self.logs_text.delete("1.0", tk.END)
            self.logs_text.insert(tk.END, "\n".join(filtered[-200:]))
            self.logs_text.configure(state="disabled")
        except tk.TclError:
            pass

    def _export_logs(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            title="Export Logs"
        )
        if path:
            try:
                with open(path, "w") as f:
                    f.write("\n".join(system_updates_log))
                messagebox.showinfo("Exported", f"Logs saved to:\n{path}")
            except Exception as e:
                messagebox.showerror("Error", str(e))

    # ==================== System Settings ====================
    def _on_threshold_change(self, val):
        global DETECTION_THRESHOLD
        with _mode_lock:
            DETECTION_THRESHOLD = round(float(val), 2)
        self.lbl_threshold.config(text=f"Current: {DETECTION_THRESHOLD:.2f}")
        save_config()

    def _toggle_privacy(self):
        global MODE_PRIVACY
        with _mode_lock:
            MODE_PRIVACY = self.var_privacy.get()
        log_system_update(f"Admin toggled Privacy Mode => {MODE_PRIVACY}")

    def _set_danger_label(self):
        label = self.entry_danger_label.get().strip().lower()
        if label:
            self.user_data.danger_label = label
            log_system_update(f"Admin set danger label to '{label}'.")
            messagebox.showinfo("Saved", f"Danger object set to: '{label}'")

    # ==================== Narada Commands ====================
    def refresh_custom_cmds(self):
        self.list_custom_cmds.delete(0, tk.END)
        for phrase, resp in CUSTOM_VOICE_COMMANDS.items():
            self.list_custom_cmds.insert(tk.END, f"  {phrase}  =>  {resp}")

    def add_command(self):
        phrase = self.entry_cmd_phrase.get().strip().lower()
        resp   = self.entry_cmd_response.get().strip()
        if not phrase or not resp:
            messagebox.showerror("Error", "Enter both phrase and response.")
            return
        CUSTOM_VOICE_COMMANDS[phrase] = resp
        self.refresh_custom_cmds()
        save_config()
        log_system_update(f"Admin added custom command: '{phrase}' => '{resp}'")
        self.entry_cmd_phrase.delete(0, tk.END)
        self.entry_cmd_response.delete(0, tk.END)

    def del_command(self):
        sel = self.list_custom_cmds.curselection()
        if not sel:
            return
        line = self.list_custom_cmds.get(sel[0])
        parts = line.split("=>", maxsplit=1)
        if len(parts) == 2:
            phrase = parts[0].strip().lower()
            CUSTOM_VOICE_COMMANDS.pop(phrase, None)
        self.refresh_custom_cmds()
        save_config()
        log_system_update(f"Admin deleted custom command: '{line.strip()}'")

    # ==================== Mode Management ====================
    def refresh_custom_modes_list(self):
        self.custom_modes_listbox.delete(0, tk.END)
        self.custom_modes_listbox.insert(tk.END, "Night (priority=10)  [built-in]")
        self.custom_modes_listbox.insert(tk.END, "Emergency (priority=11)  [built-in]")
        self.custom_modes_listbox.insert(tk.END, "─" * 28)
        for m, data in CUSTOM_MODES.items():
            prio = data.get("priority", 1)
            self.custom_modes_listbox.insert(tk.END, f"{m}  (priority={prio})")

    def save_mode(self):
        name = self.entry_mode_name.get().strip().lower()
        if not name:
            messagebox.showerror("Error", "Enter mode name.")
            return
        try:
            prio = int(self.entry_mode_priority.get().strip())
            prio = max(1, min(9, prio))
        except ValueError:
            prio = 1
        CUSTOM_MODES[name] = {"priority": prio}
        self.refresh_custom_modes_list()
        save_config()
        log_system_update(f"Admin saved mode '{name}' priority={prio}")
        self.entry_mode_name.delete(0, tk.END)
        self.entry_mode_priority.delete(0, tk.END)

    def delete_mode(self):
        sel = self.custom_modes_listbox.curselection()
        if not sel:
            return
        line = self.custom_modes_listbox.get(sel[0])
        if "[built-in]" in line or line.startswith("─"):
            messagebox.showerror("Error", "Cannot delete built-in modes.")
            return
        mode_name = line.split("(")[0].strip().lower() if "(" in line else line.strip().lower()
        if mode_name in CUSTOM_MODES:
            del CUSTOM_MODES[mode_name]
        self.refresh_custom_modes_list()
        save_config()
        log_system_update(f"Admin deleted mode '{mode_name}'")

    # ==================== Email Settings ====================
    def save_email_settings(self):
        global EMAIL_RECIPIENTS, EMAIL_COOLDOWN, EMAIL_SENDER, EMAIL_SENDER_PASS
        EMAIL_SENDER      = self.entry_sender.get().strip()
        EMAIL_SENDER_PASS = self.entry_sender_pass.get().strip()
        recips = self.entry_recipients.get().strip()
        if recips:
            EMAIL_RECIPIENTS = [r.strip() for r in recips.split(",") if r.strip()]
        try:
            EMAIL_COOLDOWN = int(self.entry_cooldown.get().strip())
        except ValueError:
            pass
        save_config()
        log_system_update(f"Email settings updated. Recipients={EMAIL_RECIPIENTS}")
        messagebox.showinfo("Saved", "Email settings saved to config.json.")

    def test_email(self):
        self.email_status_label.config(text="Sending test email...", foreground="blue")

        def _send():
            dest = EMAIL_RECIPIENTS[0] if EMAIL_RECIPIENTS else EMAIL_SENDER
            ok, err = send_otp_via_email(dest, "TEST-123")
            if ok:
                self.root.after(0, lambda: self.email_status_label.config(
                    text=f"Test email sent to {dest}!", foreground="green"))
            else:
                self.root.after(0, lambda: self.email_status_label.config(
                    text=f"Error: {err}", foreground="red"))

        threading.Thread(target=_send, daemon=True).start()

    # ==================== Logout ====================
    def logout(self):
        if self.after_id:
            self.root.after_cancel(self.after_id)
        self.root.destroy()
        root_main = tk.Tk()
        LoginHomeScreen(root_main)
        root_main.mainloop()

##############################################################################
# MAIN APP LAUNCH
##############################################################################
def run_main_app(is_admin=False, username="user"):
    global app, dashboard_gui, _app_start_time

    _app_start_time = time.time()
    user_data = user_app_callback_class()

    parser = get_default_parser()
    parser.add_argument("--video_source", default="usb", help="Set 'rpi' or device path.")
    parser.add_argument("--network", default="yolov8s",
                        choices=["yolov6n", "yolov8s", "yolox_s_leaky"])
    parser.add_argument("--hef-path", default=None)
    parser.add_argument("--labels-json", default=None)
    args = parser.parse_args()

    Gst.init(None)
    log_system_update("GStreamer initialized.")

    app = GStreamerDetectionApp(args, user_data)
    log_system_update("GStreamer pipeline created.")

    pipeline_thread = threading.Thread(target=app.run, daemon=True)
    pipeline_thread.start()
    log_system_update("Pipeline is running.")

    root = tk.Tk()
    if not is_admin:
        gui = UserDashboardGUI(root, user_data, username=username)
    else:
        gui = AdminDashboardGUI(root, user_data, username=username)
    dashboard_gui = gui

    root.mainloop()
    stop_and_exit()

##############################################################################
# LOGIN SCREENS
##############################################################################
class LoginHomeScreen:
    """Netflix-style profile selector. Shows one box per user (dynamic) + Admin box."""
    def __init__(self, root):
        self.root = root
        self.root.title("Garuda Security — Select Profile")

        # Count profiles to size window
        user_count = sum(1 for u in USERS.values() if u.get("role") != "admin")
        admin_count = sum(1 for u in USERS.values() if u.get("role") == "admin")
        total_boxes = user_count + admin_count
        win_w = max(600, min(120 + total_boxes * 130, 1100))
        self.root.geometry(f"{win_w}x460")
        self.root.resizable(True, False)

        tk.Label(self.root, text="GARUDA SECURITY",
                 font=("Helvetica", 26, "bold")).pack(pady=20)
        tk.Label(self.root, text="Select Your Profile",
                 font=("Helvetica", 13), fg="#555").pack(pady=2)

        # Scrollable canvas for many users
        canvas_frame = tk.Frame(self.root)
        canvas_frame.pack(fill=tk.BOTH, expand=True, pady=10)

        canvas = tk.Canvas(canvas_frame, height=260, highlightthickness=0)
        scrollbar = tk.Scrollbar(canvas_frame, orient="horizontal",
                                 command=canvas.xview)
        canvas.configure(xscrollcommand=scrollbar.set)
        canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.TOP, fill=tk.X)

        container = tk.Frame(canvas)
        canvas.create_window((0, 0), window=container, anchor="nw")
        container.bind("<Configure>",
                       lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        # Create a profile box for each non-admin user
        col = 0
        for uname, udata in USERS.items():
            if udata.get("role") == "admin":
                continue
            display = udata.get("display_name", uname.capitalize())
            color   = udata.get("box_color", "#1565c0")
            self._make_profile_box(container, display, color, col,
                                   lambda u=uname: self._goto_user_login(u))
            col += 1

        # Admin box (always last)
        for uname, udata in USERS.items():
            if udata.get("role") == "admin":
                display = udata.get("display_name", "Admin")
                color   = udata.get("box_color", "#e65100")
                self._make_profile_box(container, display, color, col,
                                       self.goto_admin_login, icon="⚙")
                col += 1

        tk.Button(self.root, text="Exit", font=("Helvetica", 11),
                  bg="#c62828", fg="white", relief="flat",
                  command=stop_and_exit).pack(pady=15, ipadx=15, ipady=4)

    def _make_profile_box(self, parent, label, color, col, cmd, icon="👤"):
        """Create a Netflix-style clickable profile tile."""
        frame = tk.Frame(parent, bg=color, width=110, height=130,
                         cursor="hand2")
        frame.grid(row=0, column=col, padx=15, pady=10)
        frame.grid_propagate(False)

        # Icon
        tk.Label(frame, text=icon, font=("Helvetica", 32),
                 bg=color, fg="white").place(relx=0.5, rely=0.35, anchor="center")

        # Name label
        tk.Label(frame, text=label, font=("Helvetica", 10, "bold"),
                 bg=color, fg="white", wraplength=100,
                 justify="center").place(relx=0.5, rely=0.75, anchor="center")

        # Whole frame clickable
        for widget in [frame] + frame.winfo_children():
            widget.bind("<Button-1>", lambda e, c=cmd: c())
        # Hover effect
        frame.bind("<Enter>", lambda e, f=frame, c=color: f.config(bg=self._lighten(c)))
        frame.bind("<Leave>", lambda e, f=frame, c=color: f.config(bg=c))

    def _lighten(self, hex_color):
        """Return a slightly lighter shade of a hex color for hover."""
        try:
            r = int(hex_color[1:3], 16)
            g = int(hex_color[3:5], 16)
            b = int(hex_color[5:7], 16)
            r = min(255, r + 40)
            g = min(255, g + 40)
            b = min(255, b + 40)
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return hex_color

    def _goto_user_login(self, username):
        self.root.destroy()
        root2 = tk.Tk()
        UserLoginScreen(root2, prefill_username=username)
        root2.mainloop()

    def goto_admin_login(self):
        self.root.destroy()
        root2 = tk.Tk()
        AdminLoginScreen(root2)
        root2.mainloop()


class UserLoginScreen:
    def __init__(self, root, prefill_username=None):
        self.root = root
        self.root.title("User Login")
        self.root.geometry("400x300")
        self.root.resizable(False, False)

        # Show display name if we know who's logging in
        title = "User Login"
        if prefill_username and prefill_username in USERS:
            dname = USERS[prefill_username].get("display_name", prefill_username)
            color = USERS[prefill_username].get("box_color", "#1565c0")
            title_lbl = tk.Label(root, text=dname, font=("Helvetica", 20, "bold"),
                                 fg=color)
            title_lbl.pack(pady=10)
        else:
            tk.Label(root, text=title, font=("Helvetica", 18, "bold")).pack(pady=15)

        tk.Label(root, text="Username:").pack()
        self.entry_username = tk.Entry(root, width=25)
        self.entry_username.pack(pady=3)
        if prefill_username:
            self.entry_username.insert(0, prefill_username)

        tk.Label(root, text="Password:").pack()
        self.entry_password = tk.Entry(root, show="*", width=25)
        self.entry_password.pack(pady=3)

        btn_frame = tk.Frame(root)
        btn_frame.pack(pady=8)
        tk.Button(btn_frame, text="Login", width=10, command=self.check_user).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Back",  width=10, command=self.go_back).pack(side=tk.LEFT, padx=5)

        tk.Button(root, text="Forgot Password?", fg="blue",
                  relief="flat", command=self.forgot_password).pack()

    def check_user(self):
        un = self.entry_username.get().strip()
        pw = self.entry_password.get().strip()
        if un in USERS and USERS[un]["password"] == pw and USERS[un]["role"] == "user":
            messagebox.showinfo("Success", "Login successful!")
            self.root.destroy()
            run_main_app(is_admin=False, username=un)
        else:
            messagebox.showerror("Error", "Invalid username or password.")

    def go_back(self):
        self.root.destroy()
        root_main = tk.Tk()
        LoginHomeScreen(root_main)
        root_main.mainloop()

    def forgot_password(self):
        global USER_FORGOT_OTP
        un = self.entry_username.get().strip()
        if not un or un not in USERS:
            messagebox.showerror("Error", "Enter a valid username first.")
            return
        USER_FORGOT_OTP = generate_otp_code(6)
        dest = EMAIL_RECIPIENTS[0] if EMAIL_RECIPIENTS else EMAIL_SENDER
        ok, err = send_otp_via_email(dest, USER_FORGOT_OTP)
        if not ok:
            messagebox.showerror("Email Error",
                f"Could not send OTP:\n{err}\n\nUpdate email settings in Admin mode.")
            return
        messagebox.showinfo("OTP Sent", f"OTP sent to the configured alert email.\nEnter it below.")
        ForgotPasswordOTPDialog(self.root, username=un)


class ForgotPasswordOTPDialog:
    def __init__(self, parent, username="user"):
        self.username = username
        self.top = tk.Toplevel(parent)
        self.top.title("Reset Password")
        self.top.geometry("350x250")
        self.top.grab_set()

        tk.Label(self.top, text=f"Reset password for: {username}",
                 font=("Helvetica", 11, "bold")).pack(pady=10)
        tk.Label(self.top, text="OTP Code:").pack()
        self.entry_otp = tk.Entry(self.top, width=20)
        self.entry_otp.pack(pady=4)

        tk.Label(self.top, text="New Password:").pack()
        self.entry_new_pass = tk.Entry(self.top, show="*", width=20)
        self.entry_new_pass.pack(pady=4)

        tk.Label(self.top, text="Confirm Password:").pack()
        self.entry_confirm = tk.Entry(self.top, show="*", width=20)
        self.entry_confirm.pack(pady=4)

        tk.Button(self.top, text="Verify & Reset",
                  command=self.verify_otp).pack(pady=8)

    def verify_otp(self):
        global USER_FORGOT_OTP
        user_otp = self.entry_otp.get().strip()
        new_pass  = self.entry_new_pass.get().strip()
        confirm   = self.entry_confirm.get().strip()
        if user_otp != USER_FORGOT_OTP:
            messagebox.showerror("Error", "Invalid OTP.", parent=self.top)
            return
        if not new_pass:
            messagebox.showerror("Error", "Password cannot be empty.", parent=self.top)
            return
        if new_pass != confirm:
            messagebox.showerror("Error", "Passwords do not match.", parent=self.top)
            return
        if self.username in USERS:
            USERS[self.username]["password"] = new_pass
            save_users()
            messagebox.showinfo("Success",
                f"Password for '{self.username}' has been reset.", parent=self.top)
            self.top.destroy()
        else:
            messagebox.showerror("Error", "User not found.", parent=self.top)


class AdminLoginScreen:
    def __init__(self, root):
        self.root = root
        self.root.title("Admin Login")
        self.root.geometry("400x280")
        self.root.resizable(False, False)

        tk.Label(root, text="Admin Login", font=("Helvetica", 18, "bold")).pack(pady=15)
        tk.Label(root, text="Username:").pack()
        self.entry_username = tk.Entry(root, width=25)
        self.entry_username.pack(pady=3)

        tk.Label(root, text="Password:").pack()
        self.entry_password = tk.Entry(root, show="*", width=25)
        self.entry_password.pack(pady=3)

        btn_frame = tk.Frame(root)
        btn_frame.pack(pady=8)
        tk.Button(btn_frame, text="Login", width=10, command=self.check_admin).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Back",  width=10, command=self.go_back).pack(side=tk.LEFT, padx=5)

    def check_admin(self):
        un = self.entry_username.get().strip()
        pw = self.entry_password.get().strip()
        if un in USERS and USERS[un]["password"] == pw and USERS[un]["role"] == "admin":
            global ADMIN_OTP
            ADMIN_OTP = generate_otp_code(6)
            dest = EMAIL_RECIPIENTS[0] if EMAIL_RECIPIENTS else EMAIL_SENDER
            ok, err = send_otp_via_email(dest, ADMIN_OTP)
            if not ok:
                # Offer to bypass OTP if email is broken (show warning)
                if messagebox.askyesno(
                    "Email Error",
                    f"Could not send OTP:\n{err}\n\n"
                    "Proceed without OTP verification? (Not recommended)\n"
                    "Fix email in Admin → Email Settings."
                ):
                    self.root.destroy()
                    run_main_app(is_admin=True, username=un)
                return
            messagebox.showinfo("OTP Sent", "OTP sent to the configured alert email.")
            self.root.destroy()
            root_otp = tk.Tk()
            AdminOTPFrame(root_otp, username=un)
            root_otp.mainloop()
        else:
            messagebox.showerror("Error", "Invalid admin credentials.")

    def go_back(self):
        self.root.destroy()
        root_main = tk.Tk()
        LoginHomeScreen(root_main)
        root_main.mainloop()


class AdminOTPFrame:
    def __init__(self, root, username="admin"):
        self.root = root
        self.username = username
        self.root.title("Admin OTP Verification")
        self.root.geometry("400x200")
        self.root.resizable(False, False)

        tk.Label(root, text="Enter the 6-digit OTP:", font=("Helvetica", 12)).pack(pady=15)
        self.entry_otp = tk.Entry(root, width=15, font=("Helvetica", 14), justify="center")
        self.entry_otp.pack(pady=5)
        tk.Button(root, text="Verify OTP", width=15,
                  command=self.verify_otp).pack(pady=12)

    def verify_otp(self):
        global ADMIN_OTP
        user_otp = self.entry_otp.get().strip()
        if user_otp == ADMIN_OTP:
            messagebox.showinfo("Success", "OTP verified. Welcome, Admin!")
            self.root.destroy()
            run_main_app(is_admin=True, username=self.username)
        else:
            messagebox.showerror("Error", "Invalid OTP. Try again.")

##############################################################################
# MAIN
##############################################################################
if __name__ == "__main__":
    root = tk.Tk()
    LoginHomeScreen(root)
    root.mainloop()
