"""
Garuda Cascade — Pi 5 Runtime  (Phases 2 + 3 + 4)
===================================================
Cascaded NPU pipeline on Hailo-8L (13 TOPS):

  Camera → [YOLO @ 52 FPS]
                ↓  person detected (conf > 0.60)
           crop 224×224 / 256×256
           ↓              ↓
   [MobileNetV2]     [MiDaS FastDepth]      ← both sequential on shared VDevice
   Safe/Weapon       depth map
                          ↓
                   variance < 0.005 → Spoof_Attempt

HEF files (resources/):
  resources/yolov8s_garuda.hef      — custom YOLOv8s (Hammer/Knife/Person/Scissors)
  resources/mobilenetv2_garuda.hef  — MobileNetV2 classifier (Safe/Weapon)
  resources/midas_depth.hef         — MiDaS Small depth estimator

CPU affinity:
  Core 0 — camera capture thread
  Core 1 — HailoRT inference queue thread
  Core 2 — NumPy crop / depth math + logging thread
  Core 3 — reserved (OS)

Run (after source setup_env.sh):
  python basic_pipelines/garuda_cascade.py
  python basic_pipelines/garuda_cascade.py --device /dev/video0
  python basic_pipelines/garuda_cascade.py --hef-dir resources
"""

import os
import sys
import time
import json
import queue
import threading
import argparse
import logging
from collections import deque
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst as _Gst
    _Gst.init(None)
    _GST_OK = True
except Exception:
    _GST_OK = False

try:
    from hailo_platform import (
        VDevice,
        HailoStreamInterface,
        InferVStreams,
        ConfigureParams,
        InputVStreamParams,
        OutputVStreamParams,
        FormatType,
        HEF,
    )
except ImportError:
    print("ERROR: hailo_platform not found. Run: source setup_env.sh")
    sys.exit(1)

# ─── Configuration ────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("garuda_cascade")

PERSON_CLASS_ID       = 2       # index in YOLO 4-class output: Hammer/Knife/Person/Scissors
PERSON_CONF_THRESH    = 0.60
WEAPON_CONF_THRESH    = 0.25    # lower threshold for Hammer / Knife / Scissors
WEAPON_CLASS_IDS      = {0, 1, 3}
YOLO_LABELS           = {0: "Hammer", 1: "Knife", 2: "Person", 3: "scissors"}
DEPTH_SPOOF_THRESH    = 0.005   # depth variance below this → flat-screen spoof
                                # calibrated: real persons = 0.022–0.10, flat screens < 0.005
CLASS_LABELS          = ["Safe", "Weapon"]
CLASSIFIER_INPUT_SIZE = 224
DEPTH_INPUT_SIZE      = 256     # MiDaS Small native resolution (fixed by HEF compilation)
YOLO_INPUT_SIZE       = 640
TARGET_FPS            = 52

# Lightweight RGB anti-spoof (daytime proxy, no NPU needed)
FAST_SPOOF_SIZE   = 32          # downscale crop to 32×32 for variance check
FAST_SPOOF_THRESH = 0.005       # brightness variance below this → suspect flat-screen spoof

# Log destinations (consistent with existing Garuda system)
SCRIPT_DIR          = Path(__file__).parent
SIGHTINGS_LOG       = SCRIPT_DIR / "danger_sightings.txt"
SYSTEM_LOGS_DIR     = SCRIPT_DIR / "system_logs"
CASCADE_CONFIG_PATH = SCRIPT_DIR / "cascade_config.json"

HEF_NAMES = {
    "yolo":       "yolov8s_garuda.hef",
    "classifier": "mobilenetv2_garuda.hef",
    "depth":      "midas_depth.hef",
}


# ─── Cascade config (live-reloaded from admin panel) ─────────────────────────
_cfg: dict = {"depth_mode": "night_only", "_mtime": 0.0}

