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
import glob
import socket
import shutil

import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import PIL  # minimal usage
import speech_recognition as sr

# Optional: psutil for hardware usage
try:
    import psutil
except ImportError:
    psutil = None
    print("Warning: psutil not installed. Hardware monitoring will be basic.")

from gpiozero import LED, Button, OutputDevice

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
# GLOBALS & SETTINGS
##############################################################################
app = None

SCISSORS_LOG_FILE = "danger_sightings.txt"
NIGHT_MODE_LOG_FILE = "night_mode_findings.txt"

# Persistent logs configuration
LOGS_DIR = "system_logs"
LOG_RETENTION_DAYS = 7  # Keep logs for 7 days
SYSTEM_LOG_FILE = os.path.join(LOGS_DIR, "system_log.json")
VOICE_LOG_FILE = os.path.join(LOGS_DIR, "voice_log.json")
USERS_DATA_FILE = os.path.join(LOGS_DIR, "users_data.json")
ALERT_HISTORY_FILE = os.path.join(LOGS_DIR, "alert_history.json")
SYSTEM_SETTINGS_FILE = os.path.join(LOGS_DIR, "system_settings.json")
BACKUP_DIR = os.path.join(LOGS_DIR, "backups")

# Create necessary directories
for directory in [LOGS_DIR, BACKUP_DIR]:
    if not os.path.exists(directory):
        os.makedirs(directory)

# Logging
system_updates_log = []   # System or admin updates
voice_assistant_log = []  # What Narada hears
voice_responses = []      # Narada's responses to user
latest_detection_info = ""  # Updated by GStreamer callback
alert_history = []        # History of all alerts

# Detection statistics
detection_stats = {
    "total_detections": 0,
    "scissors_count": 0,
    "person_count": 0,
    "last_detection_time": None,
    "hourly_stats": {}
}

# System settings
system_settings = {
    "quiet_hours": {"enabled": False, "start": "22:00", "end": "07:00"},
    "alert_volume": 100,
    "detection_sensitivity": 0.3,
    "auto_backup": True,
    "backup_interval_days": 3,
    "last_backup": None
}

# OTP / Emails
ADMIN_OTP = None
USER_FORGOT_OTP = None
EMAIL_SENDER = "mgonugondlamanikanta@gmail.com"
EMAIL_SENDER_PASS = "dhdd sbna rxhx fzwg"
EMAIL_RECIPIENTS = ["mgonugondlamanikanta@gmail.com"]
EMAIL_COOLDOWN = 60
last_email_sent_time = 0

# Modes
MODE_DND = False
MODE_EMAIL_OFF = False
MODE_IDLE = False
MODE_NIGHT = False
MODE_EMERGENCY = False

# Additional (custom) modes with priorities
CUSTOM_MODES = {}  # e.g., { "strict": {"priority":2, ...}, ... }
# We'll treat NIGHT=priority 10, EMERGENCY=priority 11 for demonstration

# Wake word
NARADA_WAKE_WORD = "narada"

# GStreamer references
red_led = None
buzzer = None
stop_button = None

# For voice assistant updating checkboxes
dashboard_gui = None

# Performance optimization
UPDATE_CACHE = {}
CACHE_EXPIRY = 5  # seconds

# Users, with a "history" sub-dict to store login times, Narada activity, etc.
USERS = {
    "user": {
        "password": "user",
        "role": "user",
        "history": {
            "logins": [],
            "narada_activity": []
        }
    },
    "admin": {
        "password": "root",
        "role": "admin",
        "history": {
            "logins": [],
            "narada_activity": []
        }
    }
}

# Modern UI Color Scheme
COLORS = {
    "bg_primary": "#1a1a2e",
    "bg_secondary": "#16213e",
    "bg_tertiary": "#0f3460",
    "accent": "#e94560",
    "text_primary": "#f5f5f5",
    "text_secondary": "#b8b8b8",
    "success": "#4caf50",
    "warning": "#ff9800",
    "error": "#f44336",
    "info": "#2196f3",
    "border": "#2c3e50",
    "hover": "#34495e"
}

##############################################################################
# PERSISTENT STORAGE FUNCTIONS
##############################################################################
def load_persistent_data():
    """Load logs and user data from files on startup."""
    global system_updates_log, voice_assistant_log, voice_responses, USERS, alert_history, system_settings, detection_stats
    
    # Clean old logs first
    clean_old_logs()
    
    # Load system logs
    if os.path.exists(SYSTEM_LOG_FILE):
        try:
            with open(SYSTEM_LOG_FILE, 'r') as f:
                data = json.load(f)
                system_updates_log = data.get('system_updates', [])
        except:
            system_updates_log = []
    
    # Load voice logs
    if os.path.exists(VOICE_LOG_FILE):
        try:
            with open(VOICE_LOG_FILE, 'r') as f:
                data = json.load(f)
                voice_assistant_log = data.get('voice_log', [])
                voice_responses = data.get('voice_responses', [])
        except:
            voice_assistant_log = []
            voice_responses = []
    
    # Load users data
    if os.path.exists(USERS_DATA_FILE):
        try:
            with open(USERS_DATA_FILE, 'r') as f:
                USERS = json.load(f)
        except:
            pass  # Keep default USERS
    
    # Load alert history
    if os.path.exists(ALERT_HISTORY_FILE):
        try:
            with open(ALERT_HISTORY_FILE, 'r') as f:
                alert_history = json.load(f)
        except:
            alert_history = []
    
    # Load system settings
    if os.path.exists(SYSTEM_SETTINGS_FILE):
        try:
            with open(SYSTEM_SETTINGS_FILE, 'r') as f:
                loaded_settings = json.load(f)
                system_settings.update(loaded_settings)
        except:
            pass

def save_persistent_data():
    """Save logs and user data to files."""
    # Save system logs
    try:
        with open(SYSTEM_LOG_FILE, 'w') as f:
            json.dump({
                'system_updates': system_updates_log[-1000:],  # Keep last 1000 entries for performance
                'last_updated': datetime.datetime.now().isoformat()
            }, f, indent=2)
    except:
        pass
    
    # Save voice logs
    try:
        with open(VOICE_LOG_FILE, 'w') as f:
            json.dump({
                'voice_log': voice_assistant_log[-500:],  # Keep last 500 entries
                'voice_responses': voice_responses[-500:],
                'last_updated': datetime.datetime.now().isoformat()
            }, f, indent=2)
    except:
        pass
    
    # Save users data
    try:
        with open(USERS_DATA_FILE, 'w') as f:
            json.dump(USERS, f, indent=2)
    except:
        pass
    
    # Save alert history
    try:
        with open(ALERT_HISTORY_FILE, 'w') as f:
            json.dump(alert_history[-500:], f, indent=2)  # Keep last 500 alerts
    except:
        pass
    
    # Save system settings
    try:
        with open(SYSTEM_SETTINGS_FILE, 'w') as f:
            json.dump(system_settings, f, indent=2)
    except:
        pass

def create_backup():
    """Create a backup of all system data."""
    backup_time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"backup_{backup_time}")
    
    try:
        os.makedirs(backup_path)
        
        # Copy all important files
        files_to_backup = [
            SYSTEM_LOG_FILE, VOICE_LOG_FILE, USERS_DATA_FILE,
            ALERT_HISTORY_FILE, SYSTEM_SETTINGS_FILE,
            SCISSORS_LOG_FILE, NIGHT_MODE_LOG_FILE
        ]
        
        for file_path in files_to_backup:
            if os.path.exists(file_path):
                shutil.copy2(file_path, backup_path)
        
        system_settings["last_backup"] = backup_time
        save_persistent_data()
        return True
    except Exception as e:
        log_system_update(f"Backup failed: {str(e)}")
        return False

def restore_backup(backup_name):
    """Restore system data from a backup."""
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    
    if not os.path.exists(backup_path):
        return False
    
    try:
        for file_name in os.listdir(backup_path):
            src = os.path.join(backup_path, file_name)
            dst = os.path.join(LOGS_DIR, file_name)
            if file_name in ["danger_sightings.txt", "night_mode_findings.txt"]:
                dst = file_name
            shutil.copy2(src, dst)
        
        load_persistent_data()
        return True
    except:
        return False

def get_cached_data(key, compute_func):
    """Get cached data or compute if expired."""
    current_time = time.time()
    
    if key in UPDATE_CACHE:
        cached_time, cached_data = UPDATE_CACHE[key]
        if current_time - cached_time < CACHE_EXPIRY:
            return cached_data
    
    # Compute new data
    new_data = compute_func()
    UPDATE_CACHE[key] = (current_time, new_data)
    return new_data

def clean_old_logs():
    """Remove log entries older than LOG_RETENTION_DAYS."""
    cutoff_date = datetime.datetime.now() - datetime.timedelta(days=LOG_RETENTION_DAYS)
    
    # Function to filter logs by date
    def filter_by_date(logs):
        filtered = []
        for log in logs:
            try:
                # Extract timestamp from log entry
                if log.startswith('['):
                    timestamp_str = log[1:20]  # Extract YYYY-MM-DD HH:MM:SS
                    timestamp = datetime.datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                    if timestamp > cutoff_date:
                        filtered.append(log)
                else:
                    filtered.append(log)  # Keep logs without timestamps
            except:
                filtered.append(log)  # Keep logs that can't be parsed
        return filtered
    
    # Filter system logs
    system_updates_log[:] = filter_by_date(system_updates_log)
    
    # Filter voice logs
    voice_assistant_log[:] = filter_by_date(voice_assistant_log)
    voice_responses[:] = filter_by_date(voice_responses)

##############################################################################
# HELPER & LOGGING FUNCTIONS
##############################################################################
def log_system_update(message):
    """Append a system update message to the system updates log."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    system_updates_log.append(f"[{timestamp}] {message}")
    save_persistent_data()  # Auto-save

def append_voice_log(message, user_name=None):
    """Append a message to the Narada voice log. Optionally track user_name's Narada activity."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {message}"
    voice_assistant_log.append(entry)

    # Also store in user history if provided
    if user_name and user_name in USERS:
        USERS[user_name]["history"]["narada_activity"].append(entry)
    
    save_persistent_data()  # Auto-save

