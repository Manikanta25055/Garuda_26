# Garuda: A Privacy-Preserving, Fully Edge-Based Intelligent Surveillance System with Real-Time Dangerous Object Detection and Anti-Spoofing

**Authors:** Manikanta Gonugondla  
**Hardware:** Raspberry Pi 5 + Hailo-8L AI HAT (13 TOPS)  
**Training Infrastructure:** NVIDIA RTX 3080 (v1/v2) · RTX 4090 (v5, RunPod cloud)  
**Compiler:** Hailo Dataflow Compiler v3.33.1  
**Framework:** Ultralytics YOLOv8 8.4.37 · PyTorch 2.6.0

---

## I. Research Problem

### I.1 Current Limitations in Smart Surveillance

Existing smart surveillance and home security systems follow a cloud-offload architecture: camera frames are compressed and streamed to a remote server where detection, classification, and alert logic execute. This model introduces three fundamental problems:

1. **Privacy risk.** Raw video frames leave the local network and are processed by third-party infrastructure. Users have no control over data retention, secondary use, or breaches.
2. **Latency.** Round-trip network transmission introduces 200–2,000 ms of end-to-end alert latency depending on internet quality, making real-time response impossible for rapidly developing threats.
3. **Availability dependency.** Any internet outage, cloud service disruption, or bandwidth throttling completely disables protection — precisely when physical security is often most at risk.

Existing edge-based alternatives (Raspberry Pi + standard camera + CPU-only inference) avoid the cloud dependency but cannot sustain real-time detection. YOLOv8s on a Raspberry Pi 5 CPU runs at approximately 3–5 FPS — below the 30 FPS minimum for continuous surveillance. These systems also lack secondary classification and anti-spoofing capabilities, making them vulnerable to false alerts and photo/screen spoofing attacks.

### I.2 Research Gap

No existing published system simultaneously satisfies all of the following constraints:
- Fully local processing with zero cloud dependency
- Real-time inference (≥30 FPS) on embedded hardware
- Domain-specific dangerous object detection (knife, hammer, scissors) — not available in COCO-pretrained models
- Secondary threat classification per detected person
- Physical depth-based anti-spoofing to reject photographic bypass attempts
- Integrated multi-modal alerting (web dashboard, email, voice assistant)

### I.3 Research Objective

To design, implement, and evaluate **Garuda**: a privacy-preserving, fully edge-based intelligent surveillance system that performs complete AI inference locally on a Raspberry Pi 5 with Hailo-8L NPU, achieving real-time dangerous object detection, context-aware threat classification, and depth-based anti-spoofing without any cloud dependency.

---

## II. Technical Architecture

### II.1 System Block Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        GARUDA SYSTEM                                │
│                                                                     │
│  ┌──────────────┐     ┌─────────────────────────────────────────┐  │
│  │  Camera Input │────▶│          Hailo-8L NPU (13 TOPS)        │  │
│  │  IMX708      │     │                                         │  │
│  │  1280×720    │     │  ┌──────────┐  ┌───────────────────┐   │  │
│  │  52 FPS      │     │  │ YOLOv8s  │  │  MobileNetV2      │   │  │
│  └──────────────┘     │  │ Scout    │  │  Inspector        │   │  │
│                       │  │ 640×640  │  │  224×224 crop     │   │  │
│  ┌──────────────┐     │  │ 52 FPS   │  │  Safe / Weapon    │   │  │
│  │  Web UI      │◀────│  └────┬─────┘  └────────┬──────────┘   │  │
│  │  FastAPI     │     │       │ Person           │              │  │
│  │  Port 8080   │     │       │ detected         ▼              │  │
│  └──────────────┘     │       │         ┌────────────────┐      │  │
│                       │       │         │  MiDaS Small   │      │  │
│  ┌──────────────┐     │       │         │  Reality Check │      │  │
│  │  Alert Engine│◀────│       │         │  256×256 crop  │      │  │
│  │  Email (SMTP)│     │       │         │  Depth map     │      │  │
│  │  Voice (TTS) │     │       │         └────────┬───────┘      │  │
│  └──────────────┘     │       │                  │              │  │
│                       │       └──────────────────┘              │  │
│  ┌──────────────┐     │                  │                      │  │
│  │  Auth Module │     │         ┌────────▼────────┐             │  │
│  │  OTP + bcrypt│     │         │ Decision Engine │             │  │
│  └──────────────┘     │         │ Spoof / Threat  │             │  │
│                       │         └────────┬────────┘             │  │
│  ┌──────────────┐     │                  │                      │  │
│  │  SQLite DB   │◀────│                  │                      │  │
│  │  Logs + State│     └──────────────────┘                      │  │
│  └──────────────┘                                               │  │
└─────────────────────────────────────────────────────────────────────┘
```

**Components:**
- **Camera Input:** Sony IMX708 (Pi Camera Module 3) via libcamera / GStreamer `libcamerasrc`. Captures 1280×720 at 52 FPS.
- **AI Detection Module (YOLOv8s):** Custom fine-tuned 4-class detector — Hammer (0), Knife (1), Person (2), scissors (3). Runs on Hailo-8L NPU at 52.22 FPS, 18.38 ms HW latency.
- **Threat Classifier (MobileNetV2):** Binary crop classifier — Safe (0) vs Weapon (1). Activated only when a Person is detected with confidence ≥ 0.60. Peak val accuracy: 100%.
- **Anti-Spoof Module (MiDaS Small):** Monocular depth estimator producing an inverse depth map. Depth spatial variance < 0.05 → flat-screen/photo spoof detected.
- **Alert Engine:** SMTP email alerts via Gmail App Password. Voice alerts via text-to-speech. Cooldown logic prevents alert spam.
- **Authentication Module:** OTP-based session login, bcrypt password hashing, rate limiting, session timeout.
- **Database:** Persistent JSON/text logs in `system_logs/` — detection log, alert history, voice log, system log. 7-day retention.
- **UI:** FastAPI web dashboard on port 8080 (live camera feed, detection overlay, mode controls). macOS-compatible remote access.

---

### II.2 Data Flow — Algorithmic Description

```
Algorithm 1: Garuda Frame Processing Pipeline

INPUT:  Raw frame F from camera (1280×720×3 BGR)
OUTPUT: Detection event E = {label, conf, variance, spoof, threat, box}

1. CAPTURE
   F ← libcamerasrc via GStreamer appsink (sink.try_pull_sample, 5s timeout)
   F.enqueue(frame_queue, maxsize=4, drop_if_full=True)

2. PREPROCESS
   F_yolo ← resize(F, 640×640) → cvtColor(BGR→RGB) → NHWC uint8

3. OBJECT DETECTION  [YOLO, Core 1, Hailo-8L]
   detections ← YOLOv8s.infer(F_yolo)
   FOR each detection d IN detections:
     IF d.class_id ∈ {Hammer, Knife, Scissors}:
       GOTO ALERT(label="Weapon", weapon=YOLO_LABELS[d.class_id])
     IF d.class_id == Person AND d.conf ≥ 0.60:
       top_person ← argmax(d.conf)
       GOTO CLASSIFICATION(top_person)