def _load_config():
    """Re-read cascade_config.json only when the file has changed."""
    try:
        mtime = CASCADE_CONFIG_PATH.stat().st_mtime
        if mtime != _cfg["_mtime"]:
            _cfg.update(json.loads(CASCADE_CONFIG_PATH.read_text()))
            _cfg["_mtime"] = mtime
    except Exception:
        pass

def _is_night() -> bool:
    h = datetime.now().hour
    return h >= 20 or h < 6

def _depth_active() -> bool:
    """Return True if full MiDaS depth should run this frame."""
    mode = _cfg.get("depth_mode", "night_only")
    if mode == "always":  return True
    if mode == "off":     return False
    return _is_night()    # "night_only"

def fast_rgb_spoof(frame: np.ndarray, box: tuple):
    """
    Lightweight spoof proxy — pure NumPy, <1 ms.
    Downscale the person crop to 32×32 and measure brightness variance.
    Low variance → very uniform illumination → likely flat screen / photo.
    Less accurate than MiDaS depth; use night mode for full anti-spoof.
    """
    h, w = frame.shape[:2]
    x1, y1 = max(0, int(box[0])), max(0, int(box[1]))
    x2, y2 = min(w, int(box[2])), min(h, int(box[3]))
    crop  = frame[y1:y2, x1:x2] if (x2 > x1 and y2 > y1) else frame
    gray  = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (FAST_SPOOF_SIZE, FAST_SPOOF_SIZE), interpolation=cv2.INTER_AREA)
    var   = float(np.var(small.astype(np.float32) / 255.0))
    return var < FAST_SPOOF_THRESH, var


# ─── Test-protocol metrics ────────────────────────────────────────────────────
# Single shared instance; all methods are thread-safe.
#
#   Test 1 (Baseline)  — fps(), queue_drops == 0 at idle
#   Test 2 (Trigger)   — safe_count increments; FPS unchanged
#   Test 3 (Threat)    — weapon_count increments
#   Test 4 (Spoof)     — spoof_count increments
#   Test 5 (Stress)    — queue_drops climbs; FPS stays stable
#
STATUS_INTERVAL = 5.0   # seconds between idle heartbeat lines

class TestMetrics:
    def __init__(self):
        self._lock          = threading.Lock()
        self.queue_drops    = 0
        self.safe_count     = 0
        self.weapon_count   = 0
        self.spoof_count    = 0
        # Rolling 60-frame window keeps FPS current without growing unboundedly
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

    def record_verdict(self, label: str):
        with self._lock:
            if label == "Spoof_Attempt":
                self.spoof_count += 1
            elif label == "Weapon":
                self.weapon_count += 1
            else:
                self.safe_count += 1

    def status_line(self, frame_q_depth: int) -> str:
        return (
            f"[BASELINE  ] FPS={self.fps():.1f}  "
            f"frame_q={frame_q_depth}  drops={self.queue_drops}  "
            f"safe={self.safe_count}  weapon={self.weapon_count}  spoof={self.spoof_count}"
        )


_metrics = TestMetrics()

# ─── Integration API — event callbacks ───────────────────────────────────────
# External modules (e.g. garuda_pipeline.py) register here to receive every
# detection event as a dict without touching postprocess_thread internals.
_event_callbacks: list = []

def register_event_callback(fn):
    """Register fn(entry: dict) called on every detection event."""
    _event_callbacks.append(fn)


# ─── CPU affinity ─────────────────────────────────────────────────────────────
def pin_thread(core: int):
    try:
        os.sched_setaffinity(0, {core})
        log.info(f"Thread '{threading.current_thread().name}' → CPU core {core}")
    except (AttributeError, PermissionError) as e:
        log.warning(f"CPU affinity not set (core {core}): {e}")


