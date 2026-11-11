"""Background monitors: internet connectivity, the dead man switch and the mode schedule.

Moved out of Garuda_web.py (2026-10). Function bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`: the
live module, handed over once by bind(). Garuda_web imports these functions
back under the same names.
"""
import datetime
import os
import threading
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


def _check_connectivity() -> bool:
    """Quick connectivity check — try to resolve DNS."""
    import socket
    try:
        # Closed here: one socket every 30 s was left for the collector.
        with socket.create_connection(("1.1.1.1", 53), timeout=3):
            return True
    except OSError:
        return False


def _connectivity_monitor():
    """Background thread: monitor internet connectivity, log transitions."""
    was_online = True
    while True:
        time.sleep(30)
        online = core._check_connectivity()
        if online and not was_online:
            # Just came back online
            core.STATE.system.net_online = True
            pending = core.get_pending_count()
            core.log_system_update(f"[NETWORK] Internet restored — {pending} queued events ready to sync")
            core.push_urgent_ws()
        elif not online and was_online:
            # Just went offline
            core.STATE.system.net_online = False
            core.log_system_update("[NETWORK] Internet connection lost — events will be queued locally")
            core.push_urgent_ws()
        was_online = online


def _deadman_monitor():
    """Background thread: if no /api/heartbeat in _DEADMAN_TIMEOUT seconds, send tamper alert.

    Opt-in via DEADMAN_ENABLED=1. Only meaningful with an external monitor
    hitting /api/heartbeat. Anti-spam guards: never alarms unless at least one
    real heartbeat has been received (otherwise there is simply no heartbeat
    source), and repeat alerts are rate-limited to _DEADMAN_REALERT_INTERVAL.
    """
    if not core._DEADMAN_ENABLED:
        core.log_system_update("[TAMPER] Dead-man switch disabled (set DEADMAN_ENABLED=1 to enable).")
        return
    while True:
        time.sleep(60)
        # No heartbeat has ever arrived → no monitor configured, not tampering.
        if not core.STATE.system.heartbeat_ever:
            continue
        elapsed = time.time() - core.STATE.system.last_heartbeat
        now = time.time()
        if elapsed > core._DEADMAN_TIMEOUT and not core.STATE.system.deadman_alert_sent \
                and (now - core.STATE.system.deadman_last_alert) > core._DEADMAN_REALERT_INTERVAL:
            core.STATE.system.deadman_alert_sent = True
            core.STATE.system.deadman_last_alert = now
            core.log_system_update(f"[TAMPER] No heartbeat in {int(elapsed)}s — possible system tampering!")
            # Send tamper alert email
            try:
                body = (f"Garuda dead man's switch triggered.\n"
                        f"No heartbeat received in {int(elapsed)} seconds.\n"
                        f"Possible system tampering or network failure.")
                core._send_mail("TAMPER ALERT: Garuda heartbeat missed", body)
            except Exception as e:
                core.log_system_update(f"[TAMPER] Failed to send alert email: {e}")


# The camera watch. A lens that is covered is noticed (the picture goes
# uniform); a camera that stops sending pictures was not: no frame means no
# detection, no blindness check and no alert, and the only sign was a line on
# the System page. For a security camera that is the failure that matters most.
CAMERA_STALL_S = 30.0        # frames were arriving and have stopped for this long
CAMERA_START_S = 120.0       # no frame at all this long after the service started
CAMERA_REALERT_S = 3600.0    # at most one email an hour


def camera_stall(frame_ts, started, now):
    """Seconds the camera has been silent when that is too long, else 0."""
    if frame_ts:
        age = now - frame_ts
        return age if age > CAMERA_STALL_S else 0.0
    age = now - started
    return age if age > CAMERA_START_S else 0.0


def _camera_stopped_email(seconds):
    if not (core.STATE.config.email_sender and core.STATE.config.email_recipients):
        return
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        core._send_mail(
            "CRITICAL: Garuda camera has stopped",
            f"No picture has arrived from the camera for {seconds} seconds (noticed at {stamp}).\n"
            "Nothing is being detected until it is back: the cable, the camera or the\n"
            "detector may have failed. Check the System page, or restart the Pi.")
        core.log_system_update("[CAMERA] Camera-stopped email sent.")
    except Exception as exc:
        core.log_system_update(f"[CAMERA] Camera-stopped email failed: {exc}")


def camera_watch(every=5.0, sleep=time.sleep, clock=time.time, rounds=None):
    """Background thread: say so, once, when the camera stops delivering frames,
    and again when it is back. GARUDA_CAMERA_STALL_ALERT=0 turns it off."""
    if os.environ.get("GARUDA_CAMERA_STALL_ALERT", "1") == "0":
        return
    started, stalled, last_mail = clock(), False, None
    while rounds is None or rounds > 0:
        now = clock()
        silent = camera_stall(core.STATE.camera.frame_ts, started, now)
        if silent and not stalled:
            stalled = True
            core.log_system_update(f"[CAMERA] No frames for {int(silent)} s -- the camera or the "
                                   "detector has stopped; nothing is being watched.")
            core._append_detection_perm("TAMPER", "camera_stopped", 0.0, "no frames from the camera")
            core.push_urgent_ws()
            if last_mail is None or now - last_mail >= CAMERA_REALERT_S:
                last_mail = now
                threading.Thread(target=_camera_stopped_email, args=(int(silent),),
                                 daemon=True, name="camera-stopped-mail").start()
        elif not silent and stalled:
            stalled = False
            core.log_system_update("[CAMERA] Frames are arriving again.")
            core.push_urgent_ws()
        if rounds is not None:
            rounds -= 1
        sleep(every)


def _schedule_monitor():
    """Background thread: enforce scheduled mode transitions.

    Checks every 30 s and immediately on first run so startup catches the
    correct state without a 60-s blind window.  Takes a dict snapshot before
    iterating so a concurrent update_config() call can't cause a RuntimeError.
    """
    schedulable = ("dnd", "email_off", "idle", "night")
    # What each schedule last asked for. A mode is only written when that
    # changes (the window opens or closes, or the schedule is edited): writing
    # it on every pass undid, within 30 s, any switch a person flipped by hand
    # inside the window.
    applied: dict = {}
    while True:
        try:
            sched_snap = dict(core.STATE.modes.schedule)   # snapshot outside lock — avoids racing with update_config
            for gone in [m for m in applied if m not in sched_snap]:
                applied.pop(gone, None)
            if sched_snap:
                now_str = datetime.datetime.now().strftime("%H:%M")
                changed = False
                with core.STATE.modes.lock:
                    for mode_name, sched in sched_snap.items():
                        if not isinstance(sched, dict):
                            continue
                        start = sched.get("start", "")
                        end   = sched.get("end", "")
                        if not start or not end or mode_name not in schedulable:
                            continue
                        in_range = core._time_in_range(start, end, now_str)
                        key = (start, end, in_range)
                        if applied.get(mode_name) == key:
                            continue
                        applied[mode_name] = key
                        if core.STATE.modes.get(mode_name) != in_range:
                            core.STATE.modes.set(mode_name, in_range)
                            changed = True
                            core.log_system_update(
                                f"[MODE] {mode_name} {'on' if in_range else 'off'} by schedule ({start}-{end})")
                if changed:
                    core.push_urgent_ws()
        except Exception as exc:
            core.log_system_update(f"[MODE] schedule check failed: {type(exc).__name__}: {exc}")
        time.sleep(30)   # sleep AFTER check so first run is immediate; 30 s ≤ worst-case lag