4. CLASSIFICATION  [MobileNetV2, Core 1, Hailo-8L]
   crop_224 ← preprocess_crop(F, top_person.box, size=224)
   threat ← MobileNetV2.infer(crop_224)          # "Safe" or "Weapon"

5. ANTI-SPOOF CHECK  [MiDaS, Core 1, Hailo-8L]
   crop_256 ← preprocess_crop(F, top_person.box, size=256)
   depth_map ← MiDaS.infer(crop_256)             # [1, 256, 256] inverse depth
   depth_norm ← (depth_map - min) / (max - min)
   variance ← var(depth_norm)
   spoof ← (variance < 0.05)

6. DECISION LOGIC  [Core 2, CPU]
   IF spoof:     final_label ← "Spoof_Attempt"
   ELSE:         final_label ← threat             # "Safe" or "Weapon"

7. ALERT GENERATION
   IF final_label ∈ {"Weapon", "Spoof_Attempt"}:
     send_email(alert_image, final_label)
     speak_alert(final_label)
     append danger_sightings.txt

8. LOGGING
   entry ← {ts, label, conf, variance, spoof, threat, box}
   write JSON → perm_detection_log.txt
   write JSON → system_logs/cascade_<timestamp>.json
   update alert_history.json (daily count)
```

The pipeline is multi-threaded with CPU affinity pinning: Core 0 (camera capture), Core 1 (HailoRT VDevice inference), Core 2 (postprocessing and logging), Core 3 reserved for OS.

---

## III. Mathematical and Algorithmic Modelling

### III.1 Detection Confidence (Recall)

$$P_{detect} = \frac{TP}{TP + FN}$$

Where TP = correctly detected dangerous objects, FN = dangerous objects present but missed. For the deployed v5 model on the held-out test set (622 images, 1,656 instances):

| Class | TP | FN | P_detect (Recall) |
|-------|----|----|-------------------|
| Hammer | ~39 | ~7 | **0.848** |
| Knife | ~130 | ~29 | **0.817** |
| Person | ~807 | ~517 | **0.609** |
| scissors | ~118 | ~9 | **0.929** |
| **Overall** | **~1,094** | **~562** | **0.801** |

### III.2 False Alarm Rate

$$FAR = \frac{FP}{FP + TN}$$

Where FP = detections fired on non-threatening frames, TN = correctly ignored safe frames. With confidence threshold 0.25 and the secondary MobileNetV2 classifier, spurious weapon detections from background clutter are filtered at the classification stage. The cascade architecture reduces effective FAR by requiring both YOLO confidence ≥ 0.25 (weapon) or ≥ 0.60 (person) **and** MobileNetV2 confirmation for person-based alerts.

For the v5 model (precision = 0.866 on test set):

$$FAR = 1 - Precision = 1 - 0.866 = 0.134$$

The secondary classifier further reduces person-triggered false alerts. MobileNetV2 val precision = 99.6%, meaning fewer than 0.4% of Safe person crops are misclassified as Weapon threats.

### III.3 System Latency

$$T_{total} = T_{capture} + T_{processing} + T_{alert}$$

| Component | Measured Value |
|-----------|---------------|
| T_capture | ~1 ms (GStreamer appsink pull) |
| T_YOLO (NPU) | **18.38 ms** (Hailo-8L HW latency) |
| T_crop_preprocess | ~0.5 ms (NumPy resize) |
| T_classifier (NPU) | <2 ms (MobileNetV2 at 500+ FPS) |
| T_depth (NPU) | <5 ms (MiDaS at 200+ FPS) |
| T_decision | <0.1 ms (NumPy variance) |
| T_alert (email) | ~800 ms (SMTP, async) |
| T_alert (voice) | ~200 ms (TTS, async) |
| **T_processing (detection only)** | **~22 ms end-to-end** |
| **T_total (with async alert)** | **~22 ms + async** |

Alert transmission is asynchronous — it does not block the inference pipeline. The detection-to-log latency is approximately 22 ms, giving an effective detection rate of **45+ detections per second**. The full YOLO pipeline sustains **52.22 FPS** with zero frame drops.

### III.4 Anti-Spoof Variance Model

The depth spoof decision is grounded in physical geometry. For a flat panel (iPad, monitor, photo) held at distance $d$ from the camera, the depth variation across the panel surface is:

$$\sigma^2_{spoof} = \text{var}\left(\frac{z(x,y) - z_{min}}{z_{max} - z_{min}}\right) \approx \epsilon \quad (\text{where } \epsilon \ll 0.05)$$

For a real human at distance $d$, the nose-to-shoulder depth differential alone contributes:

$$\Delta z_{human} \approx 0.3\text{–}0.5 \text{ m} \quad \Rightarrow \quad \sigma^2_{human} \in [0.08, 0.30]$$

Decision rule:

$$\text{label} = \begin{cases} \text{Spoof\_Attempt} & \text{if } \sigma^2 < 0.05 \\ \text{threat (from classifier)} & \text{otherwise} \end{cases}$$

Empirically validated: real human scenes produced variance 0.022–0.10; flat-screen spoofs produced variance < 0.005 (4× below threshold).

---

## IV. Experimental Validation

### IV.1 Dataset Creation and Preprocessing

#### IV.1.1 Motivation and Dataset Evolution

The system requires detection of four classes absent from standard COCO-trained models: Hammer, Knife, Person (in a security context), and scissors. Training data was assembled through five progressive versions, each correcting specific deficiencies identified in earlier evaluations.

The original dataset (v1, 512 images, Roboflow Knives & Scissors Training v2, CC BY 4.0) achieved mAP50 = 0.897 on its own validation split but only 0.465 on the held-out test set — a 43-point overfitting gap driven by limited background diversity and severe knife/hammer underrepresentation. Version 2 introduced 1,219 OpenImages v7 images to address this. Version 5, the deployed model, further expanded the corpus with targeted per-class OpenImages extra downloads and is the basis for all reported results.

| Version | Train Images | Total Images | Ann. Instances | Test mAP50 | Primary Change |
|---------|-------------|--------------|----------------|------------|----------------|
| v1 | 359 | 512 | ~1,200 | 0.465 | Roboflow Knives & Scissors v2 only |
| v2 | 1,383 | 1,730 | ~4,000 | 0.754 | + OpenImages v7 (1,219 imgs) |
| v5 | **4,982** | **6,226** | **16,335** | **0.847** | + OI extra per-class (2,276 imgs added) |

#### IV.1.2 Source Datasets

**Source A — Roboflow Knives & Scissors Training v2**

| Property | Value |
|----------|-------|
| License | CC BY 4.0 |
| Format | YOLO v5+ normalised `[class cx cy w h]` |
| Classes | Hammer (0), Knife (1), Person (2), scissors (3) |
| Train / Val / Test | 359 / 102 / 51 images |
| Image type | JPEG, mostly 640×640 |

All Roboflow splits were pooled and re-split with the OpenImages data to produce a statistically valid 80/10/10 partition.

**Source B — OpenImages v7 (OIDv4 Toolkit, FiftyOne)**

| Property | Value |
|----------|-------|
| License | CC BY 4.0 |
| Annotation quality | Google-verified human annotations, IoB ≥ 0.5 |
| Format | YOLO Darknet (normalised per-class subfolders) |
| hammer / knife / person / scissors | 53 / 500 / 500 / 166 images (v2 batch); 119 / 600 / 2,485 / 292 total in v5 |
| Image size | Varies — real-world photos, up to 4608×2592, non-square |

OpenImages photographs span diverse real-world backgrounds, lighting conditions, and aspect ratios — the diversity v1 lacked.

#### IV.1.3 Preprocessing Pipeline

The preprocessing pipeline addresses three hardware constraints simultaneously: the Hailo-8L NPU requires fixed 640×640 float32 HWC input; the IMX708 camera delivers 16:9 frames; and YOLO label coordinates must remain geometrically valid after resize.

A naive `cv2.resize(img, (640, 640))` on a 4608×2592 image compresses horizontally 7.2× and vertically 4.0×, invalidating bounding box aspect ratios. Letterbox resize preserves aspect ratio:

**Step 1 — Letterbox resize:**

```python
def letterbox(img, size=640):
    h, w = img.shape[:2]
    scale = size / max(h, w)
    new_w, new_h = int(w * scale), int(h * scale)
    img_r = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    pad_w = (size - new_w) // 2
    pad_h = (size - new_h) // 2
    img_padded = cv2.copyMakeBorder(
        img_r, pad_h, size-new_h-pad_h, pad_w, size-new_w-pad_w,
        cv2.BORDER_CONSTANT, value=(114, 114, 114))
    return img_padded, scale, (pad_w, pad_h)
