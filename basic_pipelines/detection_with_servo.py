#!/usr/bin/env python3
"""
Hailo Detection Pipeline with Servo Control

This script implements an object detection pipeline with servo control.
The servo automatically pans to follow detected objects (person, etc.) based on their
position in the frame.

Features:
- Real-time object detection using YOLOv6n/YOLOv8s
- Automatic servo panning to track detected objects
- Configurable detection class and servo sensitivity
- GPIO LED feedback for detection
- FPS monitoring and frame display options

Usage:
    python detection_with_servo.py -i /dev/video0 --servo-pin 17
    python detection_with_servo.py -i rpi --target-class person
    python detection_with_servo.py -i video.mp4 --disable-sync --show-fps
"""

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib
import os
import argparse
import hailo
from hailo_rpi_common import (
    get_default_parser,
    QUEUE,
    get_caps_from_pad,
    get_numpy_from_buffer,
    GStreamerApp,
    app_callback_class,
)

# Hardware control
from gpiozero import Servo, LED, DistanceSensor
import threading
import time
import setproctitle
import cv2

# ============================================================================
# Global Variables for Thread-Safe Hardware Control
# ============================================================================
servo = None
led = None
sensor = None
servo_lock = threading.Lock()
motion_detected_flag = False
servo_center_position = 0.0  # Current servo position (-1.0 to 1.0)
servo_target_position = 0.0  # Target position for servo

# ============================================================================
# Servo Control Functions
# ============================================================================

def update_servo_position(target_position, servo_speed=0.05):
    """
    Smoothly move servo to target position.

    Args:
        target_position (float): Target servo position (-1.0 to 1.0)
        servo_speed (float): Movement speed per update (0.0 to 1.0)
    """
    global servo_center_position, servo

    if servo is None:
        return

    with servo_lock:
        # Clamp to valid range
        target_position = max(-1.0, min(1.0, target_position))

        # Smooth movement towards target
        direction = 1 if target_position > servo_center_position else -1
        movement = servo_speed * direction

        # Move servo incrementally
        new_position = servo_center_position + movement

        # Clamp to valid range
        new_position = max(-1.0, min(1.0, new_position))

        # Only update if movement is significant
        if abs(new_position - servo_center_position) > 0.01:
            servo.value = new_position
            servo_center_position = new_position
            print(f"Servo position: {servo_center_position:.2f}")


def calculate_servo_position(bbox_center_x, frame_width, sensitivity=1.0):
    """
    Calculate servo position based on object's horizontal position.

    Args:
        bbox_center_x (float): Center X coordinate of bounding box (0 to frame_width)
        frame_width (int): Width of the frame in pixels
        sensitivity (float): Servo movement sensitivity (0.1 to 2.0)

    Returns:
        float: Target servo position (-1.0 to 1.0)
    """
    # Normalize bbox center to -1.0 to 1.0
    # Left side of frame = -1.0, center = 0.0, right side = 1.0
    normalized_x = (bbox_center_x / frame_width) * 2.0 - 1.0

    # Apply sensitivity
    servo_position = normalized_x * sensitivity

    # Clamp to valid range
    servo_position = max(-1.0, min(1.0, servo_position))

    return servo_position


def motion_detected():
    """Callback when motion is detected by distance sensor."""
    global motion_detected_flag
    print("Motion detected!")
    with servo_lock:
        motion_detected_flag = True
    if led:
        led.on()


def no_motion_detected():
    """Callback when motion is no longer detected."""
    global motion_detected_flag
    print("No motion detected")
    with servo_lock:
        motion_detected_flag = False
    if led:
        led.off()


# ============================================================================
# Callback Class - Stores detection state
# ============================================================================

class user_app_callback_class(app_callback_class):
    """Extended callback class with servo tracking state."""

    def __init__(self):
        super().__init__()
        self.person_detected = False
        self.detection_count = 0
        self.last_detection_time = time.time()
        self.target_class = "person"  # Class to track


# ============================================================================
# Detection Callback Function
# ============================================================================

