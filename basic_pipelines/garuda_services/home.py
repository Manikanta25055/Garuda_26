"""Home: what the home-automation side and the assistant may read and change in the security system.

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


def _drishti_authenticate(username, password):
    """Drishti login against Garuda's own user table. Returns a role or None.

    drishti_api cannot import this module to reach USERS: Garuda_web runs as a
    script, so the live globals are __main__'s, and an import would produce a
    second copy whose USERS is still the empty dict it starts as. Passing the
    function in keeps the one live table.
    """
    user = core.USERS.get(username)
    if user is None or not core._verify_password(password, user["password"]):
        return None
    return user.get("role", "user")


def _drishti_system_state():
    """Mode flags, uptime and camera liveness for the Drishti Home screen."""
    return {
        "modes": {
            "dnd": core.MODE_DND, "night": core.MODE_NIGHT, "idle": core.MODE_IDLE,
            "emergency": core.MODE_EMERGENCY, "privacy": core.MODE_PRIVACY,
            "email_off": core.MODE_EMAIL_OFF,
        },
        "uptime_s": int(time.time() - core._app_start_time),
        # There is no pipeline liveness flag, and app_gst is set before the
        # pipeline produces anything. Frame freshness is the honest signal:
        # what the screen wants to know is whether the camera is delivering.
        "pipeline": "running" if (time.time() - core._frame_ts) < 5.0 else "stopped",
        "rule_loop": core.DRISHTI_RUNTIME.health(),
    }


def _drishti_set_privacy(on):
    """Turn the camera off from the app.

    MODE_PRIVACY was reachable only through the voice assistant, so the web app
    could read the flag and never change it.
    """
    core.MODE_PRIVACY = bool(on)
    core.log_system_update(f"[DRISHTI] privacy {'on' if core.MODE_PRIVACY else 'off'}")


def _home_presence():
    """True home / False away / None when no phone is registered to watch."""
    return core._owner_present if core.KNOWN_DEVICES else None


def _home_security():
    if core._alert_active:
        return "danger"
    if core._night_presence_alert_active:
        return "night_presence"
    return "clear"


def _home_email(subject, body):
    """Home notices go to the alert recipients, unless email alerts are off."""
    if core.MODE_EMAIL_OFF or not (core.EMAIL_SENDER and core.EMAIL_SENDER_PASS and core.EMAIL_RECIPIENTS):
        return
    core._send_mail(subject, body)


def _home_modes():
    return {"dnd": core.MODE_DND, "night": core.MODE_NIGHT, "idle": core.MODE_IDLE,
            "emergency": core.MODE_EMERGENCY, "privacy": core.MODE_PRIVACY, "email_off": core.MODE_EMAIL_OFF}


def _home_set_mode(mode, value, actor):
    """The assistant's way into the same switch as POST /api/modes."""
    names = {"dnd": "MODE_DND", "email_off": "MODE_EMAIL_OFF", "idle": "MODE_IDLE",
             "night": "MODE_NIGHT", "emergency": "MODE_EMERGENCY", "privacy": "MODE_PRIVACY"}
    if mode not in names:
        raise ValueError(f"unknown mode: {mode!r}")
    if mode in core.ADMIN_ONLY_MODES and value and core.USERS.get(actor, {}).get("role") != "admin":
        raise PermissionError("only an admin can turn this mode on: it silences alerts")
    with core._mode_lock:
        core._set_mode_flag(names[mode], bool(value))
        if mode == "emergency" and value:
            core.MODE_DND = False
    core.save_config()
    core.log_system_update(f"Mode {mode} set to {bool(value)} by {actor or 'assistant'} (Narada)")
    core.push_urgent_ws()
    return f"{mode} {'on' if value else 'off'}"


def _home_security_summary():
    return {"alert_active": core._alert_active, "night_presence_alert": core._night_presence_alert_active,
            "alerts_today": core._alert_history.get(datetime.date.today().isoformat(), 0),
            "camera_live": (time.time() - core._frame_ts) < 5.0}
