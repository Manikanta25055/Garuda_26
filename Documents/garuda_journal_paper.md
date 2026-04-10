# Garuda: A Privacy-Preserving, Fully Edge-Deployed Intelligent Surveillance System on Raspberry Pi 5 with Hailo-8L NPU

---

## Section I — Problem Statement

### 1.1 Background

The proliferation of connected surveillance devices has introduced a fundamental tension between security and privacy. Contemporary smart surveillance systems — both commercial and research-grade — depend on cloud infrastructure to perform inference, storing and processing video on remote servers. This architecture introduces three compounding problems that remain largely unaddressed in deployed systems.

**Privacy Exposure.** Raw video streams transmitted over a network are inherently vulnerable to interception, unauthorized access, and third-party data retention. The end user surrenders physical custody of sensitive footage to cloud providers, with no practical guarantee of access control or deletion.

**Latency.** A cloud round-trip for inference introduces delays of 500 ms to several seconds, making real-time threat response impractical. For applications such as intrusion detection or danger-object alerts, this lag directly reduces system utility.

**Connectivity Dependency.** Cloud-reliant systems suffer complete loss of surveillance capability when internet access is unavailable — a critical single point of failure for a security application.

---

### 1.2 State of Edge AI Surveillance

Edge AI hardware has matured substantially in recent years. Dedicated Neural Processing Units (NPUs) now deliver tens of tera-operations per second (TOPS) at sub-10W power envelopes on single-board computers. Despite this, the literature on edge-based surveillance remains largely limited to isolated inference benchmarks. Published prototypes optimize for inference speed in isolation and do not address the broader system problem: secure access control, tamper resistance, privacy-preserving video handling, operator modes, offline event resilience, and encrypted evidence handling — all co-deployed on the same resource-constrained hardware.

---

### 1.3 Research Gap

No published work demonstrates a complete, hardened, production-grade surveillance system that integrates all of the following on a single embedded platform:

- Real-time AI inference with configurable threat classification
- Context-aware alert suppression using owner presence detection
- Multi-factor authentication with brute-force protection
- Privacy-preserving video processing (on-device face masking)
- Offline-resilient event logging with local action continuity
- Encrypted evidence exfiltration
- Multi-modal user interface (web, native app, voice assistant)

---

### 1.4 Clarification on Offline Operation

It is important to note that Garuda's core surveillance functionality — inference, detection, alert generation, audio alarms, email notifications, and local event logging — operates entirely on the device and does not depend on internet connectivity. The system remains fully functional in offline conditions. The only capability affected by network loss is the remote web dashboard, through which a user views live video and system state from an external device. All detection events during this period are persisted locally to an SQLite event queue and text logs, ensuring no data is lost. Once connectivity is restored, the operator can review the complete event history through the dashboard.

---

### 1.5 Research Objective

This paper presents **Garuda** — a privacy-preserving, fully edge-deployed intelligent surveillance system built on a Raspberry Pi 5 (16 GB RAM) coupled with a Hailo-8L Neural Processing Unit (13 TOPS). The system performs real-time object detection at up to 60 fps using quantized YOLO-family models compiled to the Hailo Executable Format (HEF), applies context-aware threat classification that incorporates detection confidence, operator mode state, and owner presence inferred from ARP-based network scanning, and delivers multi-channel alerts — all with zero video data leaving the device during normal operation.

---

### 1.6 Research Questions

The following specific research questions are addressed:

1. Can a fully local, consumer-grade edge platform (Raspberry Pi 5 + Hailo-8L) sustain real-time AI surveillance at ≥30 fps with sub-second alert latency?
2. Does ARP-based owner presence detection meaningfully reduce the false alert rate compared to detection-only baselines?
3. What is the measurable trade-off between detection accuracy and inference throughput across YOLOv6n, YOLOv8s, and YOLOx-S on the Hailo-8L NPU?
4. Is a hardened authentication stack (PBKDF2-SHA256, 2FA OTP, brute-force lockout) viable on embedded hardware without degrading real-time pipeline performance?

---

### 1.7 Novel Contributions

This work makes the following contributions:

- **Fully local AI inference**: End-to-end detection pipeline running on-device at 60 fps with no cloud dependency.
- **Privacy-preserving video**: Gaussian blur applied in real-time to detected persons on the camera feed.
- **Context-aware alert logic**: Alert suppression conditioned on owner presence (ARP), time-of-day mode, and consecutive-frame confidence filtering.
- **Hardened embedded authentication**: PBKDF2-SHA256 (600,000 iterations) with admin two-factor authentication via email OTP, brute-force lockout, and short-lived session tokens — running without degrading inference throughput.
- **Offline-resilient operation**: SQLite event queue and RAM-buffered log writes ensure no event data is lost during connectivity interruptions; all local actions (alerts, logging, GPIO) continue unaffected.
- **Encrypted evidence exfiltration**: Video clips encrypted with AES-256-GCM before off-device transfer via SSH.
- **Multi-modal interface**: Progressive Web App, native macOS client, and voice assistant (Narada) backed by a local LLM — all communicating with the same on-device FastAPI backend.

---

---

## Section II — System Architecture

### 2.1 System Block Diagram

*(Refer to garuda_architecture.drawio — full system architecture diagram)*

The Garuda system is composed of seven functional layers operating concurrently on a single Raspberry Pi 5 (16 GB RAM, 1 TB SSD) with a Hailo-8L NPU:

1. **Hardware Layer** — Sony IMX708 camera, microphone, LAN/WiFi interface, Hailo-8L NPU (PCIe)
2. **AI Pipeline Layer** — GStreamer pipeline driving real-time inference on the Hailo-8L
3. **Detection & Decision Layer** — Frame analysis, object classification, threat determination
4. **Alert Engine** — Multi-channel alert delivery conditioned on operator mode state
5. **API & Services Layer** — FastAPI backend with authentication, streaming, and WebSocket
6. **Interface Layer** — Web PWA, macOS native app, Narada voice assistant
7. **Storage Layer** — SQLite event queue, JSON configuration, RAM-buffered text logs, encrypted video clips

---

### 2.2 Data Flow Explanation

The following describes the complete data flow through the Garuda system from frame capture to operator notification, written algorithmically.

---

#### Step 1 — Frame Capture

The Sony IMX708 is a 12.3 MP stacked BSI CMOS sensor with a 1/2.43" optical format. It is accessed via `libcamerasrc`, the GStreamer source element interfacing with the Linux camera stack through `libcamera`. The sensor is configured to operate at **1280 × 720 resolution at 60 fps** in raw RGB format. Each frame is passed downstream as a GStreamer buffer attached with PTS (presentation timestamp) metadata for synchronisation.

The IMX708 supports phase-detection autofocus (PDAF) and high dynamic range (HDR) modes; in Garuda, autofocus is disabled and exposure is fixed to minimise per-frame latency variation.

```
Input  : Physical light → Sony IMX708 CMOS sensor (12.3 MP, 1/2.43")
Output : GStreamer buffer (RGB, 1280×720, 60 fps, with PTS)
```

---

#### Step 2 — Preprocessing

Raw frames pass through a three-stage preprocessing chain before being submitted to the neural network. This chain is implemented entirely within GStreamer, executing on CPU cores of the Raspberry Pi 5 (ARM Cortex-A76) in parallel with NPU inference.

**2a. Spatial Downscaling**

`videoscale` reduces the frame from 1280×720 to the network input resolution of **640×640 pixels** using bilinear interpolation. This constitutes a stretch resize (no letterboxing), which is consistent with the training-time preprocessing applied during the original YOLO model training on the COCO dataset. Aspect ratio distortion is accepted in favour of full-field-of-view coverage.

> **Note on letterboxing:** A letterbox-preserving resize (padding with grey bars to maintain aspect ratio) can reduce geometric distortion for non-square subjects. The trade-off is that approximately 11% of the 640×640 tensor is padded and contributes no information. The current implementation prioritises field-of-view density over geometric fidelity. This is revisited in the model fine-tuning methodology (Section VIII).

**2b. Pixel Format Normalisation**

`videoconvert` ensures the pixel format is `RGB` (3 channels, uint8, range [0, 255]) as required by the compiled HEF model. Internally, the Hailo compiler bakes the normalization transform — dividing pixel values by 255.0 to yield a floating-point range of [0.0, 1.0] — into the HEF as a pre-processing layer fused with the first network layer. This means no explicit floating-point normalisation occurs on the CPU; the NPU handles it at near-zero latency during the first inference stage.

**2c. Stream Splitting (tee)**

The preprocessed frame enters a `tee` element, which non-destructively splits the stream into two parallel branches:

- **Branch A — Bypass path:** The raw frame (1280×720) passes directly to `hailomuxer.sink_0` without further modification. This preserves the original resolution for MJPEG encoding and WebRTC streaming to the dashboard.
- **Branch B — Inference path:** The scaled frame (640×640) is routed to `hailonet` for NPU inference.

The bypass branch introduces a configurable queue (`bypass_queue`, max 20 buffers) to absorb any timing difference between the fast bypass path and the slower inference path, preventing pipeline starvation at the muxer.

```
Input  : GStreamer buffer (RGB, 1280×720)
Process: bilinear downscale → RGB uint8 → tee split
Output : Branch A: raw buffer (1280×720) for streaming
         Branch B: scaled buffer (640×640) for NPU inference
         Pixel normalisation [0,255]→[0.0,1.0] deferred to NPU
```

---

#### Step 3 — Object Detection on Hailo-8L NPU

**3a. Network Architecture**

The primary model deployed in Garuda is **YOLOv8s** (small variant), an anchor-free single-stage object detector. Its architecture is composed of three functional blocks:

*Backbone — CSPDarknet with C2f modules:*
The backbone extracts a hierarchical feature pyramid from the input image. Each C2f (Cross Stage Partial with 2 feature maps) module splits the input tensor into two branches: one passes through a stack of Bottleneck residual blocks, and the other bypasses them. The outputs are concatenated and projected, enabling gradient flow across stages while reducing parameter count. The backbone produces feature maps at three scales: P3 (80×80), P4 (40×40), and P5 (20×20), corresponding to small, medium, and large receptive fields respectively.

*Neck — PAN-FPN (Path Aggregation Network — Feature Pyramid Network):*
The neck fuses multi-scale features from the backbone using a bidirectional feature propagation scheme. A top-down FPN path propagates semantic information from deep, low-resolution feature maps (P5) to shallower, high-resolution maps (P3). A bottom-up PAN path then propagates localization information from shallow maps back up. This dual-direction aggregation produces three enriched feature maps (P3′, P4′, P5′) that combine semantic richness with spatial precision, enabling accurate detection across object sizes.