def app_callback(pad, info, user_data):
    """
    Main callback function for processing detections and controlling servo.

    This function:
    1. Extracts detections from the buffer
    2. Finds the target class (person, car, etc.)
    3. Calculates servo position based on object location
    4. Updates servo position
    5. Optionally displays results on frame
    """
    global servo_target_position, motion_detected_flag

    buffer = info.get_buffer()
    if buffer is None:
        return Gst.PadProbeReturn.OK

    # Frame counting
    user_data.increment()
    frame_count = user_data.get_count()

    # Get frame information
    format, width, height = get_caps_from_pad(pad)

    # Get video frame if requested
    frame = None
    if user_data.use_frame and format and width and height:
        frame = get_numpy_from_buffer(buffer, format, width, height)

    # Extract detections from buffer metadata
    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)

    # Process detections
    detection_count = 0
    person_detected = False
    largest_bbox = None
    largest_bbox_area = 0
    largest_bbox_center_x = 0

    for detection in detections:
        label = detection.get_label()
        confidence = detection.get_confidence()
        bbox = detection.get_bbox()

        # Check if this is our target class
        if label == user_data.target_class and confidence > 0.5:
            detection_count += 1
            person_detected = True
            user_data.last_detection_time = time.time()

            # Calculate bbox center and area
            bbox_center_x = bbox.xmin() + bbox.width() / 2.0
            bbox_area = bbox.width() * bbox.height()

            # Track the largest bounding box (closest/most prominent object)
            if bbox_area > largest_bbox_area:
                largest_bbox = bbox
                largest_bbox_area = bbox_area
                largest_bbox_center_x = bbox_center_x

            if user_data.use_frame:
                # Draw detection on frame
                xmin = int(bbox.xmin() * width)
                ymin = int(bbox.ymin() * height)
                xmax = int((bbox.xmin() + bbox.width()) * width)
                ymax = int((bbox.ymin() + bbox.height()) * height)

                # Draw bounding box
                cv2.rectangle(frame, (xmin, ymin), (xmax, ymax), (0, 255, 0), 2)

                # Draw label and confidence
                label_text = f"{label} {confidence:.2f}"
                cv2.putText(frame, label_text, (xmin, ymin - 10),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

    # Update servo position based on largest detected object
    if person_detected and largest_bbox is not None:
        # Calculate target servo position
        servo_target_position = calculate_servo_position(
            largest_bbox_center_x,
            1.0,  # Normalized coordinates (0-1)
            sensitivity=1.0
        )
        # Move servo smoothly
        update_servo_position(servo_target_position, servo_speed=0.05)
    else:
        # No detection - servo returns to center
        servo_target_position = 0.0
        update_servo_position(0.0, servo_speed=0.02)

    # Update detection status
    user_data.person_detected = person_detected

    # Display frame if requested
    if user_data.use_frame and frame is not None:
        # Add frame count and detection info
        info_text = f"Frame: {frame_count} | Detections: {detection_count}"
        cv2.putText(frame, info_text, (10, 30),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

        # Add servo position indicator
        servo_text = f"Servo: {servo_center_position:.2f}"
        cv2.putText(frame, servo_text, (10, 70),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 165, 255), 2)

        # Convert RGB to BGR for OpenCV and queue for display
        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        user_data.set_frame(frame_bgr)

    # Print detection summary
    print(f"Frame {frame_count}: {detection_count} {user_data.target_class}(s) detected, "
          f"Servo: {servo_center_position:.2f}, Target: {servo_target_position:.2f}")

    return Gst.PadProbeReturn.OK


# ============================================================================
# Gstreamer Application Class
# ============================================================================

class GStreamerDetectionServoApp(GStreamerApp):
    """
    Detection pipeline with servo control.

    Inherits from GStreamerApp and customizes the pipeline for object detection
    with servo pan control.
    """

    def __init__(self, args, user_data):
        """
        Initialize the detection app with servo control.

        Args:
            args: Command-line arguments
            user_data: Custom callback data object
        """
        super().__init__(args, user_data)

        # Set target class from arguments
        user_data.target_class = args.target_class

        # Model configuration
        self.batch_size = 2
        self.network_width = 640
        self.network_height = 640
        self.network_format = "RGB"

        # NMS thresholds
        nms_score_threshold = args.score_threshold
        nms_iou_threshold = args.iou_threshold

        # Post-processing library
        new_postprocess_path = os.path.join(
            self.current_path, '../resources/libyolo_hailortpp_post.so'
        )
        if os.path.exists(new_postprocess_path):
            self.default_postprocess_so = new_postprocess_path
        else:
            self.default_postprocess_so = os.path.join(
                self.postprocess_dir, 'libyolo_hailortpp_post.so'
            )

        # Model path
        if args.hef_path is not None:
            self.hef_path = args.hef_path
        elif args.network == "yolov6n":
            self.hef_path = os.path.join(
                self.current_path, '../resources/yolov6n.hef'
            )
        elif args.network == "yolov8s":
            self.hef_path = os.path.join(
                self.current_path, '../resources/yolov8s_h8l.hef'
            )
        elif args.network == "yolox_s_leaky":
            self.hef_path = os.path.join(
                self.current_path, '../resources/yolox_s_leaky_h8l_mz.hef'
            )
        else:
            raise ValueError(f"Unknown network: {args.network}")

        # Custom labels support
        if args.labels_json is not None:
            self.labels_config = f'config-path={args.labels_json}'
            if not os.path.exists(new_postprocess_path):
                print("Error: Custom labels require new postprocess library")
                exit(1)
        else:
            self.labels_config = ''

        # Callback function
        self.app_callback = app_callback

        # Thresholds
        self.thresholds_str = (
            f"nms-score-threshold={nms_score_threshold} "
            f"nms-iou-threshold={nms_iou_threshold} "
            f"output-format-type=HAILO_FORMAT_TYPE_FLOAT32"
        )

        # Set process title
        setproctitle.setproctitle("Hailo Detection Servo App")

        # Create pipeline
        self.create_pipeline()

    def get_pipeline_string(self):
        """Build the GStreamer pipeline string."""

        # Define video source based on input type
        if self.source_type == "rpi":
            source_element = (
                "libcamerasrc name=src_0 auto-focus-mode=2 ! "
                f"video/x-raw, format={self.network_format}, width=1536, height=864 ! "
                + QUEUE("queue_src_scale")
                + "videoscale ! "
                + f"video/x-raw, format={self.network_format}, width={self.network_width}, "
                + f"height={self.network_height}, framerate=30/1 ! "
            )
        elif self.source_type == "usb":
            source_element = (
                f"v4l2src device={self.video_source} name=src_0 ! "
                "video/x-raw, width=640, height=480, framerate=30/1 ! "
            )
        else:  # file
            source_element = (
                f"filesrc location={self.video_source} name=src_0 ! "
                + QUEUE("queue_dec264")
                + "qtdemux ! h264parse ! avdec_h264 max-threads=2 ! "
                + "video/x-raw, format=I420 ! "
            )

        # Add scaling and conversion
        source_element += QUEUE("queue_scale")
        source_element += "videoscale n-threads=2 ! "
        source_element += QUEUE("queue_src_convert")
        source_element += "videoconvert n-threads=3 name=src_convert qos=false ! "
        source_element += (
            f"video/x-raw, format={self.network_format}, "
            f"width={self.network_width}, height={self.network_height}, "
            "pixel-aspect-ratio=1/1 ! "
        )

        # Build complete pipeline
        pipeline_string = (
            "hailomuxer name=hmux "
            + source_element
            + "tee name=t ! "
            + QUEUE("bypass_queue", max_size_buffers=20)
            + "hmux.sink_0 "
            + "t. ! "
            + QUEUE("queue_hailonet")
            + "videoconvert n-threads=3 ! "
            + f"hailonet hef-path={self.hef_path} batch-size={self.batch_size} "
            + f"{self.thresholds_str} force-writable=true ! "
            + QUEUE("queue_hailofilter")
            + f"hailofilter so-path={self.default_postprocess_so} {self.labels_config} qos=false ! "
            + QUEUE("queue_hmux")
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
            + f"fpsdisplaysink video-sink=xvimagesink name=hailo_display "
            + f"sync={self.sync} text-overlay={self.options_menu.show_fps} "
            + "signal-fps-measurements=true "
        )

        print("Pipeline string:")
        print(pipeline_string)
        return pipeline_string


# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    # Initialize callback data
    user_data = user_app_callback_class()

    # Setup argument parser
    parser = get_default_parser()

    # Add custom arguments for detection
    parser.add_argument(
        "--network",
        default="yolov6n",
        choices=['yolov6n', 'yolov8s', 'yolox_s_leaky'],
        help="Which YOLO network to use (default: yolov6n)"
    )
    parser.add_argument(
        "--hef-path",
        default=None,
        help="Path to custom HEF model file"
    )
    parser.add_argument(
        "--labels-json",
        default=None,
        help="Path to custom labels JSON file"
    )
    parser.add_argument(
        "--score-threshold",
        type=float,
        default=0.3,
        help="NMS score threshold (default: 0.3)"
    )
    parser.add_argument(
        "--iou-threshold",
        type=float,
        default=0.45,
        help="NMS IoU threshold (default: 0.45)"
    )

    # Add servo control arguments
    parser.add_argument(
        "--servo-pin",
        type=int,
        default=17,
        help="GPIO pin for servo PWM control (default: 17)"
    )
    parser.add_argument(
        "--servo-min",
        type=float,
        default=-1.0,
        help="Servo minimum angle equivalent (default: -1.0)"
    )
    parser.add_argument(
        "--servo-max",
        type=float,
        default=1.0,
        help="Servo maximum angle equivalent (default: 1.0)"
    )
    parser.add_argument(
        "--servo-sensitivity",
        type=float,
        default=1.0,
        help="Servo movement sensitivity (0.1-2.0, default: 1.0)"
    )
    parser.add_argument(
        "--led-pin",
        type=int,
        default=20,
        help="GPIO pin for detection feedback LED (default: 20)"
    )
    parser.add_argument(
        "--distance-sensor",
        action="store_true",
        help="Enable HC-SR04 distance sensor for motion detection"
    )
    parser.add_argument(
        "--target-class",
        default="person",
        help="Target class to detect and track (default: person)"
    )

    args = parser.parse_args()

    # Initialize hardware (servo, LED, sensor)
    print("Initializing hardware...")
    try:
        servo = Servo(args.servo_pin, min_angle=-90, max_angle=90)
        servo.mid()  # Center position
        print(f"✓ Servo initialized on GPIO {args.servo_pin}")
    except Exception as e:
        print(f"✗ Failed to initialize servo: {e}")
        print("  Continuing without servo control...")
        servo = None

    try:
        led = LED(args.led_pin)
        led.off()
        print(f"✓ LED initialized on GPIO {args.led_pin}")
    except Exception as e:
        print(f"✗ Failed to initialize LED: {e}")
        print("  Continuing without LED feedback...")
        led = None

    if args.distance_sensor:
        try:
            sensor = DistanceSensor(echo=24, trigger=18, max_distance=2, threshold_distance=0.5)
            sensor.when_in_range = motion_detected
            sensor.when_out_of_range = no_motion_detected
            print("✓ Distance sensor initialized (Echo: GPIO 24, Trigger: GPIO 18)")
        except Exception as e:
            print(f"✗ Failed to initialize distance sensor: {e}")
            print("  Continuing without motion detection...")
            sensor = None

    # Create and run the detection app
    print("\nStarting detection pipeline with servo control...")
    print(f"Input: {args.input}")
    print(f"Network: {args.network}")
    print(f"Target class: {args.target_class}")
    print(f"Servo sensitivity: {args.servo_sensitivity}")
    print()

    try:
        app = GStreamerDetectionServoApp(args, user_data)
        app.run()
    except KeyboardInterrupt:
        print("\nShutdown requested...")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        # Cleanup hardware
        if servo:
            servo.mid()
            print("Servo centered")
        if led:
            led.off()
            print("LED off")
        if sensor:
            sensor.close()
            print("Sensor closed")
        print("Cleanup complete")
