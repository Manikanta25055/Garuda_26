# Hailo Pipelines - Quick Reference Cheat Sheet

## Running Existing Pipelines

```bash
# Object Detection
python detection.py -i /dev/video0                    # USB camera
python detection.py -i rpi                            # RPi camera
python detection.py -i video.mp4 --disable-sync       # Video file
python detection.py --network yolov8s                 # Different model
python detection.py --show-fps --use-frame            # Show FPS and frame

# Pose Estimation
python pose_estimation.py -i /dev/video0
python pose_estimation.py --use-frame                 # Display landmarks

# Instance Segmentation
python instance_segmentation.py -i /dev/video0
python instance_segmentation.py --use-frame
```

---

## Creating a Custom Pipeline - 5 Step Checklist

### Step 1: Copy Template
```bash
cp detection.py my_custom_app.py
```

### Step 2: Update Class Name
```python
class MyCustomApp(GStreamerApp):  # Change this
    def __init__(self, args, user_data):
        super().__init__(args, user_data)
```

### Step 3: Update Model Path
```python
# Find your model file
self.hef_path = os.path.join(
    self.current_path,
    '../resources/YOUR_MODEL.hef'  # Change this
)

# Update post-processor if different task
self.default_postprocess_so = os.path.join(
    self.postprocess_dir,
    'libyolo_hailortpp_post.so'  # Change if needed
)
```

### Step 4: Modify Callback Logic
```python
def app_callback(pad, info, user_data):
    buffer = info.get_buffer()
    roi = hailo.get_roi_from_buffer(buffer)

    # Get your detection type
    objects = roi.get_objects_typed(hailo.HAILO_DETECTION)

    # Your custom logic here
    for obj in objects:
        print(obj.get_label())
```

### Step 5: Update Main Section
```python
if __name__ == "__main__":
    user_data = user_app_callback_class()
    parser = get_default_parser()

    # Add your custom arguments
    parser.add_argument("--your-param", default=0.5)

    args = parser.parse_args()
    app = MyCustomApp(args, user_data)  # Update class name
    app.run()
```

---

## Common Modifications

### Change NMS Thresholds
```python
self.thresholds_str = (
    f"nms-score-threshold=0.5 "      # Score threshold (0.0-1.0)
    f"nms-iou-threshold=0.5 "        # IoU threshold (0.0-1.0)
    f"output-format-type=HAILO_FORMAT_TYPE_FLOAT32"
)
```

### Filter Detections by Confidence
```python
def app_callback(pad, info, user_data):
    buffer = info.get_buffer()
    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)

    MIN_CONFIDENCE = 0.7  # Change this

    for detection in detections:
        if detection.get_confidence() >= MIN_CONFIDENCE:
            label = detection.get_label()
            confidence = detection.get_confidence()
            print(f"{label}: {confidence:.2f}")
```

### Filter by Class
```python
def app_callback(pad, info, user_data):
    buffer = info.get_buffer()
    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)

    TARGET_CLASSES = ["person", "car", "dog"]  # Change this

    for detection in detections:
        if detection.get_label() in TARGET_CLASSES:
            print(f"Found: {detection.get_label()}")
```

### Draw on Frame
```python
import cv2

def app_callback(pad, info, user_data):
    buffer = info.get_buffer()
    format, width, height = get_caps_from_pad(pad)

    # Get frame
    if user_data.use_frame and format:
        frame = get_numpy_from_buffer(buffer, format, width, height)

        # Draw text
        cv2.putText(frame, "Text Here", (10, 30),
                   cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

        # Draw rectangle
        cv2.rectangle(frame, (100, 100), (200, 200), (0, 255, 0), 2)

        # Draw circle
        cv2.circle(frame, (150, 150), 50, (0, 255, 0), 2)

        # Queue for display
        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        user_data.set_frame(frame_bgr)
```

### Add Hardware Control (LED)
```python
from gpiozero import LED, DistanceSensor
import threading

# At top level
led = LED(17)
sensor = DistanceSensor(echo=24, trigger=18)
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

    person_detected = any(d.get_label() == "person" for d in detections)

    with state_lock:
        if person_detected and motion_flag:
            led.on()
        else:
            led.off()
```

### Count Objects
```python
def app_callback(pad, info, user_data):
    buffer = info.get_buffer()
    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)

    # Count by class
    counts = {}
    for detection in detections:
        label = detection.get_label()
        counts[label] = counts.get(label, 0) + 1

    print(f"Counts: {counts}")
```