# ─── Depth anti-spoof ─────────────────────────────────────────────────────────
def is_spoof(depth_tensor: np.ndarray, threshold: float = DEPTH_SPOOF_THRESH):
    flat = depth_tensor.astype(np.float32).ravel()
    d_min, d_max = flat.min(), flat.max()
    if d_max - d_min > 1e-6:
        flat = (flat - d_min) / (d_max - d_min)
    variance = float(np.var(flat))
    return variance < threshold, variance


# ─── Pre-processing ──────────────────────────────────────────────────────────
def preprocess_yolo(frame: np.ndarray) -> np.ndarray:
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    resized = cv2.resize(rgb, (YOLO_INPUT_SIZE, YOLO_INPUT_SIZE), interpolation=cv2.INTER_LINEAR)
    return resized[np.newaxis].astype(np.uint8)


def preprocess_crop(frame: np.ndarray, box: tuple, size: int) -> np.ndarray:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = box
    x1, y1 = max(0, int(x1)), max(0, int(y1))
    x2, y2 = min(w, int(x2)), min(h, int(y2))
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        crop = frame
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    resized = cv2.resize(rgb, (size, size), interpolation=cv2.INTER_LINEAR)
    return resized[np.newaxis].astype(np.uint8)


# ─── Network wrappers ─────────────────────────────────────────────────────────
# Each wrapper stores the configured ng + vstream params.
# Inference activates → runs → deactivates sequentially (Hailo-8L constraint:
# only one network group can be active at a time on the physical chip).

class YoloNetwork:
    def __init__(self, ng, ivp, ovp, input_name):
        self._ng, self._ivp, self._ovp = ng, ivp, ovp
        self._input_name = input_name

    def infer(self, frame_nhwc: np.ndarray) -> dict:
        with self._ng.activate():
            with InferVStreams(self._ng, self._ivp, self._ovp) as p:
                return p.infer({self._input_name: frame_nhwc})

    def parse_detections(self, raw: dict, frame_hw: tuple) -> list:
        fh, fw = frame_hw
        detections = []
        for tensor in raw.values():
            if isinstance(tensor, list):
                # HailoRT NMS: tensor may be [batch0] (batch-wrapped) or [class0, class1, ...]
                # Unwrap batch dim when tensor[0] is itself a list of per-class arrays
                class_list = tensor[0] if (tensor and isinstance(tensor[0], list)) else tensor
                for cls_id, cls_dets in enumerate(class_list):
                    arr = np.array(cls_dets, dtype=np.float32)
                    if arr.size == 0:
                        continue
                    thresh = WEAPON_CONF_THRESH if cls_id in WEAPON_CLASS_IDS else PERSON_CONF_THRESH
                    for row in arr.reshape(-1, 5):
                        y1n, x1n, y2n, x2n, conf = row
                        if conf < thresh:
                            continue
                        detections.append({
                            "class_id": cls_id,
                            "conf":     float(conf),
                            "box":      (x1n * fw, y1n * fh, x2n * fw, y2n * fh),
                        })
            else:
                arr = np.array(tensor).reshape(-1, 6)
                for row in arr:
                    y1n, x1n, y2n, x2n, conf, cls = row
                    thresh = WEAPON_CONF_THRESH if int(cls) in WEAPON_CLASS_IDS else PERSON_CONF_THRESH
                    if conf < thresh:
                        continue
                    detections.append({
                        "class_id": int(cls),
                        "conf":     float(conf),
                        "box":      (x1n * fw, y1n * fh, x2n * fw, y2n * fh),
                    })
        return detections


class ClassifierNetwork:
    def __init__(self, ng, ivp, ovp, input_name):
        self._ng, self._ivp, self._ovp = ng, ivp, ovp
        self._input_name = input_name

    def infer(self, crop_nhwc: np.ndarray) -> str:
        with self._ng.activate():
            with InferVStreams(self._ng, self._ivp, self._ovp) as p:
                results = p.infer({self._input_name: crop_nhwc})
        logits = list(results.values())[0].ravel()
        return CLASS_LABELS[int(np.argmax(logits))]


