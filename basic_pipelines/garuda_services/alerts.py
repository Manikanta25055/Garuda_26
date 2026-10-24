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
    with core.STATE.alerts.lock:
        was_active = core.STATE.alerts.active
        # Extend the 3s window every frame scissors is visible — alert stays on
        # while scissors is in frame and expires 3s after it disappears.
        core.STATE.alerts.active = True
        core.STATE.alerts.end_time = time.time() + 3
    if not was_active:
        # New alert starting: log, record, sound, email
        if night:
            core._perm_write(core.NIGHT_MODE_LOG_FILE,
                             datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        core.STATE.alerts.last_alert_time = datetime.datetime.now()
        core._record_alert_activity()
        core.log_system_update("Alert triggered.")
        core.push_urgent_ws()
        try:
            subprocess.Popen(["aplay", "/usr/share/sounds/alsa/Front_Center.wav"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


RECHECK_S = 20      # a held email is looked at again if the detector still sees it after this


def second_look(frame, bbox, label):
    """Jev's reading of a weapon detection's frame; never raises, never blocks past its timeout."""
    vision = getattr(core, "VISION", None)
    if vision is None:
        return {"verdict": "unchecked", "why": "not configured"}
    try:
        result = vision.check(frame, bbox, label)
    except Exception as exc:                        # the check must never cost the alert
        result = {"verdict": "unchecked", "why": type(exc).__name__}
    core.STATE.alerts.second_look = {**result, "label": label, "at": time.time()}
    return result


def email_after_second_look(frame, bbox, label, again=False):
    """On a weapon alert: the email, unless the second look says it was harmless."""
    result = second_look(frame, bbox, label)
    line = core._vision_say(result)
    if result.get("verdict") == "harmless":
        core.STATE.alerts.email_held_at = time.time()
        core.log_system_update(f"[DANGER] {label}: email held. {line}")
        return result
    core.STATE.alerts.email_held_at = 0.0
    send_email_alert(note=line + (" (an earlier look said harmless; the detector kept seeing it)"
                                  if again else ""), label=label)
    return result


def send_email_alert(note="", label=None):
    with core.STATE.modes.lock:
        email_off = core.STATE.modes.email_off
        idle = core.STATE.modes.idle
        emergency = core.STATE.modes.emergency
        night = core.STATE.modes.night
    if email_off or idle:
        return
    with core.STATE.alerts.email_lock:
        current_time = time.time()
        if (current_time - core.STATE.alerts.last_email_sent_time) < core.STATE.config.email_cooldown:
            return
        core.STATE.alerts.last_email_sent_time = current_time
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    # The object that was seen. Every watched label was listed, so a knife
    # alert read "Knife, Scissors, Hammer" whatever had been in view.
    label_str = label or ", ".join(core.STATE.config.danger_labels)
    subject = f"Danger Object Detected — {label_str}"
    if emergency:
        subject = "EMERGENCY: " + subject
    elif night:
        subject = "HIGH PRIORITY: " + subject
    body = f"Danger object detected at {now_str}.\nObject(s): {label_str}\nCheck your environment for safety.\n"
    if note:
        body += f"\n{note}\n"
    core._cloud.publish_alert_async(subject, body)
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
    have_mail = bool(core.STATE.config.email_sender and core.STATE.config.email_recipients)
    if not have_mail and not core._cloud.sns_enabled():
        return
    now = time.time()
    if (now - core.STATE.alerts.last_tamper_email) < core._TAMPER_EMAIL_COOLDOWN:
        return
    core.STATE.alerts.last_tamper_email = now
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    body = (
        f"CRITICAL: Camera tamper detected at {now_str}.\n"
        "The camera lens appears to be covered or the feed has gone blank.\n"
        "Immediate physical inspection required."
    )
    subject = "CRITICAL TAMPER ALERT — Garuda Camera Covered"
    core._cloud.publish_alert_async(subject, body)
    if not have_mail:
        return
    try:
        core._send_mail(subject, body)
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
