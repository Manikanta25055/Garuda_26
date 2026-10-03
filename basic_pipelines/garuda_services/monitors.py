"""Background monitors: internet connectivity, the dead man switch and the mode schedule.

Moved out of Garuda_web.py (2026-10). Function bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`: the
live module, handed over once by bind(). Garuda_web imports these functions
back under the same names.
"""
import datetime
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
        socket.create_connection(("1.1.1.1", 53), timeout=3)
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
