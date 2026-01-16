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

import threading
import tkinter as tk
from tkinter import ttk, messagebox

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

# Logging
system_updates_log = []   # System or admin updates
voice_assistant_log = []  # What Narada hears
voice_responses = []      # Narada's responses to user
latest_detection_info = ""  # Updated by GStreamer callback

# OTP / Emails
ADMIN_OTP = None
USER_FORGOT_OTP = None
EMAIL_SENDER = "mgonugondlamanikanta@gmail.com"
EMAIL_SENDER_PASS = "nhxc zjtl azxm iixw"
EMAIL_RECIPIENTS = ["amarmanikantan@gmail.com"]
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

##############################################################################
# HELPER & LOGGING FUNCTIONS
##############################################################################
def log_system_update(message):
    """Append a system update message to the system updates log."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    system_updates_log.append(f"[{timestamp}] {message}")

def append_voice_log(message, user_name=None):
    """Append a message to the Narada voice log. Optionally track user_name's Narada activity."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {message}"
    voice_assistant_log.append(entry)

    # Also store in user history if provided
    if user_name and user_name in USERS:
        USERS[user_name]["history"]["narada_activity"].append(entry)

def append_voice_response(message, user_name=None):
    """Append a response from Narada to the voice responses list. Also track user_name's Narada activity."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {message}"
    voice_responses.append(entry)

    if user_name and user_name in USERS:
        USERS[user_name]["history"]["narada_activity"].append(entry)

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
    except Exception as e:
        log_system_update(f"Failed to send OTP => {str(e)}")

##############################################################################
# DETECTIONS & ALERTS
##############################################################################
def beep_and_red_led():
    if MODE_DND or MODE_IDLE:
        log_system_update("Alert skipped (DND/Idle).")
        return

    # Even if DND is on, if NIGHT or EMERGENCY are highest priority, they override
    # but you specifically said if DND is on, it's silent. So let's keep that logic.
    # If you want night/emergency to override DND, remove the early return above.

    if MODE_NIGHT:
        try:
            with open(NIGHT_MODE_LOG_FILE, "a") as f:
                f.write(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
        except Exception as e:
            log_system_update("Error logging night mode incident: " + str(e))

    duration = 5
    if MODE_NIGHT:
        duration = 10
    if MODE_EMERGENCY:
        duration = 15

    red_led.on()
    buzzer.on()
    time.sleep(duration)
    buzzer.off()
    red_led.off()

def send_email_alert():
    global last_email_sent_time
    if MODE_EMAIL_OFF or MODE_IDLE:
        log_system_update("Email alert skipped (EmailOff/Idle).")
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
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(EMAIL_SENDER, EMAIL_SENDER_PASS)
        server.send_message(msg)
        server.quit()
        log_system_update("Email alert sent.")
    except Exception as e:
        log_system_update(f"Failed sending email => {str(e)}")

def log_scissors_detection():
    now = datetime.datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{stamp}] SCISSORS DETECTED\n"
    with open(SCISSORS_LOG_FILE, "a") as f:
        f.write(entry)
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
        text_info += f"{label} detected (conf={confidence:.2f})\n"
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
    "weather"                : "Reports simple weather snippet",
    "hi / hello"             : "Greets the user",
    "how are you"            : "Narada status update",
    "what's your name"       : "Narada introduction",
    "time"                   : "Tells the current time",
    # etc...
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

        # 2) If no custom command matched, check built-in logic
        if response is None:
            # We'll do direct logic (like your original code)
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
                # You mentioned "upon asking what, narada responds what." 
                response = "I heard you say 'what'? I'm here to help!"
            else:
                response = "I'm sorry, I'm still learning new commands."

        # Priority override logic for modes:
        # If EMERGENCY or NIGHT is turned on, it can override DND, etc. 
        # (If you want that, you'd forcibly set DND=FALSE if EMERGENCY=TRUE, etc.)
        if MODE_EMERGENCY:
            MODE_DND = False  # Example override
        if MODE_NIGHT:
            MODE_DND = False  # Example override

        append_voice_response(response, user_name=current_user)

        # If there's a dashboard, sync checkbuttons
        if dashboard_gui:
            dashboard_gui.sync_mode_checkbuttons()

        time.sleep(1)

##############################################################################
# USER DASHBOARD (Tabbed) - For "user" role
##############################################################################
class UserDashboardGUI:
    def __init__(self, root, user_data, username="user"):
        self.root = root
        self.username = username
        self.root.title("Garuda - User Mode")
        self.root.geometry("1050x750")

        style = ttk.Style(self.root)
        style.theme_use("clam")

        self.user_data = user_data

        # We'll store the after_id so we can cancel it on logout
        self.after_id = None

        # Notebook
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Tab 1: AI Detections
        self.detections_tab = ttk.Frame(self.notebook)
        self.build_detections_tab(self.detections_tab)
        self.notebook.add(self.detections_tab, text="AI Detections")

        # Tab 2: Narada
        self.narada_tab = ttk.Frame(self.notebook)
        self.build_narada_tab(self.narada_tab)
        self.notebook.add(self.narada_tab, text="Narada")

        # Add an instructions button in user mode
        instr_btn = ttk.Button(self.root, text="Instructions", command=self.show_instructions)
        instr_btn.pack(side=tk.LEFT, padx=5, pady=5)

        # Logout Button
        logout_btn = ttk.Button(self.root, text="Logout", command=self.logout)
        logout_btn.pack(side=tk.RIGHT, pady=5, padx=5)

        # Voice assistant
        self.voice_thread = None
        self.voice_stop_event = None
        self.voice_assistant_running = False

        # Start update
        self.update_gui()

        # Track user login time for user
        if username in USERS:
            USERS[username]["history"]["logins"].append(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def build_detections_tab(self, parent):
        modes_frame = ttk.LabelFrame(parent, text="Modes")
        modes_frame.pack(fill=tk.X, padx=5, pady=5)

        self.var_dnd = tk.BooleanVar(value=MODE_DND)
        self.var_email_off = tk.BooleanVar(value=MODE_EMAIL_OFF)
        self.var_idle = tk.BooleanVar(value=MODE_IDLE)
        self.var_night = tk.BooleanVar(value=MODE_NIGHT)
        self.var_emergency = tk.BooleanVar(value=MODE_EMERGENCY)

        ttk.Checkbutton(modes_frame, text="DND", variable=self.var_dnd, command=self.toggle_dnd).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(modes_frame, text="Email Off", variable=self.var_email_off, command=self.toggle_email).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(modes_frame, text="Idle", variable=self.var_idle, command=self.toggle_idle).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(modes_frame, text="Night", variable=self.var_night, command=self.toggle_night).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(modes_frame, text="Emergency", variable=self.var_emergency, command=self.toggle_emergency).pack(side=tk.LEFT, padx=5)

        # Detection info
        self.detection_text = tk.Text(parent, height=12, wrap="word", bg="white")
        sc_det = tk.Scrollbar(parent, command=self.detection_text.yview)
        self.detection_text.configure(yscrollcommand=sc_det.set)
        self.detection_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sc_det.pack(side=tk.LEFT, fill=tk.Y)

        # Console log
        self.console_text = tk.Text(parent, height=12, wrap="word", bg="white")
        sc_con = tk.Scrollbar(parent, command=self.console_text.yview)
        self.console_text.configure(yscrollcommand=sc_con.set)
        self.console_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sc_con.pack(side=tk.LEFT, fill=tk.Y)

    def build_narada_tab(self, parent):
        ctrl_frame = ttk.Frame(parent)
        ctrl_frame.pack(fill=tk.X, padx=10, pady=5)

        self.btn_voice = ttk.Button(ctrl_frame, text="Start Narada", command=self.toggle_voice)
        self.btn_voice.pack(side=tk.LEFT, padx=5)

        self.lbl_status = ttk.Label(ctrl_frame, text="Status: Not Listening")
        self.lbl_status.pack(side=tk.LEFT, padx=10)

        self.listening_indicator = ttk.Label(ctrl_frame, text="●", font=("Helvetica", 20), foreground="red")
        self.listening_indicator.pack(side=tk.LEFT, padx=5)

        self.btn_clear_log = ttk.Button(ctrl_frame, text="Clear Log", command=self.clear_narada_log)
        self.btn_clear_log.pack(side=tk.LEFT, padx=5)

        # Narada logs
        self.voice_text = tk.Text(parent, height=10, wrap="word", bg="lightyellow")
        self.voice_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Response area
        self.response_text = tk.Text(parent, height=5, wrap="word", bg="white", fg="grey")
        self.response_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    # ====================== Periodic Update =======================
    def update_gui(self):
        update_text_widget(self.detection_text, latest_detection_info)
        update_text_widget(self.console_text, "\n".join(system_updates_log[-20:]))

        update_text_widget(self.voice_text, "\n".join(voice_assistant_log))
        if not self.voice_assistant_running:
            update_text_widget(self.response_text, "Narada is not listening.\n")
        else:
            update_text_widget(self.response_text, "\n".join(voice_responses))

        self.sync_mode_checkbuttons()

        self.after_id = self.root.after(1000, self.update_gui)

    def sync_mode_checkbuttons(self):
        self.var_dnd.set(MODE_DND)
        self.var_email_off.set(MODE_EMAIL_OFF)
        self.var_idle.set(MODE_IDLE)
        self.var_night.set(MODE_NIGHT)
        self.var_emergency.set(MODE_EMERGENCY)

    # ====================== Mode Toggles ==========================
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

    # ====================== Narada Control ========================
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
            self.listening_indicator.config(foreground="green")
            append_voice_log("Narada started by user.", user_name=self.username)
        else:
            if self.voice_stop_event:
                self.voice_stop_event.set()
            self.voice_assistant_running = False
            self.btn_voice.config(text="Start Narada")
            self.lbl_status.config(text="Status: Not Listening")
            self.listening_indicator.config(foreground="red")
            append_voice_log("Narada stopped by user.", user_name=self.username)

    def clear_narada_log(self):
        global voice_assistant_log, voice_responses
        voice_assistant_log = []
        voice_responses = []
        update_text_widget(self.voice_text, "")
        update_text_widget(self.response_text, "")
        append_voice_log("Narada log cleared.", user_name=self.username)

    def logout(self):
        if self.after_id:
            self.root.after_cancel(self.after_id)
        self.root.destroy()
        # Return to main screen
        root_main = tk.Tk()
        LoginHomeScreen(root_main)
        root_main.mainloop()

    def show_instructions(self):
        instructions = (
            "Narada Instructions:\n\n"
            "- 'activate dnd', 'deactivate dnd'\n"
            "- 'activate email off', 'deactivate email off'\n"
            "- 'activate idle', 'deactivate idle'\n"
            "- 'activate night mode', 'deactivate night mode'\n"
            "- 'activate emergency mode', 'deactivate emergency mode'\n"
            "- 'weather', 'time', 'hi', 'hello'\n"
            "- 'what' => special response\n\n"
            "Enjoy Garuda's voice assistant!"
        )
        messagebox.showinfo("Narada Instructions", instructions)

##############################################################################
# ADMIN DASHBOARD (Redesigned with a left sidebar)
##############################################################################
class AdminDashboardGUI:
    def __init__(self, root, user_data, username="admin"):
        self.root = root
        self.username = username
        self.root.title("Garuda - Admin Mode")
        self.root.geometry("1100x750")

        self.user_data = user_data
        self.after_id = None

        # Admin logs a login time
        if username in USERS:
            USERS[username]["history"]["logins"].append(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        # Main container with left sidebar
        self.main_frame = ttk.Frame(self.root)
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        self.sidebar = ttk.Frame(self.main_frame, width=200)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)

        self.content = ttk.Frame(self.main_frame)
        self.content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Build sidebar buttons
        btn_dashboard = ttk.Button(self.sidebar, text="Dashboard", command=self.show_dashboard_page)
        btn_dashboard.pack(fill=tk.X, padx=5, pady=5)

        btn_user_mgmt = ttk.Button(self.sidebar, text="User Management", command=self.show_user_mgmt_page)
        btn_user_mgmt.pack(fill=tk.X, padx=5, pady=5)

        btn_logs = ttk.Button(self.sidebar, text="Logs & Monitoring", command=self.show_logs_page)
        btn_logs.pack(fill=tk.X, padx=5, pady=5)

        btn_hardware = ttk.Button(self.sidebar, text="Hardware Status", command=self.show_hardware_page)
        btn_hardware.pack(fill=tk.X, padx=5, pady=5)

        btn_narada = ttk.Button(self.sidebar, text="Narada Commands", command=self.show_narada_page)
        btn_narada.pack(fill=tk.X, padx=5, pady=5)

        btn_modes = ttk.Button(self.sidebar, text="Mode Management", command=self.show_modes_page)
        btn_modes.pack(fill=tk.X, padx=5, pady=5)

        btn_email = ttk.Button(self.sidebar, text="Email Settings", command=self.show_email_page)
        btn_email.pack(fill=tk.X, padx=5, pady=5)

        btn_instr = ttk.Button(self.sidebar, text="Instructions", command=self.show_instructions)
        btn_instr.pack(fill=tk.X, padx=5, pady=5)

        btn_logout = ttk.Button(self.sidebar, text="Logout", command=self.logout)
        btn_logout.pack(fill=tk.X, padx=5, pady=5)

        # We'll create pages in self.content
        self.pages = {}
        self.build_dashboard_page()
        self.build_user_mgmt_page()
        self.build_logs_page()
        self.build_hardware_page()
        self.build_narada_page()
        self.build_modes_page()
        self.build_email_page()

        # Show the default page
        self.show_dashboard_page()

        # Start periodic update
        self.update_gui()

    # ==================== PAGES ====================
    def build_dashboard_page(self):
        frame = ttk.Frame(self.content)
        self.pages["dashboard"] = frame

        lbl = tk.Label(frame, text="Admin Dashboard", font=("Helvetica", 16, "bold"))
        lbl.pack(pady=5)

        # detection info
        self.det_text = tk.Text(frame, height=10, wrap="word", bg="white")
        sc1 = tk.Scrollbar(frame, command=self.det_text.yview)
        self.det_text.configure(yscrollcommand=sc1.set)
        self.det_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        sc1.pack(side=tk.LEFT, fill=tk.Y)

        # console info
        self.console_text = tk.Text(frame, height=10, wrap="word", bg="white")
        sc2 = tk.Scrollbar(frame, command=self.console_text.yview)
        self.console_text.configure(yscrollcommand=sc2.set)
        self.console_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        sc2.pack(side=tk.LEFT, fill=tk.Y)

        # modes
        mode_frame = ttk.LabelFrame(frame, text="Modes")
        mode_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)

        self.var_dnd = tk.BooleanVar(value=MODE_DND)
        self.var_email_off = tk.BooleanVar(value=MODE_EMAIL_OFF)
        self.var_idle = tk.BooleanVar(value=MODE_IDLE)
        self.var_night = tk.BooleanVar(value=MODE_NIGHT)
        self.var_emergency = tk.BooleanVar(value=MODE_EMERGENCY)

        ttk.Checkbutton(mode_frame, text="DND", variable=self.var_dnd, command=self.toggle_dnd).pack(anchor="w", pady=2)
        ttk.Checkbutton(mode_frame, text="Email Off", variable=self.var_email_off, command=self.toggle_email).pack(anchor="w", pady=2)
        ttk.Checkbutton(mode_frame, text="Idle", variable=self.var_idle, command=self.toggle_idle).pack(anchor="w", pady=2)
        ttk.Checkbutton(mode_frame, text="Night", variable=self.var_night, command=self.toggle_night).pack(anchor="w", pady=2)
        ttk.Checkbutton(mode_frame, text="Emergency", variable=self.var_emergency, command=self.toggle_emergency).pack(anchor="w", pady=2)

    def build_user_mgmt_page(self):
        frame = ttk.Frame(self.content)
        self.pages["user_mgmt"] = frame

        tk.Label(frame, text="User Management", font=("Helvetica", 16, "bold")).pack()

        # Left side: user list
        left_side = ttk.Frame(frame)
        left_side.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        self.user_listbox = tk.Listbox(left_side, width=20)
        self.user_listbox.pack(side=tk.LEFT, fill=tk.Y)
        scr = ttk.Scrollbar(left_side, command=self.user_listbox.yview)
        self.user_listbox.configure(yscrollcommand=scr.set)
        scr.pack(side=tk.LEFT, fill=tk.Y)

        self.user_listbox.bind("<<ListboxSelect>>", self.on_select_user)

        self.refresh_user_list()

        # Middle: user creation
        middle_frame = ttk.Frame(frame)
        middle_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        ttk.Label(middle_frame, text="Username:").pack()
        self.entry_uname = ttk.Entry(middle_frame)
        self.entry_uname.pack()
        ttk.Label(middle_frame, text="Password:").pack()
        self.entry_pass = ttk.Entry(middle_frame, show="*")
        self.entry_pass.pack()

        ttk.Button(middle_frame, text="Add User", command=self.add_user).pack(pady=5)
        ttk.Button(middle_frame, text="Delete Selected", command=self.delete_user).pack(pady=5)
        ttk.Button(middle_frame, text="Reset Password", command=self.reset_password).pack(pady=5)
        ttk.Button(middle_frame, text="Rename User", command=self.rename_user).pack(pady=5)

        # Right side: user profile
        self.profile_frame = ttk.Frame(frame)
        self.profile_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        ttk.Label(self.profile_frame, text="Select a user to see details.").pack()

    def build_logs_page(self):
        frame = ttk.Frame(self.content)
        self.pages["logs"] = frame

        tk.Label(frame, text="System Logs & Monitoring", font=("Helvetica", 16, "bold")).pack()

        self.logs_text = tk.Text(frame, wrap="word", bg="white")
        scr = ttk.Scrollbar(frame, command=self.logs_text.yview)
        self.logs_text.configure(yscrollcommand=scr.set)
        self.logs_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        scr.pack(side=tk.LEFT, fill=tk.Y)

        btn_clr = ttk.Button(frame, text="Clear Logs", command=self.clear_logs)
        btn_clr.pack(side=tk.BOTTOM, pady=5)

    def build_hardware_page(self):
        frame = ttk.Frame(self.content)
        self.pages["hardware"] = frame

        tk.Label(frame, text="Hardware Status", font=("Helvetica", 16, "bold")).pack()

        self.label_cpu = ttk.Label(frame, text="CPU: N/A")
        self.label_cpu.pack(pady=5)
        self.label_mem = ttk.Label(frame, text="Mem: N/A")
        self.label_mem.pack(pady=5)
        self.label_disk = ttk.Label(frame, text="Disk: N/A")
        self.label_disk.pack(pady=5)

        self.label_gpio = ttk.Label(frame, text="GPIO: [LED=OFF, Buzzer=OFF]")
        self.label_gpio.pack(pady=5)

    def build_narada_page(self):
        frame = ttk.Frame(self.content)
        self.pages["narada"] = frame

        tk.Label(frame, text="Narada Commands & Customization", font=("Helvetica", 16, "bold")).pack(pady=5)

        # Show built-in commands
        lbl_bi = ttk.Label(frame, text="Built-in Commands (Read-only):", font=("Helvetica", 12, "underline"))
        lbl_bi.pack()
        self.list_built_in = tk.Listbox(frame, width=60, height=8)
        self.list_built_in.pack(pady=5)
        for phrase, desc in BUILT_IN_COMMANDS.items():
            self.list_built_in.insert(tk.END, f"[BUILT-IN] {phrase} => {desc}")

        # Show custom commands
        lbl_cc = ttk.Label(frame, text="Custom Commands:", font=("Helvetica", 12, "underline"))
        lbl_cc.pack()
        self.list_custom_cmds = tk.Listbox(frame, width=60, height=8)
        self.list_custom_cmds.pack(pady=5)
        self.refresh_custom_cmds()

        frm_add = ttk.Frame(frame)
        frm_add.pack(pady=5)
        ttk.Label(frm_add, text="Phrase:").grid(row=0, column=0, padx=5)
        self.entry_cmd_phrase = ttk.Entry(frm_add, width=30)
        self.entry_cmd_phrase.grid(row=0, column=1, padx=5)
        ttk.Label(frm_add, text="Response:").grid(row=1, column=0, padx=5)
        self.entry_cmd_response = ttk.Entry(frm_add, width=30)
        self.entry_cmd_response.grid(row=1, column=1, padx=5)

        ttk.Button(frm_add, text="Add Command", command=self.add_command).grid(row=2, column=0, pady=5)
        ttk.Button(frm_add, text="Delete Selected", command=self.del_command).grid(row=2, column=1, pady=5)

    def build_modes_page(self):
        frame = ttk.Frame(self.content)
        self.pages["modes"] = frame

        tk.Label(frame, text="Mode Management", font=("Helvetica", 16, "bold")).pack()

        left_side = ttk.Frame(frame)
        left_side.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)

        tk.Label(left_side, text="Existing Modes:").pack()
        self.custom_modes_listbox = tk.Listbox(left_side, width=30)
        self.custom_modes_listbox.pack(pady=5)
        self.refresh_custom_modes_list()

        frm_btns = ttk.Frame(left_side)
        frm_btns.pack(pady=5)
        ttk.Button(frm_btns, text="Delete Selected Mode", command=self.delete_mode).pack()

        # Right side: create new mode form
        right_side = ttk.Frame(frame)
        right_side.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

        tk.Label(right_side, text="Create / Edit a Custom Mode", font=("Helvetica", 12, "bold")).pack(pady=5)
        frm_form = ttk.Frame(right_side)
        frm_form.pack(pady=5)

        ttk.Label(frm_form, text="Mode Name:").grid(row=0, column=0, padx=5, pady=2)
        self.entry_mode_name = ttk.Entry(frm_form, width=20)
        self.entry_mode_name.grid(row=0, column=1, padx=5, pady=2)

        ttk.Label(frm_form, text="Priority (1-9, Night=10, Emerg=11):").grid(row=1, column=0, padx=5, pady=2)
        self.entry_mode_priority = ttk.Entry(frm_form, width=5)
        self.entry_mode_priority.grid(row=1, column=1, padx=5, pady=2)

        ttk.Button(right_side, text="Save Mode", command=self.save_mode).pack(pady=10)

        tk.Label(right_side, text="(Night=priority 10, Emergency=11, overrides lower modes)").pack()

    def build_email_page(self):
        frame = ttk.Frame(self.content)
        self.pages["email"] = frame

        tk.Label(frame, text="Email Settings", font=("Helvetica", 16, "bold")).pack(pady=5)

        frm_es = ttk.Frame(frame)
        frm_es.pack(pady=10)

        ttk.Label(frm_es, text="Recipients (comma):").grid(row=0, column=0, padx=5, pady=5)
        self.entry_recipients = ttk.Entry(frm_es, width=40)
        self.entry_recipients.grid(row=0, column=1, padx=5, pady=5)
        self.entry_recipients.insert(0, ", ".join(EMAIL_RECIPIENTS))

        ttk.Label(frm_es, text="Cooldown (sec):").grid(row=1, column=0, padx=5, pady=5)
        self.entry_cooldown = ttk.Entry(frm_es, width=10)
        self.entry_cooldown.grid(row=1, column=1, padx=5, pady=5)
        self.entry_cooldown.insert(0, str(EMAIL_COOLDOWN))

        ttk.Button(frm_es, text="Save", command=self.save_email_settings).grid(row=2, column=0, columnspan=2, pady=10)

    # ==================== Page Switch ====================
    def show_dashboard_page(self):
        self._hide_all_pages()
        self.pages["dashboard"].pack(fill=tk.BOTH, expand=True)

    def show_user_mgmt_page(self):
        self._hide_all_pages()
        self.pages["user_mgmt"].pack(fill=tk.BOTH, expand=True)

    def show_logs_page(self):
        self._hide_all_pages()
        self.pages["logs"].pack(fill=tk.BOTH, expand=True)

    def show_hardware_page(self):
        self._hide_all_pages()
        self.pages["hardware"].pack(fill=tk.BOTH, expand=True)

    def show_narada_page(self):
        self._hide_all_pages()
        self.pages["narada"].pack(fill=tk.BOTH, expand=True)

    def show_modes_page(self):
        self._hide_all_pages()
        self.pages["modes"].pack(fill=tk.BOTH, expand=True)

    def show_email_page(self):
        self._hide_all_pages()
        self.pages["email"].pack(fill=tk.BOTH, expand=True)

    def show_instructions(self):
        instructions = (
            "Admin Mode Instructions:\n\n"
            "Use the left sidebar to navigate:\n"
            "- Dashboard: Quick view of AI detections, console, and basic modes.\n"
            "- User Management: Add/delete/rename users, reset passwords.\n"
            "- Logs & Monitoring: See system logs, can clear them.\n"
            "- Hardware Status: CPU/mem/disk usage, GPIO states.\n"
            "- Narada Commands: Built-in commands (read-only) + custom commands (editable).\n"
            "- Mode Management: Create new custom modes with a priority.\n"
            "- Email Settings: Recipients & cooldown for alert emails.\n\n"
            "Changes here affect all users. Enjoy!"
        )
        messagebox.showinfo("Admin Instructions", instructions)

    def _hide_all_pages(self):
        for p in self.pages.values():
            p.pack_forget()

    # ==================== Periodic Update ====================
    def update_gui(self):
        # Dashboard
        if self.pages["dashboard"].winfo_ismapped():
            update_text_widget(self.det_text, latest_detection_info)
            update_text_widget(self.console_text, "\n".join(system_updates_log[-20:]))

            self.sync_mode_checkbuttons()

        # Logs page
        if self.pages["logs"].winfo_ismapped():
            self.logs_text.config(state="normal")
            self.logs_text.delete("1.0", tk.END)
            self.logs_text.insert(tk.END, "\n".join(system_updates_log[-100:]))
            self.logs_text.config(state="disabled")

        # Hardware page
        if psutil and self.pages["hardware"].winfo_ismapped():
            cpu = psutil.cpu_percent()
            mem = psutil.virtual_memory().percent
            disk = psutil.disk_usage('/').percent

            # Optional: show small "btop-like" bar
            cpu_bar = self._usage_bar(cpu)
            mem_bar = self._usage_bar(mem)
            disk_bar = self._usage_bar(disk)

            self.label_cpu.config(text=f"CPU: {cpu}% {cpu_bar}")
            self.label_mem.config(text=f"Mem: {mem}% {mem_bar}")
            self.label_disk.config(text=f"Disk: {disk}% {disk_bar}")

            led_state = "ON" if red_led and red_led.is_lit else "OFF"
            buzzer_state = "ON" if buzzer and buzzer.value == 1 else "OFF"
            self.label_gpio.config(text=f"GPIO: [LED={led_state}, Buzzer={buzzer_state}]")

        self.after_id = self.root.after(1000, self.update_gui)

    def _usage_bar(self, value, length=20):
        """Generate a text-based usage bar (like btop) for CPU/mem/disk usage."""
        bars = int((value / 100) * length)
        return "[" + "#"*bars + "-"*(length-bars) + "]"

    # ==================== Dashboard Mode Checkbuttons ====================
    def sync_mode_checkbuttons(self):
        self.var_dnd.set(MODE_DND)
        self.var_email_off.set(MODE_EMAIL_OFF)
        self.var_idle.set(MODE_IDLE)
        self.var_night.set(MODE_NIGHT)
        self.var_emergency.set(MODE_EMERGENCY)

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

        # Possibly override DND if NIGHT is on
        # if MODE_NIGHT: MODE_DND = False  # etc.

    def toggle_emergency(self):
        global MODE_EMERGENCY
        MODE_EMERGENCY = self.var_emergency.get()
        log_system_update(f"Admin toggled Emergency => {MODE_EMERGENCY}")
        # if MODE_EMERGENCY: MODE_DND = False

    # ==================== User Management ====================
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
        # rename
        USERS[newname] = USERS.pop(oldname)
        self.refresh_user_list()
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
            ttk.Label(self.profile_frame, text="User not found").pack()
            return

        user_data = USERS[uname]
        ttk.Label(self.profile_frame, text=f"Profile of {uname}", font=("Helvetica", 14, "bold")).pack(pady=5)
        ttk.Label(self.profile_frame, text=f"Role: {user_data['role']}").pack(anchor="w")
        ttk.Label(self.profile_frame, text=f"Password: {user_data['password']}").pack(anchor="w")

        # login history
        logins = user_data["history"].get("logins", [])
        ttk.Label(self.profile_frame, text="Login History:", font=("Helvetica", 12, "underline")).pack(anchor="w")
        for login_time in logins:
            ttk.Label(self.profile_frame, text=f"- {login_time}").pack(anchor="w")

        # narada activity
        narada_act = user_data["history"].get("narada_activity", [])
        ttk.Label(self.profile_frame, text="Narada Activity:", font=("Helvetica", 12, "underline")).pack(anchor="w")
        act_text = tk.Text(self.profile_frame, width=60, height=5, wrap="word")
        act_text.pack(anchor="w")
        act_text.insert(tk.END, "\n".join(narada_act))

    # ==================== Logs ====================
    def clear_logs(self):
        global system_updates_log
        system_updates_log = []
        log_system_update("Admin cleared system logs.")
        messagebox.showinfo("Cleared", "System logs cleared.")

    # ==================== Narada Commands ====================
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

    # ==================== Mode Management ====================
    def refresh_custom_modes_list(self):
        self.custom_modes_listbox.delete(0, tk.END)
        # Show standard modes with priority
        self.custom_modes_listbox.insert(tk.END, "Night (priority=10)")
        self.custom_modes_listbox.insert(tk.END, "Emergency (priority=11)")
        self.custom_modes_listbox.insert(tk.END, "--------")

        for m, data in CUSTOM_MODES.items():
            prio = data.get("priority", 1)
            self.custom_modes_listbox.insert(tk.END, f"{m} (priority={prio})")

    def save_mode(self):
        name = self.entry_mode_name.get().strip().lower()
        prio_str = self.entry_mode_priority.get().strip()
        if not name:
            messagebox.showerror("Error", "Enter mode name.")
            return
        try:
            prio = int(prio_str)
        except:
            prio = 1
        CUSTOM_MODES[name] = {"priority": prio}
        self.refresh_custom_modes_list()
        log_system_update(f"Admin created/updated mode '{name}' priority={prio}")

    def delete_mode(self):
        sel = self.custom_modes_listbox.curselection()
        if not sel:
            return
        line = self.custom_modes_listbox.get(sel[0])
        if line.startswith("Night") or line.startswith("Emergency"):
            messagebox.showerror("Error", "Cannot delete built-in mode.")
            return
        # parse out the mode name
        if "(" in line:
            mode_name = line.split("(")[0].strip().lower()
        else:
            mode_name = line.strip().lower()
        if mode_name in CUSTOM_MODES:
            del CUSTOM_MODES[mode_name]
        self.refresh_custom_modes_list()
        log_system_update(f"Admin deleted custom mode '{mode_name}'")

    # ==================== Email Settings ====================
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
        messagebox.showinfo("Saved", "Email settings updated.")
        log_system_update(f"Admin updated email recipients to {EMAIL_RECIPIENTS}, cooldown={EMAIL_COOLDOWN}")

    # ==================== LOGOUT ====================
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
    """Set up pipeline + GUI for user or admin."""
    global app, red_led, buzzer, stop_button, dashboard_gui

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
# LOGIN SCREENS
##############################################################################
class LoginHomeScreen:
    """Netflix style - select User or Admin, plus an Exit button."""
    def __init__(self, root):
        self.root = root
        self.root.title("Welcome to Garuda Security")
        self.root.geometry("600x400")

        label = tk.Label(self.root, text="Select Your Profile", font=("Helvetica", 24, "bold"))
        label.pack(pady=30)

        container = tk.Frame(self.root)
        container.pack()

        btn_user = tk.Button(container, text="USER", font=("Helvetica", 16), width=10, height=5,
                             bg="lightblue", command=self.goto_user_login)
        btn_admin = tk.Button(container, text="ADMIN", font=("Helvetica", 16), width=10, height=5,
                              bg="orange", command=self.goto_admin_login)
        btn_user.pack(side=tk.LEFT, padx=40)
        btn_admin.pack(side=tk.LEFT, padx=40)

        exit_btn = tk.Button(self.root, text="Exit", font=("Helvetica", 12), bg="red", command=stop_and_exit)
        exit_btn.pack(pady=20)

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
    """User login with a Back and Forgot Password flow."""
    def __init__(self, root):
        self.root = root
        self.root.title("User Login")
        self.root.geometry("400x250")

        tk.Label(root, text="Username:").pack(pady=5)
        self.entry_username = tk.Entry(root)
        self.entry_username.pack(pady=5)

        tk.Label(root, text="Password:").pack(pady=5)
        self.entry_password = tk.Entry(root, show="*")
        self.entry_password.pack(pady=5)

        btn_frame = tk.Frame(root)
        btn_frame.pack(pady=5)
        tk.Button(btn_frame, text="Login", command=self.check_user).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Back", command=self.go_back).pack(side=tk.LEFT, padx=5)

        tk.Button(root, text="Forgot Password?", command=self.forgot_password).pack(pady=5)

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
        send_otp_via_email(EMAIL_SENDER, USER_FORGOT_OTP)
        messagebox.showinfo("OTP Sent", "Check your email for the OTP. Then enter it in the next prompt.")
        # Show a small OTP pop
        ForgotPasswordOTPDialog(self.root)

class ForgotPasswordOTPDialog:
    """Dialog to verify user OTP for password reset."""
    def __init__(self, parent):
        self.top = tk.Toplevel(parent)
        self.top.title("Forgot Password OTP")
        tk.Label(self.top, text="Enter the 6-digit OTP:").pack(pady=5)
        self.entry_otp = tk.Entry(self.top)
        self.entry_otp.pack(pady=5)
        tk.Button(self.top, text="Verify", command=self.verify_otp).pack(pady=5)

    def verify_otp(self):
        global USER_FORGOT_OTP
        user_otp = self.entry_otp.get().strip()
        if user_otp == USER_FORGOT_OTP:
            # Reset the 'user' password => "user123" for demonstration
            if "user" in USERS:
                USERS["user"]["password"] = "user123"
            messagebox.showinfo("Success", "Password reset. New password= 'user123'.")
            self.top.destroy()
        else:
            messagebox.showerror("Error", "Invalid OTP.")

class AdminLoginScreen:
    def __init__(self, root):
        self.root = root
        self.root.title("Admin Login")
        self.root.geometry("400x250")

        tk.Label(root, text="Username:").pack(pady=5)
        self.entry_username = tk.Entry(root)
        self.entry_username.pack(pady=5)

        tk.Label(root, text="Password:").pack(pady=5)
        self.entry_password = tk.Entry(root, show="*")
        self.entry_password.pack(pady=5)

        btn_frame = tk.Frame(root)
        btn_frame.pack(pady=5)
        tk.Button(btn_frame, text="Login", command=self.check_admin).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Back", command=self.go_back).pack(side=tk.LEFT, padx=5)

    def check_admin(self):
        un = self.entry_username.get().strip()
        pw = self.entry_password.get().strip()
        if un in USERS and USERS[un]["password"] == pw and USERS[un]["role"] == "admin":
            global ADMIN_OTP
            ADMIN_OTP = generate_otp_code(6)
            send_otp_via_email(EMAIL_SENDER, ADMIN_OTP)
            messagebox.showinfo("OTP Sent", "Check your email for the OTP.")
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

        tk.Label(root, text="Enter the 6-digit OTP:").pack(pady=5)
        self.entry_otp = tk.Entry(root)
        self.entry_otp.pack(pady=5)

        tk.Button(root, text="Verify OTP", command=self.verify_otp).pack(pady=10)

    def verify_otp(self):
        global ADMIN_OTP
        user_otp = self.entry_otp.get().strip()
        if user_otp == ADMIN_OTP:
            messagebox.showinfo("Success", "Admin OTP correct!")
            self.root.destroy()
            run_main_app(is_admin=True, username=self.username)
        else:
            messagebox.showerror("Error", "Invalid OTP.")

##############################################################################
# MAIN
##############################################################################
if __name__ == "__main__":
    # Start with the main login home screen
    root = tk.Tk()
    LoginHomeScreen(root)
    root.mainloop()
