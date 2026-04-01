# Hailo RPi5 Basic Pipelines - Complete Learning Guide

## Table of Contents
1. [Project Overview](#project-overview)
2. [Architecture & Design Patterns](#architecture--design-patterns)
3. [Pipeline Execution Flow](#pipeline-execution-flow)
4. [How to Create Custom Pipelines](#how-to-create-custom-pipelines)
5. [Code Template](#code-template)
6. [Key Components Explained](#key-components-explained)
7. [Hardware Integration](#hardware-integration)
8. [Advanced Features](#advanced-features)

---

## Project Overview

**What This Is:**
- A framework for running AI/ML computer vision inference on Raspberry Pi 5 with Hailo8L accelerator
- Real-time video processing pipelines using GStreamer
- Support for multiple input sources: USB cameras, RPi CSI camera, video files

**Three Official Pipelines:**
1. **Object Detection** - Detects objects with bounding boxes (YOLOv6n, YOLOv8s, YOLOx)
2. **Pose Estimation** - Detects 17 human body keypoints
3. **Instance Segmentation** - Pixel-level object segmentation masks

---

## Architecture & Design Patterns

### Class Hierarchy

```
app_callback_class (Base)
    ↓
user_app_callback_class (Your Custom Callback Data)
    ↓
GStreamerApp (Base Pipeline App)
    ↓
GStreamerDetectionApp / GStreamerPoseEstimationApp / GStreamerInstanceSegmentationApp
```

### Design Pattern Overview

**1. Callback Class Pattern**
```python
# Base class manages:
class app_callback_class:
    - frame_count: Tracks processed frames
    - use_frame: Flag to enable frame processing
    - frame_queue: Multiprocessing queue for frame passing
    - running: Control flag for the app

# You extend it:
class user_app_callback_class(app_callback_class):
    - Add your custom state variables
    - Add your custom detection flags
```

**2. Application Class Pattern**
```python
# Base GStreamerApp handles:
- Pipeline creation and management
- GStreamer bus message handling
- FPS measurement
- Source type detection (USB, RPi, file)
- QoS (Quality of Service) management

# You extend it:
- Override get_pipeline_string() to define your pipeline
- Set model-specific parameters (HEF path, network size, format)
- Set postprocessing library paths
```

**3. Callback Function Pattern**
```python
def app_callback(pad, info, user_data):
    # Called for EVERY frame in the pipeline
    # You receive: the frame data, metadata, and your custom user_data
    # Process detections, extract frame, do custom logic
```

---

## Pipeline Execution Flow

### Step-by-Step Execution

```
1. VIDEO INPUT
   ├─ USB: /dev/video0 → v4l2src
   ├─ RPi: rpi → libcamerasrc
   └─ File: video.mp4 → filesrc

2. VIDEO SCALING & CONVERSION
   ├─ Resize to network size (e.g., 640x640)
   ├─ Convert format (NV12 → RGB)
   └─ Set pixel aspect ratio

3. VIDEO TEE (Split Stream)
   ├─ Path 1: Bypass (original size) → hailomuxer.sink_0
   └─ Path 2: Inference (resized) → hailonet

4. HAILO INFERENCE (HW Acceleration)
   ├─ Load HEF model file
   ├─ Run neural network
   └─ Attach metadata (detections/keypoints/masks) to buffer

5. POSTPROCESSING (hailofilter)
   ├─ Load shared object library (.so file)
   ├─ Run post-processing function
   └─ Format results (NMS, thresholding, etc.)

6. MUXER (Merge Streams)
   ├─ Combine bypass stream + processed metadata
   └─ Attach all metadata to original-sized frame

7. PYTHON CALLBACK (identity element)
   ├─ Your custom processing
   ├─ Extract detections from buffer metadata
   └─ Modify frame or take actions

8. VISUALIZATION (hailooverlay)
   ├─ Draw bounding boxes / landmarks / masks
   └─ Render results

9. DISPLAY
   └─ Show final annotated video (xvimagesink)
```

### Key GStreamer Concepts

**Queues (`QUEUE` helper):**
- Buffers frames between elements
- Prevents pipeline stalls
- Parameters: `max-size-buffers`, `max-size-bytes`, `max-size-time`
- Max 3 buffers by default (real-time priority)

**HailoMuxer:**
- Routes frames through two paths simultaneously
- Path 0: Bypass (unchanged)
- Path 1: Processing (inference)
- Recombines with metadata attached

**Identity Element:**
- Pipeline inspection point
- Triggers your Python callback
- Non-destructive (passes all data through)

**Pad Probe:**
- Intercepts buffers at specific points
- Allows you to read/modify data
- Connected to identity element's output pad

---

## How to Create Custom Pipelines

### Step 1: Understand Your Task

**Before coding, ask:**
- What model do I need? (Get the .hef file)
- What input format? (USB camera, file, RPi camera)
- What post-processing? (Find the .so file)
- What should I do with results? (Extract detections, control GPIO, etc.)

### Step 2: Find Required Files

**HEF Files** (Neural Network Models):
```
/path/to/resources/
├── yolov6n.hef                    (object detection)
├── yolov8s_h8l.hef                (object detection)
├── yolov8s_pose_h8l_pi.hef        (pose estimation)
├── yolov5n_seg_h8l_mz.hef         (instance segmentation)
└── your_custom_model.hef          (your trained model)
```

**Post-Processing Libraries** (Set via `TAPPAS_POST_PROC_DIR`):
```
libyolo_hailortpp_post.so          (YOLO detection post-processing)
libyolov8pose_post.so              (YOLOv8 Pose post-processing)
libyolov5seg_post.so               (YOLOv5 Segmentation post-processing)
```

### Step 3: Set Model Parameters

```python
class YourCustomApp(GStreamerApp):
    def __init__(self, args, user_data):
        super().__init__(args, user_data)

        # Model parameters (MUST match your HEF file)
        self.batch_size = 2                    # How many frames process together
        self.network_width = 640               # Model input width
        self.network_height = 640              # Model input height
        self.network_format = "RGB"            # Color format (RGB, NV12, YUYV)

        # Post-processing
        self.default_postprocess_so = os.path.join(
            self.postprocess_dir,
            'libyolo_hailortpp_post.so'        # Or your post-processor
        )

        # Model file path
        self.hef_path = os.path.join(
            self.current_path,
            '../resources/your_model.hef'
        )

        # Callback function
        self.app_callback = app_callback

        self.create_pipeline()
```

### Step 4: Define Your Callback

```python
def app_callback(pad, info, user_data):
    buffer = info.get_buffer()
    if buffer is None:
        return Gst.PadProbeReturn.OK

    # Count frames
    user_data.increment()

    # Get frame data if needed
    format, width, height = get_caps_from_pad(pad)
    frame = None
    if user_data.use_frame and format:
        frame = get_numpy_from_buffer(buffer, format, width, height)

    # Extract metadata from buffer
    roi = hailo.get_roi_from_buffer(buffer)

    # For detection: roi.get_objects_typed(hailo.HAILO_DETECTION)
    # For pose: roi.get_objects_typed(hailo.HAILO_LANDMARKS)
    # For segmentation: roi.get_objects_typed(hailo.HAILO_CONF_CLASS_MASK)

    # Process results...

    # Return to continue pipeline
    return Gst.PadProbeReturn.OK
```

### Step 5: Build Your Pipeline String

```python
def get_pipeline_string(self):
    # 1. Define source
    if self.source_type == "rpi":
        source = (
            "libcamerasrc name=src_0 auto-focus-mode=2 ! "
            f"video/x-raw, format={self.network_format}, width=1536, height=864 ! "
        )
    elif self.source_type == "usb":
        source = (
            f"v4l2src device={self.video_source} name=src_0 ! "
            "video/x-raw, width=640, height=480, framerate=30/1 ! "
        )
    else:  # file
        source = (
            f"filesrc location={self.video_source} ! "
            "qtdemux ! h264parse ! avdec_h264 max-threads=2 ! "
            "video/x-raw, format=I420 ! "
        )

    # 2. Add scaling/conversion
    source += QUEUE("queue_scale")
    source += "videoscale n-threads=2 ! "
    source += QUEUE("queue_convert")
    source += f"videoconvert n-threads=3 ! "
    source += f"video/x-raw, format={self.network_format}, width={self.network_width}, height={self.network_height} ! "

    # 3. Build full pipeline
    pipeline = (
        "hailomuxer name=hmux "
        + source
        + "tee name=t ! "
        + QUEUE("bypass_queue", max_size_buffers=20) + "hmux.sink_0 "
        + "t. ! "
        + QUEUE("queue_hailonet")
        + "videoconvert n-threads=3 ! "
        + f"hailonet hef-path={self.hef_path} batch-size={self.batch_size} ! "
        + QUEUE("queue_hailofilter")
        + f"hailofilter so-path={self.default_postprocess_so} ! "
        + QUEUE("queue_hmux") + "hmux.sink_1 "
        + "hmux. ! "
        + QUEUE("queue_python")
        + "identity name=identity_callback ! "
        + QUEUE("queue_overlay")
        + "hailooverlay ! "
        + QUEUE("queue_convert2")
        + "videoconvert n-threads=3 ! "
        + QUEUE("queue_display")
        + f"fpsdisplaysink name=hailo_display sync={self.sync}"
    )

    return pipeline
```

### Step 6: Add Command-Line Arguments

```python
if __name__ == "__main__":
    user_data = user_app_callback_class()

    parser = get_default_parser()  # Includes: input, use-frame, show-fps, disable-sync, dump-dot

    # Add your custom arguments
    parser.add_argument(
        "--model",
        default="yolov6n",
        choices=['yolov6n', 'yolov8s', 'custom'],
        help="Which model to use"
    )
    parser.add_argument(
        "--hef-path",
        default=None,
        help="Custom HEF file path"
    )
    parser.add_argument(
        "--custom-param",
        type=float,
        default=0.5,
        help="Your custom parameter"
    )

    args = parser.parse_args()
    app = YourCustomApp(args, user_data)
    app.run()
```

---

## Code Template

### Complete Minimal Pipeline Template

```python
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

# ============================================================================
# 1. CALLBACK CLASS - Store your custom state
# ============================================================================
class user_app_callback_class(app_callback_class):
    def __init__(self):
        super().__init__()
        self.detection_count = 0
        self.custom_flag = False

# ============================================================================
# 2. CALLBACK FUNCTION - Process each frame
# ============================================================================
def app_callback(pad, info, user_data):
    buffer = info.get_buffer()
    if buffer is None:
        return Gst.PadProbeReturn.OK

    user_data.increment()

    # Get frame if needed
    format, width, height = get_caps_from_pad(pad)
    frame = None
    if user_data.use_frame and format:
        frame = get_numpy_from_buffer(buffer, format, width, height)

    # Extract detections from buffer metadata
    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)

    # Process detections
    for detection in detections:
        label = detection.get_label()
        confidence = detection.get_confidence()
        bbox = detection.get_bbox()

        # Do something with detection
        if label == "person" and confidence > 0.5:
            user_data.detection_count += 1
            print(f"Person detected: {confidence:.2f}")

    return Gst.PadProbeReturn.OK

# ============================================================================
# 3. APPLICATION CLASS - Define pipeline
# ============================================================================
class MyCustomApp(GStreamerApp):
    def __init__(self, args, user_data):
        super().__init__(args, user_data)

        # Set model parameters
        self.batch_size = 2
        self.network_width = 640
        self.network_height = 640
        self.network_format = "RGB"

        # Postprocessing library
        self.default_postprocess_so = os.path.join(
            self.postprocess_dir, 'libyolo_hailortpp_post.so'
        )

        # Model file
        self.hef_path = os.path.join(
            self.current_path, '../resources/yolov6n.hef'
        )

        # Callback
        self.app_callback = app_callback

        # NMS thresholds for detection
        self.thresholds_str = (
            "nms-score-threshold=0.3 "
            "nms-iou-threshold=0.45 "
            "output-format-type=HAILO_FORMAT_TYPE_FLOAT32"
        )

        self.create_pipeline()

    def get_pipeline_string(self):
        # Define source based on input type
        if self.source_type == "rpi":
            source = (
                "libcamerasrc name=src_0 auto-focus-mode=2 ! "
                f"video/x-raw, format={self.network_format}, width=1536, height=864 ! "
                + QUEUE("queue_src_scale")
                + "videoscale ! "
                + f"video/x-raw, format={self.network_format}, width={self.network_width}, height={self.network_height}, framerate=30/1 ! "
            )
        elif self.source_type == "usb":
            source = (
                f"v4l2src device={self.video_source} ! "
                "video/x-raw, width=640, height=480, framerate=30/1 ! "
            )
        else:  # file
            source = (
                f"filesrc location={self.video_source} ! "
                + QUEUE("queue_dec264")
                + "qtdemux ! h264parse ! avdec_h264 max-threads=2 ! "
                + "video/x-raw, format=I420 ! "
            )

        # Add conversion layers
        source += QUEUE("queue_scale") + "videoscale n-threads=2 ! "
        source += QUEUE("queue_convert") + "videoconvert n-threads=3 ! "
        source += f"video/x-raw, format={self.network_format}, width={self.network_width}, height={self.network_height} ! "

        # Full pipeline
        pipeline = (
            "hailomuxer name=hmux "
            + source
            + "tee name=t ! "
            + QUEUE("bypass_queue", max_size_buffers=20) + "hmux.sink_0 "
            + "t. ! "
            + QUEUE("queue_hailonet")
            + "videoconvert n-threads=3 ! "
            + f"hailonet hef-path={self.hef_path} batch-size={self.batch_size} {self.thresholds_str} ! "
            + QUEUE("queue_hailofilter")
            + f"hailofilter so-path={self.default_postprocess_so} ! "
            + QUEUE("queue_hmux") + "hmux.sink_1 "
            + "hmux. ! "
            + QUEUE("queue_python")
            + "identity name=identity_callback ! "
            + QUEUE("queue_overlay")
            + "hailooverlay ! "
            + QUEUE("queue_convert2")
            + "videoconvert n-threads=3 ! "
            + QUEUE("queue_display")
            + f"fpsdisplaysink name=hailo_display sync={self.sync}"
        )

        return pipeline

# ============================================================================
# 4. MAIN - Run it
# ============================================================================
if __name__ == "__main__":
    user_data = user_app_callback_class()

    parser = get_default_parser()
    parser.add_argument(
        "--model",
        default="yolov6n",
        choices=['yolov6n', 'yolov8s'],
        help="Model to use"
    )

    args = parser.parse_args()
    app = MyCustomApp(args, user_data)
    app.run()
```

---

## Key Components Explained

### 1. Buffer & Metadata

```python
# Buffer: Raw video frame data
buffer = info.get_buffer()

# ROI (Region of Interest): Container for all metadata
roi = hailo.get_roi_from_buffer(buffer)

# Extract different types of objects
detections = roi.get_objects_typed(hailo.HAILO_DETECTION)
landmarks = roi.get_objects_typed(hailo.HAILO_LANDMARKS)
masks = roi.get_objects_typed(hailo.HAILO_CONF_CLASS_MASK)
```

### 2. Detection Object

```python
for detection in detections:
    label = detection.get_label()              # String (e.g., "person")
    confidence = detection.get_confidence()    # Float 0-1
    bbox = detection.get_bbox()                # Bounding box object

    # Bounding box methods
    xmin = bbox.xmin()    # Normalized 0-1
    ymin = bbox.ymin()    # Normalized 0-1
    width = bbox.width()  # Normalized 0-1
    height = bbox.height()# Normalized 0-1

    # Convert to pixel coordinates
    pixel_x = int(xmin * frame_width)
    pixel_y = int(ymin * frame_height)
    pixel_w = int(width * frame_width)
    pixel_h = int(height * frame_height)
```

### 3. Pose/Landmarks

```python
landmarks = detection.get_objects_typed(hailo.HAILO_LANDMARKS)
if landmarks:
    points = landmarks[0].get_points()  # List of keypoints

    for i, point in enumerate(points):
        x = point.x()  # Normalized to bbox
        y = point.y()  # Normalized to bbox

        # Convert to frame coordinates
        frame_x = int((x * bbox.width() + bbox.xmin()) * frame_width)
        frame_y = int((y * bbox.height() + bbox.ymin()) * frame_height)
```

### 4. Segmentation Masks

```python
masks = detection.get_objects_typed(hailo.HAILO_CONF_CLASS_MASK)
if masks:
    mask = masks[0]

    # Reshape mask
    mask_height = mask.get_height()
    mask_width = mask.get_width()
    mask_data = np.array(mask.get_data())
    mask_data = mask_data.reshape((mask_height, mask_width))

    # Upsample 4x
    mask_data = cv2.resize(
        mask_data,
        (mask_width * 4, mask_height * 4),
        interpolation=cv2.INTER_NEAREST
    )
```

### 5. Format Conversions

```python
# Supported formats
FORMAT_HANDLERS = {
    'RGB': handle_rgb,      # 3-channel, direct
    'NV12': handle_nv12,    # Y-plane + UV-plane
    'YUYV': handle_yuyv,    # Interleaved YUV
}

# Convert to NumPy
frame = get_numpy_from_buffer(buffer, format, width, height)

# Typical conversion flow
frame_rgb = get_numpy_from_buffer(...)  # RGB format
frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)  # For OpenCV
cv2.imshow("Frame", frame_bgr)
```

---

## Hardware Integration

### GPIO Control (Detection Example)

```python
from gpiozero import LED, DistanceSensor, Button, Servo

# LED on GPIO pin 17
led = LED(17)
led.on()
led.off()

# Distance sensor (HC-SR04)
sensor = DistanceSensor(echo=24, trigger=18)
sensor.when_in_range = motion_detected
sensor.when_out_of_range = no_motion_detected

# Button (e.g., GPIO 16)
button = Button(16)
button.when_pressed = button_handler

# Servo (GPIO 17)
servo = Servo(17)
servo.value = 0.5  # Center position (-1 to 1)
```

### Integrating with Callback

```python
import threading

motion_flag = False
state_lock = threading.Lock()

def motion_detected():
    global motion_flag
    with state_lock:
        motion_flag = True

def app_callback(pad, info, user_data):
    buffer = info.get_buffer()
    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)

    with state_lock:
        person_detected = any(d.get_label() == "person" for d in detections)
        if person_detected and motion_flag:
            led.on()
        else:
            led.off()

    return Gst.PadProbeReturn.OK
```

---

## Advanced Features

### 1. Custom Models

```python
# Place your retrained .hef file in resources/
self.hef_path = os.path.join(
    self.current_path,
    '../resources/my_custom_yolov8_model.hef'
)

# Use with custom labels JSON
parser.add_argument(
    "--labels-json",
    default=None,
    help="Path to custom labels JSON"
)

# In pipeline:
if args.labels_json:
    self.labels_config = f'config-path={args.labels_json}'
else:
    self.labels_config = ''

# Add to hailofilter:
f"hailofilter so-path={self.default_postprocess_so} {self.labels_config}"
```

### 2. FPS Measurement

```python
# Command-line: --show-fps
# Enables FPS counter at bottom of video

# Manual FPS tracking
fps_counter = 0
fps_time = time.time()

def app_callback(pad, info, user_data):
    global fps_counter, fps_time
    fps_counter += 1

    if time.time() - fps_time > 1.0:
        print(f"FPS: {fps_counter}")
        fps_counter = 0
        fps_time = time.time()
```

### 3. Frame Export via Callback

```python
# Use --use-frame flag
# In your callback, store frames in queue:
if user_data.use_frame:
    # Draw on frame
    cv2.putText(frame, "Text", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

    # Convert RGB to BGR for OpenCV compatibility
    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

    # Queue for display in separate thread
    user_data.set_frame(frame_bgr)
```

### 4. Pipeline Debugging

```python
# Dump pipeline graph:
python detection.py --dump-dot

# Creates: pipeline.dot in the directory
# View with: dot -Tpng pipeline.dot -o pipeline.png

# GStreamer debugging:
export GST_DEBUG=3  # 0=none, 3=info, 4=debug, 5=log

# Check available plugins:
gst-inspect-1.0 | grep hailonet
```

### 5. Multi-Model Support

```python
class MyMultiModelApp(GStreamerApp):
    def __init__(self, args, user_data):
        super().__init__(args, user_data)

        # Load different models
        models = {
            'detection': '../resources/yolov8s_h8l.hef',
            'pose': '../resources/yolov8s_pose_h8l_pi.hef',
            'segmentation': '../resources/yolov5n_seg_h8l_mz.hef',
        }

        self.hef_path = os.path.join(
            self.current_path,
            models[args.model_type]
        )
```

### 6. Custom Thresholds

```python
# For YOLO detection:
self.thresholds_str = (
    f"nms-score-threshold={args.score_threshold} "
    f"nms-iou-threshold={args.iou_threshold} "
    f"output-format-type=HAILO_FORMAT_TYPE_FLOAT32"
)

# Add command-line args:
parser.add_argument("--score-threshold", type=float, default=0.3)
parser.add_argument("--iou-threshold", type=float, default=0.45)
```

---

## Quick Reference: Common Edits

### Change Input Source
```python
# USB camera on /dev/video0
python detection.py -i /dev/video0

# RPi camera
python detection.py -i rpi

# Video file
python detection.py -i video.mp4
```

### Change Model
```python
python detection.py --network yolov8s
```

### Enable Frame Display
```python
python detection.py --use-frame
```

### View FPS
```python
python detection.py --show-fps
```

### Debug Pipeline
```python
python detection.py --dump-dot
dot -Tpng pipeline.dot -o pipeline.png
```

---

## Essential Environment Setup

```bash
# Source environment before running
source ../setup_env.sh

# This sets:
# - TAPPAS_POST_PROC_DIR (post-processing library path)
# - Python virtual environment
# - Other dependencies
```

---

## Summary: Key Takeaways for Future Development

1. **Architecture is Template-Based**: Inherit from `GStreamerApp` and implement `get_pipeline_string()`
2. **Pipeline = String**: The entire GStreamer pipeline is one string built with element properties
3. **Callbacks are Where Logic Lives**: All your custom processing happens in `app_callback()`
4. **Buffer Metadata is Key**: All results (detections, landmarks, masks) attached to buffer via `hailo` library
5. **Three Main Data Extraction Methods**:
   - Detection: `.get_objects_typed(hailo.HAILO_DETECTION)`
   - Pose: `.get_objects_typed(hailo.HAILO_LANDMARKS)`
   - Segmentation: `.get_objects_typed(hailo.HAILO_CONF_CLASS_MASK)`
6. **Hardware Integration**: Use `gpiozero` for GPIO, `threading.Lock` for thread safety
7. **Queue Management**: Use `QUEUE()` helper to prevent pipeline stalls
8. **Two Processing Paths**: Bypass path (original) + inference path (resized) = merged output

---

**Last Updated**: 2025-01-16
**Framework**: Hailo RPi5 Examples - Basic Pipelines
**Python Version**: 3.11+
**GStreamer Version**: 1.0
