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

##############################################################################
# Global Variables
##############################################################################
app = None
state_lock = threading.Lock()
SCISSORS_LOG_FILE = "danger_sightings.txt"

# GPIO objects
green_led = None
red_led = None
buzzer = None
stop_button = None

##############################################################################
# user_app_callback_class
##############################################################################
class user_app_callback_class(app_callback_class):
    """
    Minimal callback class with references to 'scissors' as the danger label.
    """
    def __init__(self):
        super().__init__()
        self.new_variable = 42
        self.person_detected = False
        self.danger_label = "scissors"

    def new_function(self):
        return "The meaning of life is: "

##############################################################################
# GStreamer Pad Callback
##############################################################################
def app_callback(pad, info, user_data):
    buffer = info.get_buffer()
    if buffer is None:
        return Gst.PadProbeReturn.OK

    user_data.increment()
    frame_count = user_data.get_count()
    string_to_print = f"Frame count: {frame_count}\n"

    # Retrieve frame if needed
    format_, width, height = get_caps_from_pad(pad)
    frame = None
    if user_data.use_frame and format_ and width and height:
        frame = get_numpy_from_buffer(buffer, format_, width, height)

    # Retrieve detection objects
    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)

    person_detected_in_frame = False

    for detection in detections:
        label = detection.get_label()
        confidence = detection.get_confidence()

        # Person detection (just for demonstration/logging)
        if label == "person":
            person_detected_in_frame = True
            string_to_print += (
                f"Person detected (confidence: {confidence:.2f})\n"
            )

        # Danger detection => "scissors"
        if label == user_data.danger_label:
            string_to_print += (
                f"Dangerous object detected: {label} {confidence:.2f}\n"
            )
            beep_and_red_led()
            log_scissors_detection()
            # Continue detection => no pipeline stop

    user_data.person_detected = person_detected_in_frame

    # Annotate frames if needed
    if user_data.use_frame and frame is not None:
        cv2.putText(
            frame,
            f"Frame: {frame_count}",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            1, (0, 255, 0), 2
        )
        cv2.putText(
            frame,
            f"{user_data.new_function()} {user_data.new_variable}",
            (10, 60),
            cv2.FONT_HERSHEY_SIMPLEX,
            1, (0, 255, 0), 2
        )
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        user_data.set_frame(frame)

    print(string_to_print)
    return Gst.PadProbeReturn.OK

##############################################################################
# Danger => beep & red LED => Log => keep running
##############################################################################
def beep_and_red_led():
    print("\n*** DANGER! SCISSORS DETECTED! ***\n"
          "Beware, for the metallic blades are near...\n"
          "Continuing detection but raising alert!\n")

    red_led.on()
    for _ in range(4):
        buzzer.on()
        time.sleep(0.3)
        buzzer.off()
        time.sleep(0.2)
    red_led.off()

def log_scissors_detection():
    now = datetime.datetime.now()
    timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp_str}] SCISSORS DETECTED\n"
    with open(SCISSORS_LOG_FILE, "a") as f:
        f.write(entry)
    print(f"Logged scissors detection at {timestamp_str} to '{SCISSORS_LOG_FILE}'")

##############################################################################
# Stop the entire pipeline if button pressed
##############################################################################
def stop_and_exit():
    print("Stopping GStreamer pipeline now...")
    if app is not None:
        app.pipeline.set_state(Gst.State.NULL)
    print("Exiting program with sys.exit(0)...")
    sys.exit(0)

def button_pressed():
    print("Push button pressed! Stopping application...")
    stop_and_exit()

##############################################################################
# Blink Green LED in separate thread
##############################################################################
def blink_green_led():
    green_led.off()
    while True:
        green_led.toggle()
        time.sleep(0.5)

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
                "libcamerasrc name=src_0 auto-focus-mode=2 ! "
                f"video/x-raw, format={self.network_format}, width=1536, height=864 ! "
                + QUEUE("queue_src_scale")
                + "videoscale ! "
                f"video/x-raw, format={self.network_format}, "
                f"width={self.network_width}, height={self.network_height}, framerate=30/1 ! "
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
        print("GStreamer Pipeline:\n", pipeline_string, "\n")
        return pipeline_string

if __name__ == "__main__":
    print("Initializing GPIO devices...")

    # Create user_data
    user_data = user_app_callback_class()

    # Green & Red LEDs
    green_led = LED(21)
    red_led = LED(20)
    red_led.off()

    # Active Buzzer on GPIO 12
    buzzer = OutputDevice(pin=12, initial_value=False)

    # Push Button => stops entire program if pressed
    stop_button = Button(16, pull_up=True)
    stop_button.when_pressed = button_pressed

    # Start a thread to blink the green LED
    threading.Thread(target=blink_green_led).start()

    # Parse arguments and run pipeline
    parser = get_default_parser()
    parser.add_argument(
        "--network",
        default="yolov8s",
        choices=["yolov6n", "yolov8s", "yolox_s_leaky"],
        help="Which Network to use, default is yolov8s",
    )
    parser.add_argument(
        "--hef-path",
        default=None,
        help="Path to HEF file",
    )
    parser.add_argument(
        "--labels-json",
        default=None,
        help="Path to custom labels JSON file",
    )
    args = parser.parse_args()

    # Create GStreamerDetectionApp
    from hailo_rpi_common import GStreamerApp
    app = GStreamerDetectionApp(args, user_data)

    try:
        app.run()  # Blocks until pipeline stops
    except KeyboardInterrupt:
        print("KeyboardInterrupt received, stopping pipeline and exiting.")
        stop_and_exit()

