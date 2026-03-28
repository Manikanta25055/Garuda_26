# user_dashboard.py
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

import serial
import re

from hailo_rpi_common import (
    get_default_parser,
    QUEUE,
    get_caps_from_pad,
    get_numpy_from_buffer,
    GStreamerApp,
    app_callback_class,
)

import threading
from gpiozero import LED, Button, OutputDevice

import tkinter as tk
from tkinter import ttk, messagebox
import PIL

import speech_recognition as sr

# Bring in common module globals and functions
from common import (MODE_DND, MODE_EMAIL_OFF, MODE_IDLE, MODE_NIGHT,
                    SCISSORS_LOG_FILE, NIGHT_MODE_LOG_FILE,
                    system_updates_log, voice_assistant_log, voice_responses,
                    log_system_update, append_voice_log, append_voice_response,
                    update_text_widget)

# Global variables (for pipeline, voice, etc.)
app = None
state_lock = threading.Lock()
latest_detection_info = ""
last_email_sent_time = 0
EMAIL_COOLDOWN = 60
red_led = None
buzzer = None
stop_button = None

# ---------- Pipeline & Detection Code (Integrated here) ----------

def take_snapshot(frame, frame_count):
    pass

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
    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)
    for d in detections:
        label = d.get_label()
        confidence = d.get_confidence()
        text_info += f"{label} detected (confidence: {confidence:.2f})\n"
        if label == user_data.danger_label:
            threading.Thread(target=beep_and_red_led, daemon=True).start()
            threading.Thread(target=log_scissors_detection, daemon=True).start()
            threading.Thread(target=send_email_alert, daemon=True).start()
    user_data.person_detected = any(d.get_label() == "person" for d in detections)
    if frame is not None:
        cv2.putText(frame, f"Frame: {frame_num}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0,255,0), 2)
        cv2.putText(frame, f"{user_data.new_function()} {user_data.new_variable}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 1, (0,255,0), 2)
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        user_data.set_frame(frame)
    latest_detection_info = text_info
    return Gst.PadProbeReturn.OK

def beep_and_red_led():
    if MODE_DND or MODE_IDLE:
        log_system_update("Alert (LED/Buzzer) skipped due to DND/Idle mode.")
        return
    if MODE_NIGHT:
        try:
            with open(NIGHT_MODE_LOG_FILE, "a") as f:
                f.write(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
        except Exception as e:
            log_system_update("Error logging night mode incident: " + str(e))
    if red_led:
        red_led.on()
    if buzzer:
        buzzer.on()
    duration = 10 if MODE_NIGHT else 5
    time.sleep(duration)
    if buzzer:
        buzzer.off()
    if red_led:
        red_led.off()

def log_scissors_detection():
    now = datetime.datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{stamp}] SCISSORS DETECTED\n"
    with open(SCISSORS_LOG_FILE, "a") as f:
        f.write(entry)
    log_system_update("Scissors detection logged in file.")

def stop_and_exit():
    print("Stopping GStreamer pipeline now...")
    log_system_update("Stopping pipeline & exiting the app.")
    if app is not None:
        app.pipeline.set_state(Gst.State.NULL)
    import sys
    sys.exit(0)

def button_pressed():
    stop_and_exit()

def voice_assistant_loop(stop_event):
    recognizer = sr.Recognizer()
    try:
        mic = sr.Microphone()
        append_voice_log("USB Microphone connected.")
    except Exception as e:
        append_voice_log("Error accessing microphone: " + str(e))
        return
    with mic as source:
        recognizer.adjust_for_ambient_noise(source)
        append_voice_log("Calibrated for ambient noise.")
    while not stop_event.is_set():
        with mic as source:
            append_voice_log("Listening...")
            try:
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
        import common
        global MODE_DND, MODE_EMAIL_OFF, MODE_IDLE, MODE_NIGHT
        # All mode mutations guarded by state_lock to prevent race with detection thread
        with state_lock:
            if "deactivate dnd" in user_input_lower:
                MODE_DND = False; common.MODE_DND = False
                response = "DND mode deactivated. LED and buzzer alerts are enabled."
            elif "activate dnd" in user_input_lower:
                MODE_DND = True; common.MODE_DND = True
                response = "DND mode activated. LED and buzzer alerts are now disabled."
            elif "deactivate email off" in user_input_lower:
                MODE_EMAIL_OFF = False; common.MODE_EMAIL_OFF = False
                response = "Email notifications turned on."
            elif "activate email off" in user_input_lower:
                MODE_EMAIL_OFF = True; common.MODE_EMAIL_OFF = True
                response = "Email notifications turned off."
            elif "deactivate idle" in user_input_lower:
                MODE_IDLE = False; common.MODE_IDLE = False
                response = "Idle mode deactivated. Alerts have been restored."
            elif "activate idle" in user_input_lower:
                MODE_IDLE = True; common.MODE_IDLE = True
                response = "Idle mode activated. All alerts are disabled."
            elif "deactivate night mode" in user_input_lower:
                MODE_NIGHT = False; common.MODE_NIGHT = False
                response = "Night mode deactivated. Normal alert settings restored."
            elif "activate night mode" in user_input_lower:
                MODE_NIGHT = True; common.MODE_NIGHT = True
                response = "Night mode activated. High priority alerts enabled."
            elif "what were your findings last night" in user_input_lower:
                MODE_NIGHT = True; common.MODE_NIGHT = True
        # Non-mode responses (file I/O and simple replies outside the lock)
        if response is None:
            if "what were your findings last night" in user_input_lower:
                if os.path.exists(NIGHT_MODE_LOG_FILE):
                    with open(NIGHT_MODE_LOG_FILE, "r") as f:
                        lines = [line.strip() for line in f.readlines() if line.strip()]
                else:
                    lines = []
                if lines:
                    response = (f"Last night, I recorded {len(lines)} incident(s) at: " +
                                ", ".join(lines))
                else:
                    response = "You are all safe and sound. No incidents were recorded last night."
            elif "weather" in user_input_lower:
                response = "The weather today is partly cloudy with a high of 25°C and a low of 15°C."
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
        time.sleep(1)

# ---------- Pipeline Class (Hailo/GStreamer) ----------
from hailo_rpi_common import GStreamerApp
import setproctitle
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

# ---------- User Dashboard (GUI) ----------

class UserDashboard(tk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.master = master
        self.voice_thread = None
        self.voice_stop_event = None
        self.voice_assistant_running = False
        self.build_ui()

    def build_ui(self):
        # Top banner
        banner = ttk.Frame(self)
        banner.pack(fill="x", padx=10, pady=5)
        self.greeting_label = ttk.Label(banner, text="Welcome to Garuda - User Dashboard", font=("Helvetica", 18, "bold"))
        self.greeting_label.pack(side="left", padx=10)
        # Main notebook with two tabs: "AI Detections" and "Narada"
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=5)
        # AI Detections Tab
        self.detections_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.detections_tab, text="AI Detections")
        self.build_detections_tab()
        # Narada Tab
        self.narada_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.narada_tab, text="Narada")
        self.build_narada_tab()

    def build_detections_tab(self):
        self.info_paned = ttk.Panedwindow(self.detections_tab, orient=tk.VERTICAL)
        self.info_paned.pack(fill="both", expand=True)
        # Detection Details
        detection_frame = tk.Frame(self.info_paned)
        self.detection_text = tk.Text(detection_frame, height=15, wrap="word", bg="white", fg="black")
        det_scroll = tk.Scrollbar(detection_frame, orient="vertical", command=self.detection_text.yview)
        self.detection_text.configure(yscrollcommand=det_scroll.set)
        det_scroll.pack(side="right", fill="y")
        self.detection_text.pack(side="left", fill="both", expand=True)
        self.info_paned.add(detection_frame, weight=2)
        # System Updates (Console)
        console_frame = tk.Frame(self.info_paned)
        self.console_text = tk.Text(console_frame, height=6, wrap="word", bg="white", fg="black")
        cons_scroll = tk.Scrollbar(console_frame, orient="vertical", command=self.console_text.yview)
        self.console_text.configure(yscrollcommand=cons_scroll.set)
        cons_scroll.pack(side="right", fill="y")
        self.console_text.pack(side="left", fill="both", expand=True)
        self.info_paned.add(console_frame, weight=1)

    def build_narada_tab(self):
        # Voice control section
        self.voice_control_frame = ttk.Frame(self.narada_tab)
        self.voice_control_frame.pack(fill="x", padx=10, pady=5)
        self.voice_button = ttk.Button(self.voice_control_frame, text="Start Narada", command=self.toggle_voice_assistant)
        self.voice_button.pack(side="left", padx=5)
        self.voice_status_label = ttk.Label(self.voice_control_frame, text="Status: Not Listening")
        self.voice_status_label.pack(side="left", padx=10)
        self.listening_indicator = ttk.Label(self.voice_control_frame, text="●", font=("Helvetica", 20))
        self.listening_indicator.pack(side="left", padx=5)
        self.listening_indicator.config(foreground="red")
        self.clear_log_button = ttk.Button(self.voice_control_frame, text="Clear Log", command=self.clear_narada_log)
        self.clear_log_button.pack(side="left", padx=5)
        # Narada container
        self.narada_container = tk.Frame(self.narada_tab, height=350)
        self.narada_container.pack(fill="x", padx=10, pady=5)
        self.narada_container.pack_propagate(False)
        # Narada Log (upper 70%)
        self.narada_log_frame = ttk.LabelFrame(self.narada_container, text="Narada Log")
        self.narada_log_frame.place(relx=0, rely=0, relwidth=1, relheight=0.7)
        self.voice_text = tk.Text(self.narada_log_frame, wrap="word", bg="lightyellow")
        voice_scroll = tk.Scrollbar(self.narada_log_frame, orient="vertical", command=self.voice_text.yview)
        self.voice_text.configure(yscrollcommand=voice_scroll.set)
        voice_scroll.pack(side="right", fill="y")
        self.voice_text.pack(side="left", fill="both", expand=True)
        # Responses (lower 30%)
        self.response_frame = ttk.LabelFrame(self.narada_container, text="Responses")
        self.response_frame.place(relx=0, rely=0.7, relwidth=1, relheight=0.3)
        self.response_text = tk.Text(self.response_frame, wrap="word", bg="white", fg="grey")
        response_scroll = tk.Scrollbar(self.response_frame, orient="vertical", command=self.response_text.yview)
        self.response_text.configure(yscrollcommand=response_scroll.set)
        response_scroll.pack(side="right", fill="y")
        self.response_text.pack(side="left", fill="both", expand=True)
        # Instructions button
        self.instructions_button = ttk.Button(self.narada_tab, text="Instructions", command=self.show_instructions)
        self.instructions_button.pack(pady=5)

    def toggle_voice_assistant(self):
        if not self.voice_assistant_running:
            self.voice_stop_event = threading.Event()
            self.voice_thread = threading.Thread(target=voice_assistant_loop, args=(self.voice_stop_event,), daemon=True)
            self.voice_thread.start()
            self.voice_assistant_running = True
            self.voice_button.config(text="Stop Narada")
            self.voice_status_label.config(text="Status: Listening")
            self.listening_indicator.config(foreground="green")
            # Log message
            from common import append_voice_log
            append_voice_log("Narada started via GUI.")
        else:
            if self.voice_stop_event:
                self.voice_stop_event.set()
            self.voice_assistant_running = False
            self.voice_button.config(text="Start Narada")
            self.voice_status_label.config(text="Status: Not Listening")
            self.listening_indicator.config(foreground="red")
            from common import append_voice_log
            append_voice_log("Narada stopped via GUI.")

    def clear_narada_log(self):
        from common import voice_assistant_log, voice_responses, append_voice_log
        voice_assistant_log.clear()
        voice_responses.clear()
        update_text_widget(self.voice_text, "")
        update_text_widget(self.response_text, "")
        append_voice_log("Narada log cleared.")

    def show_instructions(self):
        instructions = (
            "Narada Instructions:\n\n"
            "- Say 'hi' or 'hello' to greet Narada.\n"
            "- Say 'how are you' for a status update.\n"
            "- Say 'what's your name' to know my identity.\n"
            "- Say 'what time is it' to get the current time.\n"
            "- Say 'activate dnd' to enable Do Not Disturb mode.\n"
            "- Say 'deactivate dnd' to disable DND mode.\n"
            "- Say 'activate email off' to turn off email notifications.\n"
            "- Say 'deactivate email off' to turn them on.\n"
            "- Say 'activate idle' to disable all alerts.\n"
            "- Say 'deactivate idle' to re-enable alerts.\n"
            "- Say 'activate night mode' to enable high priority mode.\n"
            "- Say 'deactivate night mode' to disable night mode.\n"
            "- Say 'what were your findings last night' to hear a summary of incidents.\n"
            "- Say 'weather' to get the current weather update.\n"
        )
        instr_win = tk.Toplevel(self)
        instr_win.title("Narada Instructions")
        instr_win.geometry("500x300")
        text_widget = tk.Text(instr_win, wrap="word")
        scroll = tk.Scrollbar(instr_win, orient="vertical", command=text_widget.yview)
        text_widget.configure(yscrollcommand=scroll.set)
        text_widget.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        text_widget.insert("end", instructions)
        btn_close = ttk.Button(instr_win, text="Close", command=instr_win.destroy)
        btn_close.pack(pady=5)

    def update_narada_tab(self):
        update_text_widget(self.voice_text, "\n".join(voice_assistant_log))
        if not self.voice_assistant_running:
            update_text_widget(self.response_text, "Narada is sleeping. Please wake Narada to receive responses.\n")
        else:
            update_text_widget(self.response_text, "\n".join(voice_responses))

    def update_gui(self):
        now = datetime.datetime.now()
        greeting = "Good Morning" if now.hour < 12 else "Good Afternoon" if now.hour < 18 else "Good Evening"
        self.greeting_label.config(text=f"Dear Manikanta, {greeting}! Welcome to Garuda v2.5.")
        update_text_widget(self.detection_text, latest_detection_info)
        update_text_widget(self.console_text, "\n".join(system_updates_log[-20:]))
        self.update_narada_tab()
        self.after(100, self.update_gui)

