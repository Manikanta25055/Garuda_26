"""Home: what the home-automation side and the assistant may read and change in the security system.

Moved out of Garuda_web.py (2026-10). Function bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`: the
live module, handed over once by bind(). Garuda_web imports these functions
back under the same names.
"""
import datetime
import json
import time

try:
    from ..garuda_auto import sun as _sun
except ImportError:      # loaded as a top-level package (Garuda_web run as a script)
    from basic_pipelines.garuda_auto import sun as _sun

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
    facts["camera_covered"] = "yes" if core.STATE.alerts.blind_alert_sent else "no"
    facts["camera"] = "live" if (time.time() - core.STATE.camera.frame_ts) < 5.0 else "down"
    facts["internet"] = "online" if core.STATE.system.net_online else "offline"
    facts["recording"] = "on" if core.STATE.camera.clip_writer is not None else "off"
    # The microphone runs only while something listens: a shortcut that tests
    # the sound keeps it running (each tick renews it), nothing else does.
    shortcuts = getattr(core, "SHORTCUTS", None)
    if shortcuts is not None and core.AUDIO_FEED.available and any(
            '"sound' in json.dumps(s, default=str) for s in shortcuts.store.all()):
        core.AUDIO_FEED.keep_alive(10)
    level = core.AUDIO_FEED.level(2.0)
    facts["sound"] = level["sound"] if level else "unknown"
    facts["sound_db"] = level["db"] if level else -100.0
    loc = core.STATE.config.location or {}
    if _sun.valid_location(loc.get("lat"), loc.get("lon")):
        facts.update(_sun.facts(loc["lat"], loc["lon"]))
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
