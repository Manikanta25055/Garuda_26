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

The Sony IMX708 camera is accessed via `libcamerasrc`, the GStreamer source element for the Raspberry Pi camera stack. The sensor captures frames at **1280 × 720 resolution at 60 fps** in RGB format. Each frame is passed downstream as a GStreamer buffer.

```
Input  : Physical light → Sony IMX708 CMOS sensor
Output : GStreamer buffer (RGB, 1280×720, 60 fps)
```

---

#### Step 2 — Preprocessing

The raw frame undergoes two sequential transformations before inference:

1. **Scaling** — `videoscale` resizes the frame from 1280×720 to the network input resolution of **640×640 pixels**.
2. **Format conversion** — `videoconvert` ensures the pixel format matches the model's expected input (RGB, 8-bit per channel).

The preprocessed frame then enters a `tee` element, which splits the stream into two parallel branches:

- **Branch A (Bypass)** — The raw frame passes directly to the `hailomuxer` input `sink_0` without modification.
- **Branch B (Inference)** — The frame is routed through the Hailo-8L NPU for AI inference.

```
Input  : GStreamer buffer (RGB, 1280×720)
Output : Two parallel buffers — raw (1280×720) and scaled (640×640)
```

---

#### Step 3 — Object Detection (Hailo-8L NPU)

Branch B is processed by `hailonet`, the GStreamer element that submits the scaled frame to the Hailo-8L NPU. The NPU executes the quantized model compiled to the **Hailo Executable Format (HEF)**. Three models are supported: YOLOv8s (default), YOLOv6n, and YOLOx-S.

The raw network output is then passed to `hailofilter`, which runs the post-processing shared library (`libyolo_hailortpp_post.so`). This performs **Non-Maximum Suppression (NMS)** with the following parameters:

- NMS score threshold: **0.25**
- IOU threshold: **0.45**
- Output format: FLOAT32

The inferred detection results are then synchronised with the corresponding raw frame at the `hailomuxer`, which aligns `sink_0` (bypass) and `sink_1` (inference output) into a single annotated buffer.

```
Input  : Scaled RGB frame (640×640)
Process: Hailo-8L NPU → YOLOv8s HEF inference → NMS post-processing
Output : Annotated GStreamer buffer with HAILO_DETECTION metadata objects
         (bounding box, label string, confidence score per detection)
```

---

#### Step 4 — Classification (Normal / Danger)

The annotated buffer arrives at the `identity_callback` element, where a GStreamer pad probe invokes `app_callback()` on every frame. Inside this callback, the following classification logic executes:

**4a. Camera Integrity Check**

Before processing detections, the frame's grayscale variance is computed. If variance falls below **50** for **300 consecutive frames** (~10 seconds at 30 fps), the camera is classified as **blind** (lens covered or obstructed) and a tamper alert is raised immediately, bypassing all operator modes.

**4b. Detection Parsing**

All `HAILO_DETECTION` metadata objects attached to the buffer are iterated. Each detection is evaluated against the user-configured **detection threshold** (default: 0.35). Detections below this threshold are discarded.

**4c. Privacy Masking**

If **Privacy Mode** is active, every detection with label `person` triggers an in-place **Gaussian blur** (kernel 51×51, σ=30) applied to the corresponding bounding box region of the frame. The blurred frame is re-encoded to JPEG (quality 75) and placed into the shared MJPEG frame buffer.

**4d. Classification Decision**

Each detection above threshold is classified into one of three categories:

| Category | Condition | Action |
|----------|-----------|--------|
| **DANGER** | label == `DANGER_LABEL` (default: scissors) AND ≥ 2 consecutive frames | Trigger alert pipeline |
| **WATCH** | label ∈ {person, backpack, suitcase} | Silent log with 30 s cooldown |
| **NORMAL** | All other labels above threshold | Count only; no log |

The consecutive-frame requirement for DANGER events acts as a **false positive filter** — a single-frame detection does not trigger an alert.

```
Input  : HAILO_DETECTION objects (label, confidence, bounding box)
Output : Classification tag per detection — DANGER / WATCH / NORMAL
         Privacy-masked JPEG frame in shared MJPEG buffer
```

---

#### Step 5 — Decision Logic

Before any alert is dispatched, the system evaluates the current **operator mode state** and **owner presence**:

| Condition | Outcome |
|-----------|---------|
| `MODE_IDLE = True` | All alerts suppressed |
| `MODE_DND = True` | Audio alarm suppressed; email proceeds |
| `MODE_EMAIL_OFF = True` | Email suppressed; audio proceeds |
| `MODE_NIGHT = True` | Alert proceeds; email subject elevated to HIGH PRIORITY |
| `MODE_EMERGENCY = True` | Alert proceeds; email subject elevated to EMERGENCY; overrides DND |
| Owner present (ARP match) | Alert proceeds normally; operator can suppress via Idle mode |

Mode state is protected by a threading lock (`_mode_lock`) to prevent race conditions between the GStreamer callback thread and the FastAPI request threads that modify modes.

```
Input  : Classification tag (DANGER), current mode state, owner presence flag
Output : Dispatch decision — which alert channels are active for this event
```

---

#### Step 6 — Alert Generation

On a confirmed DANGER event with alerts enabled, the following actions execute in parallel daemon threads to avoid blocking the GStreamer pipeline:

**6a. Software Alert**
`trigger_software_alert()` sets `_alert_active = True` and extends a 3-second expiry timer. The timer resets on every frame where the danger label remains visible, keeping the alert active continuously while the threat is present. The alert clears 3 seconds after the label disappears from frame.

**6b. Audio Alarm**
`aplay` plays the system alarm sound (`Front_Center.wav`) on the rising edge of the alert (first triggering frame only).

**6c. Email Notification**
`send_email_alert()` sends an email via Gmail SMTP-SSL (port 465) with the detection timestamp and label. A **60-second cooldown** prevents alert spam during sustained detections. The email subject reflects the current mode: standard, HIGH PRIORITY (night), or EMERGENCY.

**6d. WebSocket Push**
`push_urgent_ws()` signals the async WebSocket broadcaster to immediately push the updated system state to all connected dashboard clients, ensuring the alert banner appears on the operator's screen within milliseconds.

```
Input  : Confirmed DANGER event, dispatch decision
Output : Active alert flag (3 s timer), audio playback, email sent, WS push to clients
```

---

#### Step 7 — Logging and Persistence

Every event — detection, alert, mode change, login, system action — is recorded through a two-tier persistence system designed for the constraints of embedded storage:

**Tier 1 — RAM Buffer (low-latency writes)**
All text log entries are written into an in-memory buffer (`_log_buffer`, a `defaultdict(list)`) protected by a threading lock. A background daemon thread flushes this buffer to the 1 TB SSD every **60 seconds**, eliminating per-event disk I/O and significantly reducing write amplification.

**Tier 2 — SQLite Event Queue (structured, queryable)**
Every detection event is also inserted into `garuda_events.db` with a `synced` flag. This database serves as the offline-resilient event store — it retains all events regardless of network state, and the dashboard queries it for historical review once connectivity is restored.

**Critical state** (user accounts, configuration, alert history) is written using an **atomic JSON write** pattern: data is written to a temporary file, flushed with `fsync`, then renamed into place with `os.replace`. This guarantees file integrity on unexpected power loss.

```
Input  : Detection event, system event, mode change
Output : Entry in RAM log buffer (→ SSD every 60 s)
         Row in SQLite garuda_events.db
         Atomic JSON update for critical state files
```

---

*[Section III — Mathematical Modelling to follow]*
