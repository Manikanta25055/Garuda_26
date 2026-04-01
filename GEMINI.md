# Hailo Raspberry Pi 5 AI & IoT Workspace

This project is a comprehensive development workspace for AI and IoT applications on the Raspberry Pi 5, leveraging the Hailo-8L AI processor for high-performance inference. It includes official examples, custom computer vision pipelines, and hardware interfacing scripts.

## Project Overview

- **Purpose:** Demonstrate and develop AI-powered applications (object detection, pose estimation, instance segmentation) on Raspberry Pi 5 using Hailo-8L.
- **Main Technologies:** 
  - **Python:** Core application logic and GStreamer integration.
  - **GStreamer:** Multimedia framework for efficient video processing pipelines.
  - **HailoRT:** Hardware-accelerated inference on the Hailo-8L AI processor.
  - **OpenCV:** Image processing and visualization.
  - **Gpiozero / RPi.GPIO:** Hardware interfacing for sensors (DHT22, Ultrasonic, PIR) and actuators (LEDs, Servos).
  - **Tkinter:** GUI for advanced applications like Project Garuda.

## Architecture

The project follows a template-based architecture for building GStreamer pipelines:

1.  **`app_callback_class` (Base):** Manages application state, frame counting, and inter-thread frame passing via `multiprocessing.Queue`.
2.  **`GStreamerApp` (Base):** Handles GStreamer initialization, pipeline creation from a string, bus message handling, and running the GLib main loop.
3.  **Pipeline String:** Pipelines are defined as GStreamer launch strings, typically involving a "tee" to split the stream into a bypass path (original resolution) and an inference path (resized/converted for the model).
4.  **`hailomuxer`:** Recombines the bypass stream with metadata generated from the inference path.
5.  **`identity_callback`:** A GStreamer `identity` element where a Python callback (`app_callback`) is attached to intercept buffers and extract inference results using the `hailo` Python module.

## Building and Running

### Environment Setup
The workspace uses a Python virtual environment. Always source the environment setup script before running any examples:

```bash
source setup_env.sh
```
This script:
- Activates the virtual environment (`venv_hailo_rpi5_examples`).
- Sets `TAPPAS_POST_PROC_DIR` for post-processing shared libraries.
- Exports `DEVICE_ARCHITECTURE` (e.g., `HAILO8L`).

### Running Examples
Basic pipelines are located in the `basic_pipelines/` directory.

- **Object Detection:**
  ```bash
  python basic_pipelines/detection.py --input rpi --network yolov6n
  ```
- **Pose Estimation:**
  ```bash
  python basic_pipelines/pose_estimation.py --input /dev/video0
  ```
- **Instance Segmentation:**
  ```bash
  python basic_pipelines/instance_segmentation.py --input resources/detection0.mp4
  ```

### Common Arguments
- `--input`, `-i`: Input source (`rpi`, `/dev/videoX`, or file path).
- `--use-frame`, `-u`: Enables frame processing/display via OpenCV in the callback.
- `--show-fps`, `-f`: Prints FPS information.
- `--network`: Selects the model (e.g., `yolov6n`, `yolov8s`).
- `--hef-path`: Path to a custom Hailo Executable Format (HEF) file.

## Development Conventions

- **Pipeline Customization:** Inherit from `GStreamerApp` and override `get_pipeline_string()`.
- **Metadata Extraction:** Use `hailo.get_roi_from_buffer(buffer)` in the callback to access detections, landmarks, or masks.
- **Hardware Integration:** Use `gpiozero` for modern GPIO management. For thread-safe operations between the GStreamer callback and hardware control, use `threading.Lock`.
- **Logging:** Sensor data is often logged to CSV files in the root or `system_logs/`.
- **Testing:** Use `basic_pipelines/test_hardware.py` to verify GPIO connections.

## Key Directories and Files

- `basic_pipelines/`: Core Python examples and common utility scripts.
  - `hailo_rpi_common.py`: Base classes and utility functions.
  - `PIPELINE_LEARNING_GUIDE.md`: Detailed architectural documentation.
- `resources/`: HEF model files, labels, and sample videos.
- `ProjectGaruda/`: A complete security application with a GUI and integrated pipeline.
- `doc/`: Detailed installation and usage guides for different features.
- `setup_env.sh`: Essential environment initialization script.