class DepthNetwork:
    def __init__(self, ng, ivp, ovp, input_name):
        self._ng, self._ivp, self._ovp = ng, ivp, ovp
        self._input_name = input_name

    def infer(self, crop_nhwc: np.ndarray) -> np.ndarray:
        with self._ng.activate():
            with InferVStreams(self._ng, self._ivp, self._ovp) as p:
                results = p.infer({self._input_name: crop_nhwc})
        return list(results.values())[0]


# ─── Thread workers ───────────────────────────────────────────────────────────
def camera_thread(cap, frame_queue, stop, loop=False):
    """OpenCV-based capture thread for USB cameras and video files."""
    pin_thread(0)
    interval = 1.0 / TARGET_FPS
    while not stop.is_set():
        t0 = time.monotonic()
        ret, frame = cap.read()
        if not ret:
            if loop:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            log.error("Camera read failed")
            stop.set()
            break
        _metrics.record_frame()
        try:
            frame_queue.put_nowait(frame)
        except queue.Full:
            _metrics.record_drop()
            print(f"[STRESS    ] frame DROPPED (queue full)  total_drops={_metrics.queue_drops}",
                  flush=True)
        sleep_for = interval - (time.monotonic() - t0)
        if sleep_for > 0:
            time.sleep(sleep_for)


def gst_camera_thread(frame_queue, stop):
    """
    GStreamer-based capture for Pi camera (libcamerasrc / libcamera).
    Used when OpenCV is built without GStreamer support.
    Requires gi.repository GStreamer (always available on RPi OS).
    """
    pin_thread(0)
    if not _GST_OK:
        log.error("GStreamer Python bindings unavailable — cannot capture Pi camera.")
        stop.set()
        return

    pipeline_str = (
        f"libcamerasrc ! "
        f"video/x-raw,width=1280,height=720,framerate={TARGET_FPS}/1 ! "
        "videoconvert ! video/x-raw,format=BGR ! "
        "appsink name=sink emit-signals=false max-buffers=1 drop=true sync=false"
    )
    pipeline = _Gst.parse_launch(pipeline_str)
    sink = pipeline.get_by_name("sink")
    pipeline.set_state(_Gst.State.PLAYING)
    log.info("GStreamer Pi camera pipeline started.")

    interval = 1.0 / TARGET_FPS
    while not stop.is_set():
        t0 = time.monotonic()
        sample = sink.emit("pull-sample")
        if sample is None:
            time.sleep(0.01)
            continue
        buf = sample.get_buffer()
        caps_info = sample.get_caps().get_structure(0)
        w = caps_info.get_value("width")
        h = caps_info.get_value("height")
        ok, mapinfo = buf.map(_Gst.MapFlags.READ)
        if ok:
            frame = np.frombuffer(bytes(mapinfo.data), dtype=np.uint8).reshape(h, w, 3).copy()
            buf.unmap(mapinfo)
            _metrics.record_frame()
            try:
                frame_queue.put_nowait(frame)
            except queue.Full:
                _metrics.record_drop()
        sleep_for = interval - (time.monotonic() - t0)
        if sleep_for > 0:
            time.sleep(sleep_for)

    pipeline.set_state(_Gst.State.NULL)
    log.info("GStreamer Pi camera pipeline stopped.")