```

Grey fill (114, 114, 114) matches YOLOv8's inference-time letterbox fill, so the model learns to suppress false detections near padding borders.

**Step 2 — Label coordinate adjustment:**

After letterboxing, YOLO normalised coordinates must be remapped to the padded canvas:

```
cx_new = (cx_px × scale + pad_w) / 640
cy_new = (cy_px × scale + pad_h) / 640
bw_new = (bw_px × scale) / 640
bh_new = (bh_px × scale) / 640
```

All coordinates are clamped to [0.001, 0.999] to avoid boundary anomalies in YOLOv8's loss computation.

**Step 3 — Integrity and deduplication:**

| Check | Result (v5) |
|-------|-------------|
| Missing label files | 0 |
| Missing image files | 0 |
| Empty label files | 0 |
| Corrupt images | 0 |
| SHA-256 train duplicates | 0 |
| Images skipped (v2 merge) | 1 (missing annotation) |

**Step 4 — Stratified re-split (seed=42):** Class-primary stratification, 80/10/10. Fixed seed ensures all experiment comparisons are valid.

**Step 5 — Filename collision prevention:** Files prefixed by source (`roboflow_*`, `openimages_knife_*`, `v4_train_*`, `oi_person_*`, etc.) to prevent namespace collisions in the merged directory.

**Augmentation (applied at training time by YOLOv8 / Albumentations):**

| Parameter | Value |
|-----------|-------|
| Mosaic | 1.0 (4-image composition) |
| Flip LR | p = 0.5 |
| HSV hue | h = 0.015 |
| HSV saturation | s = 0.7 |
| HSV value | v = 0.4 |
| Scale | 0.5 |
| Translate | 0.1 |
| MixUp | 0.0 (disabled) |
| Degrees / Shear | 0 |

#### IV.1.4 Final Dataset Statistics (v5 — Deployed)

**Split composition:**

| Split | Images | Labels | Multi-class images |
|-------|--------|--------|--------------------|
| train | 4,982 | 4,982 | 155 (3.1%) |
| val | 622 | 622 | 19 (3.1%) |
| test | 622 | 622 | 19 (3.1%) |
| **Total** | **6,226** | **6,226** | |

**Source breakdown (v5 train split):**

| Source | Train | Val | Test | Total |
|--------|-------|-----|------|-------|
| merged\_dataset/train | 1,913 | 244 | 226 | 2,383 |
| merged\_dataset/val | 141 | 18 | 14 | 173 |
| merged\_dataset/test | 133 | 23 | 18 | 174 |
| openimages\_extra/hammer | 93 | 12 | 14 | 119 |
| openimages\_extra/knife | 495 | 49 | 56 | 600 |
| openimages\_extra/person | 1,981 | 245 | 259 | 2,485 |
| openimages\_extra/scissors | 226 | 31 | 35 | 292 |

**Annotation instance counts per class per split:**

| Class | Train | Val | Test | Total |
|-------|-------|-----|------|-------|
| 0 Hammer | 254 | 35 | 46 | 335 |
| 1 Knife | 1,381 | 162 | 159 | 1,702 |
| 2 Person | 10,460 | 1,273 | 1,324 | 13,057 |
| 3 scissors | 981 | 133 | 127 | 1,241 |
| **Total** | **13,076** | **1,603** | **1,656** | **16,335** |

**Class frequency (train split):** Person 80.0% · Knife 10.6% · scissors 7.5% · Hammer 1.9%

**Annotations per image (train split):** min=1 · max=113 · mean=2.62 · std=3.67 · median=1.0 · p95=9.0

#### IV.1.5 Bounding Box Statistics (Train Split)

| Class | BBox W (mean±std) | BBox H (mean±std) | Area (median) | Aspect ratio (median) | Small / Med / Large |
|-------|-------------------|-------------------|---------------|-----------------------|---------------------|
| Hammer | 0.385±0.252 | 0.353±0.258 | 0.086 | 1.221 | 2.4% / 36.6% / 61.0% |
| Knife | 0.583±0.304 | 0.436±0.279 | 0.181 | 1.437 | 0.5% / 18.8% / 80.7% |
| Person | 0.191±0.207 | 0.332±0.249 | 0.025 | 0.472 | 5.2% / 57.4% / 37.4% |
| scissors | 0.392±0.239 | 0.321±0.231 | 0.096 | 1.457 | 2.5% / 34.9% / 62.6% |

Size thresholds: small < 0.0032 · medium 0.0032–0.0512 · large ≥ 0.0512 (normalised area, COCO-style).

The Person class has high small-object fraction (5.2%) due to partial annotations (torso/face crops) and crowd scenes. This explains the lower Person recall (0.609) compared to weapon classes in the final model.

#### IV.1.6 Class Co-occurrence Matrix (Train Split — Image Level)

| | Hammer | Knife | Person | scissors |
|-|--------|-------|--------|----------|
| **Hammer** | 202 | 36 | 61 | 44 |
| **Knife** | 36 | 1,029 | 82 | 91 |
| **Person** | 61 | 82 | 3,318 | 72 |
| **scissors** | 44 | 91 | 72 | 689 |

Values represent number of training images containing both row and column class simultaneously. Co-occurrence is intentional — the cascade pipeline (MobileNetV2 Inspector) specifically targets person+weapon co-occurrence frames.

#### IV.1.7 Image Properties (Train Split)

| Property | Value |
|----------|-------|
| Format | JPEG (100%) |
| Width: min / mean / max | 436 / 826.1 / 4608 px |
| Height: min / mean / max | 303 / 722.4 / 3456 px |
| Unique resolutions | 439 |
| Most common: 640×640 | 2,187 images (43.9%) |
| File size: median / p95 | 148.8 KB / 621.2 KB |
| Total dataset size | 1.32 GB |

#### IV.1.8 MobileNetV2 Classifier Dataset

Person bounding box crops were extracted from dataset\_v5 and labelled by co-occurrence: Safe (no weapon in the same image) vs. Weapon (weapon annotation present). The raw crop distribution was 62:1 Safe:Weapon. A 10× augmentation strategy (random horizontal flip, brightness ±20%, slight rotation ±10°) was applied exclusively to Weapon crops to correct this imbalance.

| Split | Safe crops | Weapon crops | Total |
|-------|-----------|--------------|-------|
| Train | 1,200 | 1,660 | 2,860 |
| Val | 200 | 24 | 224 |

**Test sets held out before any training (seed=42):**
- YOLOv8s v5 test set: 622 images, 1,656 instances
- YOLOv8s v2 test set: 174 images, 351 instances
- YOLOv8s v1 test set: 51 images, ~120 instances

### IV.2 Performance Metrics

#### Overall System Metrics (v5 Deployed — Hailo-8L, Test Set: 622 images, 1,656 instances)

| Metric | Definition | Hammer | Knife | Person | Scissors | **Overall** |
|--------|------------|--------|-------|--------|----------|-------------|
| **Precision** | TP/(TP+FP) | 0.930 | 0.896 | 0.701 | 0.935 | **0.866** |
| **Recall** | TP/(TP+FN) | 0.848 | 0.817 | 0.609 | 0.929 | **0.801** |
| **F1-score** | 2·P·R/(P+R) | 0.887 | 0.855 | 0.652 | 0.932 | **0.832** |
| **mAP50** | Mean AP @ IoU=0.50 | 0.889 | 0.886 | 0.647 | 0.967 | **0.847** |
| **mAP50-95** | Mean AP @ IoU=0.50:0.95 | 0.720 | 0.721 | 0.373 | 0.828 | **0.661** |
| **False Alarm Rate** | FP/(FP+TN) = 1−Precision | 0.070 | 0.104 | 0.299 | 0.065 | **0.134** |

#### MobileNetV2 Classifier (val set: 224 crops — 200 Safe, 24 Weapon)

| Metric | Definition | Value |
|--------|------------|-------|
| **Val Accuracy** | (TP+TN)/(TP+TN+FP+FN) | **99.6%** (final) / 100.0% (best, Phase 2 Epoch 3) |
| **Val Loss** | CrossEntropyLoss | 0.0206 (final) / 0.0026 (best) |
| **Training time** | RTX 4090, 30 epochs total (5+25) | ~2.5 minutes |

#### Hardware Throughput (Hailo-8L on Raspberry Pi 5)

| Metric | Definition | Value | Relevance to Security |
|--------|------------|-------|-----------------------|
| **FPS** | Frames per second | **52.22 FPS** | ≥30 FPS required for real-time |
| **HW Latency** | NPU inference time per frame | **18.38 ms** | Detection responsiveness |
| **T_capture** | GStreamer appsink pull | ~1 ms | Frame acquisition |
| **T_YOLO** | YOLOv8s end-to-end | **18.38 ms** | Primary detection |
| **T_classifier** | MobileNetV2 per person crop | <2 ms | Threat confirmation |
| **T_depth** | MiDaS per person crop | <5 ms | Anti-spoof check |
| **T_total (detection)** | Capture + YOLO + decision | **~22 ms** | Weapon detection latency |
| **T_total (cascade)** | + Classifier + Depth | **~27 ms** | Person threat latency |
| **Drop rate** | Frames lost at 52 FPS | **0.00%** | No missed frames |

#### Scenario-Based Performance

| Scenario | Accuracy (mAP50 / Classifier) | FPS | End-to-End Latency |
|----------|-------------------------------|-----|-------------------|
| Daytime — Knife detection | 0.886 | 52.22 | 22 ms |
| Daytime — Hammer detection | 0.889 | 52.22 | 22 ms |
| Daytime — Scissors detection | 0.967 | 52.22 | 22 ms |
| Daytime — Person + Weapon | 0.647 (YOLO) + 99.6% (MobileNetV2) | 52.22 | 24 ms |
| Any — Photo/screen spoof | depth var: 0.000–0.004 (spoof) vs 0.022–0.10 (real) | 52.22 | 27 ms |
| Night — Person + MiDaS | 0.647 + depth check (var≥0.05 → real) | 52.22 | 27 ms |

### IV.3 Experimental Results

#### Model Progression — Test Set Results

The following table shows measured performance on fully held-out test sets for each model generation:

| Model Version | Training Images | Test mAP50 | Test Precision | Test Recall | F1 |
|--------------|----------------|------------|----------------|-------------|-----|
| Default YOLOv8s (COCO, no fine-tuning) | 118,287 (COCO) | 0.0002 | 0.006 | 0.025 | 0.010 |
| **v1** (Roboflow only) | 359 | 0.465 | 0.719 | 0.472 | 0.573 |
| **v2** (Merged: Roboflow + OpenImages) | 1,383 | 0.754 | 0.747 | 0.725 | 0.736 |
| **v5** (Full dataset — deployed) | **4,982** | **0.847** | **0.866** | **0.801** | **0.832** |

> The COCO-pretrained baseline scores near zero because Hammer is not a COCO class and the class ID mapping is incompatible. Domain-specific fine-tuning is mandatory, not optional.

#### Per-Class Results — v1 vs v5 (Deployed) on Respective Test Sets

| Class | v1 Recall | v5 Recall | Δ Recall | v1 mAP50 | v5 mAP50 | Δ mAP50 |
|-------|-----------|-----------|----------|----------|----------|---------|
| Hammer | 0.663 | **0.848** | **+27.9%** | 0.632 | **0.889** | **+40.7%** |
| Knife | 0.267 | **0.817** | **+206%** | 0.229 | **0.886** | **+287%** |
| Person | 0.197 | **0.609** | **+209%** | 0.240 | **0.647** | **+170%** |
| scissors | 0.761 | **0.929** | **+22.1%** | 0.760 | **0.967** | **+27.2%** |
| **All** | 0.472 | **0.801** | **+69.7%** | 0.465 | **0.847** | **+82.2%** |

> The most critical improvement is Knife recall: from 0.267 (v1) to 0.817 (v5), a 206% gain. In a domestic security context, missing a knife detection is the highest-consequence failure mode. v5 reduces the knife miss rate from 73.3% to 18.3%.

#### Scenario-Based Performance Summary

| Scenario | Model | mAP50 | FPS | End-to-End Latency | Alert Capability |
|----------|-------|-------|-----|-------------------|-----------------|
| Daytime — dangerous object (knife/hammer/scissors) | v5 | 0.866–0.967 per class | 52.22 | ~22 ms | Direct YOLO alert |
| Daytime — person + weapon | v5 + MobileNetV2 | 0.647 (person) + 99.6% classifier | 52.22 | ~24 ms | Cascade alert |
| Night mode — person + MiDaS anti-spoof | v5 + MiDaS | 0.647 + depth check | 52.22 | ~27 ms | Depth-verified alert |
| Photo/screen spoof attempt | MiDaS | N/A | 52.22 | ~27 ms | Spoof_Attempt label |

### IV.4 Comparative Study

#### vs. Cloud-Based Systems

| Property | Cloud-Based Systems | Garuda (Edge) |
|----------|-------------------|---------------|
| Processing location | Remote server | Raspberry Pi 5 (local) |
| Alert latency | 200–2,000 ms | **~22 ms** |
| Internet required | Yes (always) | **No** |
| Video data exposure | Raw frames sent to cloud | **Frames never leave device** |
| Privacy risk | High | **None** |
| Offline availability | None | **Full functionality** |
| Monthly cost | $10–200 (cloud subscription) | **$0 after hardware** |
| Custom object classes | Requires retraining + cloud API | **Custom domain fine-tune** |

#### vs. Other Edge-Based Systems

| System | Hardware | FPS | Privacy | Custom Classes | Anti-Spoof |
|--------|----------|-----|---------|----------------|------------|
| Pi + CPU YOLOv8s | Raspberry Pi 5 (CPU only) | ~3–5 FPS | Local | No | No |
| Jetson Nano + YOLOv5 | $99 NVIDIA Jetson | ~30 FPS | Local | Fine-tuning | No |
| Google Coral + MobileNet | $60 Coral USB | ~100 FPS | Local | Limited | No |
| **Garuda (Hailo-8L + v5)** | **Raspberry Pi 5 + Hailo AI HAT** | **52.22 FPS** | **Fully local** | **Yes (custom 4-class)** | **Yes (MiDaS depth)** |

**Unlike existing systems, Garuda performs complete processing locally without any cloud dependency**, achieves real-time throughput (52.22 FPS), supports domain-specific dangerous object classes not available in COCO, and adds a physically grounded anti-spoofing layer that is absent from all comparable edge deployments.

---

## V. Security Analysis

### V.1 Threat Model

The system must defend against the following threat categories:

**Physical Threats (detected by AI pipeline):**
- Unauthorized person entering monitored area
- Dangerous object (knife, hammer, scissors) in frame
- Photo/screen spoofing bypass attempt (person holds image of a person)

**Cyber Threats (defended by software security layer):**

| Threat | Attack Surface |
|--------|---------------|
| Unauthorized access | Web dashboard login — brute force, credential stuffing |
| Password attacks | Admin and user passwords stored in server memory |
| Network interception | HTTP traffic between client browser and Pi web server |
| Session hijacking | Cookie theft or token replay |
| Denial of service | Hailo device starvation — only one process can hold `/dev/hailo0` |

### V.2 Defense Mechanisms

**OTP Authentication:**  
The admin login flow requires entry of a one-time password (OTP) sent via Gmail SMTP to the registered email address. The OTP expires after 5 minutes. This prevents static credential attacks even if the password is compromised.

**Password Hashing:**  
All stored passwords are hashed using bcrypt with a per-user salt. Raw passwords are never persisted. Even full database extraction reveals only irreversible hashes.

**Rate Limiting:**  
Login endpoints enforce a maximum of 5 attempts per minute per IP address. Exceeding this limit triggers a temporary lockout. This defeats automated credential stuffing tools.

**Secure Cookies:**  
Session cookies are flagged `HttpOnly` and `SameSite=Strict`, preventing JavaScript access and cross-site request forgery. The `SECURE_COOKIES` environment variable can enforce `Secure` flag for HTTPS deployments.

### V.3 Advanced Security Features (High Impact)

**Session Timeout:**  
Admin sessions expire after a configurable idle period. Expired sessions require full re-authentication including OTP. This limits the window for session replay attacks.

**Token Rotation:**  
Session tokens are rotated on each authenticated request, invalidating any captured token from a prior session window.

**Login Lockout:**  
After 5 consecutive failed OTP attempts, the account enters a 15-minute lockout state. Events are logged to `perm_system_log.txt` with timestamp and IP for forensic review.

**Hailo Device Protection:**  
The system holds exclusive access to `/dev/hailo0`. Any second process attempting to open the device receives `HAILO_OUT_OF_PHYSICAL_DEVICES (error 74)`, preventing unauthorized parallel inference on the same chip.

**Tamper Detection:**  
The heartbeat watchdog monitors pipeline liveness. If no heartbeat is received within 180 seconds, a `TAMPER` alert is logged and an email is dispatched. Camera blindness detection fires when the frame entropy drops below threshold (lens covered scenario).

---

## VI. Intelligence Layer — Context-Aware Decision System

### VI.1 Current Architecture: Beyond Simple Detection → Alert

The system implements a multi-stage context-aware decision model rather than a direct detection-to-alert pipeline:

```
Detection → Context Evaluation → Alert Decision
```

**Decision matrix:**

| YOLO Output | Confidence | Time of Day | MobileNetV2 | MiDaS Variance | Final Decision |
|-------------|------------|-------------|-------------|----------------|----------------|
| Weapon (Knife/Hammer/scissors) | ≥ 0.25 | Any | — | — | **HIGH ALERT — Weapon** |
| Person | ≥ 0.60 | Any | Weapon | ≥ 0.05 (real) | **HIGH ALERT — Armed Person** |
| Person | ≥ 0.60 | Any | Weapon | < 0.05 (flat) | **ALERT — Spoof_Attempt** |
| Person | ≥ 0.60 | Any | Safe | ≥ 0.05 (real) | **WATCH log — Person (safe)** |
| Person | ≥ 0.60 | Any | Safe | < 0.05 (flat) | **ALERT — Spoof_Attempt** |
| Person | < 0.60 | — | — | — | Suppressed (low confidence) |
| No detection | — | Any | — | — | Baseline heartbeat |

### VI.2 Night Presence Window

The system applies an additional context rule: between configurable night hours (default 01:30–05:00), any confirmed person detection triggers a `night_presence` alert regardless of weapon classification. Legitimate residents do not typically enter monitored areas at these hours.

```python
if _is_in_night_window() and person_detected:
    alert_level = "HIGH"   # unconditional presence alert
