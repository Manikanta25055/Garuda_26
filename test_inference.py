"""
Garuda — Direct Inference + Cascade Test
=========================================
Tests:
  1. Capture one frame from Pi camera via GStreamer
  2. Run best_v5.hef (primary YOLO) — verify knife/hammer labels parse correctly
  3. Load all 3 cascade HEFs together on VDevice — verify they co-exist
  4. Run YOLO inference on the captured frame and report detections

Run (stop Garuda_web.py first — only one process can hold /dev/hailo0):
    fuser -k 8080/tcp 2>/dev/null
    source setup_env.sh
    python test_inference.py
"""

import sys
import time
import subprocess
import numpy as np
from pathlib import Path

RESOURCES = Path(__file__).parent / "resources"

BEST_HEF     = RESOURCES / "best_v5.hef"
YOLO_HEF     = RESOURCES / "yolov8s_garuda.hef"   # cascade primary
MOB_HEF      = RESOURCES / "mobilenetv2_garuda.hef"
MIDAS_HEF    = RESOURCES / "midas_depth.hef"

# best_v5.hef outputs 4 classes: Hammer(0), Knife(1), Person(2), scissors(3)
# "unlabeled" in best_labels.json is a HailortPP offset placeholder — not an output class
BEST_LABELS  = ["Hammer", "Knife", "Person", "scissors"]
# Labels for yolov8s_garuda (cascade primary)
YOLO_LABELS  = {0: "Hammer", 1: "Knife", 2: "Person", 3: "scissors"}
DANGER_IDS   = {0, 1, 3}   # cascade: Hammer, Knife, scissors

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

results = []
def check(name, ok, detail=""):
    tag = PASS if ok else FAIL
    print(f"  [{tag}] {name}" + (f" — {detail}" if detail else ""))
    results.append((name, ok))
    return ok

# ── hailo_platform ────────────────────────────────────────────────────────────
try:
    from hailo_platform import (
        VDevice, HEF, ConfigureParams, InferVStreams,
        InputVStreamParams, OutputVStreamParams,
        FormatType, HailoStreamInterface,
    )
    check("hailo_platform import", True)
except ImportError as e:
    print(f"  [{FAIL}] hailo_platform import — {e}")
    print("  Run: source setup_env.sh first.")
    sys.exit(1)

# ── GStreamer ─────────────────────────────────────────────────────────────────
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst
Gst.init(None)
import cv2

# ──────────────────────────────────────────────────────────────────────────────
# STEP 1 — Capture one frame from Pi camera via rpicam-still
# ──────────────────────────────────────────────────────────────────────────────
print("\n[STEP 1] Capture frame from Pi camera (rpicam-still)")

frame = None
SNAP_PATH = "/tmp/garuda_snap.jpg"
try:
    result = subprocess.run(
        ["rpicam-still", "-o", SNAP_PATH, "--width", "640", "--height", "640",
         "--immediate", "--nopreview", "-t", "500"],
        capture_output=True, text=True, timeout=15
    )
    if result.returncode == 0:
        frame = cv2.imread(SNAP_PATH)
    check("Pi camera snapshot captured", frame is not None,
          f"shape={frame.shape}" if frame is not None else f"rpicam-still rc={result.returncode}: {result.stderr[:200]}")
except Exception as e:
    check("Pi camera snapshot captured", False, str(e))

# Fallback: create a neutral grey frame so remaining tests still run
if frame is None:
    print("  Using synthetic grey frame as fallback.")
    frame = np.full((640, 640, 3), 128, dtype=np.uint8)

# Save for inspection
cv2.imwrite("/tmp/garuda_test_frame.jpg", frame)
print(f"  Frame saved → /tmp/garuda_test_frame.jpg  (check for framing issues)")

# ──────────────────────────────────────────────────────────────────────────────
# STEP 2 — Run best_v5.hef (primary YOLO used by web server)
# ──────────────────────────────────────────────────────────────────────────────
print("\n[STEP 2] best_v5.hef — primary YOLO inference (web server model)")

check("best_v5.hef exists", BEST_HEF.exists(), str(BEST_HEF))

def preprocess(frame_bgr, size=640):
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    resized = cv2.resize(rgb, (size, size), interpolation=cv2.INTER_LINEAR)
    return resized[np.newaxis].astype(np.uint8)   # (1, H, W, 3)

