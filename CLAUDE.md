# CLAUDE.md
This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This repository contains examples demonstrating AI inference capabilities on Raspberry Pi 5 using the Hailo-8/8L AI accelerator. The project includes Python-based GStreamer pipelines for object detection, pose estimation, and instance segmentation tasks.

## Environment Setup

**CRITICAL: Always source the environment script before running any pipeline**

```bash
# This MUST be sourced (not executed) to set up the environment
source setup_env.sh

# Install Python dependencies (only needed once)
pip install -r requirements.txt

# Download model files (only needed once)
./download_resources.sh

# Compile custom post-processing library for retrained model support (only needed once)
./compile_postprocess.sh
```

The `setup_env.sh` script:
- Detects whether `hailo_tappas` or `hailo-tappas-core` is installed
- Activates the appropriate virtual environment (creates if needed)
- Sets required environment variables: `TAPPAS_WORKSPACE`, `TAPPAS_POST_PROC_DIR`, `DEVICE_ARCHITECTURE`
- Validates TAPPAS version compatibility (3.28.0, 3.28.2, or 3.31.0)

## Running Examples

```bash
# Detection (default: YOLOv6n)
python basic_pipelines/detection.py --input resources/detection0.mp4

# Pose estimation (YOLOv8 Pose)
python basic_pipelines/pose_estimation.py --input resources/detection0.mp4

# Instance segmentation (YOLOv5n Seg)
python basic_pipelines/instance_segmentation.py --input resources/detection0.mp4

# Run with USB camera
python basic_pipelines/detection.py --input /dev/video0

# Run with Raspberry Pi camera (Beta)
python basic_pipelines/detection.py --input rpi

# Use different detection model
python basic_pipelines/detection.py --network yolov8s

# Use custom retrained model
python basic_pipelines/detection.py --hef-path resources/yolov8s-hailo8l-barcode.hef --labels-json resources/barcode-labels.json
```

### Common Options
- `--use-frame` / `-u`: Extract and display video frame in callback (slower)
- `--show-fps` / `-f`: Display FPS overlay
- `--disable-sync`: Run as fast as possible (useful for file sources)
- `--dump-dot`: Generate pipeline.dot file for visualization

## Architecture

### GStreamer Pipeline Architecture

All examples follow the same architectural pattern:

1. **hailo_rpi_common.py**: Base infrastructure
   - `app_callback_class`: Base class for managing frame data, counters, and inter-thread communication via multiprocessing.Queue
   - `GStreamerApp`: Base class handling pipeline creation, event loop, and cleanup
   - Utility functions: `get_caps_from_pad()`, `get_numpy_from_buffer()`, `QUEUE()` macro

2. **Application Structure** (detection.py, pose_estimation.py, instance_segmentation.py):
   - `user_app_callback_class`: Extends `app_callback_class` with task-specific data
   - `app_callback()`: User-defined function processing each frame's Hailo metadata
   - `GStreamerDetectionApp` (or similar): Extends `GStreamerApp`, overrides `get_pipeline_string()` to define the pipeline

3. **GStreamer Pipeline Flow**:
   ```
   Source (camera/file) → videoscale → videoconvert → hailomuxer
                                                           ↓
   ┌──────────────────────────────────────────────────────┘
   ├─→ bypass_queue ──────────────────────────────────────┐
   │                                                       ↓
   └─→ hailonet (inference) → hailofilter (postprocess) ──┤
                                                           ↓
                                    hailomuxer.sink_0/1 → identity_callback
                                                           ↓
                                                    hailooverlay → display
   ```

   - **hailomuxer**: Synchronizes raw frames with inference results
   - **hailonet**: Runs model inference on Hailo accelerator
   - **hailofilter**: Post-processes network output (NMS, parsing)
   - **identity_callback**: Where `app_callback()` is attached as a probe

### Hailo Metadata Hierarchy

Frames are tagged with Hailo metadata objects accessible via the `hailo` Python module:

- `hailo.get_roi_from_buffer(buffer)`: Returns root `HAILO_ROI` object
- Detection: `roi.get_objects_typed(hailo.HAILO_DETECTION)` → bounding boxes, labels, confidence
- Pose: Each detection contains `hailo.HAILO_LANDMARKS` objects (17 keypoints for human pose)
- Instance Segmentation: Each detection contains `hailo.HAILO_CONF_CLASS_MASK` objects

All coordinates and masks are normalized to the detection bounding box and must be scaled to frame dimensions.

## Model Support

### Pre-trained Models
Located in `resources/` after running `download_resources.sh`:
- **Detection**: yolov6n.hef, yolov8s_h8l.hef, yolox_s_leaky_h8l_mz.hef
- **Pose**: yolov8s_pose_h8l_pi.hef
- **Segmentation**: yolov5n_seg_h8l_mz.hef
- **Custom**: yolov8s-hailo8l-barcode.hef (example retrained model)

