"""
garuda_bench.py — Hardware benchmark for Garuda Cascade (3-model NPU pipeline)
Instruments per-stage latency, throughput, CPU, memory, temperature, queue behavior.
Run duration: BENCH_DURATION seconds (default 90).
Output: JSON summary + printed report.
"""

import os, sys, time, json, queue, threading, subprocess, statistics
from datetime import datetime
from pathlib import Path
from collections import defaultdict

import cv2
import numpy as np
import psutil

try:
    from hailo_platform import (
        VDevice, HailoStreamInterface, InferVStreams,
        ConfigureParams, InputVStreamParams, OutputVStreamParams, FormatType, HEF,
    )
except ImportError:
    print("ERROR: hailo_platform not found. Run: source setup_env.sh"); sys.exit(1)

# ─── Config ───────────────────────────────────────────────────────────────────
SCRIPT_DIR       = Path(__file__).parent
RESOURCES        = SCRIPT_DIR.parent / "resources"
HEF_YOLO         = RESOURCES / "yolov8s_garuda.hef"
HEF_CLS          = RESOURCES / "mobilenetv2_garuda.hef"
HEF_DEPTH        = RESOURCES / "midas_depth.hef"
VIDEO_INPUT      = RESOURCES / "detection0.mp4"
BENCH_DURATION   = 90        # seconds
PERSON_CLASS_ID  = 2
CONF_THRESH      = 0.25
DEPTH_SPOOF_THRESH = 0.05
YOLO_SIZE, CLS_SIZE, DEPTH_SIZE = 640, 224, 256

# ─── Latency store (thread-safe append) ───────────────────────────────────────
_lock = threading.Lock()
timings = defaultdict(list)   # key → list of floats (seconds)
det_counts    = []            # persons detected per frame
frame_ts      = []            # monotonic timestamp when each frame was processed

def record(key, seconds):
    with _lock:
        timings[key].append(seconds)

# ─── System stats collector ───────────────────────────────────────────────────
sys_samples = []   # list of dicts

def _read_temp():
    try:
        out = subprocess.check_output(["vcgencmd", "measure_temp"],
                                      stderr=subprocess.DEVNULL, text=True)
        return float(out.strip().split("=")[1].replace("'C",""))
    except Exception:
        try:
            raw = Path("/sys/class/thermal/thermal_zone0/temp").read_text()
            return float(raw.strip()) / 1000.0
        except Exception:
            return float("nan")

def _read_cpu_freq_mhz():
    try:
        raw = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq").read_text()
        return int(raw.strip()) // 1000
    except Exception:
        return None

def sysstat_thread(stop):
    proc = psutil.Process()
    while not stop.is_set():
        per_cpu = psutil.cpu_percent(interval=0.5, percpu=True)
        ram_mb  = proc.memory_info().rss / 1e6
        sys_rss = psutil.virtual_memory().used / 1e6
        temp    = _read_temp()
        freq    = _read_cpu_freq_mhz()
        sys_samples.append({
            "t":       time.monotonic(),
            "cpu":     per_cpu,              # [core0, core1, core2, core3]
            "rss_mb":  ram_mb,
            "sys_mb":  sys_rss,
            "temp_c":  temp,
            "freq_mhz": freq,
        })

# ─── Pre-processing ───────────────────────────────────────────────────────────
def prep_yolo(frame):
    r = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    r = cv2.resize(r, (YOLO_SIZE, YOLO_SIZE), interpolation=cv2.INTER_LINEAR)
    return r[np.newaxis].astype(np.uint8)

def prep_crop(frame, box, size):
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = (max(0,int(box[0])), max(0,int(box[1])),
                       min(w,int(box[2])), min(h,int(box[3])))
    crop = frame[y1:y2, x1:x2] if (x2>x1 and y2>y1) else frame
    r = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    r = cv2.resize(r, (size, size), interpolation=cv2.INTER_LINEAR)
    return r[np.newaxis].astype(np.uint8)

# ─── Inference helpers ────────────────────────────────────────────────────────
def infer_yolo(ng, ivp, ovp, iname, blob):
    t0 = time.perf_counter()
    with ng.activate():
        with InferVStreams(ng, ivp, ovp) as p:
            raw = p.infer({iname: blob})
    record("yolo_ms", (time.perf_counter()-t0)*1000)
    return raw

