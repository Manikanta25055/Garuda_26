# Project Garuda

**A privacy-preserving, edge-AI home security system running entirely on-device — no cloud dependency, no data egress.**

Developed for and described in the IEEE Access submission:
> *"Garuda: A Privacy-Preserving Edge-AI Home Security System with Real-Time Object Detection and Adaptive Threat Classification"*

Hardware platform: **Raspberry Pi 5 + Hailo-8L AI HAT** (26 TOPS neural processing)

---

## For IEEE Reviewers

| What you need | Where to find it |
|---|---|
| Paper (LaTeX + PDF) | [`docs/IEEE Access Paper/`](docs/IEEE%20Access%20Paper/) |
| System architecture diagrams | [`docs/diagrams/`](docs/diagrams/) |
| Evaluation data and scripts | [`evaluation/`](evaluation/) |
| Performance benchmark logs | [`docs/reports/`](docs/reports/) |
| Hardware setup photos | [`docs/hardware/`](docs/hardware/) |
| Web dashboard screenshots | [`docs/Web_UI_pics/`](docs/Web_UI_pics/) |
| Main application code | [`basic_pipelines/Garuda_web.py`](basic_pipelines/Garuda_web.py) |
| Cascade pipeline | [`basic_pipelines/garuda_cascade.py`](basic_pipelines/garuda_cascade.py) |
| Test suite | [`tests/`](tests/) |

---

## Repository Structure

```
Garuda_26/
├── basic_pipelines/
│   ├── Garuda_web.py           # Main system — detection pipeline + web server (~3100 lines)
│   ├── garuda_cascade.py       # Cascade detection pipeline
│   ├── hailo_rpi_common.py     # GStreamer base classes
│   ├── detection.py            # Standalone detection example
│   ├── pose_estimation.py      # Standalone pose estimation
│   ├── instance_segmentation.py
│   └── garuda_web/             # Web dashboard static assets (HTML/CSS/JS)
│
├── docs/
│   ├── IEEE Access Paper/      # Paper source, revision plans, literature
│   ├── diagrams/               # System block diagrams and flowcharts (.drawio + .txt)
│   ├── reports/                # Performance benchmarks, model evaluation, LaTeX reports
│   ├── hardware/               # Hardware setup photos and PDF
│   └── Web_UI_pics/            # Dashboard screenshots
│
├── evaluation/                 # Evaluation scripts and raw results
│   ├── eval_36h.py             # 36-hour continuous run evaluation
│   ├── fps_timeseries/         # FPS trace data
│   ├── int8_eval/              # INT8 quantisation evaluation
│   ├── openset_eval/           # Open-set detection evaluation
│   ├── antispoof_eval/         # Anti-spoof evaluation
│   ├── person_recall_eval/     # Person recall evaluation
│   ├── power_measurement/      # Power draw measurements
│   └── out/                    # Generated plots and result files
│
├── Garuda/                     # iOS / macOS native companion app (SwiftUI)
├── cpp/                        # C++ YOLO post-processing (Hailo NMS decoder)
├── resources/                  # Model HEF files, sample video, label JSONs
├── scripts/                    # Utility scripts (startup, metrics monitor, systemd service)
├── tests/                      # pytest suite (auth, API, pipeline, concurrency)
├── stress_tests/               # Long-duration and load tests
├── setup_env.sh                # Environment setup — MUST be sourced before running
├── requirements.txt
└── meson.build                 # C++ post-processing build file
```

---

## System Overview

Project Garuda is a home security system where all inference, streaming, authentication, and alerting happen on the edge device. No video or detection data is sent to any cloud service.

### Key contributions (paper claims)

- **27 FPS** real-time detection on Hailo-8L at **~6.8 W** system power
- **Two-tier threat model**: general intrusion vs. elevated-threat objects (configurable COCO-class list)
- **Privacy-preserving architecture**: all processing local; no cloud account or internet required for core operation
- **Cascade pipeline** (`garuda_cascade.py`): lightweight pre-filter reduces full-model invocations by ~40%
- **Adaptive mode switching**: Active / DND / Idle / Night Mode with schedule-based transitions
- Local voice assistant (**Narada**) — offline, no external API required for core commands
- ARP-based occupant presence detection for alert suppression

### Models evaluated

| Model | Task | Notes |
|---|---|---|
| YOLOv6n | Detection | Default; fastest |
| YOLOv8s | Detection | Best accuracy |
| YOLOx-S | Detection | Paper ablation |
| YOLOv8s Pose | Pose estimation | Supplementary |
| YOLOv5n Seg | Instance segmentation | Supplementary |