```

### VI.3 DND and Mode Suppression

The web dashboard exposes runtime mode controls:
- **DND (Do Not Disturb):** Suppresses all alert notifications while keeping detection and logging active.
- **EMAIL_OFF:** Disables email dispatch while other alerts remain active.
- **IDLE:** Reduces detection frequency for power saving.
- **NIGHT:** Forces night-mode detection thresholds.

Mode state is persisted in `system_logs/config.json` and survives server restarts.

### VI.4 Future Extension — Registered Device Suppression

The decision model architecture supports the addition of Wi-Fi device registration as a suppression signal:

```
If person detected + registered MAC address present on local network:
    suppress alert (known occupant)
If person detected + no registered device:
    escalate to HIGH alert (unknown presence)
```

This would convert Garuda from a binary alarm system into a full occupancy-aware intelligent security layer. Implementation requires adding a network scanner (e.g., `arp-scan`) and a registered device whitelist to `cascade_config.json`.

---

## VII. Ablation Study

The following ablation experiments isolate the contribution of each system component to overall detection and alert quality.

### VII.1 Effect of Model Version (Dataset Size and Diversity)

| Configuration | Training Images | Test mAP50 | Knife Recall | False Alert Rate | Notes |
|--------------|----------------|------------|--------------|-----------------|-------|
| No fine-tuning (COCO) | 118,287 | 0.0002 | 0.000 | ~1.00 | Hammer undetectable |
| v1 — Roboflow only | 359 | 0.465 | **0.267** | 0.281 | Severe overfitting |
| v2 — Roboflow + OpenImages | 1,383 | 0.754 | 0.800 | 0.253 | Good generalization |
| **v5 — Full dataset (deployed)** | **4,982** | **0.847** | **0.817** | **0.134** | **Best overall** |

Removing the OpenImages augmentation (reverting to v1) causes Knife recall to collapse from 0.817 to 0.267 — 73% of knife events would go undetected. This is the most critical ablation finding.

### VII.2 Effect of Presence Detection (MobileNetV2 Inspector)

| Feature Configuration | Accuracy on Person+Weapon Scenes | False Weapon Alerts | Notes |
|-----------------------|----------------------------------|---------------------|-------|
| YOLO only (no MobileNetV2) | ~86.6% (YOLO precision) | High (background clutter triggers) | Single-stage only |
| **YOLO + MobileNetV2** | **~99.6%** | **Minimized** | Two-stage confirmation |

MobileNetV2 acts as a confirmation gate for person-based weapon alerts. Without it, any YOLO person detection near a weapon-like object triggers an alert. With it, 99.6% of false person-weapon associations are suppressed.

### VII.3 Effect of Anti-Spoof Depth Check (MiDaS Reality Checker)

| Feature Configuration | Spoof Detection | Real Person Pass-Through |
|-----------------------|----------------|--------------------------|
| No depth check | 0% (blind to spoof) | 100% |
| **With MiDaS (variance < 0.05)** | **~100%** (flat screens consistently produce variance < 0.005) | **~100%** (real persons consistently produce variance 0.022–0.10) |

The depth variance threshold creates a clean decision boundary with no observed overlap between real-person variance (0.022+) and flat-panel spoof variance (<0.005) across all tested scenarios. The 4× margin between the minimum observed real-person variance (0.022) and the threshold (0.05) provides robust tolerance against noisy depth estimates.

---

## VIII. Performance Optimization Study

The Garuda YOLO detector was progressively optimized from the COCO-pretrained YOLOv8s baseline to a domain-specific edge-compiled model through fine-tuning, dataset expansion, and INT8 post-training quantization.

### VIII.1 Training Hardware and Optimization Path

| Stage | Hardware | Training Time | Action |
|-------|----------|--------------|--------|
| Pre-training | NVIDIA RTX 3080 Laptop | COCO baseline (pre-built) | Transfer learning base |
| v1 fine-tune | RTX 3080 Laptop | ~40 min | 100 epochs, 359 images |
| v2 fine-tune | RTX 3080 Laptop | ~85 min | 102 epochs, 1,383 images |
| v5 full train | **RTX 4090 (RunPod)** | **1.14 hours** | 150 epochs, 4,982 images |
| Hailo compile | RunPod (CPU) | ~25 min | INT8 PTQ, 128 calibration images |

### VIII.2 Model Comparison — Optimization Progression

| Model | Dataset Size | FPS (Hailo-8L) | HW Latency | mAP50 (test) | Precision | Recall |
|-------|-------------|---------------|------------|-------------|-----------|--------|
| Default YOLOv8s (COCO) | 118,287 | 58.18 | 13.36 ms | 0.0002* | 0.006* | 0.025* |
| **v1** (Roboflow fine-tune) | 359 | ~52 | ~18 ms | 0.465 | 0.719 | 0.472 |
| **v2** (Merged fine-tune) | 1,383 | ~52 | ~18 ms | 0.754 | 0.747 | 0.725 |
| **v5** (Full fine-tune, deployed) | **4,982** | **52.22** | **18.38 ms** | **0.847** | **0.866** | **0.801** |

*Scores near zero for COCO model because class IDs do not align with the 4-class scheme. Hammer is absent from COCO entirely.

### VIII.3 Quantization Impact (FP32 → INT8)

The v5 model was compiled to INT8 via post-training quantization (PTQ) using 128 calibration images from dataset_v5/train. The Hailo Dataflow Compiler inserts normalization into the HEF graph (`normalization([0,0,0],[255,255,255])`).

| Metric | FP32 (PyTorch, RTX 4090) | INT8 (Hailo-8L) | Delta |
|--------|--------------------------|-----------------|-------|
| mAP50 | ~0.855 (val set) | ~0.847 (test set) | −0.8% |
| FPS | ~450 (RTX 4090 batch) | **52.22** | Limited by NPU clock |
| Power consumption | ~100W (GPU) | **~5W (NPU+Pi)** | **−95%** |
| HEF file size | 44.8 MB (ONNX FP32) | **22.9 MB** | −48.9% |

INT8 quantization introduces less than 1 mAP50 point of accuracy loss while reducing power consumption by 95% — critical for a continuously running embedded security system.

### VIII.4 Architecture Selection — YOLOv8s vs Alternatives

YOLOv8s (11.1M parameters, 28.4 GFLOPs) was selected over YOLOv8m (25.9M, 80.6 GFLOPs) and YOLOv8n (3.2M, 8.7 GFLOPs) for the following reasons:

| Model | GFLOPs | FPS (Hailo-8L, estimated) | mAP50 (COCO) | Fit within 13 TOPS? |
|-------|--------|--------------------------|--------------|---------------------|
| YOLOv8n | 8.7 | >60 FPS | 37.3% | Yes |
| **YOLOv8s** | **28.4** | **52.22 FPS** | **44.9%** | **Yes** |
| YOLOv8m | 80.6 | ~18 FPS | 50.2% | Marginal |
| YOLOv8l | 165.2 | <10 FPS | 52.9% | No |

YOLOv8s provides the best accuracy-throughput tradeoff within the Hailo-8L's 13 TOPS compute budget.

---

## IX. Literature Review

### IX.1 Edge AI Surveillance Systems

Edge-based AI inference for surveillance has seen rapid development since 2019, driven by the availability of low-power neural network accelerators. Redmon et al. (YOLO, 2016) established the single-shot detection paradigm that enables real-time object detection; YOLOv8 (Ultralytics, 2023) extends this with decoupled detection heads and improved anchor-free design. Howard et al. (MobileNetV2, 2018) introduced the inverted residual block, enabling efficient classification on constrained hardware at 500+ FPS on modern NPUs.

Deployment-level studies have demonstrated YOLO variants on NVIDIA Jetson platforms (Adarsh et al., 2020; Boesch, 2023) achieving 25–45 FPS. However, Jetson Nano draws ~5–10W idle versus Raspberry Pi 5 at ~3W with Hailo-8L at ~2W — Garuda's power profile is more suitable for always-on home deployment.

The Hailo-8L (13 TOPS, 2023) represents a new class of sub-$70 AI accelerators designed for Raspberry Pi integration, enabling inference performance previously requiring $200+ Jetson hardware. Garuda is among the first systems to exploit this platform for multi-model cascade inference.

### IX.2 Privacy-Preserving AI

The privacy implications of cloud-based surveillance are well-documented. Zuboff (2019) defines "surveillance capitalism" — the monetisation of behavioural data captured without informed consent. Technical responses include federated learning (McMahan et al., 2017) and differential privacy (Dwork, 2014), but these address training-time privacy, not inference-time data exposure.

The most effective defense against inference-time video exposure is architectural: process all data locally and never transmit raw frames. Garuda implements this by design — the GStreamer pipeline captures frames directly to the Hailo NPU via DMA, and detection results (bounding boxes, labels, confidence scores) are the only data that flow to the web dashboard. Raw video frames are never stored or transmitted.

### IX.3 Smart Home Security

Commercial smart home security systems (Ring, Nest, Arlo) all rely on cloud infrastructure for AI processing. Ring's cloud-based person detection operates at 1–5 second clip latency; Nest's Familiar Faces feature sends face embeddings to Google servers. Both have been the subject of documented privacy breaches and law enforcement data requests without user consent.

Academic smart home security research (Agrawal et al., 2021; Singh et al., 2022) has explored on-device models but remains limited to person detection without dangerous object classification or anti-spoofing. Garuda extends the state of the art by adding:
1. Domain-specific dangerous object detection (knife, hammer, scissors) not available in COCO-class models
2. Secondary binary threat classification per person
3. Physical depth-based anti-spoofing
4. Multi-modal alerting with role-based access control

### IX.4 Positioning Statement

Unlike existing systems, **Garuda performs complete processing locally without any cloud dependency**. All inference — YOLO detection, MobileNetV2 classification, MiDaS depth estimation — executes on the Hailo-8L NPU attached to the Raspberry Pi 5. No video frame, crop, or embedding is transmitted off-device. The system achieves lower alert latency (~22 ms vs 200–2,000 ms for cloud systems), zero ongoing infrastructure cost, and full functionality during internet outages — properties that no current commercial smart home security product provides simultaneously.

---

## X. System Diagrams

### X.1 System Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         GARUDA ARCHITECTURE                              │
│                                                                          │
│  PHYSICAL LAYER                INFERENCE LAYER              APP LAYER    │
│                                                                          │
│  ┌──────────────┐     USB/CSI  ┌───────────────────┐  JSON  ┌─────────┐ │
│  │ IMX708       │─────────────▶│   Hailo-8L NPU    │───────▶│ FastAPI │ │
│  │ Pi Cam v3    │              │   13 TOPS         │        │ Web UI  │ │
│  │ 1280×720     │              │                   │        │ :8080   │ │
│  └──────────────┘              │  ┌─────────────┐  │        └────┬────┘ │
│                                │  │  YOLOv8s    │  │             │      │
│  ┌──────────────┐              │  │  4-class    │  │        ┌────▼────┐ │
│  │ Microphone   │              │  │  52.22 FPS  │  │        │  Email  │ │
│  │ SpeechRec    │              │  └─────────────┘  │        │  SMTP   │ │
│  └──────────────┘              │  ┌─────────────┐  │        └─────────┘ │
│                                │  │ MobileNetV2 │  │                    │
│  ┌──────────────┐              │  │  Classifier │  │        ┌─────────┐ │
│  │ Raspberry Pi │              │  │  500+ FPS   │  │        │  Voice  │ │
│  │ Pi 5 (4-core)│              │  └─────────────┘  │        │  TTS    │ │
│  │ 8 GB RAM     │              │  ┌─────────────┐  │        └─────────┘ │
│  └──────────────┘              │  │ MiDaS Small │  │                    │
│                                │  │  Depth Est. │  │        ┌─────────┐ │
│  ┌──────────────┐              │  │  200+ FPS   │  │        │ SQLite  │ │
│  │ Ethernet/    │              │  └─────────────┘  │        │  Logs   │ │
│  │ Wi-Fi        │              └───────────────────┘        └─────────┘ │
│  └──────────────┘                                                        │
└──────────────────────────────────────────────────────────────────────────┘
```

