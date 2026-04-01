import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
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

# PyQt5 Imports
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QPushButton, QLineEdit, QTextEdit, QStackedWidget, 
    QFrame, QCheckBox, QTabWidget, QListWidget, QMessageBox, 
    QScrollArea, QProgressBar, QGridLayout, QGroupBox, QDialog
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject, QSize
from PyQt5.QtGui import QFont, QColor, QPalette, QImage, QPixmap

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
EMAIL_SENDER = os.environ.get("EMAIL_SENDER", "")
EMAIL_SENDER_PASS = os.environ.get("EMAIL_SENDER_PASS", "")
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

# Wake word
NARADA_WAKE_WORD = "narada"

# GStreamer references
red_led = None
buzzer = None
stop_button = None

# For voice assistant updating UI
dashboard_gui = None

# Users
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
# PYQT SIGNALS BRIDGE
##############################################################################
class Communicate(QObject):
    update_log = pyqtSignal(str)
    update_voice = pyqtSignal(str)
    update_response = pyqtSignal(str)
    sync_modes = pyqtSignal()
    new_frame = pyqtSignal(np.ndarray)

comm = Communicate()

##############################################################################
# HELPER & LOGGING FUNCTIONS
##############################################################################
def log_system_update(message):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {message}"
    system_updates_log.append(entry)
    comm.update_log.emit(entry)

def append_voice_log(message, user_name=None):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {message}"
    voice_assistant_log.append(entry)
    if user_name and user_name in USERS:
        USERS[user_name]["history"]["narada_activity"].append(entry)
    comm.update_voice.emit(entry)

def append_voice_response(message, user_name=None):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {message}"
    voice_responses.append(entry)
    if user_name and user_name in USERS:
        USERS[user_name]["history"]["narada_activity"].append(entry)
    comm.update_response.emit(entry)