### Custom Retrained Models
To use retrained models:
1. Model MUST be compiled with HailoRT NMS Post Process (HailortPP)
2. Use `--hef-path` to specify HEF file
3. Use `--labels-json` to provide custom labels (see `resources/barcode-labels.json` for format)
4. Requires compiled post-process library: run `./compile_postprocess.sh`

## C++ Post-Processing

The `cpp/` directory contains custom post-processing code:
- `yolo_hailortpp.cpp/hpp`: YOLO post-processing with custom label support
- `hailo_nms_decode.hpp`: NMS decoding utilities
- Built using Meson: `meson setup build.release --buildtype=release && ninja -C build.release`
- Output: `libyolo_hailortpp_post.so` loaded by GStreamer hailofilter element

## Debugging

### Pipeline Visualization
```bash
python basic_pipelines/detection.py --dump-dot
dot -Tpng basic_pipelines/pipeline.dot -o pipeline.png
```

### Common Issues

**DEVICE_IN_USE() error**: Hailo device locked by another process
```bash
# Find process using /dev/hailo0
sudo lsof /dev/hailo0

# Kill process
sudo kill -9 <PID>
```

**HAILO_DRIVER_NOT_INSTALLED error**: Kernel module not built for current kernel (common after kernel updates)
```bash
# Check if module is loaded
lsmod | grep hailo

# Check DKMS status
dkms status

# Rebuild module for current kernel
sudo dkms install hailo_pci/4.20.0 -k $(uname -r)

# Load the module
sudo modprobe hailo_pci

# Verify device exists
ls -la /dev/hailo0
```

**Import hailo module fails**: Not in Hailo virtual environment
```bash
source setup_env.sh  # Must be sourced, not executed
```

**RPi camera issues**: RPi camera support is still in Beta (use `--input rpi`)

## Development Notes

- The repository contains multiple AI security system variants (AI_Security*.py, Garuda*.py) which are user projects, not part of the core examples
- Core examples are: detection.py, pose_estimation.py, instance_segmentation.py
- When modifying pipelines, batch_size, network dimensions, and format must match the HEF file's requirements
- The `--use-frame` flag extracts frames from buffers; this is NOT optimized and slows down the pipeline
- All examples use batch_size=2 by default for detection tasks
- `detection.py` has been customized from the upstream version to integrate GPIO hardware: HC-SR04 ultrasonic distance sensor (echo=GPIO24, trigger=GPIO18) and an LED (GPIO17) via `gpiozero`. The LED activates when both person detection and motion are true simultaneously.

## Project Garuda (User AI Security System)

The `basic_pipelines/` directory contains an evolving AI home security system ("Project Garuda") with many version files (AI_Security_2.x through 4.x, Garuda.py, Garuda_fixed.py). These all extend the core Hailo detection pipeline with:

- **Voice assistant ("Narada")**: Uses `speech_recognition` for voice commands
- **Email alerts**: OTP-based authentication via `smtplib`; alert cooldown to avoid spam
- **GPIO hardware**: LED indicators, buttons, and ultrasonic sensors via `gpiozero`
- **Danger detection**: Logs sightings of specific objects (e.g., scissors) to `danger_sightings.txt`
- **Night mode**: Separate detection logic/logging for low-light conditions (`night_mode_findings.txt`)
- **Persistent logs**: JSON-based logs in `system_logs/` directory (retained 7 days)
- **GUI**: Earlier versions use `tkinter`; later versions (4.x) use `PyQt5`

The `ProjectGaruda/` directory is a refactored, modular version of the same system split into separate modules:
- `main_app.py`: Entry point; tkinter-based `MainApplication` that routes login → admin/user dashboard
- `login_module.py`: Login UI with role-based access (admin/user)
- `admin_dashboard.py` / `user_dashboard.py`: Role-specific UI views
- `common.py`: Shared global state (mode flags: DND, EMAIL_OFF, IDLE, NIGHT), log lists, and utility functions
- `garuda_pipeline.py`: Hailo GStreamer pipeline logic (started via `garuda_pipeline.start_pipeline()`)

Run with: `python ProjectGaruda/main_app.py` (after sourcing `setup_env.sh`)

### Additional Dependencies for AI Security Apps

```bash
pip install PyQt5 SpeechRecognition psutil gpiozero
```


to run a file you can use this e`Garuda.py` / `AI_Security*.py` files are standalone scripts that import from `hailo_rpi_common` and are run the same way as the core examples (after sourcing `setup_env.sh`).