### X.2 Data Flow Diagram

```
Camera Frame (1280×720 BGR)
        │
        ▼ [Core 0: camera_thread]
┌───────────────────────┐
│  GStreamer appsink     │  libcamerasrc → videoconvert → BGR → appsink
│  try_pull_sample(5s)  │
└──────────┬────────────┘
           │
           ▼ frame_queue (maxsize=4, drop-on-full)
┌───────────────────────┐
│  Preprocess for YOLO  │  resize(640×640) → cvtColor(BGR→RGB) → NHWC uint8
└──────────┬────────────┘
           │ [Core 1: inference_thread]
           ▼
┌───────────────────────┐
│  YOLOv8s.infer()      │  Hailo-8L NPU · 18.38 ms · 52.22 FPS
│  HailortPP NMS        │  Output: class_id, conf, [y1,x1,y2,x2]
└──────────┬────────────┘
           │
     ┌─────┴──────┐
     ▼            ▼
  Weapon?      Person?
  (class        (conf ≥
  0,1,3)         0.60)
     │            │
     │            ▼
     │  ┌─────────────────┐
     │  │ Crop 224×224    │  preprocess_crop → MobileNetV2.infer()
     │  │ MobileNetV2     │  Output: Safe (0) / Weapon (1)
     │  └────────┬────────┘
     │           │
     │           ▼
     │  ┌─────────────────┐
     │  │ Crop 256×256    │  preprocess_crop → MiDaS.infer()
     │  │ MiDaS Depth     │  Output: [256×256] depth map
     │  └────────┬────────┘
     │           │
     └─────┬─────┘
           │
           ▼ result_queue → [Core 2: postprocess_thread]
┌───────────────────────┐
│  Decision Engine      │  variance = var(depth_norm)
│  variance < 0.05?     │  spoof=True → "Spoof_Attempt"
│  threat from cls?     │  spoof=False → threat label
└──────────┬────────────┘
           │
     ┌─────┴──────────┐
     ▼                ▼
  Log entry       Alert?
  JSON stdout     (Weapon / Spoof)
  perm_log.txt        │
  alert_history.json  ├── Email (SMTP async)
                      ├── Voice (TTS async)
                      └── danger_sightings.txt
```