*Head — Decoupled Detection Head:*
YOLOv8 separates classification and bounding box regression into two independent branches at each scale, unlike earlier YOLO versions which shared head weights. Each scale produces predictions for all 80 COCO classes. The regression branch predicts a distribution over bounding box offsets using DFL (Distribution Focal Loss), which models the uncertainty of box boundaries probabilistically rather than as point estimates.

**3b. Transfer Learning and Fine-Tuning Methodology**

The base YOLOv8s model is pre-trained on the **COCO 2017 dataset** (118,287 training images, 80 object classes), with the backbone itself initialised from **ImageNet-21k** pre-trained weights. This two-stage pre-training provides a strong feature prior — the backbone layers encode general visual representations (edges, textures, shapes) before any task-specific training.

For deployment in Garuda's surveillance context, the model is fine-tuned on a domain-specific dataset to improve detection accuracy for the target class (`scissors`) and reduce false positives in indoor environments. The fine-tuning strategy employs **selective layer freezing**:

*Frozen layers (backbone, stages 0–5):*
The early and mid backbone layers capture low-level features (colour gradients, edge orientations, texture patterns) that are domain-invariant. Freezing these layers during fine-tuning prevents catastrophic forgetting of the pre-trained representations and reduces training time and GPU memory requirements.

*Trainable layers (backbone stages 6–9, neck, detection head):*
The deeper backbone stages capture high-level semantic features that are more dataset-specific. Together with the full neck and detection head, these layers are fine-tuned with a **reduced learning rate** (η = 1×10⁻⁴, approximately 10× lower than the base training rate) to preserve previously learned structure while adapting to the target distribution.

*Training schedule:*
- Warm-up: 3 epochs with linear learning rate ramp from η/10 to η
- Cosine annealing over the remaining epochs (total: 50–100 epochs depending on dataset size)
- Batch size: 16 (constrained by training hardware)
- Optimiser: SGD with momentum 0.937, weight decay 5×10⁻⁴

*Data augmentation (applied during fine-tuning):*
- Mosaic augmentation (4-image composition): improves small object detection
- Random horizontal flip (p=0.5)
- HSV colour jitter (hue ±0.015, saturation ±0.7, value ±0.4)
- Random affine transforms (scale ±50%, translate ±10%)
- Mixup augmentation (p=0.1): blends two training images and their labels

> **Note:** The fine-tuned model weights and dataset composition details are reported in Section VIII (Performance Optimisation Study). Results for the base COCO-trained model are used for all benchmarks in Sections IV and V.

**3c. Quantisation for Hailo-8L Deployment**

The Hailo-8L NPU operates on **INT8 quantised** weights and activations. The trained floating-point (FP32) model is compiled to the Hailo Executable Format (HEF) using the **Hailo Dataflow Compiler (DFC)**, which performs the following:

1. **Graph parsing:** The ONNX export of the YOLOv8s model is parsed and mapped to Hailo's internal computational graph representation.
2. **Layer fusion:** Convolution, batch normalisation, and activation layers are fused into single NPU operations, eliminating intermediate memory round-trips.
3. **Post-training quantisation (PTQ):** A calibration dataset (representative subset of the training data, minimum 64 images) is used to compute per-layer activation ranges. Weights and activations are quantised to INT8 using symmetric min-max quantisation. The normalisation transform ([0,255]→[0.0,1.0]) is fused into the first layer as described in Step 2b.
4. **HEF compilation:** The quantised graph is compiled to a binary HEF file optimised for the Hailo-8L's internal dataflow architecture.

The quantisation process introduces a small accuracy degradation (typically 0.3–1.5% mAP on COCO val2017) relative to the FP32 baseline, which is characterised in Section VIII.

**3d. NMS Post-Processing**

The raw NPU output (classification scores and raw box predictions for all spatial grid cells across P3′/P4′/P5′) is passed to `hailofilter`, which loads `libyolo_hailortpp_post.so` — a compiled shared library that performs **Non-Maximum Suppression (NMS)**:

For each class *c* and each candidate box *b* with objectness-weighted class score *s*:

$$s_{b,c} = p_{\text{obj}} \cdot p(c \mid \text{obj})$$

Boxes with $s_{b,c} < \tau_{\text{score}}$ (score threshold = **0.25**) are discarded. The remaining boxes are sorted by score in descending order. For each box $b_i$, all lower-scoring boxes $b_j$ with Intersection over Union (IoU) $> \tau_{\text{iou}}$ (IoU threshold = **0.45**) are suppressed:

$$\text{IoU}(b_i, b_j) = \frac{|b_i \cap b_j|}{|b_i \cup b_j|}$$

The surviving boxes are returned as `HAILO_DETECTION` metadata objects, each carrying: class label, confidence score, and normalised bounding box coordinates $(x_{\min}, y_{\min}, x_{\max}, y_{\max}) \in [0, 1]$.

The inference results are then synchronised with the corresponding raw frame at the `hailomuxer`, which aligns `sink_0` (bypass) and `sink_1` (inference output) into a single annotated buffer by matching buffer PTS values.

```
Input  : Scaled RGB frame (640×640, uint8)
Process: CSPDarknet backbone → PAN-FPN neck → decoupled head
         → INT8 NPU inference → NMS (score≥0.25, IoU≤0.45)
Output : HAILO_DETECTION metadata per surviving box
         (label, confidence ∈ [0,1], normalised bbox)
         Synchronised with raw 1280×720 frame via hailomuxer
```

---

#### Step 4 — Classification (Normal / Danger)

