import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib
import os
import json
import argparse
import multiprocessing
import queue
import threading
import time
from collections import deque
import numpy as np
import setproctitle
import cv2
import hailo
from hailo_rpi_common import (
    get_default_parser,
    QUEUE,
    get_caps_from_pad,
    get_numpy_from_buffer,
    GStreamerApp,
    app_callback_class,
)
from gpiozero import DistanceSensor, LED

# ---------------------------------------------------------------------------
# hailo_platform: needed for secondary (off-pipeline) inference.
# Device sharing requires the HailoRT multi-process service:
#   sudo systemctl start hailort
# ---------------------------------------------------------------------------
try:
    from hailo_platform import (
        VDevice, HEF, ConfigureParams, InferVStreams,
        InputVStreamParams, OutputVStreamParams,
        FormatType, HailoSchedulingAlgorithm,
    )
    _HAILO_PLATFORM_OK = True
except ImportError:
    _HAILO_PLATFORM_OK = False
    print("[SecondaryWorker] hailo_platform not importable — secondary inference disabled.")

# ---------------------------------------------------------------------------
# Paths — override with env vars or change here
# ---------------------------------------------------------------------------
_DIR = os.path.dirname(os.path.abspath(__file__))
MOBILENET_HEF_PATH    = os.path.join(_DIR, '../resources/mobilenet_v1.hef')
MOBILENET_LABELS_PATH = os.path.join(_DIR, '../resources/mobilenet_labels.json')
MIDAS_HEF_PATH        = os.path.join(_DIR, '../resources/midas_v2_1_small.hef')

MOBILENET_INPUT_HW = (224, 224)   # H, W
MIDAS_INPUT_HW     = (256, 256)

# ---------------------------------------------------------------------------
# Spoof detection — MiDaS depth variance threshold (normalized [0,1] depth).
# A flat screen/photo produces near-uniform depth → very low variance.
# A real 3D scene has foreground/background separation → higher variance.
# Tune this after Test 4: lower = more sensitive, higher = more permissive.
# ---------------------------------------------------------------------------
SPOOF_VARIANCE_THRESHOLD = 0.005

# ---------------------------------------------------------------------------
# Weapon keyword set for MobileNet top-1 classification
# ---------------------------------------------------------------------------
_WEAPON_KW = frozenset({
    "knife", "knives", "scissor", "scissors", "weapon", "gun", "blade",
    "sword", "dagger", "axe", "pistol", "rifle", "revolver", "cleaver",
})

# ---------------------------------------------------------------------------
# Bounded secondary queue — maxsize=2 means put_nowait on a full queue raises
# queue.Full immediately; the YOLO pad-probe thread never waits.
# ---------------------------------------------------------------------------
_secondary_q: queue.Queue = queue.Queue(maxsize=2)

# ---------------------------------------------------------------------------
# GPIO globals
# ---------------------------------------------------------------------------
motion_detected_flag = False
state_lock = threading.Lock()
user_data = None
led    = None
sensor = None


# ---------------------------------------------------------------------------
# TestMetrics — single shared instance, all methods are thread-safe.
# Provides the observable numbers for Tests 1–5.
# ---------------------------------------------------------------------------
class TestMetrics:
    """
    Thread-safe counters and a rolling FPS estimator.

    Tests map to fields:
      Test 1 (Baseline)  — fps(), queue_drops (should be 0 at idle)
      Test 2 (Trigger)   — secondary_triggers, safe_count
      Test 3 (Threat)    — weapon_count
      Test 4 (Spoof)     — spoof_count
      Test 5 (Stress)    — queue_drops, secondary_triggers
    """
    def __init__(self):
        self._lock             = threading.Lock()
        self.queue_drops       = 0
        self.secondary_triggers = 0
        self.safe_count        = 0
        self.weapon_count      = 0
        self.spoof_count       = 0
        # Rolling 60-frame window for FPS; deque is O(1) append+popleft
        self._frame_times: deque = deque(maxlen=60)

    def record_frame(self):
        with self._lock:
            self._frame_times.append(time.monotonic())

    def fps(self) -> float:
        with self._lock:
            times = list(self._frame_times)
        if len(times) < 2:
            return 0.0
        return (len(times) - 1) / (times[-1] - times[0])

    def record_drop(self):
        with self._lock:
            self.queue_drops += 1

    def record_trigger(self):
        with self._lock:
            self.secondary_triggers += 1

    def record_verdict(self, classification: str, is_spoof: bool):
        with self._lock:
            if is_spoof:
                self.spoof_count += 1
            elif classification == "WEAPON":
                self.weapon_count += 1
            else:
                self.safe_count += 1

    def status_line(self, q_depth: int) -> str:
        return (
            f"[BASELINE  ] FPS={self.fps():.1f}  "
            f"q_depth={q_depth}  drops={self.queue_drops}  "
            f"triggers={self.secondary_triggers}  "
            f"safe={self.safe_count}  weapon={self.weapon_count}  spoof={self.spoof_count}"
        )


