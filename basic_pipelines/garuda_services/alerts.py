"""Alerts: the software alert, alert and tamper emails, and clip exfiltration.

Moved out of Garuda_web.py (2026-10). Function bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`: the
live module, handed over once by bind(). Garuda_web imports these functions
back under the same names.
"""
import datetime
import os
import subprocess
import time

core = None


def bind(module):
    """Called by Garuda_web with itself, before anything here runs."""
    global core
    if core is not None and core is not module:
        # A second copy of Garuda_web (imported under another name) would
        # silently take over the state every function here reads.
        raise RuntimeError(f"{__name__} is already bound to {core.__name__}; "
                           f"refusing a second copy, {module.__name__}")
    core = module


def trigger_software_alert():
    with core.STATE.modes.lock:
        dnd = core.STATE.modes.dnd
        idle = core.STATE.modes.idle
        night = core.STATE.modes.night
    if dnd or idle:
        return
    with core._alert_lock:
        was_active = core._alert_active
        # Extend the 3s window every frame scissors is visible — alert stays on
        # while scissors is in frame and expires 3s after it disappears.
        core._alert_active = True
        core._alert_end_time = time.time() + 3
    if not was_active:
        # New alert starting: log, record, sound, email
        if night:
            core._perm_write(core.NIGHT_MODE_LOG_FILE,
                             datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        core._last_alert_time = datetime.datetime.now()
        core._record_alert_activity()
        core.log_system_update("Alert triggered.")
        core.push_urgent_ws()
        try:
            subprocess.Popen(["aplay", "/usr/share/sounds/alsa/Front_Center.wav"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


def send_email_alert():
    with core.STATE.modes.lock:
        email_off = core.STATE.modes.email_off
        idle = core.STATE.modes.idle
        emergency = core.STATE.modes.emergency
        night = core.STATE.modes.night
    if email_off or idle:
        return
    with core._email_lock:
        current_time = time.time()
        if (current_time - core.last_email_sent_time) < core.EMAIL_COOLDOWN:
            return
        core.last_email_sent_time = current_time
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    label_str = ", ".join(core.DANGER_LABELS)
    subject = f"Danger Object Detected — {label_str}"
    if emergency:
        subject = "EMERGENCY: " + subject
    elif night:
        subject = "HIGH PRIORITY: " + subject
    body = f"Danger object detected at {now_str}.\nObject(s): {label_str}\nCheck your environment for safety.\n"
    try:
        core._send_mail(subject, body)
        core.log_system_update("Email alert sent.")
    except Exception as e:
        core.log_system_update(f"Failed sending email alert: {e}")


def log_scissors_detection(label: str = "danger"):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    core._perm_write(core.SCISSORS_LOG_FILE, f"[{stamp}] DANGER DETECTED: {label.upper()}")


def _send_tamper_email():
    """Maximum-priority tamper alert — bypasses DND/idle/email-off modes.

    Rate-limited to one email per _TAMPER_EMAIL_COOLDOWN so a flickering /
    intermittently-dark camera cannot spam the recipient.
    """
    if not core.EMAIL_SENDER or not core.EMAIL_RECIPIENTS:
        return
    now = time.time()
    if (now - core._last_tamper_email) < core._TAMPER_EMAIL_COOLDOWN:
        return
    core._last_tamper_email = now
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    body = (
        f"CRITICAL: Camera tamper detected at {now_str}.\n"
        "The camera lens appears to be covered or the feed has gone blank.\n"
        "Immediate physical inspection required."
    )
    try:
        core._send_mail("CRITICAL TAMPER ALERT — Garuda Camera Covered", body)
        core.log_system_update("[TAMPER] Alert email sent.")
    except Exception as e:
        core.log_system_update(f"[TAMPER] Email failed: {e}")


def exfiltrate_clip(clip_path: str):
    """Encrypt a clip and upload the ciphertext off-device via SSH. Runs in a daemon thread."""
    if not core._EXFIL_HOST or core._EXFIL_AES_KEY is None:
        return   # exfiltration not configured
    enc_path = core._encrypt_clip_aes256(clip_path)
    if not enc_path:
        return
    core.log_system_update(f"[EXFIL] Encrypted → {os.path.basename(enc_path)}")
    if core._ssh_upload(enc_path, os.path.basename(enc_path)):
        core.log_system_update(f"[EXFIL] Uploaded to {core._EXFIL_HOST}:{core._EXFIL_REMOTE}")
        # Remove plaintext clip — only ciphertext kept locally (briefly)
        try:
            os.unlink(clip_path)
        except Exception:
            pass
    else:
        core.log_system_update(f"[EXFIL] Upload failed — encrypted clip retained locally: {enc_path}")