def parse_nms_output(raw: dict, labels: list, thresh=0.25) -> list:
    """
    Parse HailortPP NMS output.
    raw values are either:
      - list of per-class arrays  (HailortPP mode)
      - ndarray of shape (N, 6)   (legacy mode)
    Returns list of dicts: {label, conf, box (x1,y1,x2,y2 in 0-1)}
    """
    detections = []
    for tensor in raw.values():
        if isinstance(tensor, list):
            class_list = tensor[0] if (tensor and isinstance(tensor[0], list)) else tensor
            for cls_id, cls_dets in enumerate(class_list):
                if cls_id >= len(labels):
                    continue
                arr = np.array(cls_dets, dtype=np.float32)
                if arr.size == 0:
                    continue
                for row in arr.reshape(-1, 5):
                    y1, x1, y2, x2, conf = row
                    if conf >= thresh:
                        detections.append({
                            "label": labels[cls_id],
                            "conf":  round(float(conf), 3),
                            "box":   (round(float(x1),3), round(float(y1),3),
                                      round(float(x2),3), round(float(y2),3)),
                        })
        else:
            arr = np.array(tensor).reshape(-1, 6)
            for row in arr:
                y1, x1, y2, x2, conf, cls = row
                cls = int(cls)
                if conf >= thresh and cls < len(labels):
                    detections.append({
                        "label": labels[cls],
                        "conf":  round(float(conf), 3),
                        "box":   (round(float(x1),3), round(float(y1),3),
                                  round(float(x2),3), round(float(y2),3)),
                    })
    return detections

if BEST_HEF.exists():
    try:
        with VDevice() as vdev:
            hef_obj = HEF(str(BEST_HEF))
            params  = ConfigureParams.create_from_hef(hef_obj, interface=HailoStreamInterface.PCIe)
            ng      = vdev.configure(hef_obj, params)[0]
            ivp     = InputVStreamParams.make_from_network_group(ng, format_type=FormatType.UINT8)
            ovp     = OutputVStreamParams.make_from_network_group(ng, format_type=FormatType.FLOAT32)
            inp_name = list(ivp.keys())[0]
            check("best_v5.hef configured on VDevice", True)

            t0 = time.monotonic()
            with ng.activate():
                with InferVStreams(ng, ivp, ovp) as pipe:
                    raw = pipe.infer({inp_name: preprocess(frame)})
            ms = (time.monotonic() - t0) * 1000
            check("Inference completed", True, f"{ms:.0f} ms")

            # Debug: show raw output structure so we can verify parsing
            print("  Raw output keys/types/shapes:")
            for k, v in raw.items():
                if isinstance(v, list):
                    inner = v[0] if v else []
                    if isinstance(inner, list):
                        print(f"    {k}: list[list] len={len(v)}, inner[0] len={len(inner)}")
                        for ci, cls_dets in enumerate(inner[:6]):
                            arr = np.array(cls_dets, dtype=np.float32).reshape(-1, 5) if cls_dets else np.zeros((0, 5), dtype=np.float32)
                            max_conf = float(arr[:, 4].max()) if arr.shape[0] > 0 else 0.0
                            lbl = BEST_LABELS[ci] if ci < len(BEST_LABELS) else "?"
                            print(f"      class {ci} ({lbl}): {arr.shape[0]} dets  max_conf={max_conf:.3f}")
                    else:
                        print(f"    {k}: list len={len(v)}")
                else:
                    arr = np.array(v)
                    print(f"    {k}: ndarray {arr.shape} dtype={arr.dtype}")

            # Try at lower threshold to catch any low-conf hits
            dets_low  = parse_nms_output(raw, BEST_LABELS, thresh=0.10)
            dets = parse_nms_output(raw, BEST_LABELS, thresh=0.25)
            if dets_low and not dets:
                print(f"  NOTE: {len(dets_low)} detections at thresh=0.10 but 0 at thresh=0.25:")
                for d in dets_low:
                    print(f"        {d['label']}  conf={d['conf']}  box={d['box']}")
            if dets:
                for d in dets:
                    print(f"      DETECTED: {d['label']}  conf={d['conf']}  box={d['box']}")
                check("Detections returned", True, f"{len(dets)} objects found")
                danger_found = [d for d in dets if d['label'] in ("Knife", "Hammer", "scissors")]
                if danger_found:
                    print(f"\n  *** DANGER OBJECTS IN FRAME: "
                          f"{[d['label'] for d in danger_found]} ***")
                    check("Knife/Hammer/scissors detected", True,
                          str([d['label'] for d in danger_found]))
                else:
                    check("Knife/Hammer/scissors detected", False,
                          "not in this frame (hold one up to the camera to test)")
            else:
                check("Detections returned", False,
                      "0 detections — check confidence threshold or camera framing")

    except Exception as e:
        check("best_v5.hef inference", False, str(e))