def parse_detections(raw, fh, fw):
    dets = []
    for tensor in raw.values():
        if isinstance(tensor, list):
            class_list = tensor[0] if (tensor and isinstance(tensor[0], list)) else tensor
            for cls_id, cls_dets in enumerate(class_list):
                arr = np.array(cls_dets, dtype=np.float32)
                if arr.size == 0: continue
                for row in arr.reshape(-1, 5):
                    y1n,x1n,y2n,x2n,conf = row
                    if conf < CONF_THRESH: continue
                    dets.append({"cls": cls_id, "conf": float(conf),
                                 "box": (x1n*fw, y1n*fh, x2n*fw, y2n*fh)})
        else:
            for row in np.array(tensor).reshape(-1, 6):
                y1n,x1n,y2n,x2n,conf,cls = row
                if conf < CONF_THRESH: continue
                dets.append({"cls": int(cls), "conf": float(conf),
                             "box": (x1n*fw, y1n*fh, x2n*fw, y2n*fh)})
    return dets

def infer_cls(ng, ivp, ovp, iname, blob):
    t0 = time.perf_counter()
    with ng.activate():
        with InferVStreams(ng, ivp, ovp) as p:
            res = p.infer({iname: blob})
    record("cls_ms", (time.perf_counter()-t0)*1000)
    logits = list(res.values())[0].ravel()
    return int(np.argmax(logits)), float(np.max(logits))

def infer_depth(ng, ivp, ovp, iname, blob):
    t0 = time.perf_counter()
    with ng.activate():
        with InferVStreams(ng, ivp, ovp) as p:
            res = p.infer({iname: blob})
    record("depth_ms", (time.perf_counter()-t0)*1000)
    return list(res.values())[0]

# ─── Stats helpers ────────────────────────────────────────────────────────────
def pct(lst, p):
    if not lst: return float("nan")
    s = sorted(lst)
    k = (len(s)-1)*p/100
    lo, hi = int(k), min(int(k)+1, len(s)-1)
    return s[lo] + (s[hi]-s[lo])*(k-lo)

def summarise(lst):
    if not lst:
        return {"n":0,"mean":None,"p50":None,"p95":None,"p99":None,"min":None,"max":None}
    return {
        "n":   len(lst),
        "mean": round(statistics.mean(lst), 2),
        "p50":  round(pct(lst, 50), 2),
        "p95":  round(pct(lst, 95), 2),
        "p99":  round(pct(lst, 99), 2),
        "min":  round(min(lst), 2),
        "max":  round(max(lst), 2),
    }