def inference_thread(frame_queue, result_queue, yolo, classifier, depth_net, stop):
    pin_thread(1)
    _load_config()          # initial load
    frame_count = 0
    last_status_ts = 0.0
    while not stop.is_set():
        try:
            frame = frame_queue.get(timeout=0.1)
        except queue.Empty:
            # Test 1: still emit baseline status while idle so the terminal
            # shows something meaningful during the 60-second empty-room phase.
            now = time.monotonic()
            if now - last_status_ts >= STATUS_INTERVAL:
                last_status_ts = now
                print(_metrics.status_line(frame_queue.qsize()), flush=True)
            continue

        frame_count += 1
        if frame_count % 30 == 0:   # reload config every 30 frames (~live updates)
            _load_config()
        # Test 1: periodic status even when frames are flowing (no person).
        now = time.monotonic()
        if now - last_status_ts >= STATUS_INTERVAL:
            last_status_ts = now
            print(_metrics.status_line(frame_queue.qsize()), flush=True)

        fh, fw = frame.shape[:2]
        raw_yolo   = yolo.infer(preprocess_yolo(frame))
        detections = yolo.parse_detections(raw_yolo, (fh, fw))
        weapons    = [d for d in detections if d["class_id"] in WEAPON_CLASS_IDS]
        persons    = [d for d in detections if d["class_id"] == PERSON_CLASS_ID]

        results_for_frame = []

        # Weapon hits from YOLO are definitive — skip secondary inference
        for w in weapons:
            results_for_frame.append({
                "det":          w,
                "threat":       "Weapon",
                "weapon_label": YOLO_LABELS[w["class_id"]],
                "depth_tensor": None,
                "fast_spoof":   (False, 0.0),
            })

        if persons:
            # Run classifier + depth only on the highest-confidence person.
            # Running on all N persons was the main FPS bottleneck (N × 36 ms).
            top   = max(persons, key=lambda d: d["conf"])
            crop  = preprocess_crop(frame, top["box"], CLASSIFIER_INPUT_SIZE)
            threat = classifier.infer(crop)

            if _depth_active():
                # Full MiDaS depth inference (~29 ms on NPU) — night mode or "always".
                depth_crop = preprocess_crop(frame, top["box"], DEPTH_INPUT_SIZE)
                depth_t    = depth_net.infer(depth_crop)
                results_for_frame.append({
                    "det":          top,
                    "threat":       threat,
                    "weapon_label": None,
                    "depth_tensor": depth_t,
                    "fast_spoof":   None,
                })
            else:
                # Lightweight RGB variance proxy (<1 ms, no NPU) — daytime.
                spoof, var = fast_rgb_spoof(frame, top["box"])
                results_for_frame.append({
                    "det":          top,
                    "threat":       threat,
                    "weapon_label": None,
                    "depth_tensor": None,
                    "fast_spoof":   (spoof, var),
                })

        result_queue.put((frame, results_for_frame))


def postprocess_thread(result_queue, stop):
    pin_thread(2)
    SYSTEM_LOGS_DIR.mkdir(exist_ok=True)

    session_log = SYSTEM_LOGS_DIR / f"cascade_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    session_entries = []

    while not stop.is_set():
        try:
            frame, results = result_queue.get(timeout=0.1)
        except queue.Empty:
            continue

        ts = datetime.now().isoformat(timespec="seconds")
        for r in results:
            det          = r["det"]
            threat       = r["threat"]
            weapon_label = r.get("weapon_label")   # set for YOLO direct weapon hits

            if weapon_label:
                # YOLO already identified the weapon class — no spoof check needed
                final_label = "Weapon"
                spoof, var  = False, 0.0
            elif r.get("fast_spoof") is not None:
                spoof, var  = r["fast_spoof"]           # lightweight RGB result
                final_label = "Spoof_Attempt" if spoof else threat
            else:
                spoof, var  = is_spoof(r["depth_tensor"])  # full MiDaS result
                final_label = "Spoof_Attempt" if spoof else threat

            _metrics.record_verdict(final_label)

            entry = {
                "ts":           ts,
                "label":        final_label,
                "weapon_label": weapon_label or YOLO_LABELS.get(det["class_id"], "Person"),
                "conf":         round(det["conf"], 3),
                "variance":     round(var, 4),
                "spoof":        spoof,
                "threat":       threat,
                "box":          [round(v, 1) for v in det["box"]],
            }
            print(json.dumps(entry), flush=True)
            session_entries.append(entry)

            for cb in _event_callbacks:
                try:
                    cb(entry)
                except Exception as _cb_err:
                    log.warning(f"Event callback error: {_cb_err}")

            # Mirror danger events to danger_sightings.txt (Garuda convention)
            if final_label in ("Weapon", "Spoof_Attempt"):
                with open(SIGHTINGS_LOG, "a") as f:
                    f.write(f"[CASCADE] {ts}  {final_label}  conf={entry['conf']}  var={entry['variance']}\n")

    # Persist session log
    with open(session_log, "w") as f:
        json.dump(session_entries, f, indent=2)
    log.info(f"Session log saved: {session_log}  ({len(session_entries)} events)")