---

## Architecture

```
Camera / File Input
       │
       ▼
GStreamer Pipeline (hailo_rpi_common.py)
  videoscale → videoconvert → hailomuxer
                                    │
              ┌─────────────────────┴──────────────────────┐
              │                                            │
         bypass_queue                          hailonet (Hailo-8L inference)
              │                                            │
              │                               hailofilter (post-process / NMS)
              │                                            │
              └──────────────────┬─────────────────────────┘
                                 │
                        identity callback → app_callback()
                                 │
                        hailooverlay (draw bounding boxes)
                                 │
                  ┌──────────────┴──────────────┐
                  │                             │
           Display / MJPEG               Garuda_web.py
             encoder                    FastAPI server
                                        REST API | WebSocket | WebRTC
                                        Auth | Modes | Logging | Clips
                                              │
                        ┌─────────────────────┼──────────────┐
                        │                     │              │
                  Web Dashboard           iOS App       API clients
                  (PWA, port 8080)        (SwiftUI)
```

---

## Reproducing the Evaluation

### Prerequisites

- Raspberry Pi 5 with Hailo-8L AI HAT
- HailoRT + TAPPAS 3.28.x or 3.31.0
- Python 3.10+

### Setup

```bash
git clone https://github.com/Manikanta25055/Garuda_26.git
cd Garuda_26

# Sets up virtualenv, TAPPAS env vars, DEVICE_ARCHITECTURE
source setup_env.sh

pip install -r requirements.txt
./download_resources.sh
```

### Run the main system

```bash
source setup_env.sh
python basic_pipelines/Garuda_web.py
# Dashboard available at http://<device-ip>:8080
```

### Run evaluation scripts

```bash
source setup_env.sh

# 36-hour continuous evaluation (generates FPS timeseries, event counts)
python evaluation/eval_36h.py

# Results are written to evaluation/out/
```

### Run tests

```bash
pip install -r requirements-test.txt
pytest tests/ -v
```

### (Optional) Compile C++ post-processing for custom models

```bash
meson setup build.release --buildtype=release
ninja -C build.release
```

---

## Environment Variables

Create a `.env` file in the project root (not committed):

```bash
# Email alerts (Gmail + App Password)
ALERT_EMAIL=your_email@gmail.com
ALERT_EMAIL_PASSWORD=your_app_password
ALERT_RECIPIENT=recipient@gmail.com

# Session security
SECRET_KEY=your_random_secret_key_here
SECURE_COOKIES=false   # set true when running behind HTTPS

# Optional: Groq API for conversational dashboard
GROQ_API_KEY=your_groq_api_key
```

---

## Security Architecture

```
Incoming request
       │
  Rate limiter          (per-IP, blocks brute force)
       │
  Security headers      (CSP, X-Frame-Options, Referrer-Policy)
       │
  Session validation    (JWT, TTL, per-user invalidation)
       │
  Role check            (User | Admin)
       │
  Admin tier ────────── Email OTP required (2FA, no third-party app)
       │
  Resource
```

- Passwords: bcrypt-hashed with strength validation
- Admin 2FA: email OTP — two independent factors, no external service
- Master key: emergency recovery with separate OTP gate
- Transport: run behind HTTPS in production

---

## Web Dashboard

Served at `http://<device-ip>:8080`. Installable as a Progressive Web App.

Pages: Live camera feed, Detection logs, Mode control, User management, Device presence, Voice/chat, Settings, Email configuration.

Screenshots: [`docs/Web_UI_pics/`](docs/Web_UI_pics/)

---

## iOS Companion App

Located in [`Garuda/`](Garuda/) as an Xcode project.

Requirements: Xcode 15+, iOS 16+ / macOS 13+

Features: Live MJPEG stream, WebSocket detection events, role-based login with email OTP, full API access.

---

## Remote Access

```bash
# Cloudflare Tunnel (free)
cloudflared tunnel --url http://localhost:8080

# ngrok
ngrok http 8080
```

---

## Operating Modes

| Mode | Detection | Alerts sent | Log target |
|---|---|---|---|
| Active | Full | Yes | `detections.jsonl` |
| DND | Full | No (silent) | `detections.jsonl` |
| Idle | Reduced | No | — |
| Night Mode | Adjusted | Yes | `night_mode_findings.txt` |
| Email-Off | Full | No email | `detections.jsonl` |

Modes switch via dashboard, iOS app, voice command, or auto-schedule.

---

## License

MIT — see [LICENSE](LICENSE).

Built on the [Hailo RPi5 Examples](https://github.com/hailo-ai/hailo-rpi5-examples) framework.