### Extract Bounding Box Coordinates
```python
def app_callback(pad, info, user_data):
    buffer = info.get_buffer()
    format, width, height = get_caps_from_pad(pad)
    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)

    for detection in detections:
        bbox = detection.get_bbox()

        # Normalized coordinates (0-1)
        xmin_norm = bbox.xmin()
        ymin_norm = bbox.ymin()
        width_norm = bbox.width()
        height_norm = bbox.height()

        # Pixel coordinates
        xmin = int(xmin_norm * width)
        ymin = int(ymin_norm * height)
        xmax = int((xmin_norm + width_norm) * width)
        ymax = int((ymin_norm + height_norm) * height)

        print(f"Box: ({xmin}, {ymin}) to ({xmax}, {ymax})")
```

### Extract Pose Landmarks
```python
def app_callback(pad, info, user_data):
    buffer = info.get_buffer()
    format, width, height = get_caps_from_pad(pad)
    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)

    for detection in detections:
        landmarks_list = detection.get_objects_typed(hailo.HAILO_LANDMARKS)
        if landmarks_list:
            landmarks = landmarks_list[0]
            points = landmarks.get_points()

            # COCO keypoint order:
            # 0=nose, 1=left_eye, 2=right_eye, 3=left_ear, 4=right_ear
            # 5=left_shoulder, 6=right_shoulder, 7=left_elbow, 8=right_elbow
            # 9=left_wrist, 10=right_wrist, 11=left_hip, 12=right_hip
            # 13=left_knee, 14=right_knee, 15=left_ankle, 16=right_ankle

            for i, point in enumerate(points):
                keypoint_name = ["nose", "l_eye", "r_eye"][i] if i < 3 else f"kp{i}"
                print(f"{keypoint_name}: ({point.x():.2f}, {point.y():.2f})")
```

### Extract Segmentation Mask
```python
import numpy as np
import cv2

def app_callback(pad, info, user_data):
    buffer = info.get_buffer()
    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)

    for detection in detections:
        masks_list = detection.get_objects_typed(hailo.HAILO_CONF_CLASS_MASK)
        if masks_list:
            mask = masks_list[0]

            # Get mask data
            mask_height = mask.get_height()
            mask_width = mask.get_width()
            mask_data = np.array(mask.get_data())
            mask_data = mask_data.reshape((mask_height, mask_width))

            # Upsample 4x (typical for YOLO)
            mask_data = cv2.resize(
                mask_data,
                (mask_width * 4, mask_height * 4),
                interpolation=cv2.INTER_NEAREST
            )

            print(f"Mask shape: {mask_data.shape}")
```

---

## Debugging Commands

```bash
# Enable GStreamer debugging
export GST_DEBUG=3

# Run with debugging
python detection.py -i /dev/video0

# Dump pipeline graph
python detection.py --dump-dot

# View pipeline graph
dot -Tpng pipeline.dot -o pipeline.png
display pipeline.png

# Check system for plugins
gst-inspect-1.0 | grep hailo
gst-inspect-1.0 | grep hailomuxer

# Check if Hailo is available
python -c "import hailo; print('Hailo OK')"
```

---

## File Organization

```
basic_pipelines/
├── hailo_rpi_common.py          # Framework (DON'T MODIFY)
├── detection.py                  # Template example
├── pose_estimation.py            # Template example
├── instance_segmentation.py      # Template example
│
├── my_custom_app.py             # YOUR CUSTOM PIPELINE
├── another_custom_app.py        # YOUR OTHER PIPELINES
│
└── PIPELINE_LEARNING_GUIDE.md   # Full documentation
```

---

## Model Paths

```
../resources/
├── yolov6n.hef                          # Small, fast detection
├── yolov8s_h8l.hef                      # Medium detection
├── yolox_s_leaky_h8l_mz.hef            # YOLOX detection
├── yolov8s_pose_h8l_pi.hef             # Pose estimation (17 keypoints)
├── yolov5n_seg_h8l_mz.hef              # Instance segmentation
├── yolov8s-hailo8l-barcode.hef         # Custom barcode detection
└── detection0.mp4                       # Test video
```

---

## Post-Processing Libraries