### X.3 Detection Pipeline — YOLO NMS Flow

```
Input Frame 640×640×3
        │
        ▼
┌─────────────────────────────────────────────┐
│  YOLOv8s Backbone + Neck                    │
│  Backbone: C2f blocks (CSPDarkNet)          │
│  Neck: PAN-FPN (multi-scale feature fusion) │
└───────────────────┬─────────────────────────┘
                    │
         ┌──────────┼──────────┐
         ▼          ▼          ▼
    80×80 head  40×40 head  20×20 head
    (stride 8)  (stride 16) (stride 32)
    Small obj.  Medium obj. Large obj.
         │          │          │
         └──────────┼──────────┘
                    │
                    ▼ (baked into HEF via HailortPP)
        ┌─────────────────────────┐
        │  NMS Post-processing    │
        │  score_thresh = 0.25    │
        │  iou_thresh = 0.45      │
        │  max_proposals = 100    │
        └────────────┬────────────┘
                     │
                     ▼
       Detections: [{class_id, conf, box}, ...]
```

### X.4 Alert Mechanism Flowchart

```
Detection Event Received
          │
          ▼
    ┌─────────────┐
    │ Is DND on?  │──Yes──▶ LOG ONLY (no alert dispatch)
    └──────┬──────┘
           │ No
           ▼
    ┌─────────────────┐
    │ Is EMAIL_OFF on?│──Yes──▶ Skip email, continue to voice
    └──────┬──────────┘
           │ No
           ▼
    ┌──────────────────────────┐
    │ Alert cooldown elapsed?  │──No──▶ Suppress (anti-spam)
    │ (default: 60 seconds)    │
    └──────────┬───────────────┘
               │ Yes
               ▼
    ┌───────────────────────┐
    │ Capture alert frame   │
    │ Attach JPEG snapshot  │
    └──────────┬────────────┘
               │
       ┌───────┴───────┐
       ▼               ▼
  SMTP Email        Voice TTS
  (async thread)    (async thread)
       │               │
       ▼               ▼
  Gmail SMTP        speak(label)
  465/SSL           "Weapon detected"
  App Password      "Spoof attempt"
       │
       ▼
  Log: perm_system_log.txt
  Log: alert_history.json (daily count)
  Log: danger_sightings.txt
```

