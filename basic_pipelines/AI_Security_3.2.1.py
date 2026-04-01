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

# Additional imports (serial and re can be removed if not needed)
import serial
import re

# hailo_rpi_common imports
from hailo_rpi_common import (
    get_default_parser,
    QUEUE,
    get_caps_from_pad,
    get_numpy_from_buffer,
    GStreamerApp,
    app_callback_class,
)

# Raspberry Pi / GPIO-related imports
import threading
from gpiozero import LED, Button, OutputDevice

# For the GUI
import tkinter as tk
from tkinter import ttk, messagebox
import PIL  # Only import PIL, as requested

# Import SpeechRecognition for capturing voice input
import speech_recognition as sr

##############################################################################
# GLOBAL VARIABLES, LOGGING, AND STATE
##############################################################################
app = None
state_lock = threading.Lock()
SCISSORS_LOG_FILE = "danger_sightings.txt"

# Global mode flags (default = alerts enabled)
MODE_DND = False         # Disables LED and buzzer alerts only
MODE_EMAIL_OFF = False   # Disables email notifications only
MODE_IDLE = False        # Disables all alerts (LED, buzzer, and email)
MODE_NIGHT = False       # High priority mode: alerts last 10 sec instead of 5, and email subject is prefixed

# File for logging night mode incidents
NIGHT_MODE_LOG_FILE = "night_mode_findings.txt"

# Global logs for Narada (voice assistant)
voice_assistant_log = []   # General log (e.g. "Listening...", recognized phrases)
voice_responses = []       # Fixed responses that will be shown in the Responses section

# Global container for system updates (used in AI Detections tab)
system_updates_log = []

# This variable will be set to the GUI instance so that the voice thread can update checkbuttons.
dashboard_gui = None

# A string for detection info (updated by the pipeline callback)
latest_detection_info = ""

# For emailing / cooldown
last_email_sent_time = 0
EMAIL_COOLDOWN = 60

# GPIO objects
red_led = None
buzzer = None
stop_button = None

##############################################################################
# HELPER FUNCTIONS FOR LOGGING
##############################################################################
def log_system_update(message):
    """Append a system update with timestamp."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    system_updates_log.append(f"[{timestamp}] {message}")

def append_voice_log(message):
    """Append a timestamped entry to the Narada log."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    voice_assistant_log.append(f"[{timestamp}] {message}")