```
$TAPPAS_POST_PROC_DIR/
├── libyolo_hailortpp_post.so            # Detection post-processor
├── libyolov8pose_post.so                # Pose estimation post-processor
├── libyolov5seg_post.so                 # Segmentation post-processor
└── [other post-processors]
```

---

## Useful Utility Functions from hailo_rpi_common.py

```python
# Available imports
from hailo_rpi_common import (
    get_default_parser,                 # Standard argument parser
    QUEUE,                              # Create queues: QUEUE("name")
    get_caps_from_pad,                  # Get format/width/height
    get_numpy_from_buffer,              # Convert buffer to NumPy
    GStreamerApp,                       # Base class
    app_callback_class,                 # Callback data class
)

# get_default_parser() includes:
# -i/--input: Input source (default: /dev/video0)
# -u/--use-frame: Enable frame processing
# -f/--show-fps: Display FPS counter
# --disable-sync: Run as fast as possible
# --dump-dot: Generate pipeline graph

# QUEUE() helper
QUEUE("my_queue")                       # Standard queue
QUEUE("my_queue", max_size_buffers=5)  # Custom size

# get_caps_from_pad(pad)
format, width, height = get_caps_from_pad(pad)  # Returns: 'RGB', 640, 480

# get_numpy_from_buffer(buffer, format, width, height)
frame = get_numpy_from_buffer(buffer, 'RGB', 640, 480)  # NumPy array
```

---

## Hailo Object Types

```python
import hailo

# Get buffer ROI (all metadata)
roi = hailo.get_roi_from_buffer(buffer)

# Extract object types
detections = roi.get_objects_typed(hailo.HAILO_DETECTION)
landmarks = roi.get_objects_typed(hailo.HAILO_LANDMARKS)
masks = roi.get_objects_typed(hailo.HAILO_CONF_CLASS_MASK)

# Detection methods
detection.get_label()                   # String
detection.get_confidence()              # Float 0-1
detection.get_bbox()                    # Bounding box

# Bounding box methods
bbox.xmin()  bbox.ymin()               # Top-left normalized (0-1)
bbox.width() bbox.height()             # Size normalized (0-1)

# Landmarks/Points
landmarks[0].get_points()               # List of points
point.x() point.y()                     # Normalized to bbox

# Masks
mask.get_width()  mask.get_height()    # Dimensions
mask.get_data()                         # 1D array of data
```

---

## Performance Tips

1. **Use Smaller Models for Real-Time**
   - yolov6n (fastest)
   - yolov8s (medium)
   - yolov8l (slower, more accurate)

2. **Adjust Batch Size**
   ```python
   self.batch_size = 1  # Faster, lower latency
   self.batch_size = 4  # Higher throughput
   ```

3. **Adjust Queue Sizes**
   ```python
   QUEUE("queue_name", max_size_buffers=3)   # Less buffering = lower latency
   QUEUE("queue_name", max_size_buffers=10)  # More buffering = less drops
   ```

4. **Disable Sync for Files**
   ```bash
   python detection.py -i video.mp4 --disable-sync
   ```

5. **Filter Early**
   ```python
   # Filter by confidence in callback, not after
   MIN_CONF = 0.7
   for detection in detections:
       if detection.get_confidence() >= MIN_CONF:
           # Process
   ```

---

## Common Errors & Solutions

| Error | Solution |
|-------|----------|
| `TAPPAS_POST_PROC_DIR not set` | Run: `source ../setup_env.sh` |
| `Failed to import hailo` | Check virtual environment activation |
| `Pipeline parse error` | Check pipeline string syntax, especially quotes |
| `HEF file not found` | Verify model path in `self.hef_path` |
| `identity_callback not found` | Ensure `identity name=identity_callback` in pipeline |
| `Frames dropping` | Increase queue sizes, reduce processing |
| `Import error: gpiozero` | Run: `pip install gpiozero` |

---

## Quick Test

```bash
# Test object detection
python detection.py -i /dev/video0 --show-fps

# Test with file
python detection.py -i ../resources/detection0.mp4 --show-fps --disable-sync

# Test with frame display
python detection.py -i /dev/video0 --use-frame

# Test different model
python detection.py -i /dev/video0 --network yolov8s --show-fps

# Test pose estimation
python pose_estimation.py -i /dev/video0 --use-frame

# Test segmentation
python instance_segmentation.py -i /dev/video0 --use-frame
```

---

**Keep PIPELINE_LEARNING_GUIDE.md open when developing!**