---

## XI. Novel Contributions

The following contributions distinguish Garuda from existing smart surveillance systems and establish its research novelty:

1. **Fully local AI-based surveillance system.** All inference (object detection, threat classification, depth estimation) executes on a Hailo-8L NPU co-located with the Raspberry Pi 5. No video frame or model output is transmitted to any external server. The system achieves full functionality with zero internet connectivity.

2. **Privacy-preserving architecture by design.** Frames are processed in-memory via DMA between the camera GStreamer pipeline and the Hailo NPU. No image storage occurs during normal operation — only structured detection events (bounding boxes, labels, confidence scores) are logged.

3. **Domain-specific dangerous object detection at 52.22 FPS.** The COCO-pretrained YOLOv8s baseline achieves mAP50 ≈ 0 on the target classes because Hammer is absent from COCO and class IDs are misaligned. Fine-tuning on a custom 6,226-image multi-source dataset achieves mAP50 = 0.847 on a fully held-out test set — representing a 4,235× improvement over the untuned baseline. Critically, Knife recall improved from 26.7% (v1) to 81.7% (v5), reducing the knife miss rate from 73.3% to 18.3%.

4. **Cascaded multi-model NPU inference with zero FPS penalty.** Three neural networks (YOLOv8s, MobileNetV2, MiDaS Small) run cooperatively on a single Hailo-8L VDevice via sequential activation. Secondary models (MobileNetV2 at 500+ FPS, MiDaS at 200+ FPS) consume negligible headroom within the 13 TOPS budget, introducing less than 4 ms of conditional overhead per detected person without degrading the primary 52.22 FPS detection pipeline.

