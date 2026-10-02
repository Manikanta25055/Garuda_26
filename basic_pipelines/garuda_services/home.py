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
    user = core.STATE.auth.users.get(username)
    if user is None or not core._verify_password(password, user["password"]):
        return None
    return user.get("role", "user")


def _drishti_system_state():
    """Mode flags, uptime and camera liveness for the Drishti Home screen."""
    return {
        "modes": {
            "dnd": core.STATE.modes.dnd, "night": core.STATE.modes.night, "idle": core.STATE.modes.idle,
            "emergency": core.STATE.modes.emergency, "privacy": core.STATE.modes.privacy,
            "email_off": core.STATE.modes.email_off,
        },
        "uptime_s": int(time.time() - core._app_start_time),
        # There is no pipeline liveness flag, and app_gst is set before the
        # pipeline produces anything. Frame freshness is the honest signal:
        # what the screen wants to know is whether the camera is delivering.
        "pipeline": "running" if (time.time() - core.STATE.camera.frame_ts) < 5.0 else "stopped",
        "rule_loop": core.DRISHTI_RUNTIME.health(),
    }


def _drishti_set_privacy(on):
    """Turn the camera off from the app.

    MODE_PRIVACY was reachable only through the voice assistant, so the web app
    could read the flag and never change it.
    """
    core.STATE.modes.privacy = bool(on)
    core.log_system_update(f"[DRISHTI] privacy {'on' if core.STATE.modes.privacy else 'off'}")


def _home_presence():
    """True home / False away / None when no phone is registered to watch."""
    return core.STATE.presence.owner_present if core.STATE.config.known_devices else None


def _home_security():
    if core.STATE.alerts.active:
        return "danger"
    if core.STATE.alerts.night_presence_active:
        return "night_presence"
    return "clear"


def _home_email(subject, body):
    """Home notices go to the alert recipients, unless email alerts are off."""
    if core.STATE.modes.email_off or not (core.STATE.config.email_sender and core.STATE.config.email_sender_pass and core.STATE.config.email_recipients):
        return
    core._send_mail(subject, body)


def _home_modes():
    return {"dnd": core.STATE.modes.dnd, "night": core.STATE.modes.night, "idle": core.STATE.modes.idle,
            "emergency": core.STATE.modes.emergency, "privacy": core.STATE.modes.privacy, "email_off": core.STATE.modes.email_off}


def _home_set_mode(mode, value, actor):
    """The assistant's way into the same switch as POST /api/modes."""
    if mode not in core.STATE.modes.FLAGS:
        raise ValueError(f"unknown mode: {mode!r}")
    if mode in core.ADMIN_ONLY_MODES and value and core.STATE.auth.users.get(actor, {}).get("role") != "admin":
        raise PermissionError("only an admin can turn this mode on: it silences alerts")
    with core.STATE.modes.lock:
        core.STATE.modes.set(mode, bool(value))
        if mode == "emergency" and value:
            core.STATE.modes.dnd = False
    core.save_config()
    core.log_system_update(f"Mode {mode} set to {bool(value)} by {actor or 'assistant'} (Narada)")
    core.push_urgent_ws()
    return f"{mode} {'on' if value else 'off'}"


def _home_security_summary():
    return {"alert_active": core.STATE.alerts.active, "night_presence_alert": core.STATE.alerts.night_presence_active,
            "alerts_today": core.STATE.alerts.history.get(datetime.date.today().isoformat(), 0),
            "camera_live": (time.time() - core.STATE.camera.frame_ts) < 5.0}


def _shortcut_facts():
    """What a shortcut's condition may test: the room as the camera and sensors
    report it, each device's state, modes, alerts, presence and the camera."""
    facts = {k: v for k, v in dict(core.DRISHTI_CTX.descriptor).items()
             if isinstance(v, (str, int, float, bool))}
    for device in core.HOME.devices():
        facts[f"{device['id']}_state"] = device["state"]
    for name in core.STATE.modes.FLAGS:
        facts[f"mode_{name}"] = "on" if core.STATE.modes.get(name) else "off"
    facts["alert"] = "active" if core.STATE.alerts.active else "clear"
    facts["alert_reason"] = core.STATE.alerts.danger_trigger_info or "none"
    facts["night_presence_alert"] = "on" if core.STATE.alerts.night_presence_active else "off"
    facts["camera"] = "live" if (time.time() - core.STATE.camera.frame_ts) < 5.0 else "down"
    facts["internet"] = "online" if core.STATE.system.net_online else "offline"
    facts["recording"] = "on" if core.STATE.camera.clip_writer is not None else "off"
    for phone in core.STATE.config.known_devices:
        slug = "".join(c if c.isalnum() else "_" for c in phone.get("name", "").lower()).strip("_")
        if slug:
            facts[f"phone_{slug}"] = "home" if core._mac_online(core._device_mac(phone)) else "away"
    return facts


def _shortcut_role_of(username):
    """The role a shortcut's maker has now; None once they can no longer sign in."""
    user = core.STATE.auth.users.get(username)
    return user.get("role", "user") if user else None


def _shortcut_notify(text, email=False):
    core.HOME.notice("shortcut", text, email=email)
    core.log_system_update(f"[SHORTCUT] {text}")
