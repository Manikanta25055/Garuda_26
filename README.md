# Project Garuda

**A privacy-preserving, edge-AI home security system with real-time object detection, a local voice assistant, and a cross-platform remote interface — running entirely on a local device with no cloud dependency.**

---

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Installation](#installation)
- [Running the Application](#running-the-application)
- [iOS App](#ios-app)
- [API Reference](#api-reference)
- [Security Model](#security-model)
- [Operating Modes](#operating-modes)
- [Voice Assistant — Narada](#voice-assistant--narada)
- [Detection Pipeline](#detection-pipeline)
- [Logging and Events](#logging-and-events)
- [Tests](#tests)
- [Future Roadmap](#future-roadmap)
- [Development History](#development-history)
- [License](#license)

---

## Overview

Project Garuda is a home security system built as a full-stack local application. All AI inference, video streaming, authentication, alerting, and logging happen on the device itself.

- No video leaves your home
- No cloud account or subscription required
- No internet needed for core operation
- Works on LAN; accessible remotely via a tunnel (e.g., Cloudflare Tunnel, ngrok)

The system is implemented as a single Python process (`basic_pipelines/Garuda_web.py`, ~3,100 lines) that integrates a GStreamer AI detection pipeline with a FastAPI web server. A native iOS companion app (SwiftUI) and a Progressive Web App dashboard are included.

---

## Key Features

### AI Detection
- Real-time object detection using **YOLOv6n**, **YOLOv8s**, and **YOLOx** models via a GStreamer pipeline
- **Two-tier threat classification**: general intrusion vs. elevated threat (danger objects such as knives, scissors — configurable list)
- Danger-class detections are logged separately and trigger elevated alerts
- AI-annotated bounding boxes are drawn server-side and embedded into every video frame before streaming

### Video Streaming
| Protocol | Endpoint | Description |
|---|---|---|
| MJPEG over HTTP | `/video_feed` | Annotated live stream, works in any browser or iOS app |
| WebRTC | `/webrtc_offer` | Low-latency peer-to-peer video via SDP negotiation |
| WebSocket frames | `/ws/stream` | JPEG frames pushed over WebSocket |
| Snapshot | `/snapshot` | Single annotated JPEG on demand |
| Clip recording | `/clip/start`, `/clip/stop` | Record AI-triggered video clips locally |

### Authentication and Access Control
- **Two roles**: Admin (full control) and User (view + limited actions)
- **bcrypt** password hashing with strength validation
- **Email OTP two-factor authentication** for admin access — no third-party app needed
- **Forgot password** flow via email OTP
- **Master key system** — emergency account recovery with its own OTP gate
- Session management with expiry and forced invalidation
- Global **rate limiting** on all endpoints
- **Security headers** middleware on every response (CSP, X-Frame-Options, etc.)

### Security Modes

| Mode | Effect |
|---|---|
| **Active** | Full detection, alerts, and logging |
| **DND** | Notifications suppressed; logging continues silently |
| **Idle** | Pipeline activity reduced; no alerts |
| **Night Mode** | Separate log sink; adjusted sensitivity |
| **Email-Off** | Email alerts disabled; all else active |

Modes switch manually via API/dashboard or automatically on a **configurable schedule** (e.g., Night Mode at 22:00, Active at 06:00).

### Voice Assistant — Narada
- Runs **fully offline** on-device using `speech_recognition`
- **Rule-based engine** for direct commands (arm, disarm, change mode, status)
- **Local LLM fallback** for natural-language commands outside the fixed vocabulary
- **Groq-powered streaming chat** in the dashboard for conversational system queries

### Presence Detection
- **ARP-based polling** detects whether registered devices (family phones, laptops) are on the local network
- Alerts can be suppressed automatically when known occupants are home
- Presence events logged and viewable from the dashboard

### Alerts
- **Email alerts** via SMTP with cooldown to prevent flooding
- **Instant WebSocket push** to all connected clients (sub-second delivery)
- **Elevated alert** path for danger-class object detections
- **Deadman switch monitor** — escalates if no heartbeat is received within a threshold

### Logging
- Append-only JSON-lines detection log
- **SQLite event database** for multi-client sync with incremental polling
- Separate voice command log and system state change log
- Alert history with cooldown tracking
- 7-day rolling retention
- Logs viewable and downloadable from the dashboard

### Web Dashboard (PWA)
- Full-featured dashboard served at `http://<device-ip>:8080`
- Installable as a **Progressive Web App** (works offline for last-loaded state)
- Pages: Live camera, Detection logs, Modes, User management, Device presence, Voice/chat, Settings, Email configuration

### iOS Native App
- Built with **SwiftUI**, targets iOS and macOS
- Live MJPEG video view
- Real-time detection event feed via WebSocket
- Role-based login with email OTP for admin
- Full API access (modes, users, settings, logs)
- Custom design system (GarudaTheme, ComponentLibrary)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Garuda_web.py                        │
│                                                             │
│  ┌──────────────────┐     ┌───────────────────────────────┐ │
│  │  GStreamer        │     │         FastAPI Server        │ │
│  │  Detection        │────▶│                               │ │
│  │  Pipeline         │     │  REST API  WebSocket  MJPEG   │ │
│  │                  │     │  Auth      Events     WebRTC  │ │
│  │  Source           │     │  Modes     Logging    Clips   │ │
│  │  → hailonet       │     │                               │ │
│  │  → hailofilter    │     └──────────┬────────────────────┘ │
│  │  → hailomuxer     │                │                      │
│  │  → callback       │                │                      │
│  └──────────────────┘                │                      │
│                                       │                      │
│  ┌──────────────────────────────────┐ │                      │
│  │  Background Tasks                │ │                      │
│  │  Schedule monitor  Deadman       │ │                      │
│  │  Connectivity      ARP presence  │ │                      │
│  └──────────────────────────────────┘ │                      │
└───────────────────────────────────────┼──────────────────────┘
                                        │
              ┌─────────────────────────┼──────────────────────┐
              │                         │                      │
        ┌─────▼──────┐         ┌────────▼──────┐    ┌─────────▼──────┐
        │  Web        │         │  iOS App       │    │  WebSocket     │
        │  Dashboard  │         │  (SwiftUI)     │    │  API Client    │
        │  (PWA)      │         │                │    │                │
        └────────────┘         └───────────────┘    └───────────────┘
```

### GStreamer Pipeline

```
Camera/File → videoscale → videoconvert → hailomuxer
                                              │
                              ┌───────────────┴──────────────┐
                              │                              │
                         bypass_queue                   hailonet (inference)
                              │                              │
                              │                    hailofilter (post-process)
                              │                              │
                              └───────────┬──────────────────┘
                                          │
                                   hailomuxer (sync)
                                          │
                               identity callback (app_callback)
                                          │
                                  hailooverlay (draw boxes)
                                          │
                               Display / MJPEG encoder
```

The `hailomuxer` synchronisation step guarantees that the frame displayed and the frame on which inference ran are always the same frame, preventing annotation mismatches at high throughput.

---

## Project Structure

```
hailo-rpi5-examples/
│
├── basic_pipelines/
│   ├── Garuda_web.py           # Main application — detection pipeline + web server
│   ├── hailo_rpi_common.py     # GStreamer base classes and utilities
│   ├── detection.py            # Standalone detection example
│   ├── pose_estimation.py      # Standalone pose estimation example
│   ├── instance_segmentation.py
│   ├── detection_with_servo.py # Detection + servo camera control
│   ├── AI_Security*.py         # Version history (2.x → 4.x)
│   └── garuda_web/             # Web dashboard static assets
│
├── Garuda/                     # iOS / macOS native app (SwiftUI)
│   ├── Core/                   # AppState, SessionManager, Constants
│   ├── Networking/             # APIClient, WebSocketManager, MJPEGStreamView, Models
│   ├── Features/
│   │   ├── Admin/              # AdminPanelView, UserManagementView
│   │   ├── Dashboard/          # DashboardView, CameraPanel, ModesPanel, StatsPanel
│   │   ├── Narada/             # NaradaView (voice assistant interface)
│   │   ├── Alerts/
│   │   ├── Shell/
│   │   └── Welcome/
│   └── DesignSystem/           # GarudaTheme, ComponentLibrary, Typography
│
├── ProjectGaruda/              # Modular Python version (tkinter-based)
│   ├── main_app.py
│   ├── login_module.py
│   ├── admin_dashboard.py
│   ├── user_dashboard.py
│   └── common.py
│
├── cpp/                        # C++ custom post-processing (YOLO NMS)
├── resources/                  # Model HEF files, sample video, label JSONs
├── tests/                      # pytest test suite
├── scripts/                    # Utility scripts
├── setup_env.sh                # Environment setup (MUST be sourced)
├── requirements.txt
├── requirements-test.txt
├── garuda_report.tex           # LaTeX technical report
└── GARUDA_NOVELTIES.md         # Patent novelty summary
```

---

## Installation

### Prerequisites

- Raspberry Pi 5 with Hailo-8L AI HAT (for full pipeline), **or** any Linux machine with a webcam (for software-only mode)
- Python 3.10+
- GStreamer with Hailo TAPPAS (version 3.28.0, 3.28.2, or 3.31.0)

### Step 1 — Clone and set up environment

```bash
git clone https://github.com/Manikanta25055/Garuda_26.git
cd Garuda_26

# MUST be sourced, not executed
source setup_env.sh

# Install Python dependencies
pip install -r requirements.txt
pip install fastapi "uvicorn[standard]" "python-jose[cryptography]" "passlib[bcrypt]" \
            python-multipart aiortc aiofiles SpeechRecognition psutil
```

### Step 2 — Download model files

```bash
./download_resources.sh
```

### Step 3 — (Optional) Compile custom C++ post-processing

Required only if using retrained custom models:

```bash
meson setup build.release --buildtype=release
ninja -C build.release
```

### Environment Variables

Create a `.env` file in the project root:

```bash
# Email alerts (Gmail recommended — use an App Password)
ALERT_EMAIL=your_email@gmail.com
ALERT_EMAIL_PASSWORD=your_app_password
ALERT_RECIPIENT=recipient@gmail.com

# Security
SECRET_KEY=your_random_secret_key_here
SECURE_COOKIES=false          # Set true when serving over HTTPS

# Optional: Groq API for chat feature
GROQ_API_KEY=your_groq_api_key
```

---

## Running the Application

```bash
# Source the environment first
source setup_env.sh

# Run with default camera
python basic_pipelines/Garuda_web.py

# Run with a video file
python basic_pipelines/Garuda_web.py --input resources/detection0.mp4

# Run with a USB camera
python basic_pipelines/Garuda_web.py --input /dev/video0

# Run with Raspberry Pi camera
python basic_pipelines/Garuda_web.py --input rpi
```

The web dashboard is available at `http://<device-ip>:8080`.

### Standalone examples

```bash
python basic_pipelines/detection.py --input resources/detection0.mp4
python basic_pipelines/pose_estimation.py --input resources/detection0.mp4
python basic_pipelines/instance_segmentation.py --input resources/detection0.mp4
python basic_pipelines/detection_with_servo.py --input /dev/video0
```

---

## iOS App

The iOS app is located in `Garuda/` as an Xcode project (`Garuda.xcodeproj`).

**Requirements:** Xcode 15+, iOS 16+ / macOS 13+

1. Open `Garuda.xcodeproj` in Xcode
2. Set your development team under Signing & Capabilities
3. Build and run on your device or simulator
4. On first launch, enter the server address (e.g., `http://192.168.1.100:8080`)

### Remote access

For access outside your home network, use a tunnel:

```bash
# Cloudflare Tunnel (free, recommended)
cloudflared tunnel --url http://localhost:8080

# ngrok
ngrok http 8080
```

---

## API Reference

All endpoints require a valid session unless marked public. Admin endpoints additionally require OTP-verified admin elevation.

### Authentication

| Method | Endpoint | Access | Description |
|--------|----------|--------|-------------|
| POST | `/login` | Public | Login with username + password |
| POST | `/logout` | User | End session |
| GET | `/session` | User | Current session info |
| GET | `/users/public` | Public | User list for login form |
| POST | `/admin/send_otp` | User | Request admin OTP via email |
| POST | `/admin/verify_otp` | User | Verify OTP, elevate to admin |
| POST | `/forgot/send_otp` | Public | Request password reset OTP |
| POST | `/forgot/reset` | Public | Reset password with OTP |
| POST | `/master_key/login` | Public | Master key login |
| POST | `/master_key/verify` | Public | Verify master key OTP |

### System State and Modes

| Method | Endpoint | Access | Description |
|--------|----------|--------|-------------|
| GET | `/state` | User | Full system state |
| POST | `/mode` | Admin | Switch operating mode |
| GET | `/heartbeat` | Public | Health check |
| POST | `/emergency_stop` | Admin | Halt all pipeline activity |

### Video

| Method | Endpoint | Access | Description |
|--------|----------|--------|-------------|
| GET | `/video_feed` | User | MJPEG stream |
| GET | `/snapshot` | User | Single JPEG frame |
| POST | `/clip/start` | Admin | Start clip recording |
| POST | `/clip/stop` | Admin | Stop clip recording |
| POST | `/webrtc_offer` | User | WebRTC SDP offer |
| WS | `/ws` | User | Detection events |
| WS | `/ws/stream` | User | JPEG frames + events |

### User Management

| Method | Endpoint | Access | Description |
|--------|----------|--------|-------------|
| GET | `/users` | Admin | List all users |
| POST | `/users/add` | Admin | Add user |
| POST | `/users/delete` | Admin | Delete user |
| POST | `/users/update` | Admin | Update user |

### Configuration

| Method | Endpoint | Access | Description |
|--------|----------|--------|-------------|
| GET | `/config` | Admin | Get system config |
| POST | `/config` | Admin | Update system config |
| POST | `/commands/add` | Admin | Add custom voice command |
| POST | `/commands/delete` | Admin | Remove custom voice command |

### Devices and Presence

| Method | Endpoint | Access | Description |
|--------|----------|--------|-------------|
| GET | `/devices` | User | List registered devices |
| POST | `/devices/add` | Admin | Register device |
| POST | `/devices/delete` | Admin | Remove device |
| GET | `/arp` | Admin | Current ARP table |
| POST | `/presence/refresh` | Admin | Force presence poll |

### Logs and Events

| Method | Endpoint | Access | Description |
|--------|----------|--------|-------------|
| GET | `/logs` | Logs | Retrieve logs |
| GET | `/logs/download` | Admin | Download log file |
| GET | `/events/since/{seq}` | User | Events after sequence number |
| GET | `/events/pending` | User | Unsynced event count |
| GET | `/events/stats` | User | Event statistics |

### Chat

| Method | Endpoint | Access | Description |
|--------|----------|--------|-------------|
| POST | `/chat` | User | Single chat response |
| POST | `/chat/stream` | User | Streaming chat response |

---

## Security Model

```
Request
   │
   ▼
Rate Limiter          ← blocks brute force per IP
   │
Security Headers      ← CSP, X-Frame-Options, Referrer-Policy
   │
Session validation    ← JWT, expiry, per-user invalidation
   │
Role check            ← User / Admin
   │
Admin tier ───────────── Email OTP required
   │
   ▼
Resource
```

- **Passwords**: bcrypt hashed, strength-validated on creation
- **Sessions**: in-memory with configurable TTL; invalidated on password change or account deletion
- **Admin access**: password + email OTP (two independent factors, no third-party service)
- **Master keys**: emergency access with a separate OTP gate; managed independently of user accounts
- **Transport**: run behind HTTPS in production (`SECURE_COOKIES=true` + reverse proxy or Cloudflare Tunnel)

---

## Operating Modes

| | Alert fires | Notifications sent | Log destination | Detection active |
|---|---|---|---|---|
| **Active** | Yes | Yes | `detections.jsonl` | Full |
| **DND** | Yes | No | `detections.jsonl` | Full |
| **Idle** | No | No | — | Reduced |
| **Night Mode** | Yes | Yes | `night_mode_findings.txt` | Adjusted |
| **Email-Off** | Yes | No (email) | `detections.jsonl` | Full |

Modes can be changed via dashboard, iOS app, voice command, or automatically by the schedule monitor (configured in Settings → Schedule).

---

## Voice Assistant — Narada

Narada runs as a background thread. No audio or text leaves the device.

**Processing pipeline:**
1. Microphone → local speech recognition (offline)
2. Rule engine match → instant action (zero latency)
3. No rule match → local LLM intent parsing → action

**Example commands:**
- `"Switch to night mode"` / `"Activate DND"`
- `"Arm the system"` / `"Stand down"`
- `"What mode are we in?"`
- `"Show last detection"`

**Custom commands** can be added from the dashboard (Settings → Commands) or via the API without modifying any code.

**Chat interface** — the dashboard includes a Groq-powered conversational interface for natural-language queries about detections, logs, and system status.

---

## Detection Pipeline

### Supported models

| Model | Task | File |
|-------|------|------|
| YOLOv6n | Detection | `yolov6n.hef` |
| YOLOv8s | Detection | `yolov8s_h8l.hef` |
| YOLOx-S | Detection | `yolox_s_leaky_h8l_mz.hef` |
| YOLOv8s Pose | Pose estimation | `yolov8s_pose_h8l_pi.hef` |
| YOLOv5n Seg | Instance segmentation | `yolov5n_seg_h8l_mz.hef` |

### Custom retrained models

```bash
python basic_pipelines/Garuda_web.py \
    --hef-path resources/your_model.hef \
    --labels-json resources/your_labels.json
```

Model must be compiled with HailoRT NMS Post Process. Run `./compile_postprocess.sh` once before use.

### Danger object list

Configured in system settings. Default list includes common threat objects. Any COCO-class label can be added.

---

## Logging and Events

| File | Content |
|------|---------|
| `system_logs/detections.jsonl` | All detections (JSON-lines, 7-day rolling) |
| `basic_pipelines/danger_sightings.txt` | Elevated-threat detections |
| `basic_pipelines/night_mode_findings.txt` | Night mode detections |
| `system_logs/voice.log` | Voice commands and responses |
| `system_logs/system.log` | Mode changes and system events |

The **SQLite event database** acts as the real-time queue for WebSocket and iOS app sync. Clients use incremental polling (`/events/since/{seq}`) to receive only new events after reconnect.

---

## Tests

```bash
pip install -r requirements-test.txt
pytest tests/
```

Test coverage includes: authentication flows, API mode transitions, concurrency, input validation, and security hardening.

---

## Future Roadmap

| Feature | Description |
|---------|-------------|
| **AI camera rotation** | Servo-driven camera tracking using bounding box centroid as error signal. Code stub in `detection_with_servo.py`. |
| **Encrypted evidence exfiltration** | AES-256-GCM clip encryption on-device before pushing to remote storage. Evidence survives physical device seizure. |
| **RAM-buffered log writes** | In-memory log buffer with periodic flush. Reduces SD-card write amplification. |
| **Camera tamper detection** | Alert on sudden frame variance drop (lens covered or spray-painted). |
| **Brute-force lockout** | Per-username exponential backoff after repeated failed logins. |
| **JWT refresh token rotation** | Short-lived access tokens (15 min) + rotating refresh tokens (7 days). |

---

## Development History

| Codebase | Stage |
|----------|-------|
| `AI_Security_2.x` | Early — tkinter GUI, basic detection, GPIO |
| `AI_Security_3.x` | Night mode, danger detection, email alerts, PyQt5 |
| `AI_Security_4.x` | Voice assistant, persistent logging, multi-mode state machine |
| `Garuda.py` / `Garuda_fixed.py` | Consolidated single-file version |
| `ProjectGaruda/` | Modular refactor — separate login, admin, user dashboard modules |
| `Garuda_web.py` | **Current** — full web server, iOS API, WebRTC, Groq chat, SQLite events |

---

## License

MIT License — see [LICENSE](LICENSE) for details.

Built on the [Hailo RPi5 Examples](https://github.com/hailo-ai/hailo-rpi5-examples) framework.

---

*Project Garuda — Manikanta @ 2025