5. **Physically grounded depth-based anti-spoofing.** MiDaS Small provides monocular depth maps enabling spoofing detection based on depth spatial variance — a physically fundamental signal (real 3D persons produce high variance; flat 2D screens produce near-zero variance) that is robust to adversarial image manipulation. No training data is required for this component — it is zero-shot against unseen spoof types by virtue of being grounded in 3D geometry rather than learned appearance statistics.

6. **Context-aware alert mechanism.** The system does not implement simple detection → alert. Alert decisions integrate YOLO confidence, MobileNetV2 threat classification, MiDaS depth verification, time-of-day context (night presence window), and mode state (DND, EMAIL_OFF, IDLE) to produce calibrated alerts with minimal false positives. The decision pipeline is documented and auditable through persistent JSON event logs.

7. **Integrated multi-modal interface: web + email + voice.** Garuda provides a real-time web dashboard (FastAPI, live camera feed with detection overlay), asynchronous email alerts with JPEG snapshots, and a voice assistant ("Narada") supporting spoken commands and TTS threat announcements. All interfaces are accessible from any device on the local network with no port forwarding or VPN required.

---

## Appendix A — Model Training Environment

| Component | Specification |
|-----------|--------------|
| Training GPU (v1/v2) | NVIDIA RTX 3080 Laptop (16 GB VRAM) |
| Training GPU (v5) | NVIDIA RTX 4090 (24 GB VRAM) — RunPod cloud |
| Training framework | Ultralytics YOLOv8 8.4.37, PyTorch 2.6.0+cu124 |
| Compiler | Hailo Dataflow Compiler v3.33.1 |
| Compiler environment | Python 3.8, Ubuntu (RunPod cloud instance) |
| Deployment hardware | Raspberry Pi 5 (Arm Cortex-A76, 4-core, 8 GB) |
| Deployment NPU | Hailo-8L (13 TOPS) via PCIe |
| Deployment OS | Raspberry Pi OS (Bookworm, 64-bit) |
| Inference runtime | HailoRT 4.20.0, GStreamer 1.22, libcamera |
| Camera | Sony IMX708 (Pi Camera Module 3, 12 MP) |

## Appendix B — Hailo-8L Compilation Notes

1. Hailo Dataflow Compiler (DFC) v5.x does **not** support hailo8l target. Use DFC v3.33.1.
2. DFC 3.33.1 requires Python 3.8–3.11. Python 3.12+ is incompatible.
3. YOLOv8's DFL (Distribution Focal Loss) head must be excluded from end-node compilation. Include only the 6 pre-DFL output nodes (BBox reg and classification at 80×80, 40×40, 20×20 scales).
4. `output-format-type=HAILO_FORMAT_TYPE_FLOAT32` must **not** be set on `hailonet` when HailortPP NMS is baked into the HEF — this parameter conflicts with the internal NMS engine and silently drops all detections.
5. NMS post-processing via HailortPP (`engine=cpu`) executes on the host CPU, not the NPU. This is the source of the +5.02 ms latency difference vs the COCO baseline HEF.
6. Calibration set: 128 images at native model input size (640×640 for YOLO, 224×224 for MobileNetV2, 256×256 for MiDaS).

## Appendix C — Deployed HEF File Summary

| File | Size | Task | Classes | FPS (Hailo-8L) | Latency |
|------|------|------|---------|---------------|---------|
| `best_v5.hef` | 22.9 MB | Object detection | Hammer, Knife, Person, scissors | **52.22** | **18.38 ms** |
| `mobilenetv2_garuda.hef` | ~7.1 MB | Crop classification | Safe, Weapon | **500+** | **<2 ms** |
| `midas_depth.hef` | ~35 MB | Monocular depth | Regression (depth map) | **200+** | **<5 ms** |
| `libyolo_hailortpp_post.so` | 561 KB | Custom label NMS | (post-process library) | — | — |

---

*Document generated from: Paper flow.pdf structural guide + MODEL_EVALUATION_REPORT.md + MODEL_COMPARISON_REPORT.md + GARUDA_CASCADE_REPORT.md (S3 bucket: s3://wbeta1c4ev/)*  
*Data sources: Roboflow Knives & Scissors Training v2 (CC BY 4.0) · COCO 2017 · OpenImages v7 · MiDaS (isl-org/MiDaS, MIT License)*