# ─── Integration API — start / stop ──────────────────────────────────────────
_pipeline_thread: threading.Thread | None = None
_pipeline_stop:   threading.Event  | None = None


def start(on_event=None, input_src=None, hef_dir=None, conf=None, loop=False):
    """
    Start the cascade pipeline in a background daemon thread.
    Returns immediately; use stop() to shut it down.

    on_event  — optional fn(entry: dict) called on every detection
    input_src — camera device ('/dev/videoN'), 'rpi', or video file path
    hef_dir   — directory containing the three HEF files
    conf      — person detection confidence threshold
    loop      — loop video file input
    """
    global _pipeline_thread, _pipeline_stop, PERSON_CONF_THRESH
    if _pipeline_thread and _pipeline_thread.is_alive():
        log.warning("Cascade pipeline already running.")
        return
    if on_event is not None:
        register_event_callback(on_event)
    if conf is not None:
        PERSON_CONF_THRESH = conf
    _pipeline_stop = threading.Event()
    _pipeline_thread = threading.Thread(
        target=_pipeline_entry,
        kwargs=dict(input_src=input_src, hef_dir=hef_dir, loop=loop, stop=_pipeline_stop),
        daemon=True,
        name="GarudaCascade",
    )
    _pipeline_thread.start()
    log.info("Garuda Cascade pipeline started in background thread.")


def stop():
    """Signal the running cascade pipeline to shut down."""
    if _pipeline_stop is not None:
        _pipeline_stop.set()


def _auto_detect_camera() -> str:
    """
    Find the best available camera source:
    1. Probe /dev/video0..19 for a working V4L2 USB camera (OpenCV read test).
    2. If none found, return 'rpi' to use Pi camera via GStreamer libcamerasrc.
    OpenCV is built without GStreamer on this system, so Pi camera (/dev/video0-7
    via rp1-cfe) requires the 'rpi' path which uses gi.repository GStreamer.
    """
    for i in range(20):
        dev = f"/dev/video{i}"
        try:
            cap = cv2.VideoCapture(dev)
            if cap.isOpened():
                ret, _ = cap.read()
                cap.release()
                if ret:
                    log.info(f"Auto-detected USB camera: {dev}")
                    return dev
            else:
                cap.release()
        except Exception:
            pass
    log.info("No USB camera found — using Pi camera via GStreamer libcamerasrc (rpi)")
    return "rpi"


