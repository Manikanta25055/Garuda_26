# garuda_pipeline.py
# Thin adapter between garuda_cascade.py and the Garuda UI (main_app.py).
#
# main_app.py calls:
#   import garuda_pipeline
#   garuda_pipeline.start_pipeline()
#
# This module:
#   1. Routes cascade detection events → common.log_system_update (shows in UI)
#   2. Triggers LED + email alerts on Weapon / Spoof_Attempt events
#   3. Respects the MODE_DND / MODE_IDLE / MODE_NIGHT flags from common.py

import sys
import time
import threading
from pathlib import Path

# Make the pipeline module importable from this subdirectory
sys.path.insert(0, str(Path(__file__).parent.parent / "basic_pipelines"))

try:
    import garuda_cascade as _cascade
    _AVAILABLE = True
except ImportError as _e:
    _AVAILABLE = False
    print(f"[garuda_pipeline] cascade unavailable: {_e}")

import common

# ── Alert state ───────────────────────────────────────────────────────────────
_alert_lock   = threading.Lock()
_last_alert_t = 0.0
ALERT_COOLDOWN = 60  # seconds between email alerts

# ── Event callback ────────────────────────────────────────────────────────────

def _on_event(entry: dict):
    """
    Called by garuda_cascade.postprocess_thread for every detection.
    Runs on the postproc thread (Core 2) — keep it fast.
    """
    label = entry.get("label", "Unknown")
    conf  = entry.get("conf",  0.0)
    var   = entry.get("variance", 0.0)
    ts    = entry.get("ts", "")

    # Feed into the Garuda system updates log (visible in UserDashboard)
    common.log_system_update(
        f"[CASCADE] {label}  conf={conf:.2f}  depth_var={var:.4f}  @ {ts}"
    )

    # Trigger hardware + email alerts for threat events
    if label in ("Weapon", "Spoof_Attempt"):
        threading.Thread(
            target=_handle_alert,
            args=(label, conf),
            daemon=True,
        ).start()


def _handle_alert(label: str, conf: float):
    """Runs in a daemon thread so postproc thread is not blocked."""
    import common as _c  # re-import for current flag values

    if _c.MODE_IDLE or _c.MODE_DND:
        common.log_system_update(
            f"[CASCADE] Alert suppressed ({label}) — DND/Idle mode active"
        )
        return

    # LED + buzzer
    try:
        from gpiozero import LED, OutputDevice
        red_led = LED(17)
        buzzer  = OutputDevice(27)
        red_led.on()
        buzzer.on()
        duration = 10 if _c.MODE_NIGHT else 5
        time.sleep(duration)
        red_led.off()
        buzzer.off()
    except Exception as gpio_err:
        common.log_system_update(f"[CASCADE] GPIO alert failed: {gpio_err}")

    # Email — rate-limited by ALERT_COOLDOWN
    if _c.MODE_EMAIL_OFF:
        return
    global _last_alert_t
    with _alert_lock:
        now = time.time()
        if now - _last_alert_t < ALERT_COOLDOWN:
            return
        _last_alert_t = now

    _send_email(label, conf)


def _send_email(label: str, conf: float):
    import smtplib
    from email.mime.text import MIMEText
    import datetime

    subject = f"{'HIGH PRIORITY: ' if common.MODE_NIGHT else ''}{label} Detected"
    body = (
        f"Garuda Cascade detected: {label}\n"
        f"Confidence: {conf:.2f}\n"
        f"Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    )
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"]    = "mgonugondlamanikanta@gmail.com"
    msg["To"]      = "vishwatejdonkeshwar@gmail.com"
    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login("mgonugondlamanikanta@gmail.com", "nhxc zjtl azxm iixw")
        server.send_message(msg)
        server.quit()
        common.log_system_update(f"[CASCADE] Email alert sent: {label}")
    except Exception as e:
        common.log_system_update(f"[CASCADE] Email failed: {e}")


# ── Public API ────────────────────────────────────────────────────────────────

def _resolve_input(input_src: str | None) -> tuple[str, bool]:
    """
    Return (path, loop) for the cascade pipeline input.
    Tries USB V4L2 cameras first (/dev/video0..9); falls back to the
    bundled test video (looped) when no camera opens.
    Pi camera (IMX708/rp1-cfe) requires libcamera and is not directly
    readable by OpenCV V4L2 — a USB webcam or video file is needed.
    """
    import cv2 as _cv2
    if input_src and input_src not in ("rpi", "auto"):
        return input_src, False   # explicit path — trust the caller

    for dev in [f"/dev/video{i}" for i in range(10)]:
        try:
            cap = _cv2.VideoCapture(dev)
            ok = cap.isOpened()
            cap.release()
            if ok:
                return dev, False
        except Exception:
            pass

    # Fallback: bundled test video, looped — pipeline still runs, alerts still fire
    video = str(Path(__file__).parent.parent / "resources" / "detection0.mp4")
    common.log_system_update(
        "[CASCADE] No USB camera found — running on test video (looped). "
        "Connect a USB webcam for live monitoring."
    )
    return video, True


def start_pipeline(input_src=None, hef_dir=None, conf=0.40):
    """
    Start the Garuda Cascade pipeline in a background thread.
    Called automatically by main_app.py on startup.

    input_src — camera device, video file, or None (auto-detect)
    hef_dir   — directory with the three HEF files; defaults to resources/
    conf      — YOLO person confidence threshold
    """
    if not _AVAILABLE:
        common.log_system_update("[CASCADE] Pipeline unavailable (hailo_platform not found).")
        return

    _hef_dir    = hef_dir or str(Path(__file__).parent.parent / "resources")
    _src, _loop = _resolve_input(input_src)

    common.log_system_update(f"[CASCADE] Starting pipeline — input={_src}  loop={_loop}")
    _cascade.start(
        on_event  = _on_event,
        input_src = _src,
        hef_dir   = _hef_dir,
        conf      = conf,
        loop      = _loop,
    )


def stop_pipeline():
    """Stop the cascade pipeline gracefully."""
    if _AVAILABLE:
        _cascade.stop()
    common.log_system_update("[CASCADE] Pipeline stopped.")
