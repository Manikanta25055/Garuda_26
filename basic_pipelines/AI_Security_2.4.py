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

# Additional imports for ESP32 integration
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
from gpiozero import (
    LED,         # Green & Red LEDs
    Button,      # Stop button
    OutputDevice # Active Buzzer
)

# For the GUI
import tkinter as tk
from tkinter import ttk, messagebox
import PIL


##############################################################################
# GLOBAL VARIABLES, LOGGING, AND STATE
##############################################################################
app = None
state_lock = threading.Lock()
SCISSORS_LOG_FILE = "danger_sightings.txt"

# ESP32 data
latest_esp32_data = ""  
latest_angle = None     
latest_distance = None  
current_speed = 0.0     

# Detection info
latest_detection_info = ""

# For speed calculation across consecutive readings
prev_distance = None
prev_time = None

# For risk assessment
detection_timestamps = []

esp32_data_lock = threading.Lock()

# Email cooldown
last_email_sent_time = 0
EMAIL_COOLDOWN = 60

# GPIO objects
green_led = None
red_led = None
buzzer = None
stop_button = None

# System updates => shown in the GUI console
system_updates_log = []
def log_system_update(message):
    """Append a system update with timestamp, e.g. [2025-12-01 18:32:00] Some update."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    system_updates_log.append(f"[{timestamp}] {message}")

##############################################################################
# Snapshot Function (Not Used Here)
##############################################################################
def take_snapshot(frame, frame_count):
    pass  # Omitted in this final design

##############################################################################
# ESP32 INTEGRATION
##############################################################################
SERIAL_PORT = "/dev/ttyUSB0"   # <-- Change to your correct port
BAUD_RATE = 9600               # <-- Match your ESP32's baud rate

try:
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    print(f"Connected to ESP32 on {SERIAL_PORT}")
    log_system_update("ESP32 connected successfully.")
except Exception as e:
    print("Error opening serial port:", e)
    log_system_update("Error opening ESP32 serial port.")
    ser = None

def process_scan_data(line):
    """
    Expects format: "SCAN: ANGLE=XX, DIST=YYcm"
    Updates global angle, distance, speed, and GUI text.
    Logs non-matching lines to help debugging.
    """
    global latest_esp32_data, prev_distance, prev_time, current_speed
    global latest_angle, latest_distance

    match = re.search(r"ANGLE=(\d+),\s*DIST=(\d+)cm", line)
    if match:
        angle = int(match.group(1))
        distance = int(match.group(2))
        latest_angle = angle
        latest_distance = distance

        current_time = time.time()
        if prev_distance is not None and prev_time is not None:
            speed = abs(distance - prev_distance) / (current_time - prev_time)
            current_speed = speed
        else:
            current_speed = 0.0

        prev_distance = distance
        prev_time = current_time

        # We'll store a short user-friendly line
        with esp32_data_lock:
            latest_esp32_data = f"Angle: {angle}°, Dist: {distance} cm"
    else:
        # If the line doesn't match, log it so user can see what's coming in
        log_system_update(f"Non-matching ESP32 line => '{line}'")

def esp32_listener():
    if ser is None:
        log_system_update("ESP32 unavailable; skipping listener.")
        return
    while True:
        try:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8').strip()
                if line.startswith("SCAN:"):
                    process_scan_data(line)
                else:
                    # Log lines that don't start with SCAN: if you want to debug
                    if line:
                        log_system_update(f"Unknown ESP32 msg => '{line}'")
            time.sleep(0.1)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print("Error in ESP32 listener:", e)
            log_system_update(f"ESP32 listener error => {str(e)}")

##############################################################################
# AI RISK & EMAIL ALERT
##############################################################################
def assess_risk():
    now = datetime.datetime.now()
    # Time factor => night hours => 1
    time_factor = 1 if (now.hour < 6 or now.hour > 18) else 0

    # Speed factor => if speed > 10 cm/s => 1
    speed_threshold = 10
    with esp32_data_lock:
        speed = current_speed
    speed_factor = 1 if speed > speed_threshold else 0

    # Frequency factor => multiple detections in last 60s => 1
    global detection_timestamps
    current_time = time.time()
    detection_timestamps = [t for t in detection_timestamps if current_time - t < 60]
    frequency_factor = 1 if len(detection_timestamps) > 1 else 0

    total_score = time_factor + speed_factor + frequency_factor
    if total_score == 0:
        return "Low"
    elif total_score == 1:
        return "Medium"
    else:
        return "High"

def send_email_alert():
    global last_email_sent_time
    current_time = time.time()
    if (current_time - last_email_sent_time) < EMAIL_COOLDOWN:
        return

    last_email_sent_time = current_time
    risk_level = assess_risk()
    color_map = {"Low": "Green", "Medium": "Yellow", "High": "Red"}
    color_code = color_map.get(risk_level, "Unknown")

    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    subject = f"Scissors Detected Alert - Risk: {risk_level}"
    body = (
        f"Detected scissors at {now_str}.\n"
        f"Risk: {risk_level} ({color_code}).\n"
        f"Speed: {current_speed:.2f} cm/sec\n"
        f"Detections in last minute: {len(detection_timestamps)}\n"
    )

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
        self.latest_frame = None  # Not used in final design

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

    # Retrieve frame if you want to do extra processing
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
        # If it's the danger label => "scissors"
        if label == user_data.danger_label:
            detection_timestamps.append(time.time())
            # Do alerts in parallel threads
            threading.Thread(target=beep_and_red_led, daemon=True).start()
            threading.Thread(target=log_scissors_detection, daemon=True).start()
            threading.Thread(target=send_email_alert, daemon=True).start()

    user_data.person_detected = any(d.get_label() == "person" for d in detections)

    # Optionally annotate the frame
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
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)  # Convert back to BGR if needed
        user_data.set_frame(frame)

    latest_detection_info = text_info
    return Gst.PadProbeReturn.OK

##############################################################################
# ALERTS FOR DANGER => beep, LED, log
##############################################################################
def beep_and_red_led():
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
# LED BLINK => GREEN
##############################################################################
def blink_green_led():
    green_led.off()
    while True:
        green_led.toggle()
        time.sleep(0.5)

##############################################################################
# GSTREAMER DETECTION APP
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

        # If custom labels
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
        # For a Raspberry Pi camera or USB camera
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
# TKINTER "GARUDA" DASHBOARD
##############################################################################
class SecurityDashboardGUI:
    def __init__(self, root, user_data):
        self.root = root
        self.user_data = user_data

        # Use a theme for a modern look
        style = ttk.Style(self.root)
        style.theme_use("clam")  # or "alt", "default", "classic"
        # You can tweak style.configure(...) calls here if you like.

        self.root.title("Garuda")
        self.root.geometry("900x600")

        # Top Banner Frame
        self.banner_frame = ttk.Frame(self.root)
        self.banner_frame.pack(fill=tk.X, padx=10, pady=5)
        self.greeting_label = ttk.Label(self.banner_frame, font=("Helvetica", 18, "bold"))
        self.greeting_label.pack(side=tk.LEFT, padx=10)

        # Notebook => 2 tabs => "AI Detections" & "Device Data"
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # TAB 1: AI Detections
        self.detections_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.detections_tab, text="AI Detections")

        # PanedWindow inside
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

        # TAB 2: Device Data (ESP32)
        self.esp32_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.esp32_tab, text="Device Data")
        self.esp32_text = tk.Text(self.esp32_tab, wrap="word", bg="white", fg="black")
        self.esp32_text.configure(state="disabled")
        self.esp32_text.pack(fill=tk.BOTH, expand=True)

        # BOTTOM => Exit
        self.exit_frame = ttk.Frame(self.root)
        self.exit_frame.pack(fill=tk.X, side=tk.BOTTOM)
        self.exit_button = ttk.Button(self.exit_frame, text="Exit", command=stop_and_exit)
        self.exit_button.pack(pady=10)

        # Start updating
        self.update_gui()

    def update_gui(self):
        # Greeting
        now = datetime.datetime.now()
        if now.hour < 12:
            greeting = "Good Morning"
        elif now.hour < 18:
            greeting = "Good Afternoon"
        else:
            greeting = "Good Evening"
        self.greeting_label.config(text=f"Dear Manikanta, {greeting}! Welcome to Garuda.")

        # 1) AI Detections => parse lines so that "detected" lines are in blue
        self.detection_text.configure(state="normal")
        self.detection_text.delete("1.0", tk.END)
        lines = latest_detection_info.strip().split("\n")
        for line in lines:
            if "detected" in line:
                self.detection_text.insert(tk.END, line + "\n", ("detection_label",))
            else:
                self.detection_text.insert(tk.END, line + "\n", ("detection_other",))
        self.detection_text.configure(state="disabled")

        # 2) System Updates => last 20 lines in green
        self.console_text.configure(state="normal")
        self.console_text.delete("1.0", tk.END)
        for msg in system_updates_log[-20:]:
            self.console_text.insert(tk.END, msg + "\n", ("system_update",))
        self.console_text.configure(state="disabled")

        # 3) Device Data => angle, dist, speed, risk
        self.esp32_text.configure(state="normal")
        self.esp32_text.delete("1.0", tk.END)

        angle_val = str(latest_angle) if latest_angle is not None else "N/A"
        dist_val = str(latest_distance) if latest_distance is not None else "N/A"
        spd_val = f"{current_speed:.2f}"

        risk_val = assess_risk()
        device_info = (
            f"Latest Angle: {angle_val}\n"
            f"Latest Distance: {dist_val} cm\n"
            f"Speed: {spd_val} cm/sec\n"
            f"Risk Assessment: {risk_val}\n"
        )
        self.esp32_text.insert(tk.END, device_info)
        self.esp32_text.configure(state="disabled")

        # Schedule next update
        self.root.after(100, self.update_gui)

##############################################################################
# MAIN
##############################################################################
if __name__ == "__main__":
    Gst.init(None)
    print("Initializing GPIO devices...")
    log_system_update("Initializing GPIO devices...")

    # Create user_data (for pipeline)
    user_data = user_app_callback_class()

    # Setup GPIO
    green_led = LED(21)
    red_led = LED(20)
    red_led.off()
    buzzer = OutputDevice(pin=12, initial_value=False)
    stop_button = Button(16, pull_up=True)
    stop_button.when_pressed = button_pressed

    # Blink LED
    threading.Thread(target=blink_green_led, daemon=True).start()

    # Start ESP32 thread
    threading.Thread(target=esp32_listener, daemon=True).start()

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

    # Launch GUI in main thread
    root = tk.Tk()
    dashboard = SecurityDashboardGUI(root, user_data)
    root.mainloop()

    # On exit => stop pipeline
    stop_and_exit()