def _pipeline_entry(input_src, hef_dir, loop, stop):
    """
    Full VDevice lifecycle for one pipeline session.
    Runs inside a background thread; holds the VDevice open until stop is set.
    Shared by start() (background) and main() (foreground with CLI).
    """
    from pathlib import Path as _P
    _resources = _P(hef_dir) if hef_dir else _P(__file__).parent.parent / "resources"

    # Resolve camera input
    if input_src in (None, "auto"):
        _input = _auto_detect_camera()
    else:
        _input = input_src

    yolo_path  = _resources / HEF_NAMES["yolo"]
    cls_path   = _resources / HEF_NAMES["classifier"]
    depth_path = _resources / HEF_NAMES["depth"]

    for p in (yolo_path, cls_path, depth_path):
        if not p.exists():
            log.error(f"HEF not found: {p}")
            return

    use_gst = (_input == "rpi")  # GStreamer path for Pi camera

    if not use_gst:
        cap = cv2.VideoCapture(_input)
        if str(_input).startswith("/dev/"):
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            cap.set(cv2.CAP_PROP_FPS,          TARGET_FPS)
        if not cap.isOpened():
            log.error(f"Cannot open input: {_input}")
            return
    else:
        cap = None
        log.info("Using Pi camera via GStreamer libcamerasrc.")

    with VDevice() as vdevice:
        def _setup(hef_obj):
            params = ConfigureParams.create_from_hef(hef_obj, interface=HailoStreamInterface.PCIe)
            ng     = vdevice.configure(hef_obj, params)[0]
            ivp    = InputVStreamParams.make_from_network_group(ng, format_type=FormatType.UINT8)
            ovp    = OutputVStreamParams.make_from_network_group(ng, format_type=FormatType.FLOAT32)
            return ng, ivp, ovp, list(ivp.keys())[0]

        yolo_ng,  yolo_ivp,  yolo_ovp,  yolo_in  = _setup(HEF(str(yolo_path)))
        cls_ng,   cls_ivp,   cls_ovp,   cls_in   = _setup(HEF(str(cls_path)))
        depth_ng, depth_ivp, depth_ovp, depth_in = _setup(HEF(str(depth_path)))

        yolo       = YoloNetwork(yolo_ng,  yolo_ivp,  yolo_ovp,  yolo_in)
        classifier = ClassifierNetwork(cls_ng,   cls_ivp,   cls_ovp,   cls_in)
        depth_net  = DepthNetwork(depth_ng, depth_ivp, depth_ovp, depth_in)

        log.info("All three models configured on Hailo-8L.")
        frame_q  = queue.Queue(maxsize=4)
        result_q = queue.Queue(maxsize=16)

        if use_gst:
            cam_t = threading.Thread(target=gst_camera_thread,
                                     args=(frame_q, stop), name="cam", daemon=True)
        else:
            cam_t = threading.Thread(target=camera_thread,
                                     args=(cap, frame_q, stop, loop), name="cam", daemon=True)

        threads = [
            cam_t,
            threading.Thread(target=inference_thread,
                             args=(frame_q, result_q, yolo, classifier, depth_net, stop),
                             name="infer",   daemon=True),
            threading.Thread(target=postprocess_thread,
                             args=(result_q, stop),         name="postproc", daemon=True),
        ]
        for t in threads:
            t.start()

        while not stop.is_set():
            time.sleep(0.5)

        for t in threads:
            t.join(timeout=5)

    if cap is not None:
        cap.release()
    log.info("Garuda Cascade pipeline stopped.")


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    global PERSON_CONF_THRESH
    parser = argparse.ArgumentParser(description="Garuda Cascade — 3-model NPU pipeline")
    parser.add_argument("--hef-dir", default=str(SCRIPT_DIR.parent / "resources"),
                        help="Directory containing the three HEF files")
    parser.add_argument("--input",   default="auto",
                        help="Camera device (/dev/videoN), 'auto' (probe USB then fall back to test video), or video file path")
    parser.add_argument("--loop",    action="store_true", help="Loop video file input")
    parser.add_argument("--conf",    type=float, default=PERSON_CONF_THRESH,
                        help=f"Person detection confidence threshold (default {PERSON_CONF_THRESH})")
    args = parser.parse_args()
    PERSON_CONF_THRESH = args.conf

    hef_dir = str(Path(args.hef_dir).expanduser().resolve())
    log.info(f"HEF dir : {hef_dir}")
    log.info(f"Input   : {args.input}")

    stop = threading.Event()
    try:
        log.info("Pipeline running — Ctrl+C to stop.")
        _pipeline_entry(input_src=args.input, hef_dir=hef_dir, loop=args.loop, stop=stop)
    except KeyboardInterrupt:
        log.info("Shutting down …")
        stop.set()
    log.info("Garuda Cascade stopped.")


if __name__ == "__main__":
    main()
