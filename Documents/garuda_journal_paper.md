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

*[Section II — System Architecture to follow]*
