#!/usr/bin/env python3
"""
Hand-Controlled Robot using Hailo AI Pose Estimation

This pipeline uses YOLOv8 Pose Estimation to detect hand positions (via wrist keypoints)
and controls a servo motor to follow your hand movements.

How it works:
- Detects human pose with 17 COCO keypoints
- Extracts left_wrist (index 9) and right_wrist (index 10)
- Calculates hand center position
- Maps hand position to servo angle (-1.0 to 1.0)
- Smoothly pans servo to follow your hand

Hardware Setup:
- Servo: GPIO 17 (PWM)
- LED: GPIO 20 (detection feedback)
- Camera: USB (/dev/video0) or RPi CSI

Usage:
    python hand_controlled_servo.py -i /dev/video0 --show-fps
    python hand_controlled_servo.py -i rpi --use-frame
    python hand_controlled_servo.py -i /dev/video0 --servo-speed 0.1
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
from gpiozero import Servo, LED
from gpiozero.pins.pigpio import PiGPIOFactory
import threading
import time
import setproctitle
import cv2
import numpy as np

# ============================================================================
# Global Variables for Thread-Safe Hardware Control
# ============================================================================
servo = None
led = None
servo_lock = threading.Lock()
servo_position = 0.0  # Current servo position (-1.0 to 1.0)
target_position = 0.0  # Target position
last_hand_time = 0.0  # Last time hand was detected

# COCO Pose Keypoint Indices
KEYPOINT_NAMES = [
    'nose',           # 0
    'left_eye',       # 1
    'right_eye',      # 2
    'left_ear',       # 3
    'right_ear',      # 4
    'left_shoulder',  # 5
    'right_shoulder', # 6
    'left_elbow',     # 7
    'right_elbow',    # 8
    'left_wrist',     # 9  <- LEFT HAND
    'right_wrist',    # 10 <- RIGHT HAND
    'left_hip',       # 11
    'right_hip',      # 12
    'left_knee',      # 13
    'right_knee',     # 14
    'left_ankle',     # 15
    'right_ankle',    # 16
]

LEFT_WRIST_IDX = 9
RIGHT_WRIST_IDX = 10


# ============================================================================
# Servo Control Functions
# ============================================================================

def smooth_servo_movement(target, current, speed=0.08):
    """
    Calculate smooth servo movement towards target.

    Args:
        target (float): Target position (-1.0 to 1.0)
        current (float): Current position (-1.0 to 1.0)
        speed (float): Movement speed per update

    Returns:
        float: New position
    """
    diff = target - current

    if abs(diff) < 0.01:
        return target

    # Move incrementally
    movement = speed if diff > 0 else -speed
    new_pos = current + movement

    # Clamp to range
    new_pos = max(-1.0, min(1.0, new_pos))

    return new_pos


def update_servo(target, speed=0.08):
    """
    Update servo position smoothly towards target.

    Args:
        target (float): Target servo position (-1.0 to 1.0)
        speed (float): Movement speed
    """
    global servo, servo_position, servo_lock

    if servo is None:
        return

    with servo_lock:
        # Clamp target
        target = max(-1.0, min(1.0, target))

        # Smooth movement
        new_pos = smooth_servo_movement(target, servo_position, speed)

        # Update servo
        if abs(new_pos - servo_position) > 0.01:
            try:
                servo.value = new_pos
                servo_position = new_pos
            except Exception as e:
                print(f"Servo error: {e}")


def return_to_center(speed=0.03):
    """Return servo to center position."""
    update_servo(0.0, speed)


# ============================================================================
# Hand Position Calculation
# ============================================================================

def calculate_hand_position(wrist_points, frame_width, frame_height, bbox):
    """
    Calculate hand center position from wrist keypoints.

    Args:
        wrist_points (list): List of (x, y) wrist positions (normalized to bbox)
        frame_width (int): Frame width
        frame_height (int): Frame height
        bbox: Bounding box object

    Returns:
        tuple: (hand_center_x_pixel, hand_center_y_pixel) or None
    """
    if not wrist_points:
        return None

    # Average wrist positions (if multiple hands detected)
    avg_x = sum(p[0] for p in wrist_points) / len(wrist_points)
    avg_y = sum(p[1] for p in wrist_points) / len(wrist_points)

    # Convert from bbox-normalized to frame coordinates
    frame_x = (avg_x * bbox.width() + bbox.xmin()) * frame_width
    frame_y = (avg_y * bbox.height() + bbox.ymin()) * frame_height

    return (int(frame_x), int(frame_y))


def calculate_servo_angle(hand_x, frame_width, deadzone=0.15, sensitivity=1.2):
    """
    Map hand X position to servo angle.

    Args:
        hand_x (int): Hand X coordinate in pixels
        frame_width (int): Frame width in pixels
        deadzone (float): Center deadzone (0.0-0.5), no movement in center
        sensitivity (float): Servo sensitivity multiplier

    Returns:
        float: Servo position (-1.0 to 1.0)
    """
    # Normalize to -1.0 to 1.0
    normalized_x = (hand_x / frame_width) * 2.0 - 1.0

    # Apply deadzone (reduce jitter near center)
    if abs(normalized_x) < deadzone:
        normalized_x = 0.0
    else:
        # Scale outside deadzone
        sign = 1 if normalized_x > 0 else -1
        normalized_x = sign * ((abs(normalized_x) - deadzone) / (1.0 - deadzone))

    # Apply sensitivity
    servo_angle = normalized_x * sensitivity

    # Clamp
    servo_angle = max(-1.0, min(1.0, servo_angle))

    return servo_angle


# ============================================================================
# Callback Class
# ============================================================================

class user_app_callback_class(app_callback_class):
    """Extended callback class for hand tracking state."""

    def __init__(self):
        super().__init__()
        self.hand_detected = False
        self.hand_count = 0
        self.servo_speed = 0.08
        self.deadzone = 0.15
        self.sensitivity = 1.2


# ============================================================================
# Main Callback Function
# ============================================================================

def app_callback(pad, info, user_data):
    """
    Process pose detections and control servo based on hand position.
    """
    global target_position, last_hand_time, led

    buffer = info.get_buffer()
    if buffer is None:
        return Gst.PadProbeReturn.OK

    # Increment frame count
    user_data.increment()
    frame_num = user_data.get_count()

    # Get frame info
    format, width, height = get_caps_from_pad(pad)

    # Get frame for visualization
    frame = None
    if user_data.use_frame and format and width and height:
        frame = get_numpy_from_buffer(buffer, format, width, height)

    # Extract pose detections
    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)

    # Track hand positions
    wrist_positions = []
    person_bbox = None
    hand_detected = False

    for detection in detections:
        label = detection.get_label()
        confidence = detection.get_confidence()

        # Only process person detections with good confidence
        if label == "person" and confidence > 0.5:
            bbox = detection.get_bbox()
            person_bbox = bbox

            # Get landmarks (keypoints)
            landmarks_list = detection.get_objects_typed(hailo.HAILO_LANDMARKS)

            if landmarks_list:
                landmarks = landmarks_list[0]
                points = landmarks.get_points()

                # Extract wrist keypoints
                if len(points) >= 11:  # Ensure we have wrist keypoints
                    left_wrist = points[LEFT_WRIST_IDX]
                    right_wrist = points[RIGHT_WRIST_IDX]

                    # Check if keypoints are valid (not zero/null)
                    left_valid = left_wrist.x() > 0.01 and left_wrist.y() > 0.01
                    right_valid = right_wrist.x() > 0.01 and right_wrist.y() > 0.01

                    if left_valid:
                        wrist_positions.append((left_wrist.x(), left_wrist.y()))
                    if right_valid:
                        wrist_positions.append((right_wrist.x(), right_wrist.y()))

                    # Draw keypoints on frame
                    if user_data.use_frame and frame is not None:
                        # Draw all keypoints
                        for i, point in enumerate(points):
                            if point.x() > 0.01 and point.y() > 0.01:
                                # Convert to frame coordinates
                                px = int((point.x() * bbox.width() + bbox.xmin()) * width)
                                py = int((point.y() * bbox.height() + bbox.ymin()) * height)

                                # Color: wrists are green, others are blue
                                if i == LEFT_WRIST_IDX or i == RIGHT_WRIST_IDX:
                                    color = (0, 255, 0)  # Green for hands
                                    radius = 8
                                else:
                                    color = (255, 0, 0)  # Blue for other keypoints
                                    radius = 4

                                cv2.circle(frame, (px, py), radius, color, -1)

                                # Label wrists
                                if i == LEFT_WRIST_IDX:
                                    cv2.putText(frame, "L", (px + 10, py),
                                               cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                                elif i == RIGHT_WRIST_IDX:
                                    cv2.putText(frame, "R", (px + 10, py),
                                               cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

    # Calculate hand center and servo position
    if wrist_positions and person_bbox:
        hand_detected = True
        last_hand_time = time.time()

        # Get hand center position in frame coordinates
        hand_pos = calculate_hand_position(wrist_positions, width, height, person_bbox)

        if hand_pos:
            hand_x, hand_y = hand_pos

            # Calculate servo angle
            target_position = calculate_servo_angle(
                hand_x, width,
                deadzone=user_data.deadzone,
                sensitivity=user_data.sensitivity
            )

            # Update servo
            update_servo(target_position, speed=user_data.servo_speed)

            # LED on when hand detected
            if led:
                led.on()

            # Draw hand center on frame
            if user_data.use_frame and frame is not None:
                cv2.circle(frame, (hand_x, hand_y), 12, (0, 255, 255), 3)
                cv2.circle(frame, (hand_x, hand_y), 3, (0, 255, 255), -1)

                # Draw vertical line from hand to bottom
                cv2.line(frame, (hand_x, hand_y), (hand_x, height), (0, 255, 255), 2)

                # Show hand position text
                hand_text = f"Hand X: {hand_x}"
                cv2.putText(frame, hand_text, (10, height - 50),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
    else:
        hand_detected = False

        # Return to center if no hand detected for 2 seconds
        if time.time() - last_hand_time > 2.0:
            return_to_center(speed=0.03)
            if led:
                led.off()

    # Update state
    user_data.hand_detected = hand_detected
    user_data.hand_count = len(wrist_positions)

    # Display frame with overlays
    if user_data.use_frame and frame is not None:
        # Frame info
        info_text = f"Frame: {frame_num} | Hands: {len(wrist_positions)}"
        cv2.putText(frame, info_text, (10, 30),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

        # Servo info
        servo_text = f"Servo: {servo_position:.2f} | Target: {target_position:.2f}"
        cv2.putText(frame, servo_text, (10, 70),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 165, 0), 2)

        # Status
        status = "TRACKING" if hand_detected else "WAITING"
        status_color = (0, 255, 0) if hand_detected else (0, 0, 255)
        cv2.putText(frame, status, (10, 110),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, status_color, 2)

        # Draw center deadzone indicator
        center_x = width // 2
        deadzone_pixels = int(width * user_data.deadzone)
        cv2.rectangle(frame,
                     (center_x - deadzone_pixels, 0),
                     (center_x + deadzone_pixels, height),
                     (128, 128, 128), 2)
        cv2.putText(frame, "DEADZONE", (center_x - 50, 20),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (128, 128, 128), 1)

        # Convert and queue
        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        user_data.set_frame(frame_bgr)

    # Console output
    if hand_detected:
        print(f"[{frame_num}] Hands: {len(wrist_positions)}, "
              f"Servo: {servo_position:.2f} → {target_position:.2f}")

    return Gst.PadProbeReturn.OK


# ============================================================================
# GStreamer Application Class
# ============================================================================

class GStreamerHandControlApp(GStreamerApp):
    """Hand-controlled servo application using pose estimation."""

    def __init__(self, args, user_data):
        super().__init__(args, user_data)

        # Set servo parameters from args
        user_data.servo_speed = args.servo_speed
        user_data.deadzone = args.deadzone
        user_data.sensitivity = args.sensitivity

        # Pose estimation model parameters
        self.batch_size = 2
        self.network_width = 640
        self.network_height = 640
        self.network_format = "RGB"

        # Post-processing library for pose
        self.default_postprocess_so = os.path.join(
            self.postprocess_dir, 'libyolov8pose_post.so'
        )
        self.post_function_name = "filter"

        # HEF model path
        self.hef_path = os.path.join(
            self.current_path, '../resources/yolov8s_pose_h8l_pi.hef'
        )

        # Callback
        self.app_callback = app_callback

        # Set process title
        setproctitle.setproctitle("Hailo Hand-Controlled Robot")

        # Create pipeline
        self.create_pipeline()

    def get_pipeline_string(self):
        """Build GStreamer pipeline for pose estimation."""

        # Source element
        if self.source_type == "rpi":
            source_element = (
                "libcamerasrc name=src_0 auto-focus-mode=2 ! "
                f"video/x-raw, format={self.network_format}, width=1536, height=864 ! "
                + QUEUE("queue_src_scale")
                + "videoscale ! "
                + f"video/x-raw, format={self.network_format}, "
                + f"width={self.network_width}, height={self.network_height}, framerate=30/1 ! "
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

        # Scaling and conversion
        source_element += QUEUE("queue_scale")
        source_element += "videoscale n-threads=2 ! "
        source_element += QUEUE("queue_src_convert")
        source_element += "videoconvert n-threads=3 name=src_convert qos=false ! "
        source_element += (
            f"video/x-raw, format={self.network_format}, "
            f"width={self.network_width}, height={self.network_height}, "
            "pixel-aspect-ratio=1/1 ! "
        )

        # Full pipeline
        pipeline_string = (
            "hailomuxer name=hmux "
            + source_element
            + "tee name=t ! "
            + QUEUE("bypass_queue", max_size_buffers=20) + "hmux.sink_0 "
            + "t. ! "
            + QUEUE("queue_hailonet")
            + "videoconvert n-threads=3 ! "
            + f"hailonet hef-path={self.hef_path} batch-size={self.batch_size} "
            + "force-writable=true ! "
            + QUEUE("queue_hailofilter")
            + f"hailofilter function-name={self.post_function_name} "
            + f"so-path={self.default_postprocess_so} qos=false ! "
            + QUEUE("queue_hmux") + "hmux.sink_1 "
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

        return pipeline_string


# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("  HAND-CONTROLLED ROBOT - Hailo AI Pose Estimation")
    print("=" * 70)

    # Parse arguments
    user_data = user_app_callback_class()
    parser = get_default_parser()

    # Servo control arguments
    parser.add_argument(
        "--servo-pin", type=int, default=17,
        help="GPIO pin for servo (default: 17)"
    )
    parser.add_argument(
        "--led-pin", type=int, default=20,
        help="GPIO pin for LED indicator (default: 20)"
    )
    parser.add_argument(
        "--servo-speed", type=float, default=0.08,
        help="Servo movement speed 0.01-0.5 (default: 0.08)"
    )
    parser.add_argument(
        "--deadzone", type=float, default=0.15,
        help="Center deadzone 0.0-0.5 (default: 0.15)"
    )
    parser.add_argument(
        "--sensitivity", type=float, default=1.2,
        help="Servo sensitivity 0.5-2.0 (default: 1.2)"
    )
    parser.add_argument(
        "--use-pigpio", action="store_true",
        help="Use pigpio for better servo control (requires pigpiod daemon)"
    )

    args = parser.parse_args()

    # Initialize hardware
    print("\n[HARDWARE INITIALIZATION]")

    # Setup servo with optional pigpio
    try:
        if args.use_pigpio:
            print("Using pigpio factory (better PWM)...")
            factory = PiGPIOFactory()
            servo = Servo(args.servo_pin, pin_factory=factory)
        else:
            servo = Servo(args.servo_pin)

        servo.mid()  # Center position
        time.sleep(0.5)
        print(f"✓ Servo initialized on GPIO {args.servo_pin}")
    except Exception as e:
        print(f"✗ Servo initialization failed: {e}")
        print("  Note: For better servo control, run 'sudo pigpiod' and use --use-pigpio")
        servo = None

    # Setup LED
    try:
        led = LED(args.led_pin)
        led.off()
        print(f"✓ LED initialized on GPIO {args.led_pin}")
    except Exception as e:
        print(f"✗ LED initialization failed: {e}")
        led = None

    # Display configuration
    print("\n[CONFIGURATION]")
    print(f"Input source: {args.input}")
    print(f"Servo speed: {args.servo_speed}")
    print(f"Deadzone: {args.deadzone} ({args.deadzone*100:.0f}% of frame)")
    print(f"Sensitivity: {args.sensitivity}")
    print(f"Use frame display: {args.use_frame}")
    print(f"Show FPS: {args.show_fps}")

    # Instructions
    print("\n[INSTRUCTIONS]")
    print("1. Stand in front of the camera")
    print("2. Raise your hand(s) - the servo will follow")
    print("3. Move your hand left/right to control the servo")
    print("4. Deadzone in center prevents jitter")
    print("5. Press Ctrl+C to stop")
    print()

    # Run application
    try:
        app = GStreamerHandControlApp(args, user_data)
        print("[STARTING PIPELINE]")
        print("Waiting for hands...\n")
        app.run()
    except KeyboardInterrupt:
        print("\n\n[SHUTDOWN]")
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Cleanup
        print("Cleaning up hardware...")
        if servo:
            servo.mid()
            time.sleep(0.3)
            servo.close()
            print("✓ Servo centered and closed")
        if led:
            led.off()
            led.close()
            print("✓ LED off and closed")
        print("Done!")