The synchronised buffer arrives at the `identity_callback` element, where a GStreamer pad probe invokes `app_callback()` on every frame. This function executes in the GStreamer streaming thread.

**4a. Camera Integrity Check (Tamper Detection)**

Before processing any detections, a frame-level integrity test is performed. The RGB frame is converted to grayscale and its **pixel intensity variance** is computed:

$$\sigma^2 = \frac{1}{N} \sum_{i=1}^{N} (I_i - \bar{I})^2$$

where $N$ is the total pixel count and $\bar{I}$ is the mean intensity. A uniformly dark or uniformly bright frame (e.g., lens covered with tape, blinding light shone at the camera) produces $\sigma^2 < 50$. If this condition persists for **300 consecutive frames** (~10 seconds at 30 fps), the system concludes the camera is physically obstructed. A `TAMPER` event is raised immediately, bypassing all operator modes including DND and Idle, and an emergency email is dispatched.

**4b. Detection Parsing and Threshold Filtering**

All `HAILO_DETECTION` objects attached to the buffer are iterated. Bounding box coordinates are denormalised to pixel coordinates:

$$x_1 = x_{\min} \cdot W, \quad y_1 = y_{\min} \cdot H, \quad x_2 = x_{\max} \cdot W, \quad y_2 = y_{\max} \cdot H$$

where $W = 1280$, $H = 720$. Each detection is then evaluated against the user-configured **detection threshold** $\tau_d$ (default: 0.35). Detections with confidence $< \tau_d$ are discarded. $\tau_d$ is tunable at runtime via the dashboard (range: 0.05–0.95), allowing the operator to trade precision against recall according to the deployment environment.

**4c. Privacy Masking**

If `MODE_PRIVACY = True`, every detection with label `person` triggers an in-place spatial filter on the bounding box region. A **Gaussian blur** with kernel size 51×51 and $\sigma = 30$ is applied using OpenCV's `cv2.GaussianBlur`. This renders faces and identifying features unrecognisable while preserving the detection bounding box overlay. The blurred frame is then JPEG-encoded at quality 75 and placed into the shared MJPEG frame buffer for dashboard streaming. The raw unblurred frame is stored separately for the WebRTC track.

**4d. Classification Decision and Consecutive-Frame Filter**

Each detection above $\tau_d$ is classified into one of three categories:

| Category | Trigger Condition | Action |
|----------|------------------|--------|
| **DANGER** | `label == DANGER_LABEL` AND `consecutive_frames ≥ 2` | Invoke alert pipeline |
| **WATCH** | `label ∈ {person, backpack, suitcase}` | Append to detection log (30 s cooldown) |
| **NORMAL** | All other labels above $\tau_d$ | Increment class counter only |

The **consecutive-frame filter** maintains a per-label counter `_label_consec_frames[label]` that increments each frame the label is seen above $\tau_d$ and resets to zero the frame it disappears. A DANGER event fires only when this counter reaches 2, requiring the threat object to be visible in at least two consecutive frames. This eliminates single-frame false positives caused by motion blur, partial occlusion, or transient reflections — common sources of spurious detections in indoor surveillance.

```
Input  : HAILO_DETECTION objects (label, confidence, bbox)
Process: variance tamper check → threshold filter → privacy blur
         → consecutive frame filter → 3-class labelling
Output : Per-detection tag: DANGER / WATCH / NORMAL
         Privacy-masked JPEG frame in MJPEG buffer
         Tamper flag raised if camera blind
```

---

#### Step 5 — Decision Logic

Before any alert is dispatched, the system evaluates a **mode state vector** and an **owner presence flag**. The mode state is a set of six boolean flags maintained in a shared global state protected by `_mode_lock` (a `threading.Lock`), ensuring atomic read-modify-write across concurrent threads.

| Mode Flag | Effect on Alert Dispatch |
|-----------|--------------------------|
| `MODE_IDLE` | Suppresses all alerts (audio + email + WS banner). Detection and logging continue. |
| `MODE_DND` | Suppresses audio alarm only. Email and WS proceed. |
| `MODE_EMAIL_OFF` | Suppresses email only. Audio and WS proceed. |
| `MODE_NIGHT` | No suppression. Email subject elevated: prepended with `HIGH PRIORITY`. All detections logged regardless of class. |
| `MODE_EMERGENCY` | No suppression. Overrides DND. Email subject elevated: `EMERGENCY`. |
| `MODE_PRIVACY` | Activates Gaussian blur on person bounding boxes (Step 4c). No effect on alert routing. |