# ──────────────────────────────────────────────────────────────────────────────
# STEP 3 — Load all 3 cascade HEFs together on one VDevice
# ──────────────────────────────────────────────────────────────────────────────
print("\n[STEP 3] Cascade — load all 3 HEFs on shared VDevice")

hefs = {"yolo": YOLO_HEF, "classifier": MOB_HEF, "depth": MIDAS_HEF}
for role, path in hefs.items():
    check(f"HEF [{role}] exists", path.exists(), path.name)

if all(p.exists() for p in hefs.values()):
    try:
        with VDevice() as vdev:
            def _configure(path):
                h = HEF(str(path))
                p = ConfigureParams.create_from_hef(h, interface=HailoStreamInterface.PCIe)
                ng = vdev.configure(h, p)[0]
                ivp = InputVStreamParams.make_from_network_group(ng, format_type=FormatType.UINT8)
                ovp = OutputVStreamParams.make_from_network_group(ng, format_type=FormatType.FLOAT32)
                return ng, ivp, ovp, list(ivp.keys())[0]

            yolo_ng,  yolo_ivp,  yolo_ovp,  yolo_in  = _configure(YOLO_HEF)
            cls_ng,   cls_ivp,   cls_ovp,   cls_in   = _configure(MOB_HEF)
            depth_ng, depth_ivp, depth_ovp, depth_in = _configure(MIDAS_HEF)
            check("All 3 HEFs configured on VDevice", True)

            # Run YOLO inference through cascade model on same frame
            print("\n  Running cascade YOLO (yolov8s_garuda.hef) on captured frame…")
            t0 = time.monotonic()
            with yolo_ng.activate():
                with InferVStreams(yolo_ng, yolo_ivp, yolo_ovp) as pipe:
                    raw_cascade = pipe.infer({yolo_in: preprocess(frame)})
            ms = (time.monotonic() - t0) * 1000
            check("Cascade YOLO inference", True, f"{ms:.0f} ms")

            cascade_dets = parse_nms_output(raw_cascade, list(YOLO_LABELS.values()), thresh=0.25)
            if cascade_dets:
                for d in cascade_dets:
                    print(f"      DETECTED: {d['label']}  conf={d['conf']}  box={d['box']}")
                check("Cascade detections returned", True, f"{len(cascade_dets)} objects")
            else:
                check("Cascade detections returned", False,
                      "0 detections (frame may have no objects)")

            # Run MobileNet on a 224x224 crop of the frame
            print("\n  Running MobileNet classifier on frame crop…")
            crop224 = cv2.resize(frame, (224, 224))[np.newaxis].astype(np.uint8)
            with cls_ng.activate():
                with InferVStreams(cls_ng, cls_ivp, cls_ovp) as pipe:
                    cls_out = pipe.infer({cls_in: crop224})
            cls_arr = list(cls_out.values())[0].ravel()
            cls_result = "Weapon" if int(np.argmax(cls_arr)) == 1 else "Safe"
            check("MobileNet classifier ran", True, f"result={cls_result}")

            # Run MiDaS on a 256x256 crop
            print("\n  Running MiDaS depth estimator on frame crop…")
            crop256 = cv2.resize(frame, (256, 256))[np.newaxis].astype(np.uint8)
            with depth_ng.activate():
                with InferVStreams(depth_ng, depth_ivp, depth_ovp) as pipe:
                    dep_out = pipe.infer({depth_in: crop256})
            dep_arr = list(dep_out.values())[0].astype(np.float32).ravel()
            d_norm  = (dep_arr - dep_arr.min()) / (dep_arr.max() - dep_arr.min() + 1e-6)
            variance = float(np.var(d_norm))
            spoof = variance < 0.005
            check("MiDaS depth estimator ran", True,
                  f"depth_variance={variance:.4f}  {'→ SPOOF FLAG' if spoof else '→ real scene'}")

            check("All 3 HEFs ran sequential inference", True,
                  "YOLO → MobileNet → MiDaS on same VDevice")

    except Exception as e:
        check("Cascade 3-HEF test", False, str(e))
        import traceback; traceback.print_exc()

# ──────────────────────────────────────────────────────────────────────────────
# Summary
# ──────────────────────────────────────────────────────────────────────────────
print("\n" + "="*56)
passed = sum(1 for _, ok in results if ok)
failed = [(n, ok) for n, ok in results if not ok]
print(f"  {passed}/{len(results)} passed")
if failed:
    print("  FAILED:")
    for name, _ in failed:
        print(f"    - {name}")
print("="*56 + "\n")
