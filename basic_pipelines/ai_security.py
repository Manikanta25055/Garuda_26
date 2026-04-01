import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib
import os
import argparse
import threading
import numpy as np
import setproctitle
import cv2
import time
import hailo
from gpiozero import DistanceSensor, Servo, LED, Button, MCP3008
from gpiozero.pins.pigpio import PiGPIOFactory
from hailo_rpi_common import (
    get_default_parser,
    QUEUE,
    get_caps_from_pad,
    get_numpy_from_buffer,
    GStreamerApp,
    app_callback_class,
)

# -----------------------------------------------------------------------------------------------
# Hardware Setup
# -----------------------------------------------------------------------------------------------
factory = PiGPIOFactory()

# LEDs
green_led = LED(21, pin_factory=factory)  # System running indicator
red_led = LED(20, pin_factory=factory)    # Danger indicator

# Passive Buzzer
buzzer = LED(12, pin_factory=factory)

# Reset Button
reset_button = Button(16, pin_factory=factory)

# Servo Motor
servo = Servo(17, pin_factory=factory, min_pulse_width=0.5/1000, max_pulse_width=2.5/1000)

# Ultrasonic Sensor
sensor = DistanceSensor(echo=24, trigger=18, pin_factory=factory)

# Joystick (via MCP3008 ADC)
joystick_x = MCP3008(channel=0)  # X-axis

# -----------------------------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------------------------

def map_value(value, in_min, in_max, out_min, out_max):
    """Maps a value from one range to another."""
    return out_min + (float(value - in_min) / float(in_max - in_min) * (out_max - out_min))

def play_siren():
    """Plays a siren sound on the passive buzzer."""
    for _ in range(5):  # Beep 5 times
        buzzer.on()
        time.sleep(0.2)
        buzzer.off()
        time.sleep(0.2)

def stop_program():
    """Stops the program and plays a tone when the reset button is pressed."""
    print("Reset button pressed. Stopping program...")
    buzzer.on()
    time.sleep(1)  # Play a tone for 1 second
    buzzer.off()
    os._exit(0)  # Stop the script immediately

# Attach reset function to button
reset_button.when_pressed = stop_program

# -----------------------------------------------------------------------------------------------
# User-defined class to be used in the callback function
# -----------------------------------------------------------------------------------------------
class user_app_callback_class(app_callback_class):
    def __init__(self):
        super().__init__()
        self.person_detected = False
        self.dangerous_object_detected = False

# -----------------------------------------------------------------------------------------------
# Callback Function for Detection
# -----------------------------------------------------------------------------------------------
def app_callback(pad, info, user_data):
    buffer = info.get_buffer()
    if buffer is None:
        return Gst.PadProbeReturn.OK

    user_data.increment()  # Increment frame count
    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)

    for detection in detections:
        label = detection.get_label()
        confidence = detection.get_confidence()

        if label == "person":
            print(f"Person detected with confidence: {confidence:.2f}")

        if label == "scissors":
            print(f"DANGER! Scissors detected with confidence: {confidence:.2f}")
            red_led.on()
            play_siren()
            print("STOPPING PROGRAM: Dangerous object detected.")
            stop_program()

    return Gst.PadProbeReturn.OK

# -----------------------------------------------------------------------------------------------
# GStreamer Detection Application
# -----------------------------------------------------------------------------------------------
class GStreamerDetectionApp(GStreamerApp):
    def __init__(self, args, user_data):
        super().__init__(args, user_data)
        self.batch_size = 2
        self.network_width = 640
        self.network_height = 640
        self.network_format = "RGB"
        nms_score_threshold = 0.3
        nms_iou_threshold = 0.45
        
        # Postprocess shared object file path
        postprocess_path = os.path.join(self.current_path, '../resources/libyolo_hailortpp_post.so')
        if not os.path.exists(postprocess_path):
            print("YOLO postprocess shared object file not found.")
            exit(1)
        self.default_postprocess_so = postprocess_path

        # Load the model file
        if args.hef_path is not None:
            self.hef_path = args.hef_path
        else:
            print("No HEF file path provided!")
            exit(1)
        
        self.app_callback = app_callback  # Attach the app callback
        self.create_pipeline()

    def get_pipeline_string(self):
        source_element = (
            "libcamerasrc name=src_0 auto-focus-mode=2 ! "
            f"video/x-raw, format={self.network_format}, width=1536, height=864 ! "
            + QUEUE("queue_src_scale")
            + "videoscale ! "
            f"video/x-raw, format={self.network_format}, width={self.network_width}, height={self.network_height}, framerate=30/1 ! "
        )
        pipeline_string = (
            source_element
            + QUEUE("queue_hailonet")
            + f"hailonet hef-path={self.hef_path} batch-size={self.batch_size} {self.thresholds_str} force-writable=true ! "
            + QUEUE("queue_hailofilter")
            + f"hailofilter so-path={self.default_postprocess_so} ! "
            + QUEUE("queue_user_callback")
            + "identity name=identity_callback ! "
        )
        return pipeline_string

# -----------------------------------------------------------------------------------------------
# Main Function
# -----------------------------------------------------------------------------------------------
if __name__ == "__main__":
    # Green LED indicates the system is running
    green_led.on()

    # Servo Control and Ultrasonic Sensor Loop
    try:
        while True:
            # Joystick X-axis controls servo motor
            x_position = joystick_x.value  # Joystick X-axis value (0 to 1)
            angle = map_value(x_position, 0, 1, -1, 1)  # Map to servo range (-1 to 1)
            servo.value = angle

            # Ultrasonic sensor measures distance
            distance = sensor.distance * 100  # Convert to cm
            print(f"Distance: {distance:.2f} cm")

            time.sleep(0.1)

    except KeyboardInterrupt:
        print("Exiting gracefully...")
        green_led.off()
        servo.detach()
        red_led.off()
        buzzer.off()
        os._exit(0)

    # Start Hailo Detection
    user_data = user_app_callback_class()
    parser = get_default_parser()
    args = parser.parse_args()
    app = GStreamerDetectionApp(args, user_data)
    app.run()
