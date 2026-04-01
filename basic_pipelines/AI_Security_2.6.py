import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

import os
import argparse
import multiprocessing
import numpy as np
import setproctitle
import cv2
import time
import hailo
import sys
import datetime
import smtplib
from email.mime.text import MIMEText

# Additional imports for the pipeline
import serial  # (No longer used, but you can remove if you wish)
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
from gpiozero import (
    LED,         # Red LED only
    Button,      # Stop button
    OutputDevice # Active Buzzer
)

# For the GUI
import tkinter as tk
from tkinter import ttk, messagebox
import PIL  # <- Only import PIL, as requested

# Import SpeechRecognition for the voice assistant
import speech_recognition

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

# Global list to store voice assistant log messages
voice_assistant_log = []

# We no longer use any ESP32 variables or references.
# Just keep a simple set of data for the system updates and detection info.
latest_detection_info = ""

# For emailing / cooldown
last_email_sent_time = 0
EMAIL_COOLDOWN = 60

# GPIO objects
red_led = None
buzzer = None
stop_button = None

# System updates => shown in the GUI console
system_updates_log = []
def log_system_update(message):
    """Append a system update with timestamp, e.g. [2025-12-01 18:32:00] Some update."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    system_updates_log.append(f"[{timestamp}] {message}")

def append_voice_log(message):
    """Append a timestamped entry to the voice assistant log."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    voice_assistant_log.append(f"[{timestamp}] {message}")

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
    # Do not send email if Email notifications are off or Idle mode is enabled
    if MODE_EMAIL_OFF or MODE_IDLE:
        log_system_update("Email alert skipped due to Email Off/Idle mode.")
        return

    current_time = time.time()
    if (current_time - last_email_sent_time) < EMAIL_COOLDOWN:
        return

    last_email_sent_time = current_time
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    subject = "Scissors Detected Alert"
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
            # Danger => beep, LED, email, log
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
    # If DND or Idle mode is active, skip LED and buzzer alerts.
    if MODE_DND or MODE_IDLE:
        log_system_update("Alert (LED/Buzzer) skipped due to DND/Idle mode.")
        return
    red_led.on()
    buzzer.on()
    time.sleep(5)
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
# GStreamerDetectionApp
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

        # Decide which HEF to load
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
        # For a Raspberry Pi or USB camera, or file
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
            # For a file
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
# TKINTER "GARUDA v2.5" DASHBOARD INCLUDING VOICE ASSISTANT TAB
##############################################################################
class SecurityDashboardGUI:
    def __init__(self, root, user_data):
        self.root = root
        self.user_data = user_data

        # Modern style
        style = ttk.Style(self.root)
        style.theme_use("clam")

        self.root.title("Garuda")
        self.root.geometry("900x600")

        # Top Banner Frame
        self.banner_frame = ttk.Frame(self.root)
        self.banner_frame.pack(fill=tk.X, padx=10, pady=5)
        self.greeting_label = ttk.Label(self.banner_frame, font=("Helvetica", 18, "bold"))
        self.greeting_label.pack(side=tk.LEFT, padx=10)

        # Notebook with two tabs: AI Detections and Voice Assistant
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # AI Detections tab
        self.detections_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.detections_tab, text="AI Detections")

        # PanedWindow inside AI Detections tab
        self.info_paned = ttk.Panedwindow(self.detections_tab, orient=tk.VERTICAL)
        self.info_paned.pack(fill=tk.BOTH, expand=True)

        # 1) Detection details
        self.detection_text = tk.Text(self.info_paned, height=15, wrap="word", bg="white", fg="black")
        self.detection_text.configure(state="disabled")
        self.detection_text.tag_configure("detection_label", foreground="blue")
        self.detection_text.tag_configure("detection_other", foreground="black")
        self.info_paned.add(self.detection_text, weight=2)

        # 2) System Updates => green text
        self.console_text = tk.Text(self.info_paned, height=6, wrap="word", bg="white", fg="black")
        self.console_text.configure(state="disabled")
        self.console_text.tag_configure("system_update", foreground="green")
        self.info_paned.add(self.console_text, weight=1)

        # Voice Assistant tab
        self.voice_assistant_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.voice_assistant_tab, text="Voice Assistant")

        # Frame for mode toggles
        self.mode_frame = ttk.LabelFrame(self.voice_assistant_tab, text="Modes")
        self.mode_frame.pack(fill=tk.X, padx=10, pady=5)

        # BooleanVars to link with checkbuttons (initialize with current global flag values)
        self.var_dnd = tk.BooleanVar(value=MODE_DND)
        self.var_email_off = tk.BooleanVar(value=MODE_EMAIL_OFF)
        self.var_idle = tk.BooleanVar(value=MODE_IDLE)

        self.dnd_check = ttk.Checkbutton(self.mode_frame, text="DND Mode", variable=self.var_dnd, command=self.toggle_dnd)
        self.dnd_check.pack(side=tk.LEFT, padx=5, pady=5)
        self.email_check = ttk.Checkbutton(self.mode_frame, text="Email Off Mode", variable=self.var_email_off, command=self.toggle_email)
        self.email_check.pack(side=tk.LEFT, padx=5, pady=5)
        self.idle_check = ttk.Checkbutton(self.mode_frame, text="Idle Mode", variable=self.var_idle, command=self.toggle_idle)
        self.idle_check.pack(side=tk.LEFT, padx=5, pady=5)

        # Text widget to show Voice Assistant logs and responses
        self.voice_text = tk.Text(self.voice_assistant_tab, height=15, wrap="word", bg="lightyellow")
        self.voice_text.configure(state="disabled")
        self.voice_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # BOTTOM => Exit
        self.exit_frame = ttk.Frame(self.root)
        self.exit_frame.pack(fill=tk.X, side=tk.BOTTOM)
        self.exit_button = ttk.Button(self.exit_frame, text="Exit", command=stop_and_exit)
        self.exit_button.pack(pady=10)

        # Additional instructions or hardware mention:
        note_label = ttk.Label(
            self.root,
            text="Copyright @Garuda 2025",
            wraplength=800,
            foreground="gray"
        )
        note_label.pack(pady=5)

        # Start updating the GUI periodically
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

    def update_voice_assistant_tab(self):
        self.voice_text.configure(state="normal")
        self.voice_text.delete("1.0", tk.END)
        # Show the last 50 messages from the voice assistant log
        for msg in voice_assistant_log[-50:]:
            self.voice_text.insert(tk.END, msg + "\n")
        self.voice_text.configure(state="disabled")

    def update_gui(self):
        # Greeting
        now = datetime.datetime.now()
        if now.hour < 12:
            greeting = "Good Morning"
        elif now.hour < 18:
            greeting = "Good Afternoon"
        else:
            greeting = "Good Evening"
        self.greeting_label.config(text=f"Dear Manikanta, {greeting}! Welcome to Garuda v2.5.")

        # Update detection info (blue if line contains "detected")
        self.detection_text.configure(state="normal")
        self.detection_text.delete("1.0", tk.END)
        lines = latest_detection_info.strip().split("\n")
        for line in lines:
            if "detected" in line:
                self.detection_text.insert(tk.END, line + "\n", ("detection_label",))
            else:
                self.detection_text.insert(tk.END, line + "\n", ("detection_other",))
        self.detection_text.configure(state="disabled")

        # System updates => last 20 lines, green
        self.console_text.configure(state="normal")
        self.console_text.delete("1.0", tk.END)
        for msg in system_updates_log[-20:]:
            self.console_text.insert(tk.END, msg + "\n", ("system_update",))
        self.console_text.configure(state="disabled")

        # Update the Voice Assistant tab text widget
        self.update_voice_assistant_tab()

        # Schedule next update
        self.root.after(100, self.update_gui)