def append_voice_response(message, user_name=None):
    """Append a response from Narada to the voice responses list. Also track user_name's Narada activity."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {message}"
    voice_responses.append(entry)

    if user_name and user_name in USERS:
        USERS[user_name]["history"]["narada_activity"].append(entry)
    
    save_persistent_data()  # Auto-save

def log_alert(alert_type, details=""):
    """Log an alert event."""
    alert_entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "type": alert_type,
        "details": details,
        "modes": {
            "dnd": MODE_DND,
            "email_off": MODE_EMAIL_OFF,
            "idle": MODE_IDLE,
            "night": MODE_NIGHT,
            "emergency": MODE_EMERGENCY
        }
    }
    alert_history.append(alert_entry)
    save_persistent_data()

def update_detection_stats(label):
    """Update detection statistics."""
    global detection_stats
    current_time = datetime.datetime.now()
    hour_key = current_time.strftime("%Y-%m-%d %H:00")
    
    detection_stats["total_detections"] += 1
    detection_stats["last_detection_time"] = current_time.isoformat()
    
    if label == "scissors":
        detection_stats["scissors_count"] += 1
    elif label == "person":
        detection_stats["person_count"] += 1
    
    if hour_key not in detection_stats["hourly_stats"]:
        detection_stats["hourly_stats"][hour_key] = {"total": 0, "scissors": 0, "person": 0}
    
    detection_stats["hourly_stats"][hour_key]["total"] += 1
    if label in ["scissors", "person"]:
        detection_stats["hourly_stats"][hour_key][label] += 1

def is_quiet_hours():
    """Check if current time is within quiet hours."""
    if not system_settings["quiet_hours"]["enabled"]:
        return False
    
    current_time = datetime.datetime.now().time()
    start_time = datetime.datetime.strptime(system_settings["quiet_hours"]["start"], "%H:%M").time()
    end_time = datetime.datetime.strptime(system_settings["quiet_hours"]["end"], "%H:%M").time()
    
    if start_time <= end_time:
        return start_time <= current_time <= end_time
    else:  # Quiet hours span midnight
        return current_time >= start_time or current_time <= end_time

def get_alert_analytics():
    """Get analytics data for alerts."""
    if not alert_history:
        return {}
    
    # Analyze last 24 hours
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=24)
    recent_alerts = [a for a in alert_history if datetime.datetime.fromisoformat(a["timestamp"]) > cutoff]
    
    # Count by hour
    hourly_counts = {}
    for alert in recent_alerts:
        hour = datetime.datetime.fromisoformat(alert["timestamp"]).strftime("%H:00")
        hourly_counts[hour] = hourly_counts.get(hour, 0) + 1
    
    return {
        "total_24h": len(recent_alerts),
        "hourly": hourly_counts,
        "peak_hour": max(hourly_counts.items(), key=lambda x: x[1])[0] if hourly_counts else "N/A"
    }

def get_user_activity_summary(username):
    """Get activity summary for a user."""
    if username not in USERS:
        return {}
    
    user_data = USERS[username]
    logins = user_data["history"].get("logins", [])
    narada_activity = user_data["history"].get("narada_activity", [])
    
    # Get recent activity
    recent_logins = logins[-10:] if logins else []
    recent_narada = len([a for a in narada_activity if "[Listening..." in a])
    
    return {
        "total_logins": len(logins),
        "recent_logins": recent_logins,
        "narada_sessions": recent_narada,
        "last_login": logins[-1] if logins else "Never"
    }

def update_text_widget(widget, new_text):
    try:
        current_view = widget.yview()
    except:
        current_view = (0.0, 1.0)
    widget.configure(state="normal")
    widget.delete("1.0", tk.END)
    widget.insert(tk.END, new_text)
    if current_view[1] >= 0.99:
        widget.see(tk.END)
    else:
        widget.yview_moveto(current_view[0])
    widget.configure(state="disabled")

def stop_and_exit():
    print("Stopping GStreamer pipeline now...")
    log_system_update("Stopping pipeline & exiting the app.")
    save_persistent_data()  # Save before exit
    if app is not None:
        app.pipeline.set_state(Gst.State.NULL)
    sys.exit(0)

def button_pressed():
    stop_and_exit()

##############################################################################
# OTP / EMAIL
##############################################################################
def generate_otp_code(length=6):
    digits = string.digits
    return "".join(random.choice(digits) for _ in range(length))

def send_otp_via_email(email, otp_code):
    subject = "Your OTP Code"
    body = f"Hello,\n\nYour OTP code is: {otp_code}\n\nUse this to complete your login/forgot flow."
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = EMAIL_SENDER
    msg['To'] = email

    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(EMAIL_SENDER, EMAIL_SENDER_PASS)
        server.send_message(msg)
        server.quit()
        log_system_update(f"Sent OTP via email to {email}")
        return True
    except Exception as e:
        log_system_update(f"Failed to send OTP => {str(e)}")
        print(f"Email error details: {str(e)}")  # Debug print
        return False

##############################################################################
# STYLED WIDGETS
##############################################################################
class ModernButton(tk.Button):
    def __init__(self, parent, text="", command=None, style="primary", **kwargs):
        # Define button styles
        styles = {
            "primary": {"bg": COLORS["accent"], "fg": COLORS["text_primary"], "hover": "#ff5976"},
            "secondary": {"bg": COLORS["bg_tertiary"], "fg": COLORS["text_primary"], "hover": COLORS["hover"]},
            "success": {"bg": COLORS["success"], "fg": COLORS["text_primary"], "hover": "#45a049"},
            "warning": {"bg": COLORS["warning"], "fg": COLORS["text_primary"], "hover": "#e68900"},
            "danger": {"bg": COLORS["error"], "fg": COLORS["text_primary"], "hover": "#da190b"},
            "info": {"bg": COLORS["info"], "fg": COLORS["text_primary"], "hover": "#1976d2"}
        }
        
        style_config = styles.get(style, styles["primary"])
        
        super().__init__(
            parent,
            text=text,
            command=command,
            bg=style_config["bg"],
            fg=style_config["fg"],
            font=("Arial", 10, "bold"),
            bd=0,
            padx=20,
            pady=10,
            cursor="hand2",
            activebackground=style_config["hover"],
            activeforeground=COLORS["text_primary"],
            **kwargs
        )
        
        self.hover_bg = style_config["hover"]
        self.normal_bg = style_config["bg"]
        
        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)
    
    def on_enter(self, e):
        self.config(bg=self.hover_bg)
    
    def on_leave(self, e):
        self.config(bg=self.normal_bg)

class ModernFrame(tk.Frame):
    def __init__(self, parent, **kwargs):
        super().__init__(
            parent,
            bg=kwargs.pop("bg", COLORS["bg_secondary"]),
            highlightbackground=COLORS["border"],
            highlightthickness=kwargs.pop("border", 0),
            **kwargs
        )

class ModernLabel(tk.Label):
    def __init__(self, parent, text="", style="primary", **kwargs):
        font_size = kwargs.pop("size", 10)
        font_weight = kwargs.pop("weight", "normal")
        
        super().__init__(
            parent,
            text=text,
            bg=kwargs.pop("bg", COLORS["bg_secondary"]),
            fg=COLORS["text_primary"] if style == "primary" else COLORS["text_secondary"],
            font=("Arial", font_size, font_weight),
            **kwargs
        )

class ModernEntry(tk.Entry):
    def __init__(self, parent, **kwargs):
        super().__init__(
            parent,
            bg=COLORS["bg_primary"],
            fg=COLORS["text_primary"],
            font=("Arial", 10),
            bd=1,
            relief="solid",
            insertbackground=COLORS["text_primary"],
            highlightthickness=2,
            highlightcolor=COLORS["accent"],
            highlightbackground=COLORS["border"],
            **kwargs
        )

class ModernText(tk.Text):
    def __init__(self, parent, **kwargs):
        super().__init__(
            parent,
            bg=COLORS["bg_primary"],
            fg=COLORS["text_primary"],
            font=("Consolas", 9),
            bd=1,
            relief="solid",
            insertbackground=COLORS["text_primary"],
            highlightthickness=1,
            highlightcolor=COLORS["accent"],
            highlightbackground=COLORS["border"],
            wrap="word",
            **kwargs
        )

##############################################################################
# DETECTIONS & ALERTS
##############################################################################
def beep_and_red_led():
    if MODE_DND or MODE_IDLE:
        log_system_update("Alert skipped (DND/Idle).")
        return
    
    # Check quiet hours
    if is_quiet_hours():
        log_system_update("Alert suppressed due to quiet hours.")
        log_alert("suppressed", "Quiet hours active")
        return

    if MODE_NIGHT:
        try:
            with open(NIGHT_MODE_LOG_FILE, "a") as f:
                f.write(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
        except Exception as e:
            log_system_update("Error logging night mode incident: " + str(e))

    # Adjust duration based on mode
    duration = 5
    if MODE_NIGHT:
        duration = 10
    if MODE_EMERGENCY:
        duration = 15
    
    # Adjust volume based on settings
    volume_factor = system_settings["alert_volume"] / 100.0
    actual_duration = duration * volume_factor

    red_led.on()
    buzzer.on()
    time.sleep(actual_duration)
    buzzer.off()
    red_led.off()
    
    log_alert("audio_visual", f"Duration: {actual_duration}s")

def send_email_alert():
    global last_email_sent_time
    if MODE_EMAIL_OFF or MODE_IDLE:
        log_system_update("Email alert skipped (EmailOff/Idle).")
        return
    
    # Check quiet hours for email
    if is_quiet_hours() and not MODE_EMERGENCY:
        log_system_update("Email suppressed due to quiet hours (non-emergency).")
        return
    
    current_time = time.time()
    if (current_time - last_email_sent_time) < EMAIL_COOLDOWN:
        return
    last_email_sent_time = current_time

    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    subject = "Scissors Detected Alert"
    if MODE_EMERGENCY:
        subject = "EMERGENCY: " + subject
    elif MODE_NIGHT:
        subject = "HIGH PRIORITY: " + subject

    body = f"Detected scissors at {now_str}.\nCheck your environment for safety.\n"
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = EMAIL_SENDER
    msg['To'] = ", ".join(EMAIL_RECIPIENTS)

    try:
        # Test network connectivity first
        socket.create_connection(("gmail.com", 465), timeout=5).close()
        
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10)
        server.login(EMAIL_SENDER, EMAIL_SENDER_PASS)
        server.send_message(msg)
        server.quit()
        log_system_update("Email alert sent.")
        log_alert("email", f"Sent to {len(EMAIL_RECIPIENTS)} recipients")
    except socket.gaierror:
        log_system_update("Email failed: No internet connection (DNS resolution failed)")
    except socket.timeout:
        log_system_update("Email failed: Connection timeout")
    except smtplib.SMTPAuthenticationError:
        log_system_update("Email failed: Invalid credentials")
    except Exception as e:
        log_system_update(f"Email failed: {str(e)}")
        print(f"Email alert error: {str(e)}")  # Debug print

def log_scissors_detection():
    now = datetime.datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{stamp}] SCISSORS DETECTED\n"
    with open(SCISSORS_LOG_FILE, "a") as f:
        f.write(entry)
    log_system_update("Scissors detection logged to file.")
    log_alert("detection", "Scissors detected")

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
    global latest_detection_info
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

    for d in detections:
        label = d.get_label()
        confidence = d.get_confidence()
        
        # Check if confidence meets sensitivity threshold
        if confidence < system_settings["detection_sensitivity"]:
            continue
            
        text_info += f"{label} detected (conf={confidence:.2f})\n"
        update_detection_stats(label)
        
        if label == user_data.danger_label:
            # Danger found
            threading.Thread(target=beep_and_red_led, daemon=True).start()
            threading.Thread(target=log_scissors_detection, daemon=True).start()
            threading.Thread(target=send_email_alert, daemon=True).start()

    user_data.person_detected = any(d.get_label() == "person" for d in detections)

    # Annotate the frame
    if frame is not None:
        cv2.putText(frame, f"Frame: {frame_num}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        cv2.putText(frame, f"{user_data.new_function()} {user_data.new_variable}",
                    (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
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
# BUILT-IN VOICE COMMANDS - For display reference (Admin can see them)
##############################################################################
BUILT_IN_COMMANDS = {
    "activate dnd"           : "Enables Do Not Disturb mode",
    "deactivate dnd"         : "Disables DND mode",
    "activate email off"     : "Turns off email notifications",
    "deactivate email off"   : "Turns on email notifications",
    "activate idle"          : "Disables all alerts",
    "deactivate idle"        : "Re-enables all alerts",
    "activate night mode"    : "High priority alerts last longer",
    "deactivate night mode"  : "Return to normal alert durations",
    "activate emergency mode": "Extra-loud alerts, overrides standard modes",
    "deactivate emergency mode": "Stops emergency mode",
    "system status"          : "Get current active modes status",
    "detection count"        : "Get total detection statistics",
    "last detection"         : "When was the last detection",
    "alert summary"          : "Get 24-hour alert analytics report",
    "quiet hours"            : "Check quiet hours configuration",
    "all modes off"          : "Deactivate all modes at once",
    "quick check"            : "Quick system health check",
    "security high"          : "Activate high security (night mode on, DND off)",
    "security low"           : "Activate low security (DND on)",
    "weather"                : "Reports simple weather snippet",
    "hi / hello"             : "Greets the user",
    "how are you"            : "Narada status update",
    "what's your name"       : "Narada introduction",
    "time"                   : "Tells the current time",
}

# Custom voice commands
CUSTOM_VOICE_COMMANDS = {}

##############################################################################
# VOICE ASSISTANT LOOP
##############################################################################
def voice_assistant_loop(stop_event, current_user=None):
    """Voice recognition loop. Pass the 'current_user' name so we can track their logs."""
    recognizer = sr.Recognizer()
    try:
        mic = sr.Microphone()
        append_voice_log("USB Microphone connected.", user_name=current_user)
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
            append_voice_log(f"Recognition error: {str(e)}", user_name=current_user)
            continue

        user_input_lower = user_input.lower()
        response = None

        global MODE_DND, MODE_EMAIL_OFF, MODE_IDLE, MODE_NIGHT, MODE_EMERGENCY, NARADA_WAKE_WORD

        # 1) Check custom commands
        for phrase, resp in CUSTOM_VOICE_COMMANDS.items():
            if phrase in user_input_lower:
                response = resp
                break

        # 2) Enhanced built-in commands
        if response is None:
            # Mode controls
            if "deactivate dnd" in user_input_lower:
                MODE_DND = False
                response = "DND mode deactivated."
            elif "activate dnd" in user_input_lower:
                MODE_DND = True
                response = "DND mode activated."
            elif "deactivate email off" in user_input_lower:
                MODE_EMAIL_OFF = False
                response = "Email notifications back on."
            elif "activate email off" in user_input_lower:
                MODE_EMAIL_OFF = True
                response = "Email notifications turned off."
            elif "deactivate idle" in user_input_lower:
                MODE_IDLE = False
                response = "Idle mode deactivated."
            elif "activate idle" in user_input_lower:
                MODE_IDLE = True
                response = "Idle mode activated."
            elif "deactivate night mode" in user_input_lower:
                MODE_NIGHT = False
                response = "Night mode disabled."
            elif "activate night mode" in user_input_lower:
                MODE_NIGHT = True
                response = "Night mode enabled."
            elif "activate emergency mode" in user_input_lower:
                MODE_EMERGENCY = True
                response = "Emergency mode activated!"
            elif "deactivate emergency mode" in user_input_lower:
                MODE_EMERGENCY = False
                response = "Emergency mode off."
            
            # System status queries
            elif "system status" in user_input_lower or "what's the status" in user_input_lower:
                active_modes = []
                if MODE_DND: active_modes.append("DND")
                if MODE_EMAIL_OFF: active_modes.append("Email Off")
                if MODE_IDLE: active_modes.append("Idle")
                if MODE_NIGHT: active_modes.append("Night")
                if MODE_EMERGENCY: active_modes.append("Emergency")
                
                if active_modes:
                    response = f"Active modes: {', '.join(active_modes)}"
                else:
                    response = "All systems normal, no special modes active."
            
            elif "detection count" in user_input_lower or "how many detections" in user_input_lower:
                total = detection_stats["total_detections"]
                scissors = detection_stats["scissors_count"]
                response = f"Total detections: {total}. Scissors detected: {scissors} times."
            
            elif "last detection" in user_input_lower:
                last_time = detection_stats["last_detection_time"]
                if last_time:
                    time_obj = datetime.datetime.fromisoformat(last_time)
                    time_str = time_obj.strftime("%I:%M %p")
                    response = f"Last detection was at {time_str}."
                else:
                    response = "No detections recorded yet."
            
            elif "alert summary" in user_input_lower or "alert report" in user_input_lower:
                analytics = get_alert_analytics()
                if analytics:
                    response = f"Last 24 hours: {analytics['total_24h']} alerts. Peak hour: {analytics['peak_hour']}."
                else:
                    response = "No alerts in the last 24 hours."
            
            elif "quiet hours" in user_input_lower:
                if system_settings["quiet_hours"]["enabled"]:
                    start = system_settings["quiet_hours"]["start"]
                    end = system_settings["quiet_hours"]["end"]
                    response = f"Quiet hours are enabled from {start} to {end}."
                else:
                    response = "Quiet hours are currently disabled."
            
            elif "all modes off" in user_input_lower or "reset modes" in user_input_lower:
                MODE_DND = False
                MODE_EMAIL_OFF = False
                MODE_IDLE = False
                MODE_NIGHT = False
                MODE_EMERGENCY = False
                response = "All modes have been deactivated."
            
            elif "quick check" in user_input_lower:
                # Quick system health check
                active_count = sum([MODE_DND, MODE_EMAIL_OFF, MODE_IDLE, MODE_NIGHT, MODE_EMERGENCY])
                quiet = "active" if is_quiet_hours() else "inactive"
                response = f"Quick check: {active_count} modes active, quiet hours {quiet}, system operational."
            
            # Batch commands
            elif "security high" in user_input_lower:
                MODE_NIGHT = True
                MODE_DND = False
                MODE_IDLE = False
                response = "Security set to high. Night mode activated, alerts enabled."
            
            elif "security low" in user_input_lower:
                MODE_DND = True
                MODE_NIGHT = False
                MODE_EMERGENCY = False
                response = "Security set to low. DND mode activated."
            
            # Original commands
            elif "weather" in user_input_lower:
                response = "It's partly cloudy with a high of 25°C."
            elif any(greet in user_input_lower for greet in ["hi", "hello"]):
                response = f"Hello, I am {NARADA_WAKE_WORD.title()}, your assistant."
            elif "how are you" in user_input_lower:
                response = "I'm doing great, thanks for asking!"
            elif "what's your name" in user_input_lower:
                response = f"My name is {NARADA_WAKE_WORD.title()}."
            elif "time" in user_input_lower:
                response = "The current time is " + datetime.datetime.now().strftime("%I:%M %p") + "."
            elif "what" in user_input_lower:
                response = "I heard you say 'what'? I'm here to help!"
            else:
                response = "I'm sorry, I'm still learning new commands."

        # Priority override logic for modes
        if MODE_EMERGENCY:
            MODE_DND = False
        if MODE_NIGHT:
            MODE_DND = False

        append_voice_response(response, user_name=current_user)

        # If there's a dashboard, sync checkbuttons
        if dashboard_gui:
            dashboard_gui.sync_mode_checkbuttons()

        time.sleep(1)

##############################################################################
# USER DASHBOARD (Modern Redesign)
##############################################################################
class UserDashboardGUI:
    def __init__(self, root, user_data, username="user"):
        self.root = root
        self.username = username
        self.root.title("Garuda Security - User Dashboard")
        self.root.geometry("1200x800")
        self.root.configure(bg=COLORS["bg_primary"])
        
        # Apply modern window style
        try:
            self.root.tk.call('tk', 'scaling', 1.2)
        except:
            pass

        self.user_data = user_data
        self.after_id = None
        
        # Initialize voice assistant variables FIRST
        self.voice_thread = None
        self.voice_stop_event = None
        self.voice_assistant_running = False

        # Track user login time
        if username in USERS:
            USERS[username]["history"]["logins"].append(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            save_persistent_data()

        # Header
        self.create_header()
        
        # Main content area with sidebar
        main_container = ModernFrame(self.root)
        main_container.pack(fill=tk.BOTH, expand=True)
        
        # Sidebar
        self.create_sidebar(main_container)
        
        # Content area
        self.content_frame = ModernFrame(main_container, bg=COLORS["bg_primary"])
        self.content_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Create pages
        self.pages = {}
        self.create_detection_page()
        self.create_narada_page()
        self.create_activity_page()
        self.create_preferences_page()
        
        # Show default page
        self.show_page("detection")

        # Start update
        self.update_gui()

    def create_header(self):
        header = ModernFrame(self.root, bg=COLORS["bg_secondary"], height=60)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        
        # Title
        title = ModernLabel(header, text="GARUDA SECURITY SYSTEM", size=18, weight="bold")
        title.pack(side=tk.LEFT, padx=20, pady=15)
        
        # Quick status
        self.header_status = ModernLabel(header, text="", size=10, style="secondary")
        self.header_status.pack(side=tk.LEFT, padx=20, pady=20)
        
        # User info
        user_info = ModernLabel(header, text=f"User: {self.username}", size=12)
        user_info.pack(side=tk.RIGHT, padx=20, pady=20)
        
        # Logout button
        logout_btn = ModernButton(header, text="Logout", command=self.logout, style="danger")
        logout_btn.pack(side=tk.RIGHT, padx=10, pady=10)

    def create_sidebar(self, parent):
        sidebar = ModernFrame(parent, bg=COLORS["bg_secondary"], width=200)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)
        
        # Navigation buttons
        nav_buttons = [
            ("AI Detection", "detection", "primary"),
            ("Narada Assistant", "narada", "info"),
            ("My Activity", "activity", "success"),
            ("Preferences", "preferences", "secondary"),
            ("Instructions", "instructions", "secondary")
        ]
        
        for text, page, style in nav_buttons:
            if page == "instructions":
                btn = ModernButton(sidebar, text=text, command=self.show_instructions, style=style)
            else:
                btn = ModernButton(sidebar, text=text, command=lambda p=page: self.show_page(p), style=style)
            btn.pack(fill=tk.X, padx=10, pady=5)

    def create_detection_page(self):
        page = ModernFrame(self.content_frame)
        self.pages["detection"] = page
        
        # Title
        title = ModernLabel(page, text="AI Detection Monitor", size=16, weight="bold")
        title.pack(pady=10)
        
        # Quick actions panel
        quick_panel = ModernFrame(page, bg=COLORS["bg_secondary"], border=1)
        quick_panel.pack(fill=tk.X, padx=10, pady=5)
        
        quick_title = ModernLabel(quick_panel, text="Quick Actions", size=12, weight="bold")
        quick_title.pack(pady=5)
        
        quick_container = ModernFrame(quick_panel, bg=COLORS["bg_secondary"])
        quick_container.pack(pady=10)
        
        # Quick action buttons
        quick_actions = [
            ("All Alerts On", lambda: self.quick_action("alerts_on"), "success"),
            ("Silent Mode", lambda: self.quick_action("silent"), "warning"),
            ("High Security", lambda: self.quick_action("high_security"), "danger"),
            ("Normal Mode", lambda: self.quick_action("normal"), "info")
        ]
        
        for i, (text, cmd, style) in enumerate(quick_actions):
            btn = ModernButton(quick_container, text=text, command=cmd, style=style)
            btn.grid(row=0, column=i, padx=5, pady=5)
        
        # Modes control panel
        modes_panel = ModernFrame(page, bg=COLORS["bg_secondary"], border=1)
        modes_panel.pack(fill=tk.X, padx=10, pady=10)
        
        modes_title = ModernLabel(modes_panel, text="System Modes", size=12, weight="bold")
        modes_title.pack(pady=5)
        
        modes_container = ModernFrame(modes_panel, bg=COLORS["bg_secondary"])
        modes_container.pack(pady=10)
        
        # Mode checkbuttons with modern styling
        self.var_dnd = tk.BooleanVar(value=MODE_DND)
        self.var_email_off = tk.BooleanVar(value=MODE_EMAIL_OFF)
        self.var_idle = tk.BooleanVar(value=MODE_IDLE)
        self.var_night = tk.BooleanVar(value=MODE_NIGHT)
        self.var_emergency = tk.BooleanVar(value=MODE_EMERGENCY)
        
        modes = [
            ("Do Not Disturb", self.var_dnd, self.toggle_dnd),
            ("Email Off", self.var_email_off, self.toggle_email),
            ("Idle Mode", self.var_idle, self.toggle_idle),
            ("Night Mode", self.var_night, self.toggle_night),
            ("Emergency", self.var_emergency, self.toggle_emergency)
        ]
        
        for i, (text, var, cmd) in enumerate(modes):
            cb = tk.Checkbutton(
                modes_container, text=text, variable=var, command=cmd,
                bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
                activebackground=COLORS["bg_secondary"], activeforeground=COLORS["text_primary"],
                selectcolor=COLORS["bg_primary"], font=("Arial", 10)
            )
            cb.grid(row=0, column=i, padx=10, pady=5)
        
        # Detection info panels
        info_container = ModernFrame(page)
        info_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Detection panel
        det_panel = ModernFrame(info_container, bg=COLORS["bg_secondary"], border=1)
        det_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        
        det_label = ModernLabel(det_panel, text="Detection Info", size=12, weight="bold")
        det_label.pack(pady=5)
        
        self.detection_text = ModernText(det_panel, height=15)
        self.detection_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Console panel
        console_panel = ModernFrame(info_container, bg=COLORS["bg_secondary"], border=1)
        console_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0))
        
        console_label = ModernLabel(console_panel, text="System Console", size=12, weight="bold")
        console_label.pack(pady=5)
        
        self.console_text = ModernText(console_panel, height=15)
        self.console_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    def create_activity_page(self):
        page = ModernFrame(self.content_frame)
        self.pages["activity"] = page
        
        # Title
        title = ModernLabel(page, text="My Activity Dashboard", size=16, weight="bold")
        title.pack(pady=10)
        
        # Stats cards
        stats_frame = ModernFrame(page)
        stats_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.activity_cards = {}
        card_data = [
            ("logins", "Total Logins", "0"),
            ("narada_sessions", "Narada Sessions", "0"),
            ("alerts_today", "Alerts Today", "0"),
            ("last_active", "Last Active", "N/A")
        ]
        
        for i, (key, label, default) in enumerate(card_data):
            card = ModernFrame(stats_frame, bg=COLORS["bg_tertiary"], border=1)
            card.grid(row=0, column=i, padx=5, pady=5, sticky="ew")
            stats_frame.columnconfigure(i, weight=1)
            
            card_label = ModernLabel(card, text=label, size=10, style="secondary")
            card_label.pack(pady=(10, 5))
            
            card_value = ModernLabel(card, text=default, size=14, weight="bold")
            card_value.pack(pady=(0, 10))
            
            self.activity_cards[key] = card_value
        
        # Activity history
        history_panel = ModernFrame(page, bg=COLORS["bg_secondary"], border=1)
        history_panel.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        history_label = ModernLabel(history_panel, text="Recent Activity", size=12, weight="bold")
        history_label.pack(pady=10)
        
        self.activity_text = ModernText(history_panel, height=15)
        self.activity_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

    def create_preferences_page(self):
        page = ModernFrame(self.content_frame)
        self.pages["preferences"] = page
        
        # Title
        title = ModernLabel(page, text="User Preferences", size=16, weight="bold")
        title.pack(pady=10)
        
        # Preferences form
        prefs_panel = ModernFrame(page, bg=COLORS["bg_secondary"], border=1)
        prefs_panel.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        form_frame = ModernFrame(prefs_panel, bg=COLORS["bg_secondary"])
        form_frame.pack(pady=20)
        
        # Alert preferences
        alert_frame = ModernFrame(form_frame, bg=COLORS["bg_secondary"])
        alert_frame.pack(pady=10)
        
        ModernLabel(alert_frame, text="Alert Preferences", size=12, weight="bold").pack(pady=5)
        
        self.pref_sound = tk.BooleanVar(value=True)
        tk.Checkbutton(
            alert_frame, text="Sound Alerts", variable=self.pref_sound,
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            activebackground=COLORS["bg_secondary"], selectcolor=COLORS["bg_primary"],
            font=("Arial", 10)
        ).pack(anchor="w", padx=20, pady=5)
        
        self.pref_visual = tk.BooleanVar(value=True)
        tk.Checkbutton(
            alert_frame, text="Visual Alerts (LED)", variable=self.pref_visual,
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            activebackground=COLORS["bg_secondary"], selectcolor=COLORS["bg_primary"],
            font=("Arial", 10)
        ).pack(anchor="w", padx=20, pady=5)
        
        # Notification settings
        notif_frame = ModernFrame(form_frame, bg=COLORS["bg_secondary"])
        notif_frame.pack(pady=10)
        
        ModernLabel(notif_frame, text="Notification Settings", size=12, weight="bold").pack(pady=5)
        
        self.pref_email_alerts = tk.BooleanVar(value=True)
        tk.Checkbutton(
            notif_frame, text="Receive Email Alerts", variable=self.pref_email_alerts,
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            activebackground=COLORS["bg_secondary"], selectcolor=COLORS["bg_primary"],
            font=("Arial", 10)
        ).pack(anchor="w", padx=20, pady=5)
        
        # Display settings
        display_frame = ModernFrame(form_frame, bg=COLORS["bg_secondary"])
        display_frame.pack(pady=10)
        
        ModernLabel(display_frame, text="Display Settings", size=12, weight="bold").pack(pady=5)
        
        self.pref_auto_scroll = tk.BooleanVar(value=True)
        tk.Checkbutton(
            display_frame, text="Auto-scroll logs", variable=self.pref_auto_scroll,
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            activebackground=COLORS["bg_secondary"], selectcolor=COLORS["bg_primary"],
            font=("Arial", 10)
        ).pack(anchor="w", padx=20, pady=5)
        
        # Save button
        save_btn = ModernButton(form_frame, text="Save Preferences", command=self.save_preferences, style="success")
        save_btn.pack(pady=20)
        
        # Info text
        info_text = ModernLabel(
            prefs_panel, 
            text="Note: These preferences are saved for your user account only",
            size=9, style="secondary"
        )
        info_text.pack(pady=10)

    def create_narada_page(self):
        page = ModernFrame(self.content_frame)
        self.pages["narada"] = page
        
        # Title
        title = ModernLabel(page, text="Narada Voice Assistant", size=16, weight="bold")
        title.pack(pady=10)
        
        # Control panel
        control_panel = ModernFrame(page, bg=COLORS["bg_secondary"], border=1)
        control_panel.pack(fill=tk.X, padx=10, pady=10)
        
        control_frame = ModernFrame(control_panel, bg=COLORS["bg_secondary"])
        control_frame.pack(pady=10)
        
        self.btn_voice = ModernButton(control_frame, text="Start Narada", command=self.toggle_voice, style="success")
        self.btn_voice.pack(side=tk.LEFT, padx=10)
        
        self.lbl_status = ModernLabel(control_frame, text="Status: Not Listening", size=12)
        self.lbl_status.pack(side=tk.LEFT, padx=20)
        
        self.listening_indicator = ModernLabel(control_frame, text="●", size=20)
        self.listening_indicator.config(fg=COLORS["error"])
        self.listening_indicator.pack(side=tk.LEFT, padx=10)
        
        self.btn_clear_log = ModernButton(control_frame, text="Clear Log", command=self.clear_narada_log, style="warning")
        self.btn_clear_log.pack(side=tk.LEFT, padx=10)
        
        # Command suggestions
        suggest_panel = ModernFrame(page, bg=COLORS["bg_tertiary"], border=1)
        suggest_panel.pack(fill=tk.X, padx=10, pady=5)
        
        suggest_label = ModernLabel(suggest_panel, text="Try saying:", size=10, style="secondary")
        suggest_label.pack(side=tk.LEFT, padx=10, pady=5)
        
        suggestions = ["system status", "detection count", "alert summary", "quiet hours", "all modes off"]
        for suggestion in suggestions:
            sug_btn = tk.Button(
                suggest_panel, text=f'"{suggestion}"', 
                bg=COLORS["bg_tertiary"], fg=COLORS["text_secondary"],
                font=("Arial", 9), bd=0, cursor="hand2",
                activebackground=COLORS["bg_tertiary"]
            )
            sug_btn.pack(side=tk.LEFT, padx=5, pady=5)
        
        # Logs container
        logs_container = ModernFrame(page)
        logs_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Voice log panel
        voice_panel = ModernFrame(logs_container, bg=COLORS["bg_secondary"], border=1)
        voice_panel.pack(fill=tk.BOTH, expand=True, pady=(0, 5))
        
        voice_label = ModernLabel(voice_panel, text="Voice Log", size=12, weight="bold")
        voice_label.pack(pady=5)
        
        self.voice_text = ModernText(voice_panel, height=10, bg="#1f1f3a")
        self.voice_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Response panel
        response_panel = ModernFrame(logs_container, bg=COLORS["bg_secondary"], border=1)
        response_panel.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
        
        response_label = ModernLabel(response_panel, text="Narada Responses", size=12, weight="bold")
        response_label.pack(pady=5)
        
        self.response_text = ModernText(response_panel, height=5)
        self.response_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    def quick_action(self, action):
        global MODE_DND, MODE_EMAIL_OFF, MODE_IDLE, MODE_NIGHT, MODE_EMERGENCY
        
        if action == "alerts_on":
            MODE_DND = False
            MODE_EMAIL_OFF = False
            MODE_IDLE = False
            log_system_update("User activated all alerts via quick action")
        elif action == "silent":
            MODE_DND = True
            MODE_EMAIL_OFF = True
            log_system_update("User activated silent mode via quick action")
        elif action == "high_security":
            MODE_NIGHT = True
            MODE_DND = False
            MODE_IDLE = False
            log_system_update("User activated high security via quick action")
        elif action == "normal":
            MODE_DND = False
            MODE_EMAIL_OFF = False
            MODE_IDLE = False
            MODE_NIGHT = False
            MODE_EMERGENCY = False
            log_system_update("User reset to normal mode via quick action")
        
        self.sync_mode_checkbuttons()

    def save_preferences(self):
        # Save user preferences (extend as needed)
        log_system_update(f"User {self.username} saved preferences")
        messagebox.showinfo("Success", "Preferences saved successfully!")

    def show_page(self, page_name):
        for page in self.pages.values():
            page.pack_forget()
        if page_name in self.pages:
            self.pages[page_name].pack(fill=tk.BOTH, expand=True)

    def update_gui(self):
        # Update header status
        active_modes = []
        if MODE_DND: active_modes.append("DND")
        if MODE_EMAIL_OFF: active_modes.append("Email Off")
        if MODE_IDLE: active_modes.append("Idle")
        if MODE_NIGHT: active_modes.append("Night")
        if MODE_EMERGENCY: active_modes.append("Emergency")
        
        if active_modes:
            self.header_status.config(text=f"Active: {', '.join(active_modes)}")
        else:
            self.header_status.config(text="All systems normal")
        
        # Update detection page
        if self.pages["detection"].winfo_ismapped():
            # Update detection text with current info
            if latest_detection_info:
                update_text_widget(self.detection_text, latest_detection_info)
            else:
                update_text_widget(self.detection_text, "No detections yet.\nWaiting for object detection...")
            
            # Update console with recent logs
            if system_updates_log:
                update_text_widget(self.console_text, "\n".join(system_updates_log[-20:]))
            else:
                update_text_widget(self.console_text, "System console ready.\nAll activities will be logged here.")

        # Update Narada page
        if self.pages["narada"].winfo_ismapped():
            update_text_widget(self.voice_text, "\n".join(voice_assistant_log) if voice_assistant_log else "Voice assistant log empty.\nStart Narada to begin.")
            if not self.voice_assistant_running:
                update_text_widget(self.response_text, "Narada is not listening.\nClick 'Start Narada' to activate voice control.")
            else:
                update_text_widget(self.response_text, "\n".join(voice_responses) if voice_responses else "Waiting for voice commands...")
        
        # Update activity page
        if self.pages["activity"].winfo_ismapped():
            def compute_activity():
                summary = get_user_activity_summary(self.username)
                activity_text = []
                
                if self.username in USERS:
                    history = USERS[self.username]["history"]
                    
                    # Recent logins
                    if history["logins"]:
                        activity_text.append("Recent Logins:")
                        for login in history["logins"][-10:]:
                            activity_text.append(f"  - {login}")
                    else:
                        activity_text.append("No login history yet.")
                    
                    # Recent Narada activity
                    if history["narada_activity"]:
                        activity_text.append("\nRecent Voice Commands:")
                        recent_commands = [a for a in history["narada_activity"] if "You said:" in a][-10:]
                        for cmd in recent_commands:
                            activity_text.append(f"  {cmd}")
                    else:
                        activity_text.append("\nNo voice command history yet.")
                
                return summary, "\n".join(activity_text) if activity_text else "No activity recorded yet."
            
            summary, activity_text = get_cached_data(f"user_activity_{self.username}", compute_activity)
            
            # Update cards
            self.activity_cards["logins"].config(text=str(summary.get("total_logins", 0)))
            self.activity_cards["narada_sessions"].config(text=str(summary.get("narada_sessions", 0)))
            
            # Update alerts today
            analytics = get_alert_analytics()
            self.activity_cards["alerts_today"].config(text=str(analytics.get("total_24h", 0)))
            
            # Last active
            self.activity_cards["last_active"].config(text=summary.get("last_login", "N/A"))
            
            update_text_widget(self.activity_text, activity_text)
        
        # Update preferences page
        if self.pages["preferences"].winfo_ismapped():
            # Could update preference values here if needed
            pass

        self.sync_mode_checkbuttons()
        self.after_id = self.root.after(1000, self.update_gui)

    def sync_mode_checkbuttons(self):
        self.var_dnd.set(MODE_DND)
        self.var_email_off.set(MODE_EMAIL_OFF)
        self.var_idle.set(MODE_IDLE)
        self.var_night.set(MODE_NIGHT)
        self.var_emergency.set(MODE_EMERGENCY)

    def toggle_dnd(self):
        global MODE_DND
        MODE_DND = self.var_dnd.get()
        log_system_update(f"User toggled DND => {MODE_DND}")

    def toggle_email(self):
        global MODE_EMAIL_OFF
        MODE_EMAIL_OFF = self.var_email_off.get()
        log_system_update(f"User toggled EmailOff => {MODE_EMAIL_OFF}")

    def toggle_idle(self):
        global MODE_IDLE
        MODE_IDLE = self.var_idle.get()
        log_system_update(f"User toggled Idle => {MODE_IDLE}")

    def toggle_night(self):
        global MODE_NIGHT
        MODE_NIGHT = self.var_night.get()
        log_system_update(f"User toggled Night => {MODE_NIGHT}")

    def toggle_emergency(self):
        global MODE_EMERGENCY
        MODE_EMERGENCY = self.var_emergency.get()
        log_system_update(f"User toggled Emergency => {MODE_EMERGENCY}")

    def toggle_voice(self):
        if not self.voice_assistant_running:
            self.voice_stop_event = threading.Event()
            self.voice_thread = threading.Thread(target=voice_assistant_loop,
                                                 args=(self.voice_stop_event, self.username),
                                                 daemon=True)
            self.voice_thread.start()
            self.voice_assistant_running = True
            self.btn_voice.config(text="Stop Narada")
            self.lbl_status.config(text="Status: Listening")
            self.listening_indicator.config(fg=COLORS["success"])
            append_voice_log("Narada started by user.", user_name=self.username)
        else:
            if self.voice_stop_event:
                self.voice_stop_event.set()
            self.voice_assistant_running = False
            self.btn_voice.config(text="Start Narada")
            self.lbl_status.config(text="Status: Not Listening")
            self.listening_indicator.config(fg=COLORS["error"])
            append_voice_log("Narada stopped by user.", user_name=self.username)

    def clear_narada_log(self):
        global voice_assistant_log, voice_responses
        voice_assistant_log = []
        voice_responses = []
        save_persistent_data()
        update_text_widget(self.voice_text, "")
        update_text_widget(self.response_text, "")
        append_voice_log("Narada log cleared.", user_name=self.username)

    def logout(self):
        save_persistent_data()
        if self.after_id:
            self.root.after_cancel(self.after_id)
        self.root.destroy()
        root_main = tk.Tk()
        LoginHomeScreen(root_main)
        root_main.mainloop()

    def show_instructions(self):
        instructions = (
            "Enhanced Narada Voice Commands:\n\n"
            "SYSTEM MODES:\n"
            "- 'activate/deactivate [mode]' - Control individual modes\n"
            "- 'all modes off' - Deactivate all modes\n"
            "- 'security high/low' - Quick security presets\n\n"
            "STATUS QUERIES:\n"
            "- 'system status' - Get active modes\n"
            "- 'detection count' - Total detections\n"
            "- 'last detection' - When was last detection\n"
            "- 'alert summary' - 24-hour alert report\n"
            "- 'quiet hours' - Check quiet hours status\n"
            "- 'quick check' - System health check\n\n"
            "QUICK ACTIONS:\n"
            "Use the Quick Actions panel for instant mode changes.\n"
            "Check 'My Activity' for your personal usage stats."
        )
        messagebox.showinfo("User Instructions", instructions)

##############################################################################
# ADMIN DASHBOARD (Modern Redesign)
##############################################################################
class AdminDashboardGUI:
    def __init__(self, root, user_data, username="admin"):
        self.root = root
        self.username = username
        self.root.title("Garuda Security - Admin Control Center")
        self.root.geometry("1400x850")
        self.root.configure(bg=COLORS["bg_primary"])
        
        # Apply modern window style
        try:
            self.root.tk.call('tk', 'scaling', 1.2)
        except:
            pass

        self.user_data = user_data
        self.after_id = None
        
        # Initialize all variables needed for pages
        self.stat_cards = {}
        self.resource_monitors = {}

        # Admin logs a login time
        if username in USERS:
            USERS[username]["history"]["logins"].append(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            save_persistent_data()

        # Header
        self.create_header()
        
        # Main container
        main_container = ModernFrame(self.root)
        main_container.pack(fill=tk.BOTH, expand=True)
        
        # Sidebar
        self.create_sidebar(main_container)
        
        # Content area
        self.content_frame = ModernFrame(main_container, bg=COLORS["bg_primary"])
        self.content_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Create all pages
        self.pages = {}
        self.build_dashboard_page()
        self.build_user_mgmt_page()
        self.build_logs_page()
        self.build_hardware_page()
        self.build_narada_page()
        self.build_modes_page()
        self.build_email_page()
        
        # Initialize log filter after logs page is built
        if hasattr(self, 'log_filter'):
            self.log_filter.set("all")
        
        # Show default page
        self.show_page("dashboard")
        
        # Start periodic update
        self.update_gui()

    def create_header(self):
        header = ModernFrame(self.root, bg=COLORS["bg_secondary"], height=70)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        
        # Title
        title = ModernLabel(header, text="GARUDA ADMIN CONTROL CENTER", size=20, weight="bold")
        title.pack(side=tk.LEFT, padx=20, pady=20)
        
        # System menu
        menu_frame = ModernFrame(header, bg=COLORS["bg_secondary"])
        menu_frame.pack(side=tk.LEFT, padx=20, pady=20)
        
        system_menu = tk.Menubutton(
            menu_frame, text="System ▼", 
            bg=COLORS["bg_tertiary"], fg=COLORS["text_primary"],
            font=("Arial", 10), bd=0, cursor="hand2",
            activebackground=COLORS["hover"], activeforeground=COLORS["text_primary"]
        )
        system_menu.pack()
        
        menu = tk.Menu(system_menu, tearoff=0,
                       bg=COLORS["bg_primary"], fg=COLORS["text_primary"],
                       activebackground=COLORS["accent"], activeforeground=COLORS["text_primary"])
        system_menu.config(menu=menu)
        
        menu.add_command(label="Create Backup", command=self.create_system_backup)
        menu.add_command(label="Restore Backup", command=self.restore_system_backup)
        menu.add_separator()
        menu.add_command(label="System Info", command=self.show_system_info)
        menu.add_command(label="About", command=self.show_about)
        
        # Admin info
        admin_info = ModernLabel(header, text=f"Admin: {self.username}", size=12)
        admin_info.pack(side=tk.RIGHT, padx=20, pady=25)
        
        # Logout button
        logout_btn = ModernButton(header, text="Logout", command=self.logout, style="danger")
        logout_btn.pack(side=tk.RIGHT, padx=10, pady=15)

    def create_system_backup(self):
        if create_backup():
            messagebox.showinfo("Success", "System backup created successfully!")
            log_system_update("Admin created system backup")
        else:
            messagebox.showerror("Failed", "Could not create backup")

    def restore_system_backup(self):
        # List available backups
        try:
            backups = [d for d in os.listdir(BACKUP_DIR) if d.startswith("backup_")]
            if not backups:
                messagebox.showinfo("No Backups", "No backups found")
                return
            
            # Create selection dialog
            dialog = tk.Toplevel(self.root)
            dialog.title("Select Backup")
            dialog.geometry("400x300")
            dialog.configure(bg=COLORS["bg_primary"])
            
            ModernLabel(dialog, text="Select backup to restore:", size=12, weight="bold").pack(pady=10)
            
            listbox = tk.Listbox(dialog, bg=COLORS["bg_primary"], fg=COLORS["text_primary"],
                                selectbackground=COLORS["accent"], font=("Arial", 10))
            listbox.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
            
            for backup in sorted(backups, reverse=True):
                listbox.insert(tk.END, backup)
            
            def restore_selected():
                sel = listbox.curselection()
                if sel:
                    backup_name = listbox.get(sel[0])
                    if messagebox.askyesno("Confirm", f"Restore from {backup_name}? Current data will be overwritten."):
                        if restore_backup(backup_name):
                            messagebox.showinfo("Success", "Backup restored successfully!")
                            log_system_update(f"Admin restored backup {backup_name}")
                            dialog.destroy()
                            # Restart recommended
                            if messagebox.askyesno("Restart", "Restart application to apply changes?"):
                                self.logout()
                        else:
                            messagebox.showerror("Failed", "Could not restore backup")
            
            ModernButton(dialog, text="Restore", command=restore_selected, style="warning").pack(pady=10)
            
        except Exception as e:
            messagebox.showerror("Error", f"Could not list backups: {str(e)}")

    def show_system_info(self):
        info = []
        info.append("GARUDA SECURITY SYSTEM")
        info.append("="*30)
        info.append(f"Version: 4.1.1")
        info.append(f"Python: {sys.version.split()[0]}")
        info.append(f"Platform: {sys.platform}")
        
        if psutil:
            info.append(f"\nSystem Resources:")
            info.append(f"CPU Cores: {psutil.cpu_count()}")
            info.append(f"Total Memory: {psutil.virtual_memory().total // (1024**3)} GB")
            info.append(f"Disk Space: {psutil.disk_usage('/').total // (1024**3)} GB")
        
        info.append(f"\nData Storage:")
        info.append(f"Log Retention: {LOG_RETENTION_DAYS} days")
        info.append(f"Auto Backup: {system_settings['auto_backup']}")
        info.append(f"Last Backup: {system_settings.get('last_backup', 'Never')}")
        
        messagebox.showinfo("System Information", "\n".join(info))

    def show_about(self):
        about_text = (
            "GARUDA SECURITY SYSTEM v4.1.1\n\n"
            "An advanced AI-powered security monitoring system\n"
            "with integrated voice assistant.\n\n"
            "Features:\n"
            "• Real-time object detection using YOLO\n"
            "• Multi-mode alert system\n"
            "• Voice-controlled operations (Narada)\n"
            "• Comprehensive logging and analytics\n"
            "• User management and access control\n"
            "• Automated backup and recovery\n\n"
            "Powered by Hailo AI accelerator"
        )
        messagebox.showinfo("About Garuda", about_text)

    def create_sidebar(self, parent):
        sidebar = ModernFrame(parent, bg=COLORS["bg_secondary"], width=220)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)
        
        # Navigation menu
        nav_items = [
            ("Dashboard", "dashboard", "primary"),
            ("User Management", "user_mgmt", "info"),
            ("Logs & Monitoring", "logs", "warning"),
            ("Hardware Status", "hardware", "success"),
            ("Narada Commands", "narada", "info"),
            ("Mode Management", "modes", "secondary"),
            ("Email Settings", "email", "secondary"),
            ("Instructions", "instructions", "secondary")
        ]
        
        for text, page, style in nav_items:
            if page == "instructions":
                btn = ModernButton(sidebar, text=text, command=self.show_instructions, style=style)
            else:
                btn = ModernButton(sidebar, text=text, command=lambda p=page: self.show_page(p), style=style)
            btn.pack(fill=tk.X, padx=10, pady=5)

    def show_page(self, page_name):
        for page in self.pages.values():
            page.pack_forget()
        if page_name in self.pages:
            self.pages[page_name].pack(fill=tk.BOTH, expand=True)

    def build_dashboard_page(self):
        page = ModernFrame(self.content_frame)
        self.pages["dashboard"] = page
        
        # Title
        title = ModernLabel(page, text="System Dashboard", size=18, weight="bold")
        title.pack(pady=10)
        
        # Stats cards
        stats_frame = ModernFrame(page)
        stats_frame.pack(fill=tk.X, padx=10, pady=10)
        
        # Create stat cards with real-time data
        self.stat_cards = {}
        stats = [
            ("users", "Total Users", str(len(USERS))),
            ("active_modes", "Active Modes", "0"),
            ("detections_today", "Detections Today", "0"),
            ("system_health", "System Health", "Good")
        ]
        
        for i, (key, label, value) in enumerate(stats):
            card = ModernFrame(stats_frame, bg=COLORS["bg_tertiary"], border=1)
            card.grid(row=0, column=i, padx=5, pady=5, sticky="ew")
            stats_frame.columnconfigure(i, weight=1)
            
            card_label = ModernLabel(card, text=label, size=10, style="secondary")
            card_label.pack(pady=(10, 5))
            
            card_value = ModernLabel(card, text=value, size=16, weight="bold")
            card_value.pack(pady=(0, 10))
            
            self.stat_cards[key] = card_value
        
        # Alert analytics panel
        analytics_panel = ModernFrame(page, bg=COLORS["bg_secondary"], border=1)
        analytics_panel.pack(fill=tk.X, padx=10, pady=5)
        
        analytics_label = ModernLabel(analytics_panel, text="24-Hour Alert Analytics", size=12, weight="bold")
        analytics_label.pack(pady=5)
        
        self.analytics_text = ModernLabel(analytics_panel, text="Loading analytics...", size=10, style="secondary")
        self.analytics_text.pack(pady=5)
        
        # Detection and console panels
        panels_frame = ModernFrame(page)
        panels_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Detection panel
        det_panel = ModernFrame(panels_frame, bg=COLORS["bg_secondary"], border=1)
        det_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        
        det_label = ModernLabel(det_panel, text="AI Detection Monitor", size=12, weight="bold")
        det_label.pack(pady=5)
        
        self.det_text = ModernText(det_panel, height=12)
        self.det_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Console panel
        console_panel = ModernFrame(panels_frame, bg=COLORS["bg_secondary"], border=1)
        console_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0))
        
        console_label = ModernLabel(console_panel, text="System Console", size=12, weight="bold")
        console_label.pack(pady=5)
        
        self.console_text = ModernText(console_panel, height=12)
        self.console_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Mode controls
        mode_panel = ModernFrame(panels_frame, bg=COLORS["bg_secondary"], border=1, width=200)
        mode_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(10, 0))
        mode_panel.pack_propagate(False)
        
        mode_label = ModernLabel(mode_panel, text="Quick Controls", size=12, weight="bold")
        mode_label.pack(pady=10)
        
        self.var_dnd = tk.BooleanVar(value=MODE_DND)
        self.var_email_off = tk.BooleanVar(value=MODE_EMAIL_OFF)
        self.var_idle = tk.BooleanVar(value=MODE_IDLE)
        self.var_night = tk.BooleanVar(value=MODE_NIGHT)
        self.var_emergency = tk.BooleanVar(value=MODE_EMERGENCY)
        
        modes = [
            ("DND", self.var_dnd, self.toggle_dnd),
            ("Email Off", self.var_email_off, self.toggle_email),
            ("Idle", self.var_idle, self.toggle_idle),
            ("Night", self.var_night, self.toggle_night),
            ("Emergency", self.var_emergency, self.toggle_emergency)
        ]
        
        for text, var, cmd in modes:
            cb = tk.Checkbutton(
                mode_panel, text=text, variable=var, command=cmd,
                bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
                activebackground=COLORS["bg_secondary"], activeforeground=COLORS["text_primary"],
                selectcolor=COLORS["bg_primary"], font=("Arial", 10)
            )
            cb.pack(anchor="w", padx=20, pady=5)

    def build_user_mgmt_page(self):
        page = ModernFrame(self.content_frame)
        self.pages["user_mgmt"] = page
        
        title = ModernLabel(page, text="User Management", size=18, weight="bold")
        title.pack(pady=10)
        
        main_frame = ModernFrame(page)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # User list panel
        list_panel = ModernFrame(main_frame, bg=COLORS["bg_secondary"], border=1, width=250)
        list_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        list_panel.pack_propagate(False)
        
        list_label = ModernLabel(list_panel, text="Users", size=12, weight="bold")
        list_label.pack(pady=10)
        
        list_frame = ModernFrame(list_panel, bg=COLORS["bg_secondary"])
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        
        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.user_listbox = tk.Listbox(
            list_frame, yscrollcommand=scrollbar.set,
            bg=COLORS["bg_primary"], fg=COLORS["text_primary"],
            selectbackground=COLORS["accent"], selectforeground=COLORS["text_primary"],
            font=("Arial", 10), bd=0, highlightthickness=0
        )
        self.user_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.user_listbox.yview)
        
        self.user_listbox.bind("<<ListboxSelect>>", self.on_select_user)
        self.refresh_user_list()
        
        # User operations panel
        ops_panel = ModernFrame(main_frame, bg=COLORS["bg_secondary"], border=1, width=300)
        ops_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        ops_panel.pack_propagate(False)
        
        ops_label = ModernLabel(ops_panel, text="User Operations", size=12, weight="bold")
        ops_label.pack(pady=10)
        
        form_frame = ModernFrame(ops_panel, bg=COLORS["bg_secondary"])
        form_frame.pack(pady=20)
        
        ModernLabel(form_frame, text="Username:").grid(row=0, column=0, padx=10, pady=5, sticky="e")
        self.entry_uname = ModernEntry(form_frame, width=20)
        self.entry_uname.grid(row=0, column=1, padx=10, pady=5)
        
        ModernLabel(form_frame, text="Password:").grid(row=1, column=0, padx=10, pady=5, sticky="e")
        self.entry_pass = ModernEntry(form_frame, show="*", width=20)
        self.entry_pass.grid(row=1, column=1, padx=10, pady=5)
        
        btn_frame = ModernFrame(ops_panel, bg=COLORS["bg_secondary"])
        btn_frame.pack(pady=10)
        
        ModernButton(btn_frame, text="Add User", command=self.add_user, style="success").pack(pady=5)
        ModernButton(btn_frame, text="Delete Selected", command=self.delete_user, style="danger").pack(pady=5)
        ModernButton(btn_frame, text="Reset Password", command=self.reset_password, style="warning").pack(pady=5)
        ModernButton(btn_frame, text="Rename User", command=self.rename_user, style="info").pack(pady=5)
        
        # User profile panel
        self.profile_frame = ModernFrame(main_frame, bg=COLORS["bg_secondary"], border=1)
        self.profile_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        profile_label = ModernLabel(self.profile_frame, text="User Profile", size=12, weight="bold")
        profile_label.pack(pady=10)
        
        ModernLabel(self.profile_frame, text="Select a user to view details", style="secondary").pack(pady=20)

    def build_logs_page(self):
        page = ModernFrame(self.content_frame)
        self.pages["logs"] = page
        
        title = ModernLabel(page, text="System Logs & Monitoring", size=18, weight="bold")
        title.pack(pady=10)
        
        # Enhanced controls bar
        controls_bar = ModernFrame(page, bg=COLORS["bg_secondary"], border=1)
        controls_bar.pack(fill=tk.X, padx=10, pady=5)
        
        controls_frame = ModernFrame(controls_bar, bg=COLORS["bg_secondary"])
        controls_frame.pack(pady=5)
        
        # Log filters
        ModernLabel(controls_frame, text="Filter:", size=10).pack(side=tk.LEFT, padx=5)
        
        self.log_filter = tk.StringVar(value="all")
        filters = [("All", "all"), ("System", "system"), ("Alerts", "alert"), ("User", "user")]
        for text, value in filters:
            rb = tk.Radiobutton(
                controls_frame, text=text, variable=self.log_filter, value=value,
                bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
                activebackground=COLORS["bg_secondary"], selectcolor=COLORS["bg_primary"],
                font=("Arial", 9), command=self.apply_log_filter
            )
            rb.pack(side=tk.LEFT, padx=5)
        
        # Export and clear buttons
        export_btn = ModernButton(controls_frame, text="Export Logs", command=self.export_logs, style="info")
        export_btn.pack(side=tk.RIGHT, padx=5)
        
        clear_btn = ModernButton(controls_frame, text="Clear All Logs", command=self.clear_logs, style="danger")
        clear_btn.pack(side=tk.RIGHT, padx=5)
        
        # Log info bar
        info_bar = ModernFrame(page, bg=COLORS["bg_tertiary"], border=1)
        info_bar.pack(fill=tk.X, padx=10, pady=5)
        
        self.log_info_text = ModernLabel(
            info_bar, 
            text=f"Logs are automatically retained for {LOG_RETENTION_DAYS} days. Older entries are purged on startup. Total logs: {len(system_updates_log)}",
            size=10, style="secondary"
        )
        self.log_info_text.pack(pady=10)
        
        # Logs panel with search
        logs_panel = ModernFrame(page, bg=COLORS["bg_secondary"], border=1)
        logs_panel.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Search bar
        search_frame = ModernFrame(logs_panel, bg=COLORS["bg_secondary"])
        search_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ModernLabel(search_frame, text="Search:", size=10).pack(side=tk.LEFT, padx=5)
        self.log_search = ModernEntry(search_frame, width=30)
        self.log_search.pack(side=tk.LEFT, padx=5)
        self.log_search.bind("<KeyRelease>", self.search_logs)
        
        # Log count
        self.log_count_label = ModernLabel(search_frame, text="0 entries", size=10, style="secondary")
        self.log_count_label.pack(side=tk.RIGHT, padx=10)
        
        logs_header = ModernFrame(logs_panel, bg=COLORS["bg_secondary"])
        logs_header.pack(fill=tk.X)
        
        logs_label = ModernLabel(logs_header, text="System Logs", size=12, weight="bold")
        logs_label.pack(side=tk.LEFT, padx=10, pady=5)
        
        self.logs_text = ModernText(logs_panel, height=20)
        self.logs_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        
        # Initialize with current logs
        self.apply_log_filter()

    def build_hardware_page(self):
        page = ModernFrame(self.content_frame)
        self.pages["hardware"] = page
        
        title = ModernLabel(page, text="Hardware Status Monitor", size=18, weight="bold")
        title.pack(pady=10)
        
        # Main container
        main_container = ModernFrame(page)
        main_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # System resources panel
        resources_panel = ModernFrame(main_container, bg=COLORS["bg_secondary"], border=1)
        resources_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        
        resources_label = ModernLabel(resources_panel, text="System Resources", size=14, weight="bold")
        resources_label.pack(pady=10)
        
        # CPU, Memory, Disk with visual bars
        self.resource_monitors = {}
        resources = ["CPU", "Memory", "Disk"]
        
        for resource in resources:
            frame = ModernFrame(resources_panel, bg=COLORS["bg_secondary"])
            frame.pack(fill=tk.X, padx=20, pady=10)
            
            label = ModernLabel(frame, text=f"{resource}: N/A", size=12)
            label.pack(anchor="w")
            
            # Progress bar container
            bar_frame = ModernFrame(frame, bg=COLORS["bg_primary"], height=20)
            bar_frame.pack(fill=tk.X, pady=5)
            bar_frame.pack_propagate(False)
            
            # Progress bar fill
            bar_fill = ModernFrame(bar_frame, bg=COLORS["success"], width=0)
            bar_fill.pack(side=tk.LEFT, fill=tk.Y)
            
            self.resource_monitors[resource] = {
                "label": label,
                "bar": bar_fill,
                "container": bar_frame
            }
        
        # Hardware info panel
        hw_info_panel = ModernFrame(main_container, bg=COLORS["bg_secondary"], border=1)
        hw_info_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0))
        
        hw_label = ModernLabel(hw_info_panel, text="Hardware Information", size=14, weight="bold")
        hw_label.pack(pady=10)
        
        # GPIO status
        gpio_frame = ModernFrame(hw_info_panel, bg=COLORS["bg_tertiary"])
        gpio_frame.pack(fill=tk.X, padx=20, pady=20)
        
        gpio_label = ModernLabel(gpio_frame, text="GPIO Status", size=12, weight="bold")
        gpio_label.pack(pady=10)
        
        self.label_gpio = ModernLabel(gpio_frame, text="GPIO: [LED=OFF, Buzzer=OFF]", size=10)
        self.label_gpio.pack(pady=10)
        
        # System uptime
        self.uptime_label = ModernLabel(hw_info_panel, text="Uptime: Calculating...", size=10)
        self.uptime_label.pack(pady=10)
        
        # Temperature (if available)
        self.temp_label = ModernLabel(hw_info_panel, text="Temperature: N/A", size=10)
        self.temp_label.pack(pady=10)

    def build_narada_page(self):
        page = ModernFrame(self.content_frame)
        self.pages["narada"] = page
        
        title = ModernLabel(page, text="Narada Commands Configuration", size=18, weight="bold")
        title.pack(pady=10)
        
        # Tabbed interface for commands
        tab_frame = ModernFrame(page, bg=COLORS["bg_secondary"], border=1)
        tab_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.command_tab = tk.StringVar(value="builtin")
        tabs = [("Built-in", "builtin"), ("Custom", "custom"), ("Statistics", "stats")]
        
        for text, value in tabs:
            rb = tk.Radiobutton(
                tab_frame, text=text, variable=self.command_tab, value=value,
                bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
                activebackground=COLORS["bg_secondary"], selectcolor=COLORS["bg_primary"],
                font=("Arial", 10, "bold"), command=self.switch_command_tab
            )
            rb.pack(side=tk.LEFT, padx=20, pady=5)
        
        # Commands container
        self.commands_container = ModernFrame(page)
        self.commands_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Create all tabs
        self.create_builtin_commands_tab()
        self.create_custom_commands_tab()
        self.create_stats_tab()
        
        # Show default tab
        self.switch_command_tab()

    def create_builtin_commands_tab(self):
        self.builtin_tab = ModernFrame(self.commands_container, bg=COLORS["bg_secondary"], border=1)
        
        label = ModernLabel(self.builtin_tab, text="Built-in Commands Reference", size=12, weight="bold")
        label.pack(pady=10)
        
        # Enhanced built-in commands display
        commands_frame = ModernFrame(self.builtin_tab, bg=COLORS["bg_secondary"])
        commands_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        
        scrollbar = tk.Scrollbar(commands_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.list_built_in = tk.Listbox(
            commands_frame, yscrollcommand=scrollbar.set,
            bg=COLORS["bg_primary"], fg=COLORS["text_primary"],
            font=("Consolas", 9), bd=0, highlightthickness=0, height=15
        )
        self.list_built_in.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.list_built_in.yview)
        
        # Enhanced built-in commands with all commands from BUILT_IN_COMMANDS
        for phrase, desc in BUILT_IN_COMMANDS.items():
            self.list_built_in.insert(tk.END, f"[BUILT-IN] {phrase} => {desc}")
        
        # Add info label
        info_label = ModernLabel(
            self.builtin_tab, 
            text="These are the built-in voice commands. Users can activate them by speaking to Narada.",
            size=9, style="secondary"
        )
        info_label.pack(pady=5)

    def create_custom_commands_tab(self):
        self.custom_tab = ModernFrame(self.commands_container, bg=COLORS["bg_secondary"], border=1)
        
        label = ModernLabel(self.custom_tab, text="Custom Commands", size=12, weight="bold")
        label.pack(pady=10)
        
        # Commands list
        list_frame = ModernFrame(self.custom_tab, bg=COLORS["bg_secondary"])
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        
        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.list_custom_cmds = tk.Listbox(
            list_frame, yscrollcommand=scrollbar.set,
            bg=COLORS["bg_primary"], fg=COLORS["text_primary"],
            font=("Consolas", 9), bd=0, highlightthickness=0, height=8
        )
        self.list_custom_cmds.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.list_custom_cmds.yview)
        
        self.refresh_custom_cmds()
        
        # Add command form
        form_frame = ModernFrame(self.custom_tab, bg=COLORS["bg_secondary"])
        form_frame.pack(pady=10)
        
        ModernLabel(form_frame, text="Phrase:", size=10).grid(row=0, column=0, padx=10, pady=5, sticky="e")
        self.entry_cmd_phrase = ModernEntry(form_frame, width=30)
        self.entry_cmd_phrase.grid(row=0, column=1, padx=10, pady=5)
        
        ModernLabel(form_frame, text="Response:", size=10).grid(row=1, column=0, padx=10, pady=5, sticky="e")
        self.entry_cmd_response = ModernEntry(form_frame, width=30)
        self.entry_cmd_response.grid(row=1, column=1, padx=10, pady=5)
        
        btn_frame = ModernFrame(form_frame, bg=COLORS["bg_secondary"])
        btn_frame.grid(row=2, column=0, columnspan=2, pady=10)
        
        ModernButton(btn_frame, text="Add Command", command=self.add_command, style="success").pack(side=tk.LEFT, padx=5)
        ModernButton(btn_frame, text="Delete Selected", command=self.del_command, style="danger").pack(side=tk.LEFT, padx=5)
        ModernButton(btn_frame, text="Import", command=self.import_commands, style="info").pack(side=tk.LEFT, padx=5)
        ModernButton(btn_frame, text="Export", command=self.export_commands, style="info").pack(side=tk.LEFT, padx=5)

    def create_stats_tab(self):
        self.stats_tab = ModernFrame(self.commands_container, bg=COLORS["bg_secondary"], border=1)
        
        label = ModernLabel(self.stats_tab, text="Narada Usage Statistics", size=12, weight="bold")
        label.pack(pady=10)
        
        # Stats display
        self.narada_stats_text = ModernText(self.stats_tab, height=15)
        self.narada_stats_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    def switch_command_tab(self):
        # Hide all tabs
        for widget in self.commands_container.winfo_children():
            widget.pack_forget()
        
        # Show selected tab
        tab = self.command_tab.get()
        if tab == "builtin":
            self.builtin_tab.pack(fill=tk.BOTH, expand=True)
        elif tab == "custom":
            self.custom_tab.pack(fill=tk.BOTH, expand=True)
        elif tab == "stats":
            self.stats_tab.pack(fill=tk.BOTH, expand=True)
            self.update_narada_stats()

    def build_modes_page(self):
        page = ModernFrame(self.content_frame)
        self.pages["modes"] = page
        
        title = ModernLabel(page, text="Advanced Mode Management", size=18, weight="bold")
        title.pack(pady=10)
        
        main_container = ModernFrame(page)
        main_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # System settings panel
        settings_panel = ModernFrame(main_container, bg=COLORS["bg_secondary"], border=1)
        settings_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        
        settings_label = ModernLabel(settings_panel, text="System Settings", size=14, weight="bold")
        settings_label.pack(pady=10)
        
        # Current settings display
        current_settings = ModernFrame(settings_panel, bg=COLORS["bg_tertiary"])
        current_settings.pack(fill=tk.X, padx=20, pady=10)
        
        ModernLabel(current_settings, text="Current Settings:", size=11, weight="bold").pack(pady=5)
        self.current_settings_label = ModernLabel(current_settings, text="", size=9, style="secondary")
        self.current_settings_label.pack(pady=5)
        self.update_current_settings_display()
        
        # Quiet hours
        quiet_frame = ModernFrame(settings_panel, bg=COLORS["bg_secondary"])
        quiet_frame.pack(padx=20, pady=10)
        
        self.quiet_enabled = tk.BooleanVar(value=system_settings["quiet_hours"]["enabled"])
        quiet_cb = tk.Checkbutton(
            quiet_frame, text="Enable Quiet Hours", variable=self.quiet_enabled,
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            activebackground=COLORS["bg_secondary"], selectcolor=COLORS["bg_primary"],
            font=("Arial", 10), command=self.toggle_quiet_hours
        )
        quiet_cb.pack(anchor="w")
        
        time_frame = ModernFrame(quiet_frame, bg=COLORS["bg_secondary"])
        time_frame.pack(pady=5)
        
        ModernLabel(time_frame, text="Start:", size=9).pack(side=tk.LEFT, padx=5)
        self.quiet_start = ModernEntry(time_frame, width=8)
        self.quiet_start.pack(side=tk.LEFT, padx=5)
        self.quiet_start.insert(0, system_settings["quiet_hours"]["start"])
        
        ModernLabel(time_frame, text="End:", size=9).pack(side=tk.LEFT, padx=5)
        self.quiet_end = ModernEntry(time_frame, width=8)
        self.quiet_end.pack(side=tk.LEFT, padx=5)
        self.quiet_end.insert(0, system_settings["quiet_hours"]["end"])
        
        # Alert volume
        volume_frame = ModernFrame(settings_panel, bg=COLORS["bg_secondary"])
        volume_frame.pack(padx=20, pady=10)
        
        ModernLabel(volume_frame, text="Alert Volume:", size=10).pack(anchor="w")
        self.volume_scale = tk.Scale(
            volume_frame, from_=0, to=100, orient=tk.HORIZONTAL,
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            highlightbackground=COLORS["bg_secondary"], troughcolor=COLORS["bg_primary"],
            activebackground=COLORS["accent"], length=200,
            command=self.update_volume_label
        )
        self.volume_scale.set(system_settings["alert_volume"])
        self.volume_scale.pack(pady=5)
        
        self.volume_label = ModernLabel(volume_frame, text=f"Current: {system_settings['alert_volume']}%", size=9, style="secondary")
        self.volume_label.pack()
        
        # Detection sensitivity
        sens_frame = ModernFrame(settings_panel, bg=COLORS["bg_secondary"])
        sens_frame.pack(padx=20, pady=10)
        
        ModernLabel(sens_frame, text="Detection Sensitivity:", size=10).pack(anchor="w")
        self.sens_scale = tk.Scale(
            sens_frame, from_=0.1, to=0.9, resolution=0.1, orient=tk.HORIZONTAL,
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            highlightbackground=COLORS["bg_secondary"], troughcolor=COLORS["bg_primary"],
            activebackground=COLORS["accent"], length=200,
            command=self.update_sensitivity_label
        )
        self.sens_scale.set(system_settings["detection_sensitivity"])
        self.sens_scale.pack(pady=5)
        
        self.sens_label = ModernLabel(sens_frame, text=f"Current: {system_settings['detection_sensitivity']}", size=9, style="secondary")
        self.sens_label.pack()
        
        # Save settings button
        save_btn = ModernButton(settings_panel, text="Save Settings", command=self.save_system_settings, style="success")
        save_btn.pack(pady=20)
        
        # Modes management panel
        modes_panel = ModernFrame(main_container, bg=COLORS["bg_secondary"], border=1)
        modes_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        modes_label = ModernLabel(modes_panel, text="Custom Modes", size=14, weight="bold")
        modes_label.pack(pady=10)
        
        # Mode creation form
        create_frame = ModernFrame(modes_panel, bg=COLORS["bg_secondary"])
        create_frame.pack(padx=20, pady=10)
        
        ModernLabel(create_frame, text="Create New Mode:", size=11, weight="bold").pack(pady=5)
        
        form_frame = ModernFrame(create_frame, bg=COLORS["bg_secondary"])
        form_frame.pack(pady=10)
        
        ModernLabel(form_frame, text="Mode Name:").grid(row=0, column=0, padx=5, pady=5, sticky="e")
        self.entry_mode_name = ModernEntry(form_frame, width=20)
        self.entry_mode_name.grid(row=0, column=1, padx=5, pady=5)
        
        ModernLabel(form_frame, text="Priority (1-9):").grid(row=1, column=0, padx=5, pady=5, sticky="e")
        self.entry_mode_priority = ModernEntry(form_frame, width=10)
        self.entry_mode_priority.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        
        ModernButton(form_frame, text="Create Mode", command=self.save_mode, style="success").grid(row=2, column=0, columnspan=2, pady=10)
        
        # Existing modes list
        modes_list_frame = ModernFrame(modes_panel, bg=COLORS["bg_secondary"])
        modes_list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        ModernLabel(modes_list_frame, text="Existing Modes:", size=10, weight="bold").pack()
        
        scrollbar = tk.Scrollbar(modes_list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.custom_modes_listbox = tk.Listbox(
            modes_list_frame, yscrollcommand=scrollbar.set,
            bg=COLORS["bg_primary"], fg=COLORS["text_primary"],
            font=("Arial", 10), bd=0, highlightthickness=0, height=10
        )
        self.custom_modes_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.custom_modes_listbox.yview)
        
        self.refresh_custom_modes_list()
        
        delete_btn = ModernButton(modes_panel, text="Delete Selected", command=self.delete_mode, style="danger")
        delete_btn.pack(pady=(0, 10))
        
        info_label = ModernLabel(
            modes_panel, 
            text="Note: Night=priority 10, Emergency=priority 11",
            style="secondary", size=9
        )
        info_label.pack(pady=5)

    def update_current_settings_display(self):
        if hasattr(self, 'current_settings_label'):
            quiet_status = "Enabled" if system_settings["quiet_hours"]["enabled"] else "Disabled"
            quiet_time = f"{system_settings['quiet_hours']['start']} - {system_settings['quiet_hours']['end']}"
            text = f"Quiet Hours: {quiet_status} ({quiet_time})\nAlert Volume: {system_settings['alert_volume']}%\nDetection Sensitivity: {system_settings['detection_sensitivity']}"
            self.current_settings_label.config(text=text)

    def update_volume_label(self, value):
        if hasattr(self, 'volume_label'):
            self.volume_label.config(text=f"Current: {int(float(value))}%")

    def update_sensitivity_label(self, value):
        if hasattr(self, 'sens_label'):
            self.sens_label.config(text=f"Current: {float(value):.1f}")

    def build_email_page(self):
        page = ModernFrame(self.content_frame)
        self.pages["email"] = page
        
        title = ModernLabel(page, text="Email & Notification Settings", size=18, weight="bold")
        title.pack(pady=10)
        
        # Current email status
        status_frame = ModernFrame(page, bg=COLORS["bg_tertiary"], border=1)
        status_frame.pack(fill=tk.X, padx=10, pady=5)
        
        email_status = f"Email Sender: {EMAIL_SENDER}\nCurrent Recipients: {len(EMAIL_RECIPIENTS)}\nCooldown: {EMAIL_COOLDOWN} seconds"
        status_label = ModernLabel(status_frame, text=email_status, size=10, style="secondary")
        status_label.pack(pady=10)
        
        # Main settings panel
        settings_panel = ModernFrame(page, bg=COLORS["bg_secondary"], border=1)
        settings_panel.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Email settings
        email_frame = ModernFrame(settings_panel, bg=COLORS["bg_secondary"])
        email_frame.pack(pady=20)
        
        ModernLabel(email_frame, text="Email Configuration", size=14, weight="bold").pack(pady=10)
        
        form_frame = ModernFrame(email_frame, bg=COLORS["bg_secondary"])
        form_frame.pack(pady=10)
        
        ModernLabel(form_frame, text="Recipients (comma-separated):").grid(row=0, column=0, padx=10, pady=10, sticky="e")
        self.entry_recipients = ModernEntry(form_frame, width=40)
        self.entry_recipients.grid(row=0, column=1, padx=10, pady=10)
        self.entry_recipients.insert(0, ", ".join(EMAIL_RECIPIENTS))
        
        ModernLabel(form_frame, text="Cooldown Period (seconds):").grid(row=1, column=0, padx=10, pady=10, sticky="e")
        self.entry_cooldown = ModernEntry(form_frame, width=15)
        self.entry_cooldown.grid(row=1, column=1, padx=10, pady=10, sticky="w")
        self.entry_cooldown.insert(0, str(EMAIL_COOLDOWN))
        
        # Test email button
        test_btn = ModernButton(form_frame, text="Send Test Email", command=self.send_test_email, style="info")
        test_btn.grid(row=2, column=0, columnspan=2, pady=10)
        
        # Notification preferences
        notif_frame = ModernFrame(settings_panel, bg=COLORS["bg_tertiary"])
        notif_frame.pack(fill=tk.X, padx=20, pady=20)
        
        ModernLabel(notif_frame, text="Notification Preferences", size=12, weight="bold").pack(pady=10)
        
        self.notif_prefs = {}
        prefs = [
            ("email_on_scissors", "Email on scissors detection", True),
            ("email_on_person", "Email on person detection", False),
            ("email_digest", "Daily digest email", False),
            ("email_emergency_only", "Emergency alerts only", False)
        ]
        
        prefs_info = ModernLabel(
            notif_frame, 
            text="Configure when email alerts should be sent:",
            size=9, style="secondary"
        )
        prefs_info.pack(pady=5)
        
        for key, text, default in prefs:
            var = tk.BooleanVar(value=default)
            cb = tk.Checkbutton(
                notif_frame, text=text, variable=var,
                bg=COLORS["bg_tertiary"], fg=COLORS["text_primary"],
                activebackground=COLORS["bg_tertiary"], selectcolor=COLORS["bg_primary"],
                font=("Arial", 10)
            )
            cb.pack(anchor="w", padx=20, pady=2)
            self.notif_prefs[key] = var
        
        # Save button
        save_btn = ModernButton(settings_panel, text="Save Email Settings", command=self.save_email_settings, style="success")
        save_btn.pack(pady=20)
        
        # Help text
        help_text = ModernLabel(
            settings_panel,
            text="Note: Email alerts require active internet connection. Test email to verify configuration.",
            size=9, style="secondary"
        )
        help_text.pack(pady=5)

    def update_gui(self):
        # Dashboard updates with caching
        if self.pages["dashboard"].winfo_ismapped():
            # Update detection and console text
            update_text_widget(self.det_text, latest_detection_info if latest_detection_info else "No detections yet.\nWaiting for object detection...")
            update_text_widget(self.console_text, "\n".join(system_updates_log[-20:]) if system_updates_log else "System console ready.\nAll activities will be logged here.")
            self.sync_mode_checkbuttons()
            
            # Update stat cards
            active_modes = sum([MODE_DND, MODE_EMAIL_OFF, MODE_IDLE, MODE_NIGHT, MODE_EMERGENCY])
            self.stat_cards["active_modes"].config(text=str(active_modes))
            
            # Detection stats
            today_count = 0
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            for hour_key in detection_stats["hourly_stats"]:
                if hour_key.startswith(today):
                    today_count += detection_stats["hourly_stats"][hour_key]["total"]
            self.stat_cards["detections_today"].config(text=str(today_count))
            
            # System health
            if MODE_EMERGENCY:
                health = "Emergency"
                self.stat_cards["system_health"].config(text=health, fg=COLORS["error"])
            elif active_modes > 3:
                health = "Limited"
                self.stat_cards["system_health"].config(text=health, fg=COLORS["warning"])
            else:
                health = "Good"
                self.stat_cards["system_health"].config(text=health, fg=COLORS["success"])
            
            # Alert analytics
            analytics = get_alert_analytics()
            if analytics:
                analytics_text = f"24h Alerts: {analytics['total_24h']} | Peak Hour: {analytics['peak_hour']}"
            else:
                analytics_text = "No alerts in the last 24 hours"
            self.analytics_text.config(text=analytics_text)

        # Logs page
        if self.pages["logs"].winfo_ismapped():
            self.update_logs_display()

        # Hardware page updates
        if psutil and self.pages["hardware"].winfo_ismapped():
            # CPU usage
            cpu = psutil.cpu_percent()
            self.resource_monitors["CPU"]["label"].config(text=f"CPU Usage: {cpu}%")
            bar_width = int((cpu / 100) * self.resource_monitors["CPU"]["container"].winfo_width())
            self.resource_monitors["CPU"]["bar"].config(width=bar_width)
            if cpu > 80:
                self.resource_monitors["CPU"]["bar"].config(bg=COLORS["error"])
            elif cpu > 60:
                self.resource_monitors["CPU"]["bar"].config(bg=COLORS["warning"])
            else:
                self.resource_monitors["CPU"]["bar"].config(bg=COLORS["success"])
            
            # Memory usage
            mem = psutil.virtual_memory().percent
            self.resource_monitors["Memory"]["label"].config(text=f"Memory Usage: {mem}%")
            bar_width = int((mem / 100) * self.resource_monitors["Memory"]["container"].winfo_width())
            self.resource_monitors["Memory"]["bar"].config(width=bar_width)
            if mem > 80:
                self.resource_monitors["Memory"]["bar"].config(bg=COLORS["error"])
            elif mem > 60:
                self.resource_monitors["Memory"]["bar"].config(bg=COLORS["warning"])
            else:
                self.resource_monitors["Memory"]["bar"].config(bg=COLORS["success"])
            
            # Disk usage
            disk = psutil.disk_usage('/').percent
            self.resource_monitors["Disk"]["label"].config(text=f"Disk Usage: {disk}%")
            bar_width = int((disk / 100) * self.resource_monitors["Disk"]["container"].winfo_width())
            self.resource_monitors["Disk"]["bar"].config(width=bar_width)
            if disk > 80:
                self.resource_monitors["Disk"]["bar"].config(bg=COLORS["error"])
            elif disk > 60:
                self.resource_monitors["Disk"]["bar"].config(bg=COLORS["warning"])
            else:
                self.resource_monitors["Disk"]["bar"].config(bg=COLORS["success"])
            
            # GPIO status
            led_state = "ON" if red_led and red_led.is_lit else "OFF"
            buzzer_state = "ON" if buzzer and buzzer.value == 1 else "OFF"
            self.label_gpio.config(text=f"GPIO Status: [LED={led_state}, Buzzer={buzzer_state}]")
            
            # System uptime
            import uptime
            up = uptime.uptime()
            hours = int(up // 3600)
            minutes = int((up % 3600) // 60)
            self.uptime_label.config(text=f"Uptime: {hours}h {minutes}m")
            
            # Temperature (Raspberry Pi specific)
            try:
                with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                    temp = float(f.read()) / 1000.0
                    self.temp_label.config(text=f"Temperature: {temp:.1f}°C")
            except:
                self.temp_label.config(text="Temperature: N/A")

        self.after_id = self.root.after(1000, self.update_gui)

    def sync_mode_checkbuttons(self):
        self.var_dnd.set(MODE_DND)
        self.var_email_off.set(MODE_EMAIL_OFF)
        self.var_idle.set(MODE_IDLE)
        self.var_night.set(MODE_NIGHT)
        self.var_emergency.set(MODE_EMERGENCY)

    # Mode toggles
    def toggle_dnd(self):
        global MODE_DND
        MODE_DND = self.var_dnd.get()
        log_system_update(f"Admin toggled DND => {MODE_DND}")

    def toggle_email(self):
        global MODE_EMAIL_OFF
        MODE_EMAIL_OFF = self.var_email_off.get()
        log_system_update(f"Admin toggled EmailOff => {MODE_EMAIL_OFF}")

    def toggle_idle(self):
        global MODE_IDLE
        MODE_IDLE = self.var_idle.get()
        log_system_update(f"Admin toggled Idle => {MODE_IDLE}")

    def toggle_night(self):
        global MODE_NIGHT
        MODE_NIGHT = self.var_night.get()
        log_system_update(f"Admin toggled Night => {MODE_NIGHT}")

    def toggle_emergency(self):
        global MODE_EMERGENCY
        MODE_EMERGENCY = self.var_emergency.get()
        log_system_update(f"Admin toggled Emergency => {MODE_EMERGENCY}")

    # User management methods
    def refresh_user_list(self):
        self.user_listbox.delete(0, tk.END)
        for uname in USERS.keys():
            self.user_listbox.insert(tk.END, uname)

    def add_user(self):
        uname = self.entry_uname.get().strip()
        upass = self.entry_pass.get().strip()
        if not uname or not upass:
            messagebox.showerror("Error", "Enter valid username/password.")
            return
        if uname in USERS:
            messagebox.showerror("Error", "User already exists.")
            return
        USERS[uname] = {
            "password": upass,
            "role": "user",
            "history": {
                "logins": [],
                "narada_activity": []
            }
        }
        self.refresh_user_list()
        save_persistent_data()
        log_system_update(f"Admin added user '{uname}'.")

    def delete_user(self):
        sel = self.user_listbox.curselection()
        if not sel:
            return
        uname = self.user_listbox.get(sel[0])
        if uname == "admin":
            messagebox.showerror("Error", "Cannot delete admin user.")
            return
        confirm = messagebox.askyesno("Delete User", f"Are you sure you want to delete '{uname}'?")
        if confirm:
            USERS.pop(uname, None)
            self.refresh_user_list()
            save_persistent_data()
            log_system_update(f"Admin deleted user '{uname}'.")

    def reset_password(self):
        sel = self.user_listbox.curselection()
        if not sel:
            return
        uname = self.user_listbox.get(sel[0])
        if uname not in USERS:
            return
        new_pass = "user123"
        USERS[uname]["password"] = new_pass
        save_persistent_data()
        messagebox.showinfo("Password Reset", f"New password for '{uname}' is '{new_pass}'.")
        log_system_update(f"Admin reset password for '{uname}'.")

    def rename_user(self):
        sel = self.user_listbox.curselection()
        if not sel:
            return
        oldname = self.user_listbox.get(sel[0])
        newname = self.entry_uname.get().strip()
        if not newname:
            messagebox.showerror("Error", "Enter new username.")
            return
        if newname in USERS:
            messagebox.showerror("Error", "User already exists with that name.")
            return
        if oldname == "admin":
            messagebox.showerror("Error", "Cannot rename admin user.")
            return
        USERS[newname] = USERS.pop(oldname)
        self.refresh_user_list()
        save_persistent_data()
        log_system_update(f"Admin renamed '{oldname}' to '{newname}'.")

    def on_select_user(self, event):
        sel = self.user_listbox.curselection()
        if not sel:
            return
        uname = self.user_listbox.get(sel[0])
        self.show_user_profile(uname)

    def show_user_profile(self, uname):
        for w in self.profile_frame.winfo_children():
            w.destroy()

        if uname not in USERS:
            ModernLabel(self.profile_frame, text="User not found").pack()
            return

        user_data = USERS[uname]
        
        profile_title = ModernLabel(self.profile_frame, text=f"Profile: {uname}", size=14, weight="bold")
        profile_title.pack(pady=10)
        
        info_frame = ModernFrame(self.profile_frame, bg=COLORS["bg_secondary"])
        info_frame.pack(pady=10, padx=20, anchor="w")
        
        ModernLabel(info_frame, text=f"Role: {user_data['role']}", size=11).pack(anchor="w", pady=2)
        ModernLabel(info_frame, text=f"Password: {'*' * len(user_data['password'])}", size=11).pack(anchor="w", pady=2)
        
        # Login history
        logins = user_data["history"].get("logins", [])
        if logins:
            ModernLabel(info_frame, text="Recent Logins:", size=11, weight="bold").pack(anchor="w", pady=(10, 5))
            for login_time in logins[-5:]:  # Show last 5 logins
                ModernLabel(info_frame, text=f"  • {login_time}", size=9, style="secondary").pack(anchor="w")
        
        # Narada activity
        narada_act = user_data["history"].get("narada_activity", [])
        if narada_act:
            ModernLabel(self.profile_frame, text="Recent Narada Activity:", size=11, weight="bold").pack(anchor="w", padx=20, pady=(10, 5))
            act_text = ModernText(self.profile_frame, height=6, width=60)
            act_text.pack(padx=20, pady=5)
            act_text.insert(tk.END, "\n".join(narada_act[-20:]))  # Show last 20 entries
            act_text.config(state="disabled")

    # Logs management methods
    def apply_log_filter(self):
        filter_type = self.log_filter.get()
        filtered_logs = []
        
        if filter_type == "all":
            filtered_logs = system_updates_log
        elif filter_type == "system":
            filtered_logs = [log for log in system_updates_log if "system" in log.lower() or "admin" in log.lower()]
        elif filter_type == "alert":
            filtered_logs = [log for log in system_updates_log if "alert" in log.lower() or "detection" in log.lower()]
        elif filter_type == "user":
            filtered_logs = [log for log in system_updates_log if "user" in log.lower() or "narada" in log.lower()]
        
        self.update_logs_display(filtered_logs)

    def update_logs_display(self, logs=None):
        if logs is None:
            self.apply_log_filter()
            return
        
        self.logs_text.config(state="normal")
        self.logs_text.delete("1.0", tk.END)
        
        if logs:
            self.logs_text.insert(tk.END, "\n".join(logs[-100:]))  # Show last 100 entries
        else:
            self.logs_text.insert(tk.END, "No logs found matching the filter.")
        
        self.logs_text.config(state="disabled")
        self.log_count_label.config(text=f"{len(logs)} entries")

    def search_logs(self, event=None):
        search_term = self.log_search.get().lower()
        if not search_term:
            self.apply_log_filter()
            return
        
        filter_type = self.log_filter.get()
        all_logs = system_updates_log
        
        # First apply filter
        if filter_type == "system":
            all_logs = [log for log in all_logs if "system" in log.lower() or "admin" in log.lower()]
        elif filter_type == "alert":
            all_logs = [log for log in all_logs if "alert" in log.lower() or "detection" in log.lower()]
        elif filter_type == "user":
            all_logs = [log for log in all_logs if "user" in log.lower() or "narada" in log.lower()]
        
        # Then search
        filtered_logs = [log for log in all_logs if search_term in log.lower()]
        self.update_logs_display(filtered_logs)

    def export_logs(self):
        filename = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        if filename:
            try:
                with open(filename, 'w') as f:
                    f.write("\n".join(system_updates_log))
                messagebox.showinfo("Success", f"Logs exported to {filename}")
                log_system_update(f"Admin exported logs to {filename}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to export logs: {str(e)}")

    def clear_logs(self):
        global system_updates_log
        if messagebox.askyesno("Confirm", "Are you sure you want to clear all system logs?"):
            system_updates_log = []
            save_persistent_data()
            log_system_update("Admin cleared system logs.")
            self.apply_log_filter()
            messagebox.showinfo("Success", "System logs cleared.")

    # Narada commands methods
    def refresh_custom_cmds(self):
        self.list_custom_cmds.delete(0, tk.END)
        for phrase, resp in CUSTOM_VOICE_COMMANDS.items():
            self.list_custom_cmds.insert(tk.END, f"{phrase} => {resp}")

    def add_command(self):
        phrase = self.entry_cmd_phrase.get().strip().lower()
        resp = self.entry_cmd_response.get().strip()
        if not phrase or not resp:
            messagebox.showerror("Error", "Enter phrase and response.")
            return
        CUSTOM_VOICE_COMMANDS[phrase] = resp
        self.refresh_custom_cmds()
        log_system_update(f"Admin added custom voice command: '{phrase}' => '{resp}'")
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
        log_system_update(f"Admin deleted custom voice command '{line}'")

    def import_commands(self):
        filename = filedialog.askopenfilename(
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if filename:
            try:
                with open(filename, 'r') as f:
                    imported = json.load(f)
                    if isinstance(imported, dict):
                        CUSTOM_VOICE_COMMANDS.update(imported)
                        self.refresh_custom_cmds()
                        messagebox.showinfo("Success", f"Imported {len(imported)} commands")
                        log_system_update(f"Admin imported commands from {filename}")
                    else:
                        messagebox.showerror("Error", "Invalid command file format")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to import: {str(e)}")

    def export_commands(self):
        filename = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if filename:
            try:
                with open(filename, 'w') as f:
                    json.dump(CUSTOM_VOICE_COMMANDS, f, indent=2)
                messagebox.showinfo("Success", f"Commands exported to {filename}")
                log_system_update(f"Admin exported commands to {filename}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to export: {str(e)}")

    def update_narada_stats(self):
        stats_text = []
        stats_text.append("NARADA USAGE STATISTICS")
        stats_text.append("="*30)
        
        # Count command usage
        command_usage = {}
        for log in voice_assistant_log:
            if "You said:" in log:
                command_usage[log] = command_usage.get(log, 0) + 1
        
        stats_text.append(f"\nTotal voice logs: {len(voice_assistant_log)}")
        stats_text.append(f"Total responses: {len(voice_responses)}")
        
        # User statistics
        stats_text.append("\nUser Activity:")
        for username, user_data in USERS.items():
            narada_count = len(user_data["history"].get("narada_activity", []))
            if narada_count > 0:
                stats_text.append(f"  {username}: {narada_count} interactions")
        
        # Most common commands
        if command_usage:
            stats_text.append("\nMost Common Commands:")
            sorted_commands = sorted(command_usage.items(), key=lambda x: x[1], reverse=True)[:10]
            for cmd, count in sorted_commands:
                stats_text.append(f"  {count}x - {cmd}")
        
        self.narada_stats_text.config(state="normal")
        self.narada_stats_text.delete("1.0", tk.END)
        self.narada_stats_text.insert(tk.END, "\n".join(stats_text))
        self.narada_stats_text.config(state="disabled")

    # Mode management methods
    def toggle_quiet_hours(self):
        system_settings["quiet_hours"]["enabled"] = self.quiet_enabled.get()
        self.update_current_settings_display()

    def save_system_settings(self):
        global system_settings
        
        # Update quiet hours
        system_settings["quiet_hours"]["enabled"] = self.quiet_enabled.get()
        system_settings["quiet_hours"]["start"] = self.quiet_start.get()
        system_settings["quiet_hours"]["end"] = self.quiet_end.get()
        
        # Update volume and sensitivity
        system_settings["alert_volume"] = int(self.volume_scale.get())
        system_settings["detection_sensitivity"] = float(self.sens_scale.get())
        
        save_persistent_data()
        self.update_current_settings_display()
        
        messagebox.showinfo("Success", "System settings saved successfully!")
        log_system_update("Admin updated system settings")

    def refresh_custom_modes_list(self):
        self.custom_modes_listbox.delete(0, tk.END)
        self.custom_modes_listbox.insert(tk.END, "=== Built-in Modes ===")
        self.custom_modes_listbox.insert(tk.END, "Night Mode (priority=10)")
        self.custom_modes_listbox.insert(tk.END, "Emergency Mode (priority=11)")
        
        if CUSTOM_MODES:
            self.custom_modes_listbox.insert(tk.END, "")
            self.custom_modes_listbox.insert(tk.END, "=== Custom Modes ===")
            for m, data in CUSTOM_MODES.items():
                prio = data.get("priority", 1)
                self.custom_modes_listbox.insert(tk.END, f"{m} (priority={prio})")
        else:
            self.custom_modes_listbox.insert(tk.END, "")
            self.custom_modes_listbox.insert(tk.END, "No custom modes created yet.")

    def save_mode(self):
        name = self.entry_mode_name.get().strip().lower()
        prio_str = self.entry_mode_priority.get().strip()
        if not name:
            messagebox.showerror("Error", "Enter mode name.")
            return
        try:
            prio = int(prio_str)
            if prio < 1 or prio > 9:
                raise ValueError
        except:
            messagebox.showerror("Error", "Priority must be between 1 and 9.")
            return
        CUSTOM_MODES[name] = {"priority": prio}
        self.refresh_custom_modes_list()
        log_system_update(f"Admin created/updated mode '{name}' priority={prio}")
        self.entry_mode_name.delete(0, tk.END)
        self.entry_mode_priority.delete(0, tk.END)

    def delete_mode(self):
        sel = self.custom_modes_listbox.curselection()
        if not sel:
            return
        line = self.custom_modes_listbox.get(sel[0])
        if line.startswith("Night") or line.startswith("Emergency") or "===" in line:
            messagebox.showerror("Error", "Cannot delete built-in mode.")
            return
        if "(" in line:
            mode_name = line.split("(")[0].strip().lower()
        else:
            mode_name = line.strip().lower()
        if mode_name in CUSTOM_MODES:
            del CUSTOM_MODES[mode_name]
        self.refresh_custom_modes_list()
        log_system_update(f"Admin deleted custom mode '{mode_name}'")

    # Email settings methods
    def save_email_settings(self):
        global EMAIL_RECIPIENTS, EMAIL_COOLDOWN
        recips = self.entry_recipients.get().strip()
        cd_str = self.entry_cooldown.get().strip()
        if recips:
            EMAIL_RECIPIENTS = [r.strip() for r in recips.split(",") if r.strip()]
        try:
            EMAIL_COOLDOWN = int(cd_str)
        except:
            pass
        messagebox.showinfo("Success", "Email settings updated.")
        log_system_update(f"Admin updated email recipients to {EMAIL_RECIPIENTS}, cooldown={EMAIL_COOLDOWN}")

    def send_test_email(self):
        test_msg = MIMEText("This is a test email from Garuda Security System.\n\nIf you received this, email alerts are working correctly.")
        test_msg['Subject'] = "Garuda Test Email"
        test_msg['From'] = EMAIL_SENDER
        test_msg['To'] = ", ".join(EMAIL_RECIPIENTS)
        
        try:
            server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
            server.login(EMAIL_SENDER, EMAIL_SENDER_PASS)
            server.send_message(test_msg)
            server.quit()
            messagebox.showinfo("Success", "Test email sent successfully!")
            log_system_update("Admin sent test email")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to send test email: {str(e)}")
            log_system_update(f"Admin test email failed: {str(e)}")

    def logout(self):
        save_persistent_data()
        if self.after_id:
            self.root.after_cancel(self.after_id)
        self.root.destroy()
        root_main = tk.Tk()
        LoginHomeScreen(root_main)
        root_main.mainloop()

    def show_instructions(self):
        instructions = (
            "Admin Control Center Instructions:\n\n"
            "Dashboard - Overview of system status and quick controls\n"
            "User Management - Add, delete, rename users and reset passwords\n"
            "Logs & Monitoring - View/search/export system logs (retained for 7 days)\n"
            "Hardware Status - Monitor CPU, memory, disk usage and GPIO states\n"
            "Narada Commands - Configure custom voice commands, import/export\n"
            "Mode Management - System settings and custom modes with priorities\n"
            "Email Settings - Configure alert recipients and cooldown\n\n"
            "System Menu - Create/restore backups, view system info\n\n"
            "All changes are automatically saved and affect all users."
        )
        messagebox.showinfo("Admin Instructions", instructions)

##############################################################################
# MAIN APP LAUNCH
##############################################################################
def check_auto_backup():
    """Check if automatic backup is needed."""
    if not system_settings.get("auto_backup", True):
        return
    
    last_backup = system_settings.get("last_backup")
    backup_interval = system_settings.get("backup_interval_days", 3)
    
    if last_backup:
        try:
            last_backup_date = datetime.datetime.strptime(last_backup, "%Y%m%d_%H%M%S")
            days_since = (datetime.datetime.now() - last_backup_date).days
            
            if days_since >= backup_interval:
                log_system_update(f"Auto-backup triggered (last backup {days_since} days ago)")
                create_backup()
        except:
            # Invalid date format, create backup
            create_backup()
    else:
        # No backup exists
        log_system_update("Creating initial backup")
        create_backup()

def run_main_app(is_admin=False, username="user"):
    """Set up pipeline + GUI for user or admin."""
    global app, red_led, buzzer, stop_button, dashboard_gui

    # Load persistent data on startup
    load_persistent_data()
    
    # Check for auto-backup
    check_auto_backup()

    # Create user_data
    user_data = user_app_callback_class()

    parser = get_default_parser()
    parser.add_argument("--video_source", default="usb", help="Set 'rpi' or device path.")
    parser.add_argument("--network", default="yolov8s", choices=["yolov6n", "yolov8s", "yolox_s_leaky"])
    parser.add_argument("--hef-path", default=None)
    parser.add_argument("--labels-json", default=None)
    args = parser.parse_args()

    Gst.init(None)
    log_system_update("Initializing GPIO devices...")

    red_led = LED(20)
    red_led.off()
    buzzer = OutputDevice(pin=12, initial_value=False)
    stop_button = Button(16, pull_up=True)
    stop_button.when_pressed = button_pressed

    app = GStreamerDetectionApp(args, user_data)
    log_system_update("GStreamer pipeline created & starting...")

    pipeline_thread = threading.Thread(target=app.run, daemon=True)
    pipeline_thread.start()
    log_system_update("Pipeline is running.")

    root = tk.Tk()
    if not is_admin:
        gui = UserDashboardGUI(root, user_data, username=username)
        dashboard_gui = gui
    else:
        gui = AdminDashboardGUI(root, user_data, username=username)
        dashboard_gui = gui

    root.mainloop()
    stop_and_exit()

##############################################################################
# LOGIN SCREENS (Modern Redesign)
##############################################################################
class LoginHomeScreen:
    """Modern login selection screen."""
    def __init__(self, root):
        self.root = root
        self.root.title("Garuda Security System")
        self.root.geometry("800x600")
        self.root.configure(bg=COLORS["bg_primary"])
        
        # Load persistent data on startup
        load_persistent_data()
        
        # Main container
        main_frame = ModernFrame(self.root, bg=COLORS["bg_primary"])
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Logo/Title section
        title_frame = ModernFrame(main_frame, bg=COLORS["bg_primary"])
        title_frame.pack(pady=(80, 40))
        
        title = ModernLabel(title_frame, text="GARUDA", size=36, weight="bold")
        title.pack()
        
        subtitle = ModernLabel(title_frame, text="Security System", size=18, style="secondary")
        subtitle.pack()
        
        # Profile selection
        selection_frame = ModernFrame(main_frame, bg=COLORS["bg_primary"])
        selection_frame.pack(pady=40)
        
        ModernLabel(selection_frame, text="Select Your Profile", size=20).pack(pady=20)
        
        # Profile buttons container
        buttons_frame = ModernFrame(selection_frame, bg=COLORS["bg_primary"])
        buttons_frame.pack()
        
        # User profile button
        user_frame = ModernFrame(buttons_frame, bg=COLORS["bg_tertiary"], border=2)
        user_frame.pack(side=tk.LEFT, padx=20)
        
        user_btn = tk.Button(
            user_frame, text="USER", font=("Arial", 24, "bold"),
            bg=COLORS["info"], fg=COLORS["text_primary"],
            width=10, height=4, bd=0, cursor="hand2",
            activebackground="#1976d2", activeforeground=COLORS["text_primary"],
            command=self.goto_user_login
        )
        user_btn.pack(padx=2, pady=2)
        
        # Admin profile button
        admin_frame = ModernFrame(buttons_frame, bg=COLORS["bg_tertiary"], border=2)
        admin_frame.pack(side=tk.LEFT, padx=20)
        
        admin_btn = tk.Button(
            admin_frame, text="ADMIN", font=("Arial", 24, "bold"),
            bg=COLORS["warning"], fg=COLORS["text_primary"],
            width=10, height=4, bd=0, cursor="hand2",
            activebackground="#e68900", activeforeground=COLORS["text_primary"],
            command=self.goto_admin_login
        )
        admin_btn.pack(padx=2, pady=2)
        
        # Exit button
        exit_btn = ModernButton(main_frame, text="Exit System", command=stop_and_exit, style="danger")
        exit_btn.pack(pady=40)

    def goto_user_login(self):
        self.root.destroy()
        root2 = tk.Tk()
        UserLoginScreen(root2)
        root2.mainloop()

    def goto_admin_login(self):
        self.root.destroy()
        root2 = tk.Tk()
        AdminLoginScreen(root2)
        root2.mainloop()

class UserLoginScreen:
    """Modern user login screen."""
    def __init__(self, root):
        self.root = root
        self.root.title("User Login - Garuda Security")
        self.root.geometry("500x600")
        self.root.configure(bg=COLORS["bg_primary"])
        
        # Main container
        main_frame = ModernFrame(self.root, bg=COLORS["bg_primary"])
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Header
        header_frame = ModernFrame(main_frame, bg=COLORS["bg_primary"])
        header_frame.pack(pady=(60, 40))
        
        ModernLabel(header_frame, text="USER LOGIN", size=24, weight="bold").pack()
        ModernLabel(header_frame, text="Enter your credentials", size=12, style="secondary").pack(pady=(10, 0))
        
        # Login form
        form_frame = ModernFrame(main_frame, bg=COLORS["bg_secondary"], border=1)
        form_frame.pack(padx=50, pady=20)
        
        inner_frame = ModernFrame(form_frame, bg=COLORS["bg_secondary"])
        inner_frame.pack(padx=40, pady=40)
        
        ModernLabel(inner_frame, text="Username", size=11).pack(anchor="w", pady=(0, 5))
        self.entry_username = ModernEntry(inner_frame, width=25)
        self.entry_username.pack(pady=(0, 20))
        
        ModernLabel(inner_frame, text="Password", size=11).pack(anchor="w", pady=(0, 5))
        self.entry_password = ModernEntry(inner_frame, show="*", width=25)
        self.entry_password.pack(pady=(0, 30))
        
        # Buttons
        btn_frame = ModernFrame(inner_frame, bg=COLORS["bg_secondary"])
        btn_frame.pack()
        
        login_btn = ModernButton(btn_frame, text="Login", command=self.check_user, style="primary")
        login_btn.pack(side=tk.LEFT, padx=5)
        
        back_btn = ModernButton(btn_frame, text="Back", command=self.go_back, style="secondary")
        back_btn.pack(side=tk.LEFT, padx=5)
        
        # Forgot password link
        forgot_frame = ModernFrame(main_frame, bg=COLORS["bg_primary"])
        forgot_frame.pack(pady=20)
        
        forgot_btn = tk.Button(
            forgot_frame, text="Forgot Password?", 
            bg=COLORS["bg_primary"], fg=COLORS["accent"],
            font=("Arial", 10, "underline"), bd=0, cursor="hand2",
            activebackground=COLORS["bg_primary"], activeforeground="#ff5976",
            command=self.forgot_password
        )
        forgot_btn.pack()

    def check_user(self):
        un = self.entry_username.get().strip()
        pw = self.entry_password.get().strip()
        if un in USERS and USERS[un]["password"] == pw and USERS[un]["role"] == "user":
            messagebox.showinfo("Success", "User login successful!")
            self.root.destroy()
            run_main_app(is_admin=False, username=un)
        else:
            messagebox.showerror("Error", "Invalid user credentials.")

    def go_back(self):
        self.root.destroy()
        root_main = tk.Tk()
        LoginHomeScreen(root_main)
        root_main.mainloop()

    def forgot_password(self):
        """Send OTP to the same email used for admin. Then ask user to enter OTP. If correct => reset password to 'user123'."""
        global USER_FORGOT_OTP
        USER_FORGOT_OTP = generate_otp_code(6)
        if send_otp_via_email(EMAIL_SENDER, USER_FORGOT_OTP):
            messagebox.showinfo("OTP Sent", "Check your email for the OTP. Then enter it in the next prompt.")
            # Show a small OTP pop
            ForgotPasswordOTPDialog(self.root)
        else:
            messagebox.showerror("Error", "Failed to send OTP. Please check your internet connection.")

class ForgotPasswordOTPDialog:
    """Modern OTP verification dialog."""
    def __init__(self, parent):
        self.top = tk.Toplevel(parent)
        self.top.title("OTP Verification")
        self.top.geometry("400x300")
        self.top.configure(bg=COLORS["bg_primary"])
        
        # Center the dialog
        self.top.transient(parent)
        self.top.grab_set()
        
        main_frame = ModernFrame(self.top, bg=COLORS["bg_primary"])
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        ModernLabel(main_frame, text="Enter OTP Code", size=18, weight="bold").pack(pady=(40, 20))
        ModernLabel(main_frame, text="A 6-digit code has been sent to your email", size=10, style="secondary").pack()
        
        self.entry_otp = ModernEntry(main_frame, width=15)
        self.entry_otp.pack(pady=30)
        
        ModernButton(main_frame, text="Verify", command=self.verify_otp, style="primary").pack()

    def verify_otp(self):
        global USER_FORGOT_OTP
        user_otp = self.entry_otp.get().strip()
        if user_otp == USER_FORGOT_OTP:
            if "user" in USERS:
                USERS["user"]["password"] = "user123"
                save_persistent_data()
            messagebox.showinfo("Success", "Password reset successful!\nNew password: 'user123'")
            self.top.destroy()
        else:
            messagebox.showerror("Error", "Invalid OTP code.")

class AdminLoginScreen:
    """Modern admin login screen."""
    def __init__(self, root):
        self.root = root
        self.root.title("Admin Login - Garuda Security")
        self.root.geometry("500x600")
        self.root.configure(bg=COLORS["bg_primary"])
        
        # Main container
        main_frame = ModernFrame(self.root, bg=COLORS["bg_primary"])
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Header
        header_frame = ModernFrame(main_frame, bg=COLORS["bg_primary"])
        header_frame.pack(pady=(60, 40))
        
        ModernLabel(header_frame, text="ADMIN LOGIN", size=24, weight="bold").pack()
        ModernLabel(header_frame, text="Authorized personnel only", size=12, style="secondary").pack(pady=(10, 0))
        
        # Warning
        warning_frame = ModernFrame(main_frame, bg=COLORS["error"])
        warning_frame.pack(fill=tk.X, padx=50, pady=(0, 20))
        
        ModernLabel(
            warning_frame, text="Two-Factor Authentication Required",
            size=10, bg=COLORS["error"]
        ).pack(pady=10)
        
        # Login form
        form_frame = ModernFrame(main_frame, bg=COLORS["bg_secondary"], border=1)
        form_frame.pack(padx=50, pady=20)
        
        inner_frame = ModernFrame(form_frame, bg=COLORS["bg_secondary"])
        inner_frame.pack(padx=40, pady=40)
        
        ModernLabel(inner_frame, text="Username", size=11).pack(anchor="w", pady=(0, 5))
        self.entry_username = ModernEntry(inner_frame, width=25)
        self.entry_username.pack(pady=(0, 20))
        
        ModernLabel(inner_frame, text="Password", size=11).pack(anchor="w", pady=(0, 5))
        self.entry_password = ModernEntry(inner_frame, show="*", width=25)
        self.entry_password.pack(pady=(0, 30))
        
        # Buttons
        btn_frame = ModernFrame(inner_frame, bg=COLORS["bg_secondary"])
        btn_frame.pack()
        
        login_btn = ModernButton(btn_frame, text="Login", command=self.check_admin, style="warning")
        login_btn.pack(side=tk.LEFT, padx=5)
        
        back_btn = ModernButton(btn_frame, text="Back", command=self.go_back, style="secondary")
        back_btn.pack(side=tk.LEFT, padx=5)

    def check_admin(self):
        un = self.entry_username.get().strip()
        pw = self.entry_password.get().strip()
        if un in USERS and USERS[un]["password"] == pw and USERS[un]["role"] == "admin":
            global ADMIN_OTP
            ADMIN_OTP = generate_otp_code(6)
            if send_otp_via_email(EMAIL_SENDER, ADMIN_OTP):
                messagebox.showinfo("OTP Sent", "Check your email for the OTP.")
                self.root.destroy()
                root_otp = tk.Tk()
                AdminOTPFrame(root_otp, username=un)
                root_otp.mainloop()
            else:
                messagebox.showerror("Error", "Failed to send OTP. Please check your internet connection.")
        else:
            messagebox.showerror("Error", "Invalid admin credentials.")

    def go_back(self):
        self.root.destroy()
        root_main = tk.Tk()
        LoginHomeScreen(root_main)
        root_main.mainloop()

class AdminOTPFrame:
    """Modern admin OTP verification screen."""
    def __init__(self, root, username="admin"):
        self.root = root
        self.username = username
        self.root.title("Admin OTP Verification - Garuda Security")
        self.root.geometry("500x400")
        self.root.configure(bg=COLORS["bg_primary"])
        
        # Main container
        main_frame = ModernFrame(self.root, bg=COLORS["bg_primary"])
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Header
        header_frame = ModernFrame(main_frame, bg=COLORS["bg_primary"])
        header_frame.pack(pady=(60, 40))
        
        ModernLabel(header_frame, text="TWO-FACTOR AUTHENTICATION", size=20, weight="bold").pack()
        ModernLabel(header_frame, text="Enter the 6-digit code sent to your email", size=12, style="secondary").pack(pady=(10, 0))
        
        # OTP form
        form_frame = ModernFrame(main_frame, bg=COLORS["bg_secondary"], border=1)
        form_frame.pack(padx=50, pady=20)
        
        inner_frame = ModernFrame(form_frame, bg=COLORS["bg_secondary"])
        inner_frame.pack(padx=40, pady=40)
        
        ModernLabel(inner_frame, text="OTP Code", size=11).pack(anchor="w", pady=(0, 5))
        self.entry_otp = ModernEntry(inner_frame, width=20)
        self.entry_otp.pack(pady=(0, 30))
        
        verify_btn = ModernButton(inner_frame, text="Verify OTP", command=self.verify_otp, style="warning")
        verify_btn.pack()
        
        # Security note
        note_frame = ModernFrame(main_frame, bg=COLORS["bg_primary"])
        note_frame.pack(pady=20)
        
        ModernLabel(
            note_frame, 
            text="For security reasons, this code expires in 5 minutes",
            size=9, style="secondary"
        ).pack()

    def verify_otp(self):
        global ADMIN_OTP
        user_otp = self.entry_otp.get().strip()
        if user_otp == ADMIN_OTP:
            messagebox.showinfo("Success", "Authentication successful!")
            self.root.destroy()
            run_main_app(is_admin=True, username=self.username)
        else:
            messagebox.showerror("Error", "Invalid OTP code.")

##############################################################################
# MAIN
##############################################################################
if __name__ == "__main__":
    try:
        # Start with the main login home screen
        root = tk.Tk()
        LoginHomeScreen(root)
        root.mainloop()
    except Exception as e:
        print(f"Error starting application: {str(e)}")
        import traceback
        traceback.print_exc()