_metrics = TestMetrics()
_last_status_ts = 0.0
STATUS_INTERVAL = 5.0   # seconds between periodic baseline status prints


# ---------------------------------------------------------------------------
# Label helpers
# ---------------------------------------------------------------------------
def _load_labels(path: str) -> list:
    """
    Load a JSON labels file. Accepts two formats:
      list  — ["cat", "dog", ...]  (index == position)
      dict  — {"0": "cat", "1": "dog", ...}
    Returns an empty list if the file does not exist.
    """
    if not os.path.exists(path):
        return []
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        max_idx = max(int(k) for k in data)
        labels = [""] * (max_idx + 1)
        for k, v in data.items():
            labels[int(k)] = str(v)
        return labels
    return []


def _classify_label(label_name: str) -> str:
    """Map a label string to WEAPON or SAFE using keyword matching."""
    lower = label_name.lower()
    return "WEAPON" if any(kw in lower for kw in _WEAPON_KW) else "SAFE"


# ---------------------------------------------------------------------------
# Spoof detection
# ---------------------------------------------------------------------------
def _check_spoof(depth_map: np.ndarray):
    """
    Returns (is_spoof: bool, variance: float).

    Normalises depth_map to [0, 1] so the threshold is scale-independent,
    then computes pixel variance.  A flat screen/printed photo held close to
    the lens produces near-uniform depth → variance << SPOOF_VARIANCE_THRESHOLD.
    A real person in a room has clear foreground/background separation → higher
    variance.

    The normalisation step is intentional: raw MiDaS output magnitudes vary
    wildly with scene scale, so comparing raw values across frames would need
    a different threshold for indoors vs outdoors lighting.
    """
    d = depth_map.astype(np.float32)
    d_range = float(d.max() - d.min())
    if d_range < 1e-6:
        # Completely flat output → certain spoof
        return True, 0.0
    norm = (d - d.min()) / d_range
    var  = float(np.var(norm))
    return var < SPOOF_VARIANCE_THRESHOLD, var


# ---------------------------------------------------------------------------
# User callback class
# ---------------------------------------------------------------------------
class user_app_callback_class(app_callback_class):
    def __init__(self):
        super().__init__()
        self.new_variable = 42
        self.person_detected = False
        self._person_frame_count    = 0
        self._no_person_frame_count = 0

    def new_function(self):
        return "The meaning of life is: "


PERSON_CONFIRM_FRAMES = 3
PERSON_CLEAR_FRAMES   = 5