##############################################################################
# VOICE ASSISTANT LOOP (runs in a separate thread)
##############################################################################
def voice_assistant_loop():
    recognizer = sr.Recognizer()
    try:
        mic = sr.Microphone()  # Use default USB microphone
        append_voice_log("USB Microphone connected.")
    except Exception as e:
        append_voice_log("Error accessing microphone: " + str(e))
        return

    # Calibrate ambient noise (optional but recommended)
    with mic as source:
        recognizer.adjust_for_ambient_noise(source)
        append_voice_log("Calibrated for ambient noise.")

    while True:
        with mic as source:
            append_voice_log("Listening...")
            try:
                audio = recognizer.listen(source, timeout=5)
            except sr.WaitTimeoutError:
                continue
        try:
            command = recognizer.recognize_google(audio)
            command = command.lower()
            append_voice_log("You said: " + command)
            response = None
            # Process some example commands:
            if "your name" in command:
                if "what" in command:
                    response = "My name is Garuda, your security assistant."
            elif "weather" in command:
                response = "It seems clear outside, but I cannot fetch live weather data right now."
            elif "activate dnd" in command:
                global MODE_DND
                MODE_DND = True
                response = "DND mode activated. LED and buzzer alerts are disabled."
            elif "deactivate dnd" in command:
                MODE_DND = False
                response = "DND mode deactivated. LED and buzzer alerts are enabled."
            elif "activate email" in command and "off" in command:
                global MODE_EMAIL_OFF
                MODE_EMAIL_OFF = True
                response = "Email notifications are turned off."
            elif "deactivate email" in command and "off" in command:
                MODE_EMAIL_OFF = False
                response = "Email notifications are turned on."
            elif "activate idle" in command:
                global MODE_IDLE
                MODE_IDLE = True
                response = "Idle mode activated. All alerts are disabled."
            elif "deactivate idle" in command:
                MODE_IDLE = False
                response = "Idle mode deactivated. Alerts are restored."
            elif "status" in command:
                response = (f"Current modes - DND: {MODE_DND}, "
                            f"Email Off: {MODE_EMAIL_OFF}, Idle: {MODE_IDLE}")
            else:
                response = "I'm sorry, I did not understand that command."
            if response:
                append_voice_log("Response: " + response)
        except sr.UnknownValueError:
            append_voice_log("Could not understand audio.")
        except sr.RequestError as e:
            append_voice_log("Recognition error; check your internet connection. Error: " + str(e))
        time.sleep(1)