def stop_and_exit():
    print("Stopping GStreamer pipeline now...")
    if app is not None:
        app.pipeline.set_state(Gst.State.NULL)
    QApplication.quit()
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

    if MODE_NIGHT:
        try:
            with open(NIGHT_MODE_LOG_FILE, "a") as f:
                f.write(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
        except Exception as e:
            log_system_update("Error logging night mode incident: " + str(e))

    duration = 5
    if MODE_NIGHT: duration = 10
    if MODE_EMERGENCY: duration = 15

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
    if MODE_EMERGENCY: subject = "EMERGENCY: " + subject
    elif MODE_NIGHT: subject = "HIGH PRIORITY: " + subject

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

    danger_found = False
    for d in detections:
        label = d.get_label()
        confidence = d.get_confidence()
        text_info += f"{label} detected (conf={confidence:.2f})\n"
        if label == user_data.danger_label:
            danger_found = True

    if danger_found:
        threading.Thread(target=beep_and_red_led, daemon=True).start()
        threading.Thread(target=log_scissors_detection, daemon=True).start()
        threading.Thread(target=send_email_alert, daemon=True).start()

    user_data.person_detected = any(d.get_label() == "person" for d in detections)

    if frame is not None:
        cv2.putText(frame, f"Frame: {frame_num}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        cv2.putText(frame, f"{user_data.new_function()} {user_data.new_variable}",
                    (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        user_data.set_frame(frame)
        comm.new_frame.emit(frame)

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
# VOICE ASSISTANT LOOP
##############################################################################
def voice_assistant_loop(stop_event, current_user=None):
    recognizer = sr.Recognizer()
    try:
        mic = sr.Microphone()
        append_voice_log("USB Microphone connected.", user_name=current_user)
    except Exception as e:
        append_voice_log("Error accessing microphone: " + str(e), user_name=current_user)
        return

    with mic as source:
        recognizer.adjust_for_ambient_noise(source)

    while not stop_event.is_set():
        with mic as source:
            try:
                audio = recognizer.listen(source, timeout=5, phrase_time_limit=5)
            except sr.WaitTimeoutError:
                continue

        try:
            user_input = recognizer.recognize_google(audio)
            append_voice_log(f"You said: {user_input}", user_name=current_user)
        except:
            continue

        user_input_lower = user_input.lower()
        response = None

        global MODE_DND, MODE_EMAIL_OFF, MODE_IDLE, MODE_NIGHT, MODE_EMERGENCY

        if "deactivate dnd" in user_input_lower:
            MODE_DND = False
            response = "DND deactivated."
        elif "activate dnd" in user_input_lower:
            MODE_DND = True
            response = "DND activated."
        elif "activate night mode" in user_input_lower:
            MODE_NIGHT = True
            response = "Night mode enabled."
        elif "deactivate night mode" in user_input_lower:
            MODE_NIGHT = False
            response = "Night mode disabled."
        elif "activate emergency mode" in user_input_lower:
            MODE_EMERGENCY = True
            response = "Emergency mode activated!"
        elif "deactivate emergency mode" in user_input_lower:
            MODE_EMERGENCY = False
            response = "Emergency mode off."
        elif "time" in user_input_lower:
            response = "It's " + datetime.datetime.now().strftime("%I:%M %p") + "."
        elif "hello" in user_input_lower or "hi" in user_input_lower:
            response = f"Hello! I am {NARADA_WAKE_WORD.title()}."
        else:
            response = "Command received."

        if MODE_EMERGENCY or MODE_NIGHT: MODE_DND = False
        
        append_voice_response(response, user_name=current_user)
        comm.sync_modes.emit()

##############################################################################
# STYLESHEET
##############################################################################
DARK_STYLE = """
QMainWindow, QWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
    font-family: 'Segoe UI', sans-serif;
}
QGroupBox {
    border: 2px solid #45475a;
    border-radius: 8px;
    margin-top: 1ex;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    padding: 0 3px;
    color: #89b4fa;
}
QPushButton {
    background-color: #89b4fa;
    color: #11111b;
    border-radius: 5px;
    padding: 8px 15px;
    font-weight: bold;
}
QPushButton:hover {
    background-color: #b4befe;
}
QLineEdit {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 5px;
    color: #cdd6f4;
}
QTextEdit {
    background-color: #181825;
    border: 1px solid #45475a;
    border-radius: 4px;
    color: #a6adc8;
}
QProgressBar {
    border: 1px solid #45475a;
    border-radius: 5px;
    text-align: center;
    background-color: #313244;
}
QProgressBar::chunk {
    background-color: #f38ba8;
    width: 10px;
}
QTabWidget::pane { border: 1px solid #45475a; }
QTabBar::tab {
    background: #313244;
    padding: 10px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}
QTabBar::tab:selected {
    background: #45475a;
    color: #89b4fa;
}
"""

##############################################################################
# LOGIN SCREENS
##############################################################################
class ProfileSelector(QWidget):
    def __init__(self, parent):
        super().__init__()
        self.parent = parent
        layout = QVBoxLayout()
        
        label = QLabel("GARUDA SECURITY")
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("font-size: 32px; font-weight: bold; color: #f38ba8; margin: 40px;")
        layout.addWidget(label)
        
        btn_layout = QHBoxLayout()
        self.btn_user = QPushButton("USER")
        self.btn_user.setFixedSize(200, 200)
        self.btn_user.setStyleSheet("font-size: 24px; background-color: #89b4fa;")
        self.btn_user.clicked.connect(lambda: parent.setCurrentIndex(1))
        
        self.btn_admin = QPushButton("ADMIN")
        self.btn_admin.setFixedSize(200, 200)
        self.btn_admin.setStyleSheet("font-size: 24px; background-color: #fab387;")
        self.btn_admin.clicked.connect(lambda: parent.setCurrentIndex(2))
        
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_user)
        btn_layout.addSpacing(50)
        btn_layout.addWidget(self.btn_admin)
        btn_layout.addStretch()
        
        layout.addLayout(btn_layout)
        
        exit_btn = QPushButton("EXIT")
        exit_btn.setFixedWidth(100)
        exit_btn.setStyleSheet("background-color: #f38ba8;")
        exit_btn.clicked.connect(stop_and_exit)
        layout.addWidget(exit_btn, alignment=Qt.AlignCenter)
        layout.addStretch()
        
        self.setLayout(layout)

class LoginWidget(QWidget):
    def __init__(self, parent, role="user"):
        super().__init__()
        self.parent = parent
        self.role = role
        layout = QVBoxLayout()
        
        self.label = QLabel(f"{role.upper()} LOGIN")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("font-size: 24px; font-weight: bold; color: #89b4fa; margin: 20px;")
        layout.addWidget(self.label)
        
        form = QGridLayout()
        form.addWidget(QLabel("Username:"), 0, 0)
        self.uname = QLineEdit()
        form.addWidget(self.uname, 0, 1)
        
        form.addWidget(QLabel("Password:"), 1, 0)
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        form.addWidget(self.pw, 1, 1)
        
        layout.addLayout(form)
        
        btn_box = QHBoxLayout()
        login_btn = QPushButton("LOGIN")
        login_btn.clicked.connect(self.do_login)
        btn_box.addWidget(login_btn)
        
        back_btn = QPushButton("BACK")
        back_btn.clicked.connect(lambda: parent.setCurrentIndex(0))
        btn_box.addWidget(back_btn)
        
        layout.addLayout(btn_box)
        
        if role == "user":
            forgot_btn = QPushButton("Forgot Password?")
            forgot_btn.setFlat(True)
            forgot_btn.clicked.connect(self.forgot_pw)
            layout.addWidget(forgot_btn)
            
        layout.addStretch()
        self.setLayout(layout)
        self.setMaximumWidth(400)

    def do_login(self):
        un = self.uname.text().strip()
        pw = self.pw.text().strip()
        if un in USERS and USERS[un]["password"] == pw and USERS[un]["role"] == self.role:
            if self.role == "admin":
                global ADMIN_OTP
                ADMIN_OTP = generate_otp_code(6)
                send_otp_via_email(EMAIL_SENDER, ADMIN_OTP)
                QMessageBox.information(self, "OTP Sent", "Admin OTP has been sent to your email.")
                # Show OTP dialog
                otp, ok = QDialog.getStaticText(self, "OTP Verification", "Enter 6-digit OTP:") # simplified for script
                # Real implementation should be a QDialog
                dlg = OTPDialog(self, ADMIN_OTP)
                if dlg.exec_():
                    self.parent.start_app(True, un)
            else:
                self.parent.start_app(False, un)
        else:
            QMessageBox.critical(self, "Error", "Invalid credentials")

    def forgot_pw(self):
        global USER_FORGOT_OTP
        USER_FORGOT_OTP = generate_otp_code(6)
        send_otp_via_email(EMAIL_SENDER, USER_FORGOT_OTP)
        QMessageBox.information(self, "OTP Sent", "OTP sent. Default pass will be 'user123' if verified.")
        dlg = OTPDialog(self, USER_FORGOT_OTP)
        if dlg.exec_():
            USERS["user"]["password"] = "user123"
            QMessageBox.information(self, "Success", "Password reset to 'user123'")

class OTPDialog(QDialog):
    def __init__(self, parent, target_otp):
        super().__init__(parent)
        self.target_otp = target_otp
        self.setWindowTitle("OTP Verification")
        layout = QVBoxLayout()
        layout.addWidget(QLabel("Enter the 6-digit code:"))
        self.entry = QLineEdit()
        layout.addWidget(self.entry)
        btn = QPushButton("VERIFY")
        btn.clicked.connect(self.verify)
        layout.addWidget(btn)
        self.setLayout(layout)

    def verify(self):
        if self.entry.text().strip() == self.target_otp:
            self.accept()
        else:
            QMessageBox.warning(self, "Error", "Invalid OTP")

##############################################################################
# MAIN DASHBOARDS
##############################################################################
class ModernDashboard(QMainWindow):
    def __init__(self, is_admin, username, user_data):
        super().__init__()
        self.is_admin = is_admin
        self.username = username
        self.user_data = user_data
        self.setWindowTitle(f"GARUDA SECURITY - {username.upper()}")
        self.resize(1200, 800)
        self.setStyleSheet(DARK_STYLE)

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        self.layout = QHBoxLayout(main_widget)

        # Sidebar
        self.sidebar = QVBoxLayout()
        self.layout.addLayout(self.sidebar, 1)
        
        self.lbl_user = QLabel(f"👤 {username.upper()}")
        self.lbl_user.setStyleSheet("font-size: 18px; font-weight: bold; color: #89b4fa; padding: 10px;")
        self.sidebar.addWidget(self.lbl_user)

        self.btn_mon = QPushButton("📊 MONITORING")
        self.btn_mon.clicked.connect(lambda: self.tabs.setCurrentIndex(0))
        self.sidebar.addWidget(self.btn_mon)

        self.btn_narada = QPushButton("🎙️ NARADA")
        self.btn_narada.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
        self.sidebar.addWidget(self.btn_narada)

        if is_admin:
            self.btn_admin = QPushButton("⚙️ ADMIN PANEL")
            self.btn_admin.clicked.connect(lambda: self.tabs.setCurrentIndex(2))
            self.sidebar.addWidget(self.btn_admin)

        self.sidebar.addStretch()
        
        logout_btn = QPushButton("🚪 LOGOUT")
        logout_btn.setStyleSheet("background-color: #f38ba8;")
        logout_btn.clicked.connect(self.logout)
        self.sidebar.addWidget(logout_btn)

        # Main Content Area
        self.tabs = QStackedWidget()
        self.layout.addWidget(self.tabs, 5)

        self.setup_monitoring_tab()
        self.setup_narada_tab()
        if is_admin:
            self.setup_admin_tab()

        # Connect signals
        comm.update_log.connect(self.append_log)
        comm.update_voice.connect(self.append_voice)
        comm.update_response.connect(self.append_response)
        comm.sync_modes.connect(self.sync_modes_ui)
        comm.new_frame.connect(self.update_video)

        # Hardware update timer
        self.hw_timer = QTimer()
        self.hw_timer.timeout.connect(self.update_hardware_stats)
        self.hw_timer.start(2000)

        # Voice state
        self.voice_running = False
        self.voice_stop_event = None

    def setup_monitoring_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        
        # Top: Video & Detections
        top_row = QHBoxLayout()
        
        # Video placeholder
        self.video_label = QLabel("Waiting for AI Stream...")
        self.video_label.setFixedSize(640, 480)
        self.video_label.setStyleSheet("background-color: black; border: 2px solid #89b4fa;")
        self.video_label.setAlignment(Qt.AlignCenter)
        top_row.addWidget(self.video_label)
        
        # Mode Controls
        modes_box = QGroupBox("SYSTEM MODES")
        modes_layout = QVBoxLayout()
        self.chk_dnd = QCheckBox("Do Not Disturb")
        self.chk_email = QCheckBox("Email Notifications")
        self.chk_idle = QCheckBox("Idle Mode")
        self.chk_night = QCheckBox("Night Mode")
        self.chk_emer = QCheckBox("Emergency Mode")
        
        for chk in [self.chk_dnd, self.chk_email, self.chk_idle, self.chk_night, self.chk_emer]:
            chk.stateChanged.connect(self.mode_changed)
            modes_layout.addWidget(chk)
        modes_box.setLayout(modes_layout)
        top_row.addWidget(modes_box)
        
        layout.addLayout(top_row)
        
        # Bottom: Logs
        bottom_row = QHBoxLayout()
        
        self.det_log = QTextEdit()
        self.det_log.setReadOnly(True)
        self.det_log.setPlaceholderText("Detection Details...")
        bottom_row.addWidget(self.det_log)
        
        self.sys_log = QTextEdit()
        self.sys_log.setReadOnly(True)
        self.sys_log.setPlaceholderText("System Events...")
        bottom_row.addWidget(self.sys_log)
        
        layout.addLayout(bottom_row)
        self.tabs.addWidget(page)

    def setup_narada_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        
        header = QHBoxLayout()
        self.btn_voice_ctrl = QPushButton("START NARADA")
        self.btn_voice_ctrl.clicked.connect(self.toggle_voice)
        header.addWidget(self.btn_voice_ctrl)
        
        self.voice_status = QLabel("● OFFLINE")
        self.voice_status.setStyleSheet("color: #f38ba8; font-weight: bold;")
        header.addWidget(self.voice_status)
        layout.addLayout(header)
        
        layout.addWidget(QLabel("Narada Transcript:"))
        self.voice_log = QTextEdit()
        self.voice_log.setReadOnly(True)
        layout.addWidget(self.voice_log)
        
        layout.addWidget(QLabel("Narada Response:"))
        self.voice_resp = QTextEdit()
        self.voice_resp.setReadOnly(True)
        self.voice_resp.setMaximumHeight(100)
        layout.addWidget(self.voice_resp)
        
        self.tabs.addWidget(page)

    def setup_admin_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        
        admin_tabs = QTabWidget()
        
        # User Mgmt
        u_tab = QWidget()
        u_lay = QHBoxLayout(u_tab)
        self.user_list = QListWidget()
        self.refresh_users()
        u_lay.addWidget(self.user_list)
        
        u_ctrl = QVBoxLayout()
        self.new_un = QLineEdit(); self.new_un.setPlaceholderText("Username")
        self.new_pw = QLineEdit(); self.new_pw.setPlaceholderText("Password")
        u_ctrl.addWidget(self.new_un); u_ctrl.addWidget(self.new_pw)
        
        btn_add = QPushButton("Add User"); btn_add.clicked.connect(self.add_user)
        u_ctrl.addWidget(btn_add)
        u_lay.addLayout(u_ctrl)
        admin_tabs.addTab(u_tab, "Users")
        
        # Hardware
        h_tab = QWidget()
        h_lay = QVBoxLayout(h_tab)
        self.cpu_bar = QProgressBar(); h_lay.addWidget(QLabel("CPU Usage")); h_lay.addWidget(self.cpu_bar)
        self.mem_bar = QProgressBar(); h_lay.addWidget(QLabel("Memory Usage")); h_lay.addWidget(self.mem_bar)
        self.disk_bar = QProgressBar(); h_lay.addWidget(QLabel("Disk Usage")); h_lay.addWidget(self.disk_bar)
        admin_tabs.addTab(h_tab, "Hardware")
        
        layout.addWidget(admin_tabs)
        self.tabs.addWidget(page)

    def update_video(self, frame):
        h, w, ch = frame.shape
        bytes_per_line = ch * w
        q_img = QImage(frame.data, w, h, bytes_per_line, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(q_img)
        self.video_label.setPixmap(pixmap.scaled(self.video_label.size(), Qt.KeepAspectRatio))
        self.det_log.setText(latest_detection_info)

    def append_log(self, msg):
        self.sys_log.append(msg)
    
    def append_voice(self, msg):
        self.voice_log.append(msg)
        
    def append_response(self, msg):
        self.voice_resp.append(msg)

    def mode_changed(self):
        global MODE_DND, MODE_EMAIL_OFF, MODE_IDLE, MODE_NIGHT, MODE_EMERGENCY
        MODE_DND = self.chk_dnd.isChecked()
        MODE_EMAIL_OFF = self.chk_email.isChecked()
        MODE_IDLE = self.chk_idle.isChecked()
        MODE_NIGHT = self.chk_night.isChecked()
        MODE_EMERGENCY = self.chk_emer.isChecked()

    def sync_modes_ui(self):
        self.chk_dnd.setChecked(MODE_DND)
        self.chk_email.setChecked(MODE_EMAIL_OFF)
        self.chk_idle.setChecked(MODE_IDLE)
        self.chk_night.setChecked(MODE_NIGHT)
        self.chk_emer.setChecked(MODE_EMERGENCY)

    def toggle_voice(self):
        if not self.voice_running:
            self.voice_stop_event = threading.Event()
            threading.Thread(target=voice_assistant_loop, args=(self.voice_stop_event, self.username), daemon=True).start()
            self.voice_running = True
            self.btn_voice_ctrl.setText("STOP NARADA")
            self.voice_status.setText("● LISTENING")
            self.voice_status.setStyleSheet("color: #a6e3a1;")
        else:
            self.voice_stop_event.set()
            self.voice_running = False
            self.btn_voice_ctrl.setText("START NARADA")
            self.voice_status.setText("● OFFLINE")
            self.voice_status.setStyleSheet("color: #f38ba8;")

    def update_hardware_stats(self):
        if psutil and self.is_admin and hasattr(self, 'cpu_bar'):
            self.cpu_bar.setValue(int(psutil.cpu_percent()))
            self.mem_bar.setValue(int(psutil.virtual_memory().percent))
            self.disk_bar.setValue(int(psutil.disk_usage('/').percent))

    def refresh_users(self):
        self.user_list.clear()
        for u in USERS.keys():
            self.user_list.addItem(u)

    def add_user(self):
        u = self.new_un.text().strip()
        p = self.new_pw.text().strip()
        if u and p:
            USERS[u] = {"password": p, "role": "user", "history": {"logins": [], "narada_activity": []}}
            self.refresh_users()
            log_system_update(f"Admin added user {u}")

    def logout(self):
        self.close()
        # In a real app, we'd go back to login stack, but here we can just restart the login flow
        main_win.show_login()

class MainAppFlow(QStackedWidget):
    def __init__(self):
        super().__init__()
        self.main_window = None
        self.selector = ProfileSelector(self)
        self.user_login = LoginWidget(self, "user")
        self.admin_login = LoginWidget(self, "admin")
        
        self.addWidget(self.selector)
        self.addWidget(self.user_login)
        self.addWidget(self.admin_login)
        
        self.setWindowTitle("GARUDA SECURITY SYSTEM")
        self.resize(800, 600)
        self.setStyleSheet(DARK_STYLE)

    def start_app(self, is_admin, username):
        self.hide()
        self.dashboard = ModernDashboard(is_admin, username, user_data_global)
        self.dashboard.show()

    def show_login(self):
        self.setCurrentIndex(0)
        self.show()

##############################################################################
# MAIN
##############################################################################
user_data_global = None
main_win = None

def run_main_app():
    global user_data_global, main_win, app, red_led, buzzer, stop_button
    
    user_data_global = user_app_callback_class()
    parser = get_default_parser()
    parser.add_argument("--video_source", default="usb", help="Set 'rpi' or device path.")
    parser.add_argument("--network", default="yolov8s", choices=["yolov6n", "yolov8s", "yolox_s_leaky"])
    parser.add_argument("--hef-path", default=None)
    parser.add_argument("--labels-json", default=None)
    args = parser.parse_args()

    Gst.init(None)
    red_led = LED(20); red_led.off()
    buzzer = OutputDevice(pin=12, initial_value=False)
    stop_button = Button(16, pull_up=True)
    stop_button.when_pressed = button_pressed

    app = GStreamerDetectionApp(args, user_data_global)
    threading.Thread(target=app.run, daemon=True).start()

    qt_app = QApplication(sys.argv)
    main_win = MainAppFlow()
    main_win.show()
    sys.exit(qt_app.exec_())

if __name__ == "__main__":
    run_main_app()