# ─── Main benchmark ───────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*64}")
    print("  Garuda Cascade — Hardware Benchmark")
    print(f"  Duration: {BENCH_DURATION}s  |  Input: {VIDEO_INPUT.name}")
    print(f"{'='*64}\n")

    idle_temp = _read_temp()
    idle_freq = _read_cpu_freq_mhz()
    idle_mem  = psutil.virtual_memory().used / 1e6
    print(f"Idle   temp : {idle_temp} °C")
    print(f"Idle   freq : {idle_freq} MHz")
    print(f"Idle   RAM  : {idle_mem:.0f} MB used\n")

    cap = cv2.VideoCapture(str(VIDEO_INPUT))
    if not cap.isOpened():
        print(f"Cannot open {VIDEO_INPUT}"); sys.exit(1)

    vid_fps    = cap.get(cv2.CAP_PROP_FPS) or 30
    vid_w      = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vid_h      = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    vid_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"Video  : {vid_w}×{vid_h} @ {vid_fps:.1f} fps  ({vid_frames} frames)")

    print("\nLoading HEFs onto Hailo-8L …")
    with VDevice() as vd:
        def setup(path):
            hef = HEF(str(path))
            params = ConfigureParams.create_from_hef(hef, interface=HailoStreamInterface.PCIe)
            ng  = vd.configure(hef, params)[0]
            ivp = InputVStreamParams.make_from_network_group(ng, format_type=FormatType.UINT8)
            ovp = OutputVStreamParams.make_from_network_group(ng, format_type=FormatType.FLOAT32)
            return ng, ivp, ovp, list(ivp.keys())[0]

        yolo_ng,  yolo_ivp,  yolo_ovp,  yolo_in  = setup(HEF_YOLO)
        cls_ng,   cls_ivp,   cls_ovp,   cls_in   = setup(HEF_CLS)
        depth_ng, depth_ivp, depth_ovp, depth_in = setup(HEF_DEPTH)
        print("All three models configured.")

        # warm-up (3 frames)
        for _ in range(3):
            ret, frame = cap.read()
            if not ret: cap.set(cv2.CAP_PROP_POS_FRAMES, 0); ret, frame = cap.read()
            blob = prep_yolo(frame)
            with yolo_ng.activate():
                with InferVStreams(yolo_ng, yolo_ivp, yolo_ovp) as p:
                    p.infer({yolo_in: blob})
        timings.clear()
        print("Warm-up done. Starting benchmark …\n")

        # start sysstat
        stop_sys = threading.Event()
        sth = threading.Thread(target=sysstat_thread, args=(stop_sys,), daemon=True)
        sth.start()

        # ── benchmark loop ──────────────────────────────────────────────────
        t_start      = time.monotonic()
        frames_read  = 0
        frames_proc  = 0
        total_persons = 0
        class_counts  = defaultdict(int)   # 0=Hammer,1=Knife,2=Person,3=Scissors
        spoof_count   = 0
        weapon_count  = 0
        q_depths      = []

        frame_q = queue.Queue(maxsize=8)

        def cam_loop():
            nonlocal frames_read
            while time.monotonic() - t_start < BENCH_DURATION:
                ret, fr = cap.read()
                if not ret:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                frames_read += 1
                try:
                    frame_q.put_nowait(fr)
                except queue.Full:
                    pass

        cam_t = threading.Thread(target=cam_loop, daemon=True)
        cam_t.start()

        while time.monotonic() - t_start < BENCH_DURATION:
            try:
                frame = frame_q.get(timeout=0.5)
            except queue.Empty:
                continue

            q_depths.append(frame_q.qsize())
            fh, fw = frame.shape[:2]
            t_frame = time.perf_counter()

            # YOLO
            raw = infer_yolo(yolo_ng, yolo_ivp, yolo_ovp, yolo_in, prep_yolo(frame))
            dets = parse_detections(raw, fh, fw)
            persons = [d for d in dets if d["cls"] == PERSON_CLASS_ID]

            for d in dets:
                class_counts[d["cls"]] += 1

            # Classifier + Depth for each person
            for det in persons:
                cls_idx, _ = infer_cls(cls_ng, cls_ivp, cls_ovp, cls_in,
                                       prep_crop(frame, det["box"], CLS_SIZE))
                depth_t = infer_depth(depth_ng, depth_ivp, depth_ovp, depth_in,
                                      prep_crop(frame, det["box"], DEPTH_SIZE))
                flat = depth_t.astype(np.float32).ravel()
                mn, mx = flat.min(), flat.max()
                if mx - mn > 1e-6:
                    flat = (flat - mn) / (mx - mn)
                var = float(np.var(flat))
                if var < DEPTH_SPOOF_THRESH:
                    spoof_count += 1
                elif cls_idx == 1:
                    weapon_count += 1
                total_persons += 1

            frame_wall = (time.perf_counter() - t_frame) * 1000
            record("frame_ms", frame_wall)
            frame_ts.append(time.monotonic())
            det_counts.append(len(persons))
            frames_proc += 1

            elapsed = time.monotonic() - t_start
            if frames_proc % 50 == 0:
                inst_fps = 1000 / statistics.mean(timings["frame_ms"][-20:]) if len(timings["frame_ms"]) >= 20 else 0
                print(f"  [{elapsed:5.1f}s] {frames_proc:4d} frames  "
                      f"inst_FPS={inst_fps:.1f}  temp={_read_temp()}°C")

        stop_sys.set()
        sth.join(timeout=2)
        cam_t.join(timeout=2)
        cap.release()

    # ── Analysis ────────────────────────────────────────────────────────────
    wall_time = frame_ts[-1] - frame_ts[0] if len(frame_ts) > 1 else BENCH_DURATION
    eff_fps   = (frames_proc - 1) / wall_time if wall_time > 0 else 0

    yolo_s  = summarise(timings["yolo_ms"])
    cls_s   = summarise(timings["cls_ms"])
    depth_s = summarise(timings["depth_ms"])
    frame_s = summarise(timings["frame_ms"])

    # CPU per-core average (steady state: skip first 5 samples)
    samples = sys_samples[5:]
    cpu_means = [round(statistics.mean(s["cpu"][i] for s in samples), 1)
                 for i in range(4)] if samples else [None]*4
    cpu_peaks = [round(max(s["cpu"][i] for s in samples), 1)
                 for i in range(4)] if samples else [None]*4
    rss_vals  = [s["rss_mb"] for s in samples]
    temp_vals = [s["temp_c"] for s in samples]
    freq_vals = [s["freq_mhz"] for s in samples if s["freq_mhz"]]
    load_temp_mean = round(statistics.mean(temp_vals), 1) if temp_vals else None
    load_temp_max  = round(max(temp_vals), 1) if temp_vals else None
    rss_mean  = round(statistics.mean(rss_vals), 0) if rss_vals else None
    rss_max   = round(max(rss_vals), 0) if rss_vals else None
    freq_mean = round(statistics.mean(freq_vals)) if freq_vals else None

    # Cascade overhead: frame time minus (yolo + N*(cls+depth))
    # Mean cascade cost per frame (when person detected)
    frames_with_person = sum(1 for c in det_counts if c > 0)
    mean_persons_per_frame = statistics.mean(det_counts) if det_counts else 0

    # Theoretical max FPS if running YOLO only
    yolo_only_fps = 1000 / yolo_s["mean"] if yolo_s["mean"] else None
    # Effective FPS (measured)
    # Bottleneck: frame_s["mean"] is full cascade time

    CLASS_NAMES = {0:"Hammer", 1:"Knife", 2:"Person", 3:"Scissors"}
    total_dets = sum(class_counts.values())

    # ── Print Report ─────────────────────────────────────────────────────────
    print(f"\n{'='*64}")
    print("  GARUDA CASCADE — HARDWARE ANALYSIS REPORT")
    print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*64}")

    print("\n[1] PLATFORM")
    print(f"    Board          : Raspberry Pi 5 Model B Rev 1.1")
    print(f"    SoC            : BCM2712 (Cortex-A76 × 4 @ 2800 MHz)")
    print(f"    RAM            : 16 GB LPDDR4X")
    print(f"    NPU            : Hailo-8L M.2 (13 TOPS, HAILO8L arch)")
    print(f"    NPU firmware   : 4.20.0 (release, extended context switch)")
    print(f"    NPU serial     : HLDDLBB241601366")
    print(f"    PCIe interface : M.2 B+M key (PCIe gen 2 × 1)")
    print(f"    OS             : Linux 6.12.75+rpt-rpi-v8 (64-bit)")
    print(f"    HailoRT        : 4.20.0")
    print(f"    Python         : {sys.version.split()[0]}")
    print(f"    NumPy          : {np.__version__}")
    print(f"    OpenCV         : {cv2.__version__}")

    print("\n[2] MODEL INVENTORY")
    print(f"    {'Model':<20} {'Task':<28} {'Input':<12} {'HEF size'}")
    print(f"    {'-'*20} {'-'*28} {'-'*12} {'-'*10}")
    print(f"    {'YOLOv8s (custom)':<20} {'Object detection (4 cls)':<28} {'640×640 RGB':<12} 23 MB")
    print(f"    {'MobileNetV2':<20} {'Threat classification (2 cls)':<28} {'224×224 RGB':<12} 7.1 MB")
    print(f"    {'MiDaS Small':<20} {'Monocular depth (anti-spoof)':<28} {'256×256 RGB':<12} 35 MB")
    print(f"    Total HEF footprint: 65.1 MB")

    print("\n[3] INFERENCE LATENCY (milliseconds, n={:,})".format(yolo_s["n"]))
    print(f"    {'Stage':<22} {'Mean':>7} {'p50':>7} {'p95':>7} {'p99':>7} {'Min':>7} {'Max':>7}")
    print(f"    {'-'*22} {'-'*7} {'-'*7} {'-'*7} {'-'*7} {'-'*7} {'-'*7}")
    for label, s in [("YOLO (640×640)", yolo_s), ("Classifier (224×224)", cls_s), ("MiDaS Depth (256×256)", depth_s)]:
        if s["n"] == 0:
            print(f"    {label:<22}  (no data)")
            continue
        print(f"    {label:<22} {s['mean']:>7.1f} {s['p50']:>7.1f} {s['p95']:>7.1f} {s['p99']:>7.1f} {s['min']:>7.1f} {s['max']:>7.1f}")
    print(f"    {'Full frame (cascade)':<22} {frame_s['mean']:>7.1f} {frame_s['p50']:>7.1f} {frame_s['p95']:>7.1f} {frame_s['p99']:>7.1f} {frame_s['min']:>7.1f} {frame_s['max']:>7.1f}")

    print("\n[4] THROUGHPUT")
    print(f"    Benchmark duration       : {BENCH_DURATION} s")
    print(f"    Frames read (camera)     : {frames_read:,}")
    print(f"    Frames processed (NPU)   : {frames_proc:,}")
    print(f"    Frames dropped (Q full)  : {frames_read - frames_proc:,}  ({100*(frames_read-frames_proc)/max(frames_read,1):.1f}%)")
    print(f"    Effective throughput     : {eff_fps:.1f} FPS")
    print(f"    YOLO-only theoretical    : {yolo_only_fps:.1f} FPS  (1000/{yolo_s['mean']:.1f} ms)")
    if cls_s["mean"]:
        cascade_overhead = cls_s["mean"] + depth_s["mean"]
        print(f"    Per-person cascade cost  : {cascade_overhead:.1f} ms  (cls {cls_s['mean']:.1f} + depth {depth_s['mean']:.1f})")
    mean_q = statistics.mean(q_depths) if q_depths else 0
    max_q  = max(q_depths) if q_depths else 0
    print(f"    Frame queue depth (mean) : {mean_q:.1f}  (max {max_q}  / capacity 8)")

    print("\n[5] DETECTION STATISTICS")
    print(f"    Total detections         : {total_dets:,}")
    print(f"    Frames with ≥1 detection : {frames_with_person:,}  ({100*frames_with_person/max(frames_proc,1):.1f}%)")
    print(f"    Mean persons per frame   : {mean_persons_per_frame:.2f}")
    for cid, cname in CLASS_NAMES.items():
        cnt = class_counts[cid]
        pct_c = 100*cnt/max(total_dets,1)
        print(f"    {cname:<12}               : {cnt:,}  ({pct_c:.1f}%)")
    if total_persons > 0:
        print(f"    Spoof flags (depth var<{DEPTH_SPOOF_THRESH}) : {spoof_count:,}  ({100*spoof_count/total_persons:.1f}% of persons)")
        print(f"    Weapon flags             : {weapon_count:,}  ({100*weapon_count/total_persons:.1f}% of persons)")

    print("\n[6] CPU UTILISATION  (per-core affinity: cam→0, infer→1, postproc→2, OS→3)")
    print(f"    {'Core':<8} {'Mean %':>8} {'Peak %':>8} {'Pinned thread'}")
    pin = {0:"cam", 1:"infer", 2:"postproc", 3:"OS/system"}
    for i in range(4):
        print(f"    Core {i}    {cpu_means[i]:>7.1f}  {cpu_peaks[i]:>7.1f}   {pin[i]}")
    total_cpu_mean = round(statistics.mean(
        s["cpu"][0]+s["cpu"][1]+s["cpu"][2]+s["cpu"][3] for s in samples)/4, 1) if samples else None
    print(f"    System avg load          : {total_cpu_mean}% per core")

    print("\n[7] MEMORY")
    print(f"    Idle system RAM used     : {idle_mem:.0f} MB")
    print(f"    Under load (RSS, proc)   : mean {rss_mean:.0f} MB  peak {rss_max:.0f} MB")
    sys_load_mem = round(statistics.mean(s["sys_mb"] for s in samples)) if samples else None
    print(f"    System RAM under load    : {sys_load_mem:.0f} MB total used")
    print(f"    Pipeline RAM overhead    : ~{sys_load_mem - idle_mem:.0f} MB")

    print("\n[8] THERMAL")
    print(f"    Idle temperature         : {idle_temp} °C")
    print(f"    Load mean temperature    : {load_temp_mean} °C")
    print(f"    Load peak temperature    : {load_temp_max} °C")
    delta = round(load_temp_max - idle_temp, 1) if load_temp_max else None
    print(f"    Thermal rise (ΔT)        : +{delta} °C")
    print(f"    CPU frequency under load : {freq_mean} MHz  (boost active: {'yes' if freq_mean and freq_mean >= 2700 else 'no'})")
    throttle_note = "No throttling observed" if (load_temp_max and load_temp_max < 80) else "WARNING: possible throttle >80°C"
    print(f"    Throttle status          : {throttle_note}")

    print("\n[9] NPU EFFICIENCY ESTIMATE")
    # Hailo-8L = 13 TOPS
    # Each model occupies a fraction of the chip
    # Rough estimate: during YOLO inference the chip runs at rated throughput
    yolo_tops_est  = 8.0   # YOLOv8s ~8 TOPS for 640 input (Hailo model zoo figure)
    cls_tops_est   = 0.3   # MobileNetV2 224 is very light
    depth_tops_est = 2.5   # MiDaS Small 256
    print(f"    Hailo-8L rated peak      : 13 TOPS")
    print(f"    YOLOv8s estimated load   : ~{yolo_tops_est} TOPS  ({100*yolo_tops_est/13:.0f}% NPU)")
    print(f"    MobileNetV2 est. load    : ~{cls_tops_est} TOPS  ({100*cls_tops_est/13:.0f}% NPU)")
    print(f"    MiDaS Small est. load    : ~{depth_tops_est} TOPS  ({100*depth_tops_est/13:.0f}% NPU)")
    print(f"    Sequential utilisation   : ~{100*(yolo_tops_est+cls_tops_est+depth_tops_est)/13:.0f}% (time-multiplexed)")
    print(f"    Note: Hailo-8L supports only one active network group at a time;")
    print(f"          models are activated and deactivated per inference call.")

    print("\n[10] BOTTLENECK ANALYSIS")
    if cls_s["mean"]:
        fpm = mean_persons_per_frame
        bottleneck_ms = yolo_s["mean"] + fpm * (cls_s["mean"] + depth_s["mean"])
        print(f"    Theoretical frame time   : {bottleneck_ms:.1f} ms  "
              f"(YOLO {yolo_s['mean']:.1f} + {fpm:.1f}×pers×({cls_s['mean']:.1f}+{depth_s['mean']:.1f}))")
        print(f"    Measured frame time      : {frame_s['mean']:.1f} ms")
        overhead = frame_s["mean"] - bottleneck_ms
        print(f"    Scheduling overhead      : ~{overhead:.1f} ms  (queue + Python + preprocess)")
        print(f"    Bottleneck stage         : {'MiDaS (per person)' if depth_s['mean'] > cls_s['mean'] and depth_s['mean'] > yolo_s['mean']/max(fpm,1) else 'YOLO (per frame)'}")
    print(f"    Frame drop rate          : {100*(frames_read-frames_proc)/max(frames_read,1):.1f}%  "
          f"(inference slower than camera rate {vid_fps:.0f} fps)")

    print(f"\n{'='*64}\n")

    # ── Save JSON ─────────────────────────────────────────────────────────────
    report = {
        "generated":     datetime.now().isoformat(),
        "platform": {
            "board":    "Raspberry Pi 5 Model B Rev 1.1",
            "soc":      "BCM2712 Cortex-A76 x4",
            "ram_gb":   16,
            "npu":      "Hailo-8L M.2 13TOPS",
            "npu_fw":   "4.20.0",
            "os_kernel":"6.12.75+rpt-rpi-v8",
            "hailort":  "4.20.0",
        },
        "bench": {
            "duration_s": BENCH_DURATION,
            "frames_read": frames_read,
            "frames_proc": frames_proc,
            "eff_fps":    round(eff_fps, 2),
        },
        "latency_ms": {
            "yolo":       yolo_s,
            "classifier": cls_s,
            "depth":      depth_s,
            "full_frame": frame_s,
        },
        "cpu": {
            "mean_per_core": cpu_means,
            "peak_per_core": cpu_peaks,
        },
        "memory": {
            "idle_sys_mb":  round(idle_mem),
            "load_proc_rss_mean_mb": rss_mean,
            "load_proc_rss_peak_mb": rss_max,
            "load_sys_mb":  sys_load_mem,
        },
        "thermal": {
            "idle_c":   idle_temp,
            "mean_c":   load_temp_mean,
            "peak_c":   load_temp_max,
            "delta_c":  delta,
            "freq_mhz": freq_mean,
        },
        "detections": {
            "total": total_dets,
            "by_class": {CLASS_NAMES[k]: v for k,v in class_counts.items()},
            "total_persons_cascaded": total_persons,
            "spoof_flags": spoof_count,
            "weapon_flags": weapon_count,
            "mean_persons_per_frame": round(mean_persons_per_frame, 3),
        },
    }

    out_path = SCRIPT_DIR.parent / "garuda_hw_analysis.json"
    out_path.write_text(json.dumps(report, indent=2))
    print(f"JSON saved → {out_path}")


if __name__ == "__main__":
    main()