##############################################################################
# MAIN
##############################################################################
if __name__ == "__main__":
    Gst.init(None)
    print("Initializing GPIO devices...")
    log_system_update("Initializing GPIO devices...")

    # Create user_data
    user_data = user_app_callback_class()

    # Only red LED & Buzzer
    red_led = LED(20)
    red_led.off()
    buzzer = OutputDevice(pin=12, initial_value=False)

    # Push button => stop pipeline
    stop_button = Button(16, pull_up=True)
    stop_button.when_pressed = button_pressed

    # We removed the green LED toggling entirely

    # Parse arguments => pipeline
    parser = get_default_parser()
    parser.add_argument("--network", default="yolov8s",
                        choices=["yolov6n", "yolov8s", "yolox_s_leaky"],
                        help="Which Network to use (default yolov8s)")
    parser.add_argument("--hef-path", default=None, help="Path to HEF file")
    parser.add_argument("--labels-json", default=None, help="Path to custom labels JSON")
    args = parser.parse_args()

    app = GStreamerDetectionApp(args, user_data)
    log_system_update("GStreamer pipeline created & starting...")

    # Run pipeline in separate thread
    pipeline_thread = threading.Thread(target=app.run, daemon=True)
    pipeline_thread.start()
    log_system_update("Pipeline is running. System is ready.")

    # Start voice assistant thread
    voice_thread = threading.Thread(target=voice_assistant_loop, daemon=True)
    voice_thread.start()

    # Launch GUI in main thread
    root = tk.Tk()
    dashboard = SecurityDashboardGUI(root, user_data)
    root.mainloop()

    # On exit => stop pipeline
    stop_and_exit()