def append_voice_response(message):
    """Append a timestamped entry to the Narada responses."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    voice_responses.append(f"[{timestamp}] {message}")

##############################################################################
# SNAPSHOT FUNCTION (Not Used)
##############################################################################
def take_snapshot(frame, frame_count):
    pass

##############################################################################
# EMAIL ALERT (Scissors)
##############################################################################
def send_email_alert():
    global last_email_sent_time
    if MODE_EMAIL_OFF or MODE_IDLE:
        log_system_update("Email alert skipped due to Email Off/Idle mode.")
        return

    current_time = time.time()
    if (current_time - last_email_sent_time) < EMAIL_COOLDOWN:
        return

    last_email_sent_time = current_time
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    subject = "Scissors Detected Alert"
    if MODE_NIGHT:
        subject = "HIGH PRIORITY: " + subject
    body = f"Detected scissors at {now_str}.\nCheck your environment for safety.\n"
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = "mgonugondlamanikanta@gmail.com"
    msg['To'] = "vishwatejdonkeshwar@gmail.com"

    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login("mgonugondlamanikanta@gmail.com", "nhxc zjtl azxm iixw")
        server.send_message(msg)
        server.quit()
        log_system_update("Email alert sent successfully.")
    except Exception as e:
        print("Failed to send email alert:", e)
        log_system_update(f"Failed sending email => {str(e)}")

##############################################################################
# user_app_callback_class for Hailo
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
# DETECTION CALLBACK
##############################################################################
def app_callback(pad, info, user_data):
    global latest_detection_info
    buffer = info.get_buffer()
    if buffer is None:
        return Gst.PadProbeReturn.OK

    user_data.increment()
    frame_num = user_data.get_count()
    text_info = f"Frame count: {frame_num}\n"

    format_, width, height = get_caps_from_pad(pad)
    if user_data.use_frame and format_ and width and height:
        frame = get_numpy_from_buffer(buffer, format_, width, height)
    else:
        frame = None

    # Check detections
    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)

    for d in detections:
        label = d.get_label()
        confidence = d.get_confidence()
        text_info += f"{label} detected (confidence: {confidence:.2f})\n"
        if label == user_data.danger_label:
            # Trigger alerts if danger is detected
            threading.Thread(target=beep_and_red_led, daemon=True).start()
            threading.Thread(target=log_scissors_detection, daemon=True).start()
            threading.Thread(target=send_email_alert, daemon=True).start()

    user_data.person_detected = any(d.get_label() == "person" for d in detections)

    if frame is not None:
        cv2.putText(
            frame,
            f"Frame: {frame_num}",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            1, (0, 255, 0),
            2
        )
        cv2.putText(
            frame,
            f"{user_data.new_function()} {user_data.new_variable}",
            (10, 60),
            cv2.FONT_HERSHEY_SIMPLEX,
            1, (0, 255, 0),
            2
        )
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        user_data.set_frame(frame)

    latest_detection_info = text_info
    return Gst.PadProbeReturn.OK

##############################################################################
# ALERTS FOR SCISSORS => beep, LED, log
##############################################################################
def beep_and_red_led():
    if MODE_DND or MODE_IDLE:
        log_system_update("Alert (LED/Buzzer) skipped due to DND/Idle mode.")
        return
    # If Night Mode is active, log the incident timestamp separately.
    if MODE_NIGHT:
        try:
            with open(NIGHT_MODE_LOG_FILE, "a") as f:
                f.write(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
        except Exception as e:
            log_system_update("Error logging night mode incident: " + str(e))
    red_led.on()
    buzzer.on()
    duration = 10 if MODE_NIGHT else 5
    time.sleep(duration)
    buzzer.off()
    red_led.off()

def log_scissors_detection():
    now = datetime.datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{stamp}] SCISSORS DETECTED\n"
    with open(SCISSORS_LOG_FILE, "a") as f:
        f.write(entry)
    log_system_update("Scissors detection logged in file.")

##############################################################################
# STOP PIPELINE => EXIT
##############################################################################
def stop_and_exit():
    print("Stopping GStreamer pipeline now...")
    log_system_update("Stopping pipeline & exiting the app.")
    if app is not None:
        app.pipeline.set_state(Gst.State.NULL)
    sys.exit(0)

def button_pressed():
    stop_and_exit()

##############################################################################
# VOICE ASSISTANT LOOP (Fixed-Response Version with Longer Listening Time)
##############################################################################
def voice_assistant_loop(stop_event):
    recognizer = sr.Recognizer()
    try:
        mic = sr.Microphone()  # Using your USB microphone
        append_voice_log("USB Microphone connected.")
    except Exception as e:
        append_voice_log("Error accessing microphone: " + str(e))
        return

    # Calibrate ambient noise
    with mic as source:
        recognizer.adjust_for_ambient_noise(source)
        append_voice_log("Calibrated for ambient noise.")

    while not stop_event.is_set():
        with mic as source:
            append_voice_log("Listening...")
            try:
                # Increase listening time: timeout and phrase_time_limit set to 10 seconds.
                audio = recognizer.listen(source, timeout=10, phrase_time_limit=10)
            except sr.WaitTimeoutError:
                continue
        try:
            user_input = recognizer.recognize_google(audio)
            append_voice_log("You said: " + user_input)
        except sr.UnknownValueError:
            append_voice_log("Could not understand audio.")
            continue
        except sr.RequestError as e:
            append_voice_log("Recognition error: " + str(e))
            continue

        user_input_lower = user_input.lower()
        response = None
        global MODE_DND, MODE_EMAIL_OFF, MODE_IDLE, MODE_NIGHT

        # Check for "deactivate" commands first to avoid substring conflicts:
        if "deactivate dnd" in user_input_lower:
            MODE_DND = False
            response = "DND mode deactivated. LED and buzzer alerts are enabled."
        elif "activate dnd" in user_input_lower:
            MODE_DND = True
            response = "DND mode activated. LED and buzzer alerts are now disabled."
        elif "deactivate email off" in user_input_lower:
            MODE_EMAIL_OFF = False
            response = "Email notifications turned on."
        elif "activate email off" in user_input_lower:
            MODE_EMAIL_OFF = True
            response = "Email notifications turned off."
        elif "deactivate idle" in user_input_lower:
            MODE_IDLE = False
            response = "Idle mode deactivated. Alerts have been restored."
        elif "activate idle" in user_input_lower:
            MODE_IDLE = True
            response = "Idle mode activated. All alerts are disabled."
        elif "deactivate night mode" in user_input_lower:
            MODE_NIGHT = False
            response = "Night mode deactivated. Normal alert settings restored."
        elif "activate night mode" in user_input_lower:
            MODE_NIGHT = True
            response = "Night mode activated. High priority alerts enabled."
        elif "what were your findings last night" in user_input_lower:
            # Activate Night Mode and update GUI
            MODE_NIGHT = True
            if dashboard_gui:
                dashboard_gui.var_night.set(True)
            if os.path.exists(NIGHT_MODE_LOG_FILE):
                with open(NIGHT_MODE_LOG_FILE, "r") as f:
                    lines = [line.strip() for line in f.readlines() if line.strip()]
            else:
                lines = []
            if lines:
                num = len(lines)
                response = (f"Last night, I recorded {num} incident(s) at the following times: " +
                            ", ".join(lines))
            else:
                response = "You are all safe and sound. No incidents were recorded last night."
        elif any(greet in user_input_lower for greet in ["hi", "hello"]):
            response = "Hello Manikanta, I am Narada, your friendly assistant."
        elif "how are you" in user_input_lower:
            response = "I'm doing great, thank you for asking!"
        elif "what's your name" in user_input_lower:
            response = "My name is Narada, your security assistant."
        elif "what time is it" in user_input_lower:
            response = "The current time is " + datetime.datetime.now().strftime("%I:%M %p") + "."
        else:
            response = "I'm sorry, I did not understand that command."

        append_voice_response(response)

        # Update the GUI checkbuttons to reflect any mode changes.
        if dashboard_gui:
            dashboard_gui.var_dnd.set(MODE_DND)
            dashboard_gui.var_email_off.set(MODE_EMAIL_OFF)
            dashboard_gui.var_idle.set(MODE_IDLE)
            dashboard_gui.var_night.set(MODE_NIGHT)

        time.sleep(1)

##############################################################################
# GStreamerDetectionApp (Pipeline)
##############################################################################
from hailo_rpi_common import GStreamerApp
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
# TKINTER "GARUDA v2.5" DASHBOARD WITH AI DETECTIONS AND NARADA TABS
##############################################################################
class SecurityDashboardGUI:
    def __init__(self, root, user_data):
        self.root = root
        self.user_data = user_data

        style = ttk.Style(self.root)
        style.theme_use("clam")

        self.root.title("Garuda")
        self.root.geometry("900x700")

        # Top Banner
        self.banner_frame = ttk.Frame(self.root)
        self.banner_frame.pack(fill=tk.X, padx=10, pady=5)
        self.greeting_label = ttk.Label(self.banner_frame, font=("Helvetica", 18, "bold"))
        self.greeting_label.pack(side=tk.LEFT, padx=10)

        # Notebook with Two Tabs: AI Detections and Narada
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # AI Detections Tab
        self.detections_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.detections_tab, text="AI Detections")

        # PanedWindow for detection details and system updates
        self.info_paned = ttk.Panedwindow(self.detections_tab, orient=tk.VERTICAL)
        self.info_paned.pack(fill=tk.BOTH, expand=True)

        # Detection Details with Scrollbar
        self.detection_frame = tk.Frame(self.info_paned)
        self.detection_text = tk.Text(self.detection_frame, height=15, wrap="word", bg="white", fg="black")
        self.detection_scroll = tk.Scrollbar(self.detection_frame, orient="vertical", command=self.detection_text.yview)
        self.detection_text.configure(yscrollcommand=self.detection_scroll.set)
        self.detection_scroll.pack(side="right", fill="y")
        self.detection_text.pack(side="left", fill="both", expand=True)
        self.info_paned.add(self.detection_frame, weight=2)

        # System Updates (Console) with Scrollbar
        self.console_frame = tk.Frame(self.info_paned)
        self.console_text = tk.Text(self.console_frame, height=6, wrap="word", bg="white", fg="black")
        self.console_scroll = tk.Scrollbar(self.console_frame, orient="vertical", command=self.console_text.yview)
        self.console_text.configure(yscrollcommand=self.console_scroll.set)
        self.console_scroll.pack(side="right", fill="y")
        self.console_text.pack(side="left", fill="both", expand=True)
        self.info_paned.add(self.console_frame, weight=1)

        # Modes Section (in AI Detections Tab)
        self.modes_frame = ttk.LabelFrame(self.detections_tab, text="Modes")
        self.modes_frame.pack(fill=tk.X, padx=10, pady=5)
        self.var_dnd = tk.BooleanVar(value=MODE_DND)
        self.var_email_off = tk.BooleanVar(value=MODE_EMAIL_OFF)
        self.var_idle = tk.BooleanVar(value=MODE_IDLE)
        self.var_night = tk.BooleanVar(value=MODE_NIGHT)
        self.dnd_check = ttk.Checkbutton(self.modes_frame, text="DND Mode", variable=self.var_dnd, command=self.toggle_dnd)
        self.dnd_check.pack(side=tk.LEFT, padx=5, pady=5)
        self.email_check = ttk.Checkbutton(self.modes_frame, text="Email Off Mode", variable=self.var_email_off, command=self.toggle_email)
        self.email_check.pack(side=tk.LEFT, padx=5, pady=5)
        self.idle_check = ttk.Checkbutton(self.modes_frame, text="Idle Mode", variable=self.var_idle, command=self.toggle_idle)
        self.idle_check.pack(side=tk.LEFT, padx=5, pady=5)
        style.configure("Night.TCheckbutton", foreground="blue")
        self.night_check = ttk.Checkbutton(self.modes_frame, text="Night Mode", variable=self.var_night, command=self.toggle_night, style="Night.TCheckbutton")
        self.night_check.pack(side=tk.LEFT, padx=5, pady=5)

        # Narada Tab
        self.narada_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.narada_tab, text="Narada")

        # Narada Control Section (for start/stop)
        self.voice_control_frame = ttk.Frame(self.narada_tab)
        self.voice_control_frame.pack(fill=tk.X, padx=10, pady=5)
        self.voice_button = ttk.Button(self.voice_control_frame, text="Start Narada", command=self.toggle_voice_assistant)
        self.voice_button.pack(side=tk.LEFT, padx=5)
        self.voice_status_label = ttk.Label(self.voice_control_frame, text="Status: Not Listening")
        self.voice_status_label.pack(side=tk.LEFT, padx=10)

        # Narada container occupies 50% of the tab height.
        self.narada_container = tk.Frame(self.narada_tab, height=350)
        self.narada_container.pack(fill=tk.X, padx=10, pady=5)
        self.narada_container.pack_propagate(False)

        # Upper 70% for Narada Log with Scrollbar (approx. 245 pixels)
        self.narada_log_frame = ttk.LabelFrame(self.narada_container, text="Narada Log")
        self.narada_log_frame.place(relx=0, rely=0, relwidth=1, relheight=0.7)
        self.voice_text = tk.Text(self.narada_log_frame, wrap="word", bg="lightyellow")
        self.voice_scroll = tk.Scrollbar(self.narada_log_frame, orient="vertical", command=self.voice_text.yview)
        self.voice_text.configure(yscrollcommand=self.voice_scroll.set)
        self.voice_scroll.pack(side="right", fill="y")
        self.voice_text.pack(side="left", fill="both", expand=True)

        # Lower 30% for Responses with Scrollbar (approx. 105 pixels) with white bg and grey text
        self.response_frame = ttk.LabelFrame(self.narada_container, text="Responses")
        self.response_frame.place(relx=0, rely=0.7, relwidth=1, relheight=0.3)
        self.response_text = tk.Text(self.response_frame, wrap="word", bg="white", fg="grey")
        self.response_scroll = tk.Scrollbar(self.response_frame, orient="vertical", command=self.response_text.yview)
        self.response_text.configure(yscrollcommand=self.response_scroll.set)
        self.response_scroll.pack(side="right", fill="y")
        self.response_text.pack(side="left", fill="both", expand=True)

        # Instructions Button (at the bottom of the Narada tab)
        self.instructions_button = ttk.Button(self.narada_tab, text="Instructions", command=self.show_instructions)
        self.instructions_button.pack(pady=5)

        # Exit Button (common to both tabs)
        self.exit_frame = ttk.Frame(self.root)
        self.exit_frame.pack(fill=tk.X, side=tk.BOTTOM)
        self.exit_button = ttk.Button(self.exit_frame, text="Exit", command=stop_and_exit)
        self.exit_button.pack(pady=10)

        note_label = ttk.Label(self.root, text="Copyright @Garuda 2025", wraplength=800, foreground="gray")
        note_label.pack(pady=5)

        # Narada (voice assistant) thread and stop event initialization
        self.voice_thread = None
        self.voice_stop_event = None
        self.voice_assistant_running = False

        self.update_gui()

    def toggle_dnd(self):
        global MODE_DND
        MODE_DND = self.var_dnd.get()
        state = "activated" if MODE_DND else "deactivated"
        append_voice_log(f"DND mode {state} via GUI.")

    def toggle_email(self):
        global MODE_EMAIL_OFF
        MODE_EMAIL_OFF = self.var_email_off.get()
        state = "activated" if MODE_EMAIL_OFF else "deactivated"
        append_voice_log(f"Email Off mode {state} via GUI.")

    def toggle_idle(self):
        global MODE_IDLE
        MODE_IDLE = self.var_idle.get()
        state = "activated" if MODE_IDLE else "deactivated"
        append_voice_log(f"Idle mode {state} via GUI.")

    def toggle_night(self):
        global MODE_NIGHT
        MODE_NIGHT = self.var_night.get()
        state = "activated" if MODE_NIGHT else "deactivated"
        append_voice_log(f"Night mode {state} via GUI.")

    def toggle_voice_assistant(self):
        if not self.voice_assistant_running:
            self.voice_stop_event = threading.Event()
            self.voice_thread = threading.Thread(target=voice_assistant_loop, args=(self.voice_stop_event,), daemon=True)
            self.voice_thread.start()
            self.voice_assistant_running = True
            self.voice_button.config(text="Stop Narada")
            self.voice_status_label.config(text="Status: Listening...")
            append_voice_log("Narada started via GUI.")
        else:
            if self.voice_stop_event:
                self.voice_stop_event.set()
            self.voice_assistant_running = False
            self.voice_button.config(text="Start Narada")
            self.voice_status_label.config(text="Status: Not Listening")
            append_voice_log("Narada stopped via GUI.")

    def show_instructions(self):
        instructions = (
            "Narada Instructions:\n\n"
            "- Say 'hi' or 'hello' to greet Narada.\n"
            "- Say 'how are you' for a status update.\n"
            "- Say 'what's your name' to know my identity.\n"
            "- Say 'what time is it' to get the current time.\n"
            "- Say 'activate dnd' to enable Do Not Disturb mode (disables LED/Buzzer alerts).\n"
            "- Say 'deactivate dnd' to disable DND mode.\n"
            "- Say 'activate email off' to turn off email notifications.\n"
            "- Say 'deactivate email off' to turn them on.\n"
            "- Say 'activate idle' to disable all alerts.\n"
            "- Say 'deactivate idle' to re-enable alerts.\n"
            "- Say 'activate night mode' to enable high priority mode (alerts last 10 seconds).\n"
            "- Say 'deactivate night mode' to disable night mode.\n"
            "- Say 'what were your findings last night' to hear a summary of recorded night incidents.\n"
        )
        messagebox.showinfo("Narada Instructions", instructions)

    def update_narada_tab(self):
        # Update Narada Log Section
        self.voice_text.configure(state="normal")
        self.voice_text.delete("1.0", tk.END)
        for msg in voice_assistant_log[-50:]:
            self.voice_text.insert(tk.END, msg + "\n")
        self.voice_text.configure(state="disabled")

        # Update Responses Section
        self.response_text.configure(state="normal")
        self.response_text.delete("1.0", tk.END)
        if not self.voice_assistant_running:
            self.response_text.insert(tk.END, "Narada is sleeping. Please wake Narada to receive responses.\n")
        else:
            for msg in voice_responses[-50:]:
                self.response_text.insert(tk.END, msg + "\n")
        self.response_text.configure(state="disabled")

    def update_gui(self):
        now = datetime.datetime.now()
        greeting = "Good Morning" if now.hour < 12 else "Good Afternoon" if now.hour < 18 else "Good Evening"
        self.greeting_label.config(text=f"Dear Manikanta, {greeting}! Welcome to Garuda v2.5.")

        # Update Detection Details
        self.detection_text.configure(state="normal")
        self.detection_text.delete("1.0", tk.END)
        for line in latest_detection_info.strip().split("\n"):
            if "detected" in line:
                self.detection_text.insert(tk.END, line + "\n", ("detection_label",))
            else:
                self.detection_text.insert(tk.END, line + "\n", ("detection_other",))
        self.detection_text.configure(state="disabled")

        # Update System Updates (Console)
        self.console_text.configure(state="normal")
        self.console_text.delete("1.0", tk.END)
        for msg in system_updates_log[-20:]:
            self.console_text.insert(tk.END, msg + "\n", ("system_update",))
        self.console_text.configure(state="disabled")

        # Update Narada tab (logs and responses)
        self.update_narada_tab()

        self.root.after(100, self.update_gui)

##############################################################################
# MAIN
##############################################################################
if __name__ == "__main__":
    Gst.init(None)
    print("Initializing GPIO devices...")
    log_system_update("Initializing GPIO devices...")

    # Create user_data
    user_data = user_app_callback_class()

    # Initialize only red LED & Buzzer
    red_led = LED(20)
    red_led.off()
    buzzer = OutputDevice(pin=12, initial_value=False)

    # Push button => stop pipeline
    stop_button = Button(16, pull_up=True)
    stop_button.when_pressed = button_pressed

    # Parse arguments for the pipeline
    parser = get_default_parser()
    parser.add_argument("--network", default="yolov8s",
                        choices=["yolov6n", "yolov8s", "yolox_s_leaky"],
                        help="Which Network to use (default: yolov8s)")
    parser.add_argument("--hef-path", default=None, help="Path to HEF file")
    parser.add_argument("--labels-json", default=None, help="Path to custom labels JSON")
    args = parser.parse_args()

    app = GStreamerDetectionApp(args, user_data)
    log_system_update("GStreamer pipeline created & starting...")

    # Run the pipeline in a separate thread
    pipeline_thread = threading.Thread(target=app.run, daemon=True)
    pipeline_thread.start()
    log_system_update("Pipeline is running. System is ready.")

    # Create and assign the GUI instance (dashboard_gui is used by the voice assistant loop)
    root = tk.Tk()
    dashboard = SecurityDashboardGUI(root, user_data)
    dashboard_gui = dashboard

    # Narada (voice assistant) will be started/stopped via its GUI button.
    root.mainloop()

    # On exit => stop pipeline
    stop_and_exit()
