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
from email.mime.text import MIMEText  # For email alerts

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
# Global Variables and Shared Data for GUI and Logging
##############################################################################
app = None
state_lock = threading.Lock()
SCISSORS_LOG_FILE = "danger_sightings.txt"

# (No snapshots folder is needed in this version.)
# Global variables for ESP32 data display
latest_esp32_data = ""  # Updated by the ESP32 listener thread
latest_angle = None     # Latest angle from ESP32
latest_distance = None  # Latest distance from ESP32

# Global variable to hold the latest detection info (frame count, detection details, etc.)
latest_detection_info = ""

# NEW GLOBALS for ESP32 scan data update and risk assessment
last_printed_angle = -1   # To track the last angle printed (update every 10°)
prev_distance = None      # For speed calculation
prev_time = None          # For speed calculation
current_speed = 0.0       # Latest calculated speed (cm/sec)

# For keeping track of recent detections (for risk assessment)
detection_timestamps = []

esp32_data_lock = threading.Lock()

# Email cooldown variables
last_email_sent_time = 0
EMAIL_COOLDOWN = 60  # seconds

# GPIO objects (to be initialized later)
green_led = None
red_led = None
buzzer = None
stop_button = None

# Global system updates log list (for console output in GUI)
system_updates_log = []

def log_system_update(message):
    """Append a system update with a timestamp."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    system_updates_log.append(f"[{timestamp}] {message}")

##############################################################################
# Snapshot Functionality (not used in this version)
##############################################################################
def take_snapshot(frame, frame_count):
    # This function is retained for future use if needed.
    pass

##############################################################################
# ESP32 Integration
##############################################################################
SERIAL_PORT = "/dev/ttyUSB0"  # Adjust as needed
BAUD_RATE = 9600

try:
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    print(f"Connected to ESP32 on {SERIAL_PORT}")
    log_system_update("ESP32 connected successfully.")
except Exception as e:
    print("Error opening serial port:", e)
    log_system_update("Error opening ESP32 serial port.")
    ser = None  # Continue without ESP32 functionality if connection fails

def process_scan_data(line):
    """
    Process a line from the ESP32.
    Expected format: "SCAN: ANGLE=XX, DIST=YYcm"
    Updates latest_angle, latest_distance, and current_speed.
    """
    global latest_esp32_data, last_printed_angle, prev_distance, prev_time, current_speed
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

        # Update display every 10° change (avoid duplicates)
        if angle % 10 == 0 and angle != last_printed_angle:
            last_printed_angle = angle
            message = f"Angle: {angle}°, Dist: {distance} cm"
            with esp32_data_lock:
                latest_esp32_data = message
    else:
        pass

def esp32_listener():
    if ser is None:
        log_system_update("ESP32 serial not available; listener skipped.")
        return
    while True:
        try:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8').strip()
                if line:
                    if line.startswith("SCAN:"):
                        process_scan_data(line)
                    else:
                        pass
            time.sleep(0.1)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print("Error in ESP32 listener:", e)
            log_system_update("Error in ESP32 listener.")

##############################################################################
# AI Risk Assessment & Email Alert Functionality
##############################################################################
def assess_risk():
    now = datetime.datetime.now()
    time_factor = 1 if (now.hour < 6 or now.hour > 18) else 0
    speed_threshold = 10  # cm/sec threshold
    with esp32_data_lock:
        speed = current_speed
    speed_factor = 1 if speed > speed_threshold else 0
    global detection_timestamps
    current_time = time.time()
    detection_timestamps = [t for t in detection_timestamps if current_time - t < 60]
    frequency_factor = 1 if len(detection_timestamps) > 1 else 0
    risk_score = time_factor + speed_factor + frequency_factor
    if risk_score == 0:
        return "Low"
    elif risk_score == 1:
        return "Medium"
    else:
        return "High"

def send_email_alert():
    global last_email_sent_time
    current_time = time.time()
    if current_time - last_email_sent_time < EMAIL_COOLDOWN:
        return
    last_email_sent_time = current_time

    risk_level = assess_risk()
    color_code = {"Low": "Green", "Medium": "Yellow", "High": "Red"}.get(risk_level, "Unknown")
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    subject = f"Scissors Detected Alert - Risk Level: {risk_level}"
    body = (
        f"Alert: Scissors detected at {now_str}.\n"
        f"Risk: {risk_level} (Color: {color_code}).\n"
        f"Speed: {current_speed:.2f} cm/sec\n"
        f"Detections (last minute): {len(detection_timestamps)}\n"
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
        log_system_update("Email alert sent.")
    except Exception as e:
        print("Failed to send email alert:", e)
        log_system_update("Failed to send email alert.")

##############################################################################
# Custom Callback Class for Detection (stores latest frame)
##############################################################################
class user_app_callback_class(app_callback_class):
    def __init__(self):
        super().__init__()
        self.new_variable = 42
        self.person_detected = False
        self.danger_label = "scissors"
        self.latest_frame = None  # Not used in this version

    def new_function(self):
        return "The meaning of life is: "

    def set_frame(self, frame):
        self.latest_frame = frame

    def get_frame(self):
        return self.latest_frame

##############################################################################
# GStreamer Pad Callback (updates detection info)
##############################################################################
def app_callback(pad, info, user_data):
    global latest_detection_info
    buffer = info.get_buffer()
    if buffer is None:
        return Gst.PadProbeReturn.OK

    user_data.increment()
    frame_count = user_data.get_count()
    info_text = f"Frame count: {frame_count}\n"

    # Retrieve frame (if needed)
    format_, width, height = get_caps_from_pad(pad)
    frame = None
    if user_data.use_frame and format_ and width and height:
        frame = get_numpy_from_buffer(buffer, format_, width, height)

    # Iterate over all detections (show label and confidence)
    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)
    for detection in detections:
        label = detection.get_label()
        confidence = detection.get_confidence()
        info_text += f"{label} detected (confidence: {confidence:.2f})\n"
        if label == user_data.danger_label:
            detection_timestamps.append(time.time())
            threading.Thread(target=beep_and_red_led, daemon=True).start()
            threading.Thread(target=log_scissors_detection, daemon=True).start()
            threading.Thread(target=send_email_alert, daemon=True).start()

    user_data.person_detected = any(d.get_label() == "person" for d in detections)
    if user_data.use_frame and frame is not None:
        cv2.putText(frame, f"Frame: {frame_count}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        cv2.putText(frame, f"{user_data.new_function()} {user_data.new_variable}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        user_data.set_frame(frame)

    latest_detection_info = info_text
    return Gst.PadProbeReturn.OK

##############################################################################
# Danger Alert: Buzzer & LED (5-second activation)
##############################################################################
def beep_and_red_led():
    red_led.on()
    buzzer.on()
    time.sleep(5)
    buzzer.off()
    red_led.off()

def log_scissors_detection():
    now = datetime.datetime.now()
    timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp_str}] SCISSORS DETECTED\n"
    with open(SCISSORS_LOG_FILE, "a") as f:
        f.write(entry)
    log_system_update("Scissors detection logged.")

##############################################################################
# Stop the entire pipeline if button pressed
##############################################################################
def stop_and_exit():
    print("Stopping GStreamer pipeline...")
    log_system_update("Stopping pipeline and exiting.")
    if app is not None:
        app.pipeline.set_state(Gst.State.NULL)
    sys.exit(0)

def button_pressed():
    stop_and_exit()

##############################################################################
# Blink Green LED in a separate thread (status indication)
##############################################################################
def blink_green_led():
    green_led.off()
    while True:
        green_led.toggle()
        time.sleep(0.5)

##############################################################################
# GStreamerDetectionApp (pipeline setup)
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
# Modern Tkinter GUI for "Garuda" (White background version)
##############################################################################
class SecurityDashboardGUI:
    def __init__(self, root, user_data):
        self.root = root
        self.user_data = user_data

        # Set window title and size; using default white background
        self.root.title("Garuda")
        self.root.geometry("800x600")
        # Remove any explicit background color setting so it uses white

        # Top banner with greeting
        self.banner_frame = ttk.Frame(self.root)
        self.banner_frame.pack(fill=tk.X, padx=10, pady=5)
        self.greeting_label = ttk.Label(self.banner_frame, font=("Helvetica", 18, "bold"))
        self.greeting_label.pack(side=tk.LEFT)

        # Notebook with two tabs: Detection Info and ESP32 Data
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Tab 1: Detection Info (includes a built-in console)
        self.info_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.info_tab, text="Detection Info")
        self.info_paned = ttk.Panedwindow(self.info_tab, orient=tk.VERTICAL)
        self.info_paned.pack(fill=tk.BOTH, expand=True)
        self.detection_text = tk.Text(self.info_paned, height=15, wrap="word", bg="white", fg="black")
        self.detection_text.configure(state="disabled")
        self.info_paned.add(self.detection_text, weight=2)
        self.console_text = tk.Text(self.info_paned, height=5, wrap="word", bg="white", fg="black")
        self.console_text.configure(state="disabled")
        self.info_paned.add(self.console_text, weight=1)

        # Tab 2: ESP32 Data
        self.esp32_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.esp32_tab, text="ESP32 Data")
        self.esp32_text = tk.Text(self.esp32_tab, wrap="word", bg="white", fg="black")
        self.esp32_text.pack(fill=tk.BOTH, expand=True)
        self.esp32_text.configure(state="disabled")

        # Exit Button at the bottom
        self.exit_button = ttk.Button(self.root, text="Exit", command=stop_and_exit)
        self.exit_button.pack(pady=10)

        # Start periodic updates of the GUI
        self.update_gui()

    def update_gui(self):
        # Update greeting based on time
        now = datetime.datetime.now()
        if now.hour < 12:
            greeting = "Good Morning"
        elif now.hour < 18:
            greeting = "Good Afternoon"
        else:
            greeting = "Good Evening"
        self.greeting_label.config(text=f"Dear Manikanta, {greeting}! Welcome to Garuda.")

        # Update Detection Info tab with latest detection details
        self.detection_text.configure(state="normal")
        self.detection_text.delete("1.0", tk.END)
        self.detection_text.insert(tk.END, latest_detection_info)
        self.detection_text.configure(state="disabled")

        # Update System Updates console with the last 20 log messages
        self.console_text.configure(state="normal")
        self.console_text.delete("1.0", tk.END)
        console_output = "\n".join(system_updates_log[-20:])
        self.console_text.insert(tk.END, console_output)
        self.console_text.configure(state="disabled")

        # Update ESP32 Data tab with detailed info
        self.esp32_text.configure(state="normal")
        self.esp32_text.delete("1.0", tk.END)
        esp_details = (
            f"Latest Angle: {latest_angle if latest_angle is not None else 'N/A'}\n"
            f"Latest Distance: {latest_distance if latest_distance is not None else 'N/A'} cm\n"
            f"Speed: {current_speed:.2f} cm/sec\n"
            f"Risk Assessment: {assess_risk()}\n"
        )
        self.esp32_text.insert(tk.END, esp_details)
        self.esp32_text.configure(state="disabled")

        self.root.after(50, self.update_gui)

##############################################################################
# Main
##############################################################################
if __name__ == "__main__":
    Gst.init(None)

    print("Initializing GPIO devices...")
    log_system_update("Initializing GPIO devices...")

    # Create user_data instance
    user_data = user_app_callback_class()

    # Initialize GPIO devices
    green_led = LED(21)
    red_led = LED(20)
    red_led.off()
    buzzer = OutputDevice(pin=12, initial_value=False)
    stop_button = Button(16, pull_up=True)
    stop_button.when_pressed = button_pressed

    # Start green LED blinking thread
    threading.Thread(target=blink_green_led, daemon=True).start()

    # Start ESP32 listener thread
    threading.Thread(target=esp32_listener, daemon=True).start()

    # Parse arguments and create the GStreamerDetectionApp pipeline
    parser = get_default_parser()
    parser.add_argument("--network", default="yolov8s", choices=["yolov6n", "yolov8s", "yolox_s_leaky"],
                        help="Which Network to use, default is yolov8s")
    parser.add_argument("--hef-path", default=None, help="Path to HEF file")
    parser.add_argument("--labels-json", default=None, help="Path to custom labels JSON file")
    args = parser.parse_args()
    app = GStreamerDetectionApp(args, user_data)
    log_system_update("GStreamer pipeline created and starting...")

    # Run the GStreamer pipeline in a separate thread
    pipeline_thread = threading.Thread(target=app.run, daemon=True)
    pipeline_thread.start()
    log_system_update("GStreamer pipeline is running.")

    # Create and run the Tkinter GUI (main thread)
    root = tk.Tk()
    dashboard = SecurityDashboardGUI(root, user_data)
    root.mainloop()

    # On exit, stop the pipeline.
    stop_and_exit()
