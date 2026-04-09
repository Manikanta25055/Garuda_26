# Project Garuda — Novelties

## What it is
An AI-powered home security system that runs entirely on a local device — no cloud, no subscription, no video leaving the home.

## Key Novelties

1. **On-device AI inference pipeline** — YOLOv8-class detection runs locally via a GStreamer pipeline. No cloud round-trip, no latency, no privacy leak.

2. **Two-tier threat classification** — Separates generic intrusion ("person detected") from elevated threat ("dangerous object detected"). Configurable danger-object list matched against YOLO labels at runtime, with a separate audit log.

3. **Multi-mode security state machine** — Active / DND / Idle / Night Mode / Email-Off, each independently changing alert behavior, detection sensitivity, and log destination. Modes switch automatically on a schedule.

4. **Local offline voice assistant (Narada)** — Voice commands control the system on-device. No Alexa, no Google, no internet needed.

5. **Unified multi-client backend** — Single FastAPI server simultaneously serves a web dashboard, a native iOS app (SwiftUI), WebSocket live events, and MJPEG video — all without a cloud relay.

6. **Role-based access + Email OTP auth** — Admin tier requires password + email OTP. User tier is password only. No third-party auth service involved.

7. **AI-annotated MJPEG streaming** — Detection bounding boxes are drawn server-side before encoding, so clients receive an already-annotated stream.

8. **Event-triggered clip recording** — Video clips are saved only when the AI confirms a detection event, linked to the structured JSON log entry.

9. **Muxer-synchronized pipeline** — `hailomuxer` guarantees the displayed/saved frame is the exact frame the inference ran on — critical for accurate annotation and evidence integrity.

10. **AI-driven camera rotation for blind spot elimination** — When the AI detects a person near the edge of the frame, the system calculates the subject's position offset from center and drives a servo motor to rotate the camera toward the subject. The camera physically tracks the detected target, eliminating fixed blind spots without adding more cameras. This closes the loop between inference output (bounding box centroid) and actuator control (servo PWM signal) — the detection pipeline directly steers the camera.
