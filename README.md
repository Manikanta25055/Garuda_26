# Project Garuda

**A privacy-preserving, edge-AI home security system running entirely on-device — no cloud dependency, no data egress.**

Developed for and described in the IEEE Access submission:
> *"Garuda: A Privacy-Preserving Edge-AI Home Security System with Real-Time Object Detection and Adaptive Threat Classification"*

Hardware platform: **Raspberry Pi 5 + Hailo-8L AI HAT** (26 TOPS neural processing)

**Live demo:** [garuda.veeramanikanta.in](https://garuda.veeramanikanta.in)
NOTE: backend is served through api.veeramanikanta.in  
Credentails - user, user  


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
| Training datasets + trained HEF models | S3 (RunPod) — see [Datasets & Models](#datasets--models) below |
| Live system demo | [garuda.veeramanikanta.in](https://garuda.veeramanikanta.in) |

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

1. **Integration architecture** — asynchronous, CPU-pinned, bounded-queue design co-locating a primary detector, a secondary MobileNetV2 verifier, and a MiDaS depth-variance pre-filter on one 13 TOPS Hailo-8L device; FastAPI web stack, voice assistant, authentication, remote-access, encrypted-evidence, and logging services run concurrently on the same Raspberry Pi 5, with NPU and CPU paths engineered to be non-interfering by construction.

2. **INT8 deployment characterisation** — four-class domestic-threat YOLOv8s compiled to a 22.9 MB HEF binary, re-evaluated on-chip on the held-out test split: **mAP@0.5 = 0.854**, **mAP@0.5:0.95 = 0.682** — within +0.007 / +0.012 of the FP32 reference.

3. **Per-stage latency and throughput budget** — **52.2 FPS** at 18.4 ms primary detection latency; MobileNetV2 verifier and MiDaS pre-filter share the same device inside a detect-to-decision envelope of 22–27 ms.

4. **FPS flatness under concurrent load** — **52.26 ± 0.76 FPS** across six operating modes (Baseline, Idle, DND, Privacy, Emergency, Active) with zero camera-side frame drops while SMTP alerts, AES-encrypted SFTP clip uploads, voice queries, and MJPEG streaming run concurrently on the CPU side; system power **5.8 W**.

### Models evaluated

| Model | Task | Notes |
|---|---|---|
| YOLOv6n | Detection | Default; fastest |
| **YOLOv8s **| Detection | Best accuracy |
| YOLOx-S | Detection | Paper ablation |
| YOLOv8s Pose | Pose estimation | Supplementary |
| YOLOv5n Seg | Instance segmentation | Supplementary |

Tuned YOLOv8s is used for the evaluation of the system. 
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

# Raspberry Pi camera (standard hardware setup)
python basic_pipelines/Garuda_web.py --input rpi

# USB camera
python basic_pipelines/Garuda_web.py --input /dev/video0

# Video file (for testing without camera)
python basic_pipelines/Garuda_web.py --input resources/detection0.mp4
```

Dashboard available at `http://<device-ip>:8080`.

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

## Datasets & Models

All training was done on a RunPod cloud instance (RTX 4090, 24 GB VRAM). Training artifacts, raw datasets, and compiled HEF models are stored on a RunPod S3 bucket.

**S3 endpoint:** `https://s3api-us-il-1.runpod.io`  
**Bucket:** `wbeta1c4ev`

```bash
aws configure
```

> Credentails to access the S3 server:
> Access key - user_3CCqL5IC5SyiZKCYMAuUzHtWOTS
> Secret access key - rps_0OS1AFV1HVS9HDV2S7449IKHRW9G4LTVJSJ9H1NR1mkvk1
> region - us-il-1
> format - json

(Please use this only for the evaluation purposes)

**NO MISUSES ARE ENTERTAINED**

### Dataset sources (publicly citable)

| Dataset | Source | License | Used for |
|---|---|---|---|
| Roboflow Knives & Scissors Training v2 | Roboflow Universe | CC BY 4.0 | Weapon class seed images (359 imgs) |
| COCO 2017 | cocodataset.org | CC BY 4.0 | Person class (~1,200 imgs extracted) |
| OpenImages v7 — Hammer, Knife, Person, Scissors | storage.googleapis.com/openimages | CC BY 4.0 | Bulk class images (~4,600 imgs v5; +2,000 Person in v6) |
| CelebA-Spoof | mmlab.ie.cuhk.edu.hk | Research only | Anti-spoof classifier training |

### Training datasets on S3

| Path | Description | Split sizes |
|---|---|---|
| `datasets/knives_scissors/` | Dataset v5 — 4 classes (Hammer, Knife, Person, Scissors) | Train 4,982 / Val 622 / Test 622 |
| `datasets/merged_dataset/` | Dataset v6 — v5 + 2,000 extra Person images | Train 6,505 / Val 812 / Test 812 |
| `datasets/openimages/` | OpenImages v7 subset (raw, pre-merge) | — |
| `datasets/openimages_extra/` | Extra OpenImages Person images | — |
| `antispoof_val/` | Anti-spoof validation set (real / spoof / spoof\_print / spoof\_screen) | — |

### Trained models on S3

| Path | Description |
|---|---|
| `models/hef/yolo.hef` | YOLOv8s retrained on dataset v6, compiled for Hailo-8L |
| `models/hef/classifier.hef` | MobileNetV2 threat classifier (Safe / Weapon), compiled for Hailo-8L |
| `models/hef/depth.hef` | Depth estimation model for anti-spoof stage |
| `models/mobilenetv2_garuda_final.pth` | PyTorch checkpoint (pre-compile) |
| `ieee_access_submission/access.pdf` | Final paper PDF as submitted |

### Downloading artifacts

```bash
# requires AWS CLI with RunPod credentials configured
export AWS_ENDPOINT_URL=https://s3api-us-il-1.runpod.io

# Download trained HEF models
aws s3 cp s3://wbeta1c4ev/models/hef/yolo.hef resources/
aws s3 cp s3://wbeta1c4ev/models/hef/classifier.hef resources/
aws s3 cp s3://wbeta1c4ev/models/hef/depth.hef resources/

# Download paper PDF
aws s3 cp s3://wbeta1c4ev/ieee_access_submission/access.pdf docs/

# Download a dataset
aws s3 sync s3://wbeta1c4ev/datasets/merged_dataset/ datasets/merged_dataset/
```

### Evaluation scripts

Training and evaluation scripts are in the S3 bucket root:

| Script | Purpose |
|---|---|
| `eval_antispoof_v4.py` | Anti-spoof classifier evaluation (ROC, confusion matrix) |
| `eval_int8_test.py` | INT8 quantisation accuracy check |
| `eval_fp32_test.py` | FP32 baseline accuracy check |
| `build_hefs.py` | Compile ONNX → HEF via Hailo SDK |
| `retrain_classifier_v2.py` | MobileNetV2 classifier training |

Local evaluation scripts (no S3 required) are in [`evaluation/`](evaluation/).

---

## License

MIT — see [LICENSE](LICENSE).

Built on the [Hailo RPi5 Examples](https://github.com/hailo-ai/hailo-rpi5-examples) framework.