# ---------------------------------------------------------------------------
# GStreamer pad-probe callback  ← PRIMARY YOLO THREAD
# Must return in microseconds. Never blocks on _secondary_q.
# ---------------------------------------------------------------------------
def app_callback(pad, info, user_data):
    global _last_status_ts

    buffer = info.get_buffer()
    if buffer is None:
        return Gst.PadProbeReturn.OK

    user_data.increment()
    _metrics.record_frame()

    format, width, height = get_caps_from_pad(pad)
    frame = None
    if format is not None and width is not None and height is not None:
        frame = get_numpy_from_buffer(buffer, format, width, height)

    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)

    detection_count = 0
    person_detected_in_frame = False
    for detection in detections:
        label      = detection.get_label()
        confidence = detection.get_confidence()
        if label == "person":
            detection_count += 1
            person_detected_in_frame = True

    # Temporal filtering + LED decision under one lock
    with state_lock:
        if person_detected_in_frame:
            user_data._person_frame_count   += 1
            user_data._no_person_frame_count = 0
            if user_data._person_frame_count >= PERSON_CONFIRM_FRAMES:
                user_data.person_detected = True
        else:
            user_data._no_person_frame_count += 1
            user_data._person_frame_count    = 0
            if user_data._no_person_frame_count >= PERSON_CLEAR_FRAMES:
                user_data.person_detected = False
        should_led_on = user_data.person_detected and motion_detected_flag

    if led is not None:
        threading.Thread(
            target=lambda v=should_led_on: led.on() if v else led.off(),
            daemon=True,
        ).start()

    # --- Async bypass hand-off ---
    # put_nowait never blocks; queue.Full means the worker is still processing
    # a prior frame — we drop and let the YOLO pipeline run unimpeded.
    if user_data.person_detected and frame is not None:
        try:
            _secondary_q.put_nowait(frame.copy())
            _metrics.record_trigger()
        except queue.Full:
            _metrics.record_drop()
            # Test 5 observable: print only when drops happen so it stands out
            print(f"[STRESS    ] frame DROPPED (queue full)  "
                  f"total_drops={_metrics.queue_drops}")

    # Periodic baseline status — Test 1 observable (every STATUS_INTERVAL s)
    now = time.monotonic()
    if now - _last_status_ts >= STATUS_INTERVAL:
        _last_status_ts = now
        print(_metrics.status_line(_secondary_q.qsize()))

    # Optional display overlay
    if user_data.use_frame and frame is not None:
        cv2.putText(frame, f"Detections: {detection_count}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        cv2.putText(frame, f"{user_data.new_function()} {user_data.new_variable}", (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        user_data.set_frame(frame)

    return Gst.PadProbeReturn.OK


# ---------------------------------------------------------------------------
# GPIO helpers
# ---------------------------------------------------------------------------
def update_led_state():
    if led is None or user_data is None:
        return
    with state_lock:
        if user_data.person_detected and motion_detected_flag:
            led.on()
        else:
            led.off()

def motion_detected():
    global motion_detected_flag
    print("Motion detected!")
    with state_lock:
        motion_detected_flag = True
    update_led_state()

def no_motion_detected():
    global motion_detected_flag
    print("No motion detected")
    with state_lock:
        motion_detected_flag = False
    update_led_state()


# ---------------------------------------------------------------------------
# Secondary Inference Worker  ← SEPARATE DAEMON THREAD
#
# Flow per frame:
#   1. Crop + resize to MOBILENET_INPUT_HW → MobileNet → top-1 label → SAFE/WEAPON
#   2. Crop + resize to MIDAS_INPUT_HW     → MiDaS     → depth variance → REAL/SPOOF
#   3. Print structured result; update _metrics
#
# Device sharing: VDevice with ROUND_ROBIN; the Hailo scheduler arbitrates
# between this session and the GStreamer hailonet element automatically.
# ---------------------------------------------------------------------------
class SecondaryInferenceWorker(threading.Thread):
    def __init__(self, mobilenet_hef: str, midas_hef: str, labels_path: str):
        super().__init__(daemon=True, name="SecondaryInferWorker")
        self.mobilenet_hef_path = mobilenet_hef
        self.midas_hef_path     = midas_hef
        self.labels             = _load_labels(labels_path)
        self._stop              = threading.Event()

    def run(self):
        if not _HAILO_PLATFORM_OK:
            print("[SecondaryWorker] Disabled (hailo_platform unavailable).")
            return

        missing = [p for p in (self.mobilenet_hef_path, self.midas_hef_path)
                   if not os.path.exists(p)]
        if missing:
            for p in missing:
                print(f"[SecondaryWorker] HEF not found: {p}")
            print("[SecondaryWorker] Cannot start without both HEF files.")
            return

        try:
            params = VDevice.create_params()
            params.scheduling_algorithm = HailoSchedulingAlgorithm.ROUND_ROBIN
        except Exception as e:
            print(f"[SecondaryWorker] VDevice params error: {e}")
            return

        try:
            with VDevice(params) as vdev:
                self._open_and_run(vdev)
        except Exception as e:
            print(f"[SecondaryWorker] Fatal: {e}")

    def _open_and_run(self, vdev):
        mob_hef   = HEF(self.mobilenet_hef_path)
        midas_hef = HEF(self.midas_hef_path)

        mob_ng   = vdev.configure(mob_hef,   ConfigureParams.create_from_hef(mob_hef))[0]
        midas_ng = vdev.configure(midas_hef, ConfigureParams.create_from_hef(midas_hef))[0]

        mob_in   = InputVStreamParams.make(mob_ng,   quantized=False, format_type=FormatType.FLOAT32)
        mob_out  = OutputVStreamParams.make(mob_ng,  quantized=False, format_type=FormatType.FLOAT32)
        mid_in   = InputVStreamParams.make(midas_ng, quantized=False, format_type=FormatType.FLOAT32)
        mid_out  = OutputVStreamParams.make(midas_ng, quantized=False, format_type=FormatType.FLOAT32)

        mob_inp_name  = mob_ng.get_input_vstream_infos()[0].name
        midas_inp_name = midas_ng.get_input_vstream_infos()[0].name

        # Keep vstream contexts alive for the whole session — no per-frame setup cost
        with InferVStreams(mob_ng,   mob_in,  mob_out)  as mob_pipe, \
             InferVStreams(midas_ng, mid_in,  mid_out)  as midas_pipe:
            print("[SecondaryWorker] Ready — waiting for person frames.")
            self._loop(mob_pipe, mob_inp_name, midas_pipe, midas_inp_name)

    def _loop(self, mob_pipe, mob_inp, midas_pipe, midas_inp):
        while not self._stop.is_set():
            try:
                frame = _secondary_q.get(timeout=0.1)
            except queue.Empty:
                continue

            try:
                # ── MobileNet classification ──────────────────────────────
                mob_crop = cv2.resize(frame, (MOBILENET_INPUT_HW[1], MOBILENET_INPUT_HW[0]))
                mob_crop = mob_crop.astype(np.float32) / 255.0
                mob_out  = mob_pipe.infer({mob_inp: np.expand_dims(mob_crop, 0)})
                scores   = list(mob_out.values())[0].ravel()
                top_idx  = int(np.argmax(scores))
                top_name = self.labels[top_idx] if top_idx < len(self.labels) else str(top_idx)
                verdict  = _classify_label(top_name)

                # ── MiDaS depth + spoof check ─────────────────────────────
                depth_crop = cv2.resize(frame, (MIDAS_INPUT_HW[1], MIDAS_INPUT_HW[0]))
                depth_crop = depth_crop.astype(np.float32) / 255.0
                depth_out  = midas_pipe.infer({midas_inp: np.expand_dims(depth_crop, 0)})
                depth_map  = list(depth_out.values())[0][0]
                is_spoof, var = _check_spoof(depth_map)
                depth_verdict = "SPOOF_ATTEMPT" if is_spoof else "REAL"

                _metrics.record_verdict(verdict, is_spoof)

                # ── Structured output (all 5 tests readable here) ─────────
                alert = ""
                if verdict == "WEAPON" and not is_spoof:
                    alert = "  *** WEAPON ALERT ***"
                elif is_spoof:
                    alert = "  *** SPOOF FLAG ***"

                print(
                    f"[CLASSIFY  ] "
                    f"MobileNet→{verdict} ({top_name})  "
                    f"MiDaS var={var:.4f}→{depth_verdict}"
                    f"{alert}"
                )

            except Exception as e:
                print(f"[SecondaryWorker] Inference error: {e}")
            finally:
                _secondary_q.task_done()

    def stop(self):
        self._stop.set()


# ---------------------------------------------------------------------------
# GStreamer Application
# ---------------------------------------------------------------------------
class GStreamerDetectionApp(GStreamerApp):
    def __init__(self, args, user_data):
        super().__init__(args, user_data)
        self.batch_size     = 1
        self.network_width  = 640
        self.network_height = 640
        self.network_format = "RGB"
        nms_score_threshold = 0.25
        nms_iou_threshold   = 0.45

        new_postprocess_path = os.path.join(self.current_path, '../resources/libyolo_hailortpp_post.so')
        if os.path.exists(new_postprocess_path):
            self.default_postprocess_so = new_postprocess_path
        else:
            self.default_postprocess_so = os.path.join(self.postprocess_dir, 'libyolo_hailortpp_post.so')

        if args.hef_path is not None:
            self.hef_path = args.hef_path
        elif args.network == "yolov6n":
            self.hef_path = os.path.join(self.current_path, '../resources/yolov6n.hef')
        elif args.network == "yolov8s":
            self.hef_path = os.path.join(self.current_path, '../resources/yolov8s_h8l.hef')
        elif args.network == "yolox_s_leaky":
            self.hef_path = os.path.join(self.current_path, '../resources/yolox_s_leaky_h8l_mz.hef')
        else:
            assert False, "Invalid network type"

        if args.labels_json is not None:
            self.labels_config = f' config-path={args.labels_json} '
            if not os.path.exists(new_postprocess_path):
                print("New postprocess so file is missing. Required for custom labels.")
                exit(1)
        else:
            self.labels_config = ''

        self.app_callback = app_callback

        if args.labels_json is not None:
            self.thresholds_str = (
                f"nms-score-threshold={nms_score_threshold} "
                f"nms-iou-threshold={nms_iou_threshold}"
            )
        else:
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
                "video/x-raw, width=1536, height=864 ! "
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
                + " qtdemux ! h264parse ! avdec_h264 max-threads=2 ! "
                " video/x-raw, format=I420 ! "
            )
        source_element += QUEUE("queue_scale")
        source_element += "videoscale n-threads=2 ! "
        source_element += QUEUE("queue_src_convert")
        source_element += "videoconvert n-threads=3 name=src_convert qos=false ! "
        source_element += (
            f"video/x-raw, format={self.network_format}, "
            f"width={self.network_width}, height={self.network_height}, pixel-aspect-ratio=1/1 ! "
        )

        pipeline_string = (
            "hailomuxer name=hmux "
            + source_element
            + "tee name=t ! "
            + QUEUE("bypass_queue", max_size_buffers=20)
            + "hmux.sink_0 "
            + "t. ! "
            + QUEUE("queue_hailonet")
            + f"hailonet hef-path={self.hef_path} batch-size={self.batch_size} {self.thresholds_str} force-writable=true ! "
            + QUEUE("queue_hailofilter")
            + f"hailofilter so-path={self.default_postprocess_so} {self.labels_config} qos=false ! "
            + QUEUE("queue_hmuc")
            + "hmux.sink_1 "
            + "hmux. ! "
            + QUEUE("queue_user_callback")
            + "identity name=identity_callback ! "
            + QUEUE("queue_hailooverlay")
            + "hailooverlay ! "
            + QUEUE("queue_videoconvert")
            + "videoconvert n-threads=3 qos=false ! "
            + QUEUE("queue_hailo_display")
            + f"fpsdisplaysink video-sink={self.video_sink} name=hailo_display sync={self.sync} "
            + f"text-overlay={self.options_menu.show_fps} signal-fps-measurements=true "
        )
        print(pipeline_string)
        return pipeline_string


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    user_data = user_app_callback_class()

    sensor = DistanceSensor(echo=24, trigger=18, max_distance=2, threshold_distance=0.5)
    led    = LED(17)
    sensor.when_in_range     = motion_detected
    sensor.when_out_of_range = no_motion_detected

    secondary_worker = SecondaryInferenceWorker(
        MOBILENET_HEF_PATH, MIDAS_HEF_PATH, MOBILENET_LABELS_PATH
    )
    secondary_worker.start()

    parser = get_default_parser()
    parser.add_argument(
        "--network",
        default="yolov8s",
        choices=['yolov6n', 'yolov8s', 'yolox_s_leaky'],
        help="Which network to use",
    )
    parser.add_argument("--hef-path",    default=None, help="Path to HEF file")
    parser.add_argument("--labels-json", default=None, help="Path to custom labels JSON")
    args = parser.parse_args()

    app = GStreamerDetectionApp(args, user_data)
    app.run()

    secondary_worker.stop()
    secondary_worker.join(timeout=2)
