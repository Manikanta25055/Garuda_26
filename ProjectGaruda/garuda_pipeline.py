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

def start_pipeline(input_src=None, hef_dir=None, conf=0.40):
    """
    Start the Garuda Cascade pipeline in a background thread.
    Called automatically by main_app.py on startup.

    input_src — camera device or video file; defaults to /dev/video0
    hef_dir   — directory with the three HEF files; defaults to resources/
    conf      — YOLO person confidence threshold
    """
    if not _AVAILABLE:
        common.log_system_update("[CASCADE] Pipeline unavailable (hailo_platform not found).")
        return

    _hef_dir = hef_dir or str(Path(__file__).parent.parent / "resources")
    _src     = input_src or "/dev/video0"

    common.log_system_update(f"[CASCADE] Starting pipeline — input={_src}")
    _cascade.start(
        on_event  = _on_event,
        input_src = _src,
        hef_dir   = _hef_dir,
        conf      = conf,
    )


def stop_pipeline():
    """Stop the cascade pipeline gracefully."""
    if _AVAILABLE:
        _cascade.stop()
    common.log_system_update("[CASCADE] Pipeline stopped.")