**Owner presence:** A background thread (`_presence_poller`) scans the local ARP table every 30 seconds for the MAC addresses of registered devices (owner's phone). If a registered device is detected, `_owner_present = True`. This flag is made available to the decision layer and exposed in the dashboard state. The operator is expected to activate `MODE_IDLE` manually when present; automatic alert suppression based solely on presence is intentionally not implemented, as the operator may still want alerts even when home (e.g., during night mode). This design decision preserves operator agency over the alert policy.

**Mode scheduling:** A separate daemon thread (`_schedule_monitor`) enforces time-based mode transitions. The operator can configure start/end times (HH:MM) for any mode via the dashboard. The scheduler checks the current time every 30 seconds and applies transitions automatically. This enables, for example, automatic activation of `MODE_NIGHT` from 22:00 to 06:00 without manual intervention.

```
Input  : DANGER tag, mode state vector, owner presence flag
Output : Dispatch decision tuple: {audio: bool, email: bool, ws: bool, priority: str}
```

---

#### Step 6 — Alert Generation

On a confirmed DANGER event with at least one alert channel active, the following actions are dispatched as **daemon threads** to ensure the GStreamer callback returns within the pipeline's frame budget (~16 ms at 60 fps), avoiding buffer starvation.

**6a. Visual Alert (Dashboard)**

`_alert_active` is set to `True` and `_alert_end_time` is set to `time.time() + 3`. On every subsequent frame where the danger label remains above threshold, `_alert_end_time` is extended by 3 seconds. The alert therefore remains active continuously while the threat is visible and expires **3 seconds after the threat leaves frame**. The FastAPI state endpoint and WebSocket broadcaster read `_alert_active` under `_alert_lock` (a separate lock from `_mode_lock`) to serve the alert banner to connected dashboard clients.

**6b. Audio Alarm**

On the **rising edge** of the alert (first triggering frame only), `subprocess.Popen(["aplay", ...])` is called in a non-blocking subprocess. This fires once per alert event, not once per frame, preventing continuous audio repetition during sustained detections.

**6c. Email Notification**

`send_email_alert()` opens an SMTP-SSL connection to Gmail on port 465, authenticates using an application-specific password stored in the `.env` file (never in `config.json`), and sends an alert email to the configured recipient list. A thread-safe **60-second cooldown** (enforced via `_email_lock` and comparing `time.time()` against `last_email_sent_time`) prevents alert spam during sustained detections. The email subject is dynamically composed:

- Normal: `"Scissors Detected Alert"`
- Night mode: `"HIGH PRIORITY: Scissors Detected Alert"`
- Emergency mode: `"EMERGENCY: Scissors Detected Alert"`

**6d. WebSocket Push**

`push_urgent_ws()` uses `asyncio.call_soon_threadsafe()` to signal the async WebSocket broadcaster event loop from the synchronous GStreamer thread. This causes the broadcaster to immediately push the current system state JSON to all connected WebSocket clients, delivering the alert banner to the dashboard within one network round-trip of the detection event.

```
Input  : Dispatch decision tuple, _danger_trigger_info snapshot
Output : _alert_active flag set (3 s sliding timer)
         Audio subprocess (rising edge only)
         Email sent (60 s cooldown)
         Async WS push → alert banner on all connected clients
```

---

#### Step 7 — Logging and Persistence

Every detection, alert, mode change, login, and system event is recorded through a **two-tier persistence architecture** designed specifically for the constraints of SSD-based embedded storage — minimising write amplification while guaranteeing zero event loss.

**7a. Tier 1 — RAM Buffer (Write Coalescing)**

All text log writes accumulate in `_log_buffer`, a `defaultdict(list)` mapping file paths to lists of pending log lines. This buffer is protected by `_log_buffer_lock`. A background daemon thread (`_flush_log_thread`) sleeps for **60 seconds**, then acquires the lock, snapshots the buffer, clears it, releases the lock, and writes all pending lines to their respective files on the SSD in a single sequential write per file. This pattern reduces the write frequency from once-per-event (potentially hundreds per minute) to once per 60 seconds, dramatically reducing write amplification and extending SSD longevity.

Log files are **rotated at 10 MB** — the file is renamed to `<name>.1` and a fresh file begins. At most two rotation files are kept per log.

**7b. Tier 2 — SQLite Event Queue (Structured Persistence)**

Every detection event is also inserted into `garuda_events.db` via `queue_event()`, which opens a SQLite connection under `_eq_lock`. Each row stores: ISO timestamp, event type, label, confidence, info string, and a `synced` flag (default 0). This database serves as the **offline-resilient event store**: events accumulate regardless of network state and are available for historical review on the dashboard at any time.

The schema includes two indexes — on `synced` (for pending-count queries) and on `timestamp` (for range queries) — ensuring $O(\log n)$ retrieval even with large event histories.

**7c. Atomic JSON Writes (Critical State)**

User accounts, system configuration, and alert history are written using the **atomic write pattern**:

```
1. os.makedirs(dir, exist_ok=True)
2. fd, tmp_path = tempfile.mkstemp(dir=dir, suffix=".tmp")
3. json.dump(data, fd); fd.flush(); os.fsync(fd.fileno())
4. os.replace(tmp_path, target_path)   ← atomic on POSIX
```

`os.replace` is atomic on POSIX filesystems — the target path transitions instantaneously from the old file to the new, with no window where the file is missing or partially written. Combined with `fsync`, this guarantees that a hard power-off at any point leaves the state file either fully old or fully new — never corrupt.

```
Input  : Detection / system / mode change event
Output : Line appended to RAM buffer (→ SSD flush every 60 s)
         Row inserted into garuda_events.db (SQLite, indexed)
         Atomic JSON file update for users / config / alert_history
```

---

---

## Section III — Mathematical Modelling

This section develops formal models for the four measurable behaviours of the Garuda system: (1) detection performance, (2) end-to-end alert latency, (3) context-aware alert suppression, and (4) tamper detection. Where closed-form expressions exist, they are derived from first principles. Where system parameters are empirically determined, the measurement methodology is stated.

---

### 3.1 Detection Performance Model

The system classifies each frame independently. For the binary case — danger-object present or absent — each frame constitutes a single trial. We define the confusion matrix at frame level as follows. Let $y \in \{0, 1\}$ denote the ground-truth label for a frame ($y=1$ if the danger object is physically present in the scene) and let $\hat{y}(\tau_d) \in \{0,1\}$ denote the system's decision given detection threshold $\tau_d$:

$$\hat{y}(\tau_d) = \begin{cases} 1 & \text{if } \exists \, b \in \mathcal{D}_t : \ell(b) = \text{DANGER} \wedge \text{conf}(b) \geq \tau_d \\ 0 & \text{otherwise} \end{cases}$$

where $\mathcal{D}_t$ is the set of bounding-box detections returned by the NMS stage at frame $t$, $\ell(b)$ is the class label of detection $b$, and $\text{conf}(b) \in [0,1]$ is its confidence score.

The frame-level confusion counts are:

$$\text{TP} = \sum_t \mathbf{1}[y_t=1 \wedge \hat{y}_t=1], \quad \text{FP} = \sum_t \mathbf{1}[y_t=0 \wedge \hat{y}_t=1]$$
$$\text{FN} = \sum_t \mathbf{1}[y_t=1 \wedge \hat{y}_t=0], \quad \text{TN} = \sum_t \mathbf{1}[y_t=0 \wedge \hat{y}_t=0]$$

The standard detection metrics are then:

$$\text{Precision}(\tau_d) = \frac{\text{TP}}{\text{TP} + \text{FP}}, \qquad \text{Recall}(\tau_d) = \frac{\text{TP}}{\text{TP} + \text{FN}}$$

$$F_1(\tau_d) = \frac{2 \cdot \text{Precision} \cdot \text{Recall}}{\text{Precision} + \text{Recall}}$$

The **False Alert Rate (FAR)** — the fraction of danger-absent frames that trigger a false positive — is:

$$\text{FAR}(\tau_d) = \frac{\text{FP}}{\text{FP} + \text{TN}}$$

Because FAR and Recall move in opposite directions as $\tau_d$ varies, the operating threshold is chosen to minimise $\text{FAR}$ subject to $\text{Recall} \geq 0.90$ — a constraint that ensures no real threat is missed more than 10% of the time. The resulting $\tau_d^*$ is determined empirically over the evaluation dataset (Section IV).

#### 3.1.1 Effect of the Consecutive-Frame Filter on FAR

The consecutive-frame filter (Section 2.2, Step 4d) conditions a DANGER alert on the danger label being present in at least $k=2$ successive frames. Modelling per-frame false detections as independent Bernoulli trials with probability $p = \text{FAR}(\tau_d)$, the probability that a spurious alert fires on any given pair of consecutive frames is:

$$P_{\text{spurious}}(k=2) = p^2$$

For the base FAR of $p = 0.05$ (5% per-frame FP rate at $\tau_d = 0.35$):

$$P_{\text{spurious}} = (0.05)^2 = 0.0025$$

This is a 20× reduction in spurious alert rate relative to a single-frame trigger. More generally, for filter length $k$:

$$P_{\text{spurious}}(k) = p^k$$

At $k=3$, $P_{\text{spurious}} = 1.25 \times 10^{-4}$ — a further 20× reduction, but with a corresponding increase in true-alert latency of one additional frame period ($\approx 16.7$ ms at 60 fps). The choice of $k=2$ balances latency against false-alert rate.

**Reset behaviour.** The consecutive counter resets to zero on any frame where the label is absent. If spurious detections occur at random with probability $p$ per frame, the time $T_{\text{reset}}$ until the counter resets follows a geometric distribution:

$$P(T_{\text{reset}} = n) = (1-p)^{n-1} p, \quad E[T_{\text{reset}}] = \frac{1}{p}$$

For $p = 0.05$, the expected inter-spurious-frame gap is 20 frames (~0.33 s at 60 fps), making back-to-back false detections improbable even without the consecutive filter.

---

### 3.2 End-to-End Alert Latency Model

The total alert latency $T_{\text{total}}$ is the elapsed time from the moment the danger object first appears in frame to the moment the alert is observable by the operator. The pipeline introduces latency at each processing stage. Formally:

$$T_{\text{total}} = T_{\text{cap}} + T_{\text{queue}} + T_{\text{infer}} + T_{\text{nms}} + T_{\text{cb}} + T_{\text{dispatch}}$$

where each component is defined and bounded as follows.

**$T_{\text{cap}}$ — Frame period.** The camera operates at 60 fps, so each new frame is produced every:

$$T_{\text{cap}} = \frac{1}{f_{\text{cam}}} = \frac{1}{60} \approx 16.7 \text{ ms}$$

In the worst case, the threat first appears immediately after a frame is captured, so the next captured frame is the first opportunity for detection — contributing a maximum latency of one frame period.

**$T_{\text{queue}}$ — Pipeline queue delay.** The `bypass_queue` can hold up to $Q_{\max} = 20$ buffers. If the queue is full (the inference path is slower than the source rate), a newly captured frame waits up to $Q_{\max} \cdot T_{\text{cap}}$ before reaching the muxer. In practice the Hailo-8L processes frames faster than the queue fills, so $T_{\text{queue}} \approx 0$ under normal load.

**$T_{\text{infer}}$ — NPU inference time.** The Hailo-8L executes the compiled YOLOv8s HEF. Measured inference time on the Hailo-8L for a 640×640 input is approximately 8–12 ms (reported by `hrtimestamp` profiling in the DFC tools). The mean is denoted $\bar{T}_{\text{infer}}$.

**$T_{\text{nms}}$ — NMS post-processing.** Executed in `hailofilter` on CPU. NMS over $N_{\text{boxes}}$ raw predictions has complexity $O(N_{\text{boxes}} \log N_{\text{boxes}})$ for the sort step plus $O(N_{\text{boxes}}^2)$ worst case for IoU evaluation (though practically $O(N_{\text{boxes}} \cdot K)$ where $K$ is the mean number of surviving boxes). For YOLOv8s at 80 classes across the P3/P4/P5 feature maps, $N_{\text{boxes}} \approx 8400$ before score filtering. With score threshold 0.25, the working set is typically $< 100$ boxes, and measured $T_{\text{nms}} < 1$ ms.

**$T_{\text{cb}}$ — Python callback execution.** `app_callback()` runs in the GStreamer streaming thread. It performs: grayscale variance computation ($O(WH)$), detection iteration ($O(K)$), optional Gaussian blur ($O(WH)$ per bounding box, only in privacy mode), and JPEG encode. At 1280×720 resolution, grayscale variance and JPEG encode together measure approximately 5–12 ms per frame using OpenCV (varies with scene content and CPU load).

**$T_{\text{dispatch}}$ — Alert dispatch.** Alert dispatch is split into three parallel daemon threads. The binding constraint is the slowest channel needed to confirm the alert:

$$T_{\text{dispatch}} = \min\left(T_{\text{ws}},\, T_{\text{audio}}\right)$$

since the operator observes whichever channel arrives first. The WebSocket push uses `asyncio.call_soon_threadsafe()`, which delivers the state JSON within the next event loop tick — effectively $T_{\text{ws}} \approx$ one network round-trip to the browser (< 100 ms on LAN, < 500 ms over Cloudflare tunnel). Audio `aplay` via subprocess launch is effectively instantaneous ($T_{\text{audio}} \approx 0$ for the subprocess start; audio onset is ~50 ms thereafter). Email dispatch ($T_{\text{email}} \approx$ 1–3 s for SMTP-SSL handshake + transmission) is the slowest channel but is off the critical path for immediate detection.

**Total latency (LAN scenario, alert to dashboard):**

$$T_{\text{total}} \approx T_{\text{cap}} + \bar{T}_{\text{infer}} + T_{\text{nms}} + T_{\text{cb}} + T_{\text{ws}}$$
$$\approx 16.7 + 10 + 1 + 8 + 5 = \mathbf{40.7 \text{ ms}}$$

This is the expected alert-to-dashboard latency under nominal load on a LAN connection. The consecutive-frame filter adds $k-1 = 1$ additional frame period before the alert is raised:

$$T_{\text{total, filter}} = T_{\text{total}} + (k-1) \cdot T_{\text{cap}} = 40.7 + 16.7 \approx \mathbf{57.4 \text{ ms}}$$

This is well under the 500 ms perceptual threshold for real-time response. Empirical validation of these estimates is reported in Section IV.

---

### 3.3 Context-Aware Alert Suppression Model

Garuda's alert decision is not a simple threshold on detection confidence; it is a function of the full system state. We formalise this as a **suppression predicate** $S(t)$, which determines whether an otherwise valid DANGER detection at frame $t$ is suppressed before dispatch.

Let the mode state at time $t$ be the vector:

$$\mathbf{M}(t) = \bigl(\texttt{IDLE}, \texttt{DND}, \texttt{EMAIL\_OFF}, \texttt{NIGHT}, \texttt{EMERGENCY}, \texttt{PRIVACY}\bigr) \in \{0,1\}^6$$

Let $\mathcal{C} = \{\texttt{audio}, \texttt{email}, \texttt{ws}\}$ be the set of alert channels. Each mode flag acts as a suppressor on a specific subset of channels:

$$\text{Suppress}_\texttt{IDLE}(\mathcal{C}) = \mathcal{C}$$
$$\text{Suppress}_\texttt{DND}(\mathcal{C}) = \{\texttt{audio}\}$$
$$\text{Suppress}_\texttt{EMAIL\_OFF}(\mathcal{C}) = \{\texttt{email}\}$$

Emergency mode overrides DND:

$$\texttt{EMERGENCY} = 1 \implies \text{Suppress}_\texttt{DND} = \varnothing$$

The active channel set at time $t$ is:

$$\mathcal{C}_{\text{active}}(t) = \mathcal{C} \;\setminus\; \bigcup_{m \in \mathbf{M}(t)} \text{Suppress}_m(\mathcal{C})$$

The **per-channel alert indicator** for channel $c$ is:

$$A_c(t) = \mathbf{1}\bigl[D(t) \geq \tau_d\bigr] \cdot \mathbf{1}\bigl[f(t) \geq 2\bigr] \cdot \mathbf{1}\bigl[c \in \mathcal{C}_{\text{active}}(t)\bigr]$$

where $D(t)$ is the maximum confidence of any DANGER-labelled detection at frame $t$, and $f(t)$ is the consecutive-frame count. The system generates an alert event if and only if:

$$\exists \, c \in \mathcal{C} : A_c(t) = 1$$

**Tamper exception.** Camera tamper events bypass the suppression predicate entirely. The tamper alert is dispatched to all channels regardless of $\mathbf{M}(t)$:

$$A_c^{\text{tamper}}(t) = \mathbf{1}\bigl[\sigma^2_t < 50 \text{ for } N_{\text{persist}} \text{ consecutive frames}\bigr]$$

This hard override is necessary because a covered camera is an attack on the detection system itself, not a detection event.

#### 3.3.1 Alert Rate Under Mode Scheduling

Let $\lambda$ denote the rate of valid DANGER detections per unit time (events per hour). Under mode scheduling, suppose Night mode is active for a fraction $r_N$ of time and Idle mode for a fraction $r_I$. The expected alert delivery rate to the email channel is:

$$\lambda_{\text{email}} = \lambda \cdot \bigl(1 - r_I\bigr) \cdot \bigl(1 - r_{\text{EMAIL\_OFF}}\bigr)$$

For a typical residential deployment (Idle mode during daytime working hours, Night mode 22:00–06:00):

$$r_I \approx \frac{9}{24} = 0.375, \quad r_N \approx \frac{8}{24} = 0.333$$

The email alert rate during Idle periods is zero. During Night mode, the email subject carries `HIGH PRIORITY` but the channel is not suppressed — so $\lambda_{\text{email}}$ is unaffected by Night mode itself. Only the Email-Off flag directly reduces $\lambda_{\text{email}}$.

---

### 3.4 Tamper Detection — Statistical Threshold Derivation

The camera tamper detector relies on the per-frame intensity variance as a proxy for visual information content. For a legitimate scene with spatial structure (furniture, walls, doorways), the variance $\sigma^2$ is in the range of hundreds to thousands of digital-number (DN) squared units. A covered or blinded camera produces a nearly uniform frame, driving $\sigma^2$ close to zero.

Formally, let $I_i$ denote the grayscale intensity of pixel $i \in \{1, \ldots, N\}$ where $N = W \cdot H = 1280 \times 720 = 921{,}600$:

$$\bar{I} = \frac{1}{N}\sum_{i=1}^N I_i, \qquad \sigma^2 = \frac{1}{N}\sum_{i=1}^N \left(I_i - \bar{I}\right)^2$$

The tamper threshold $\Theta = 50$ DN² is a conservative lower bound. To contextualise this: a uniformly grey frame at intensity $\mu$ with additive Gaussian read noise $\sigma_r \sim \mathcal{N}(0, \sigma_r^2)$ would yield a measured variance of:

$$E[\hat{\sigma}^2] = \sigma_r^2$$

Camera sensor read noise for the IMX708 at ISO 100 is approximately 1.5–2.5 DN, so $\sigma_r^2 \approx 2.25$–$6.25$ DN² under normal sensor noise. Any scene with measurable structure will produce $\sigma^2 \gg 10$ DN². The threshold $\Theta = 50$ DN² thus provides a margin of roughly $20\times$ above sensor-noise-only variance, making the detector robust to low-light scenes without generating tamper false positives.

**Persistence window.** A single low-variance frame does not trigger a tamper event. The system requires $N_{\text{persist}} = 300$ consecutive low-variance frames (approximately 10 seconds at 30 fps). This temporal filter rejects transient events (a hand briefly passing in front of the lens, a sudden fade to black during scene transitions) and ensures the tamper event fires only when the camera is persistently obstructed.

The probability of $N_{\text{persist}}$ consecutive frames all independently falling below $\Theta$ by chance — in an unobstructed scene where the per-frame probability of $\sigma^2 < 50$ is $p_{\Theta}$ — is:

$$P_{\text{false tamper}} = p_{\Theta}^{N_{\text{persist}}}$$

Empirically, for a surveillance scene with normal indoor content, $p_{\Theta} < 10^{-4}$ (the camera would need to produce an essentially uniform frame), so:

$$P_{\text{false tamper}} < \left(10^{-4}\right)^{300} \approx 0$$

This establishes that the tamper detector has an effectively zero false-positive rate for unobstructed scenes.

---

### 3.5 ARP-Based Owner Presence: A Reliability Model

The presence detection subsystem polls the local ARP table every $T_{\text{poll}} = 30$ seconds. Before reading the ARP cache, it triggers an ARP refresh by sending UDP probes to all hosts on the local subnet (port 9 — discard). This forces up-to-date ARP entries and reduces the stale-entry false-negative rate.

Let $p_{\text{detect}}$ be the probability that a single ARP poll correctly identifies the owner's device as present, given that the device is on the network. Define the **away grace period** as $G = 90$ s (3 missed polls). The device is declared absent only if $\lceil G / T_{\text{poll}} \rceil = 3$ consecutive polls fail to observe its MAC address.

Modelling individual poll failures as independent Bernoulli events with miss probability $q = 1 - p_{\text{detect}}$, the probability of a false-absent declaration (device is present but missed for 3 consecutive polls) is:

$$P_{\text{false-absent}} = q^3$$

For a reliable WiFi environment with $p_{\text{detect}} = 0.97$ (3% per-poll miss probability due to ARP cache age or brief connectivity interruption), $q = 0.03$:

$$P_{\text{false-absent}} = (0.03)^3 = 2.7 \times 10^{-5}$$

This is negligibly small — one false-absent event expected per approximately 37,000 polls, equivalent to roughly 12.8 days of continuous polling. The grace period thus provides strong immunity against transient WiFi interruptions while keeping the declared-absent latency bounded at $G = 90$ s after the device genuinely leaves the network.

**Effect on alert rate.** The `_owner_present` flag is exposed to the dashboard and the operator, but alert suppression based on presence is intentionally not automated (Section 2.2, Step 5). The practical benefit of presence detection is therefore indirect: it gives the operator a real-time signal to activate Idle mode when appropriate, reducing the effective false alert rate through informed manual action rather than autonomous suppression. This design choice — preserving operator agency — is discussed further in Section VI.

---

*[Section IV — Experimental Validation to follow]*
