"""Persistence: users, config, master keys, alert history and the presence log on disk.

Moved out of Garuda_web.py (2026-10). Function bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`: the
live module, handed over once by bind(). Garuda_web imports these functions
back under the same names.
"""
import asyncio
import datetime
import json
import os

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


def load_users():
    # The legacy file holds plaintext passwords from before hashing. It is read
    # only on a machine that has never had a users.json; a users.json that is
    # there but unreadable must not quietly bring those old passwords back.
    candidates = [core.USERS_FILE] if os.path.exists(core.USERS_FILE) else [core.USERS_FILE, "system_logs/users_data.json"]
    for path in candidates:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    data = json.load(f)
                if isinstance(data, dict) and data:
                    default_colors = ["#1565c0","#2e7d32","#6a1b9a","#00838f",
                                      "#f57f17","#4527a0","#ad1457"]
                    idx = 0
                    for uname, udata in data.items():
                        if "display_name" not in udata:
                            udata["display_name"] = uname.capitalize()
                        if "box_color" not in udata:
                            udata["box_color"] = "#e65100" if udata.get("role") == "admin" \
                                else default_colors[idx % len(default_colors)]
                            idx += 1
                        if "history" not in udata:
                            udata["history"] = {"logins": [], "narada_activity": []}
                    core.USERS = data
                    return
            except Exception as e:
                print(f"Warning: failed to load users from {path}: {e}")


def save_users():
    try:
        core._atomic_json_write(core.USERS_FILE, core.USERS)
    except Exception as e:
        core.log_system_update(f"Failed to save users: {e}")


def load_config():
    # NOTE: EMAIL_SENDER_PASS is NOT loaded from config.json —
    # it lives exclusively in .env / environment variables for security.
    if os.path.exists(core.CONFIG_FILE):
        try:
            with open(core.CONFIG_FILE) as f:
                cfg = json.load(f)
            core.STATE.config.custom_voice_commands = cfg.get("custom_voice_commands", core.STATE.config.custom_voice_commands)
            core.STATE.modes.custom = cfg.get("custom_modes", core.STATE.modes.custom)
            core.STATE.config.email_recipients = cfg.get("email_recipients", core.STATE.config.email_recipients)
            core.STATE.config.email_cooldown = cfg.get("email_cooldown", core.STATE.config.email_cooldown)
            core.STATE.config.email_sender = cfg.get("email_sender", core.STATE.config.email_sender)
            core.STATE.config.detection_threshold = cfg.get("detection_threshold", core.STATE.config.detection_threshold)
            core.STATE.config.known_devices = cfg.get("known_devices", core.STATE.config.known_devices)
            core.STATE.config.watch_labels = cfg.get("watch_labels", core.STATE.config.watch_labels)
            # Support both legacy "danger_label" (str) and new "danger_labels" (list)
            if "danger_labels" in cfg:
                core.STATE.config.danger_labels = cfg["danger_labels"]
            elif "danger_label" in cfg:
                core.STATE.config.danger_labels = [cfg["danger_label"]]
            core.STATE.config.night_presence_window = cfg.get("night_presence_window", core.STATE.config.night_presence_window)
            # Restore persisted mode states
            modes = cfg.get("modes", {})
            core.STATE.modes.dnd       = bool(modes.get("dnd",       core.STATE.modes.dnd))
            core.STATE.modes.email_off = bool(modes.get("email_off", core.STATE.modes.email_off))
            core.STATE.modes.idle      = bool(modes.get("idle",      core.STATE.modes.idle))
            core.STATE.modes.night     = bool(modes.get("night",     core.STATE.modes.night))
            core.STATE.modes.emergency = bool(modes.get("emergency", core.STATE.modes.emergency))
            core.STATE.modes.privacy   = bool(modes.get("privacy",   core.STATE.modes.privacy))
            core.STATE.modes.schedule  = cfg.get("mode_schedule", core.STATE.modes.schedule)
        except Exception as e:
            print(f"Warning: failed to load config: {e}")


def _load_alert_history():
    """Load alert-activity history from disk into _alert_history."""
    try:
        if os.path.exists(core.ALERT_HISTORY_FILE):
            with open(core.ALERT_HISTORY_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                core.STATE.alerts.history = data
            elif isinstance(data, list):
                # Legacy list format — migrate to {date: count} by counting entries per day
                migrated: dict = {}
                for entry in data:
                    if isinstance(entry, dict) and "timestamp" in entry:
                        day = entry["timestamp"][:10]
                        migrated[day] = migrated.get(day, 0) + 1
                core.STATE.alerts.history = migrated
                core._atomic_json_write(core.ALERT_HISTORY_FILE, core.STATE.alerts.history)
            else:
                core.STATE.alerts.history = {}
    except Exception:
        core.STATE.alerts.history = {}


def _record_alert_activity():
    """Increment today's alert count and persist to disk."""
    today = datetime.date.today().isoformat()
    core.STATE.alerts.history[today] = core.STATE.alerts.history.get(today, 0) + 1
    try:
        core._atomic_json_write(core.ALERT_HISTORY_FILE, core.STATE.alerts.history)
    except Exception:
        pass


def _remember_user_activity(user_name, kind, entry):
    """Append to a user's history list, keeping only the recent entries."""
    user = core.USERS.get(user_name) if user_name else None
    if not isinstance(user, dict):
        return
    items = user.setdefault("history", {}).setdefault(kind, [])
    items.append(entry)
    if len(items) > core._USER_HISTORY_MAX:
        del items[:-core._USER_HISTORY_MAX]


def _load_presence_log():
    try:
        if os.path.exists(core.PRESENCE_LOG_FILE):
            with open(core.PRESENCE_LOG_FILE) as f:
                data = json.load(f)
            core._presence_log = data[-core._PRESENCE_LOG_MAX:] if isinstance(data, list) else []
    except Exception:
        core._presence_log = []


def _append_presence_log(event: str, device: str, mac: str):
    """Append one presence event, persist to disk, and queue for sync."""
    core._presence_log.append({
        "ts":     datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "event":  event,
        "device": device,
        "mac":    mac,
    })
    # The whole list is rewritten on every event; without a cap that write
    # (and the file) grew for ever.
    if len(core._presence_log) > core._PRESENCE_LOG_MAX:
        core._presence_log[:] = core._presence_log[-core._PRESENCE_LOG_MAX:]
    try:
        core._atomic_json_write(core.PRESENCE_LOG_FILE, core._presence_log)
    except Exception:
        pass
    core.queue_event("PRESENCE", device, 0.0, f"{event} (mac={mac})")


def load_master_keys():
    try:
        if os.path.exists(core.MASTER_KEYS_FILE):
            with open(core.MASTER_KEYS_FILE) as f:
                data = json.load(f)
            if isinstance(data.get("keys"), list) and data["keys"]:
                keys = [k for k in data["keys"] if isinstance(k, str) and k]
                if any(not core._mk_is_hashed(k) for k in keys):
                    # One-time migration of a file written before hashing.
                    keys = [k if core._mk_is_hashed(k) else core._mk_hash(k) for k in keys]
                    core.MASTER_KEYS[:] = keys
                    core.save_master_keys()
                else:
                    core.MASTER_KEYS[:] = keys
                return
    except Exception:
        pass
    # If no key file, seed from MASTER_KEY env var (set in .env)
    bootstrap = os.environ.get("MASTER_KEY", "").strip()
    if bootstrap:
        core.MASTER_KEYS[:] = [core._mk_hash(bootstrap)]
        core.save_master_keys()  # persist to file for future runs


def save_master_keys():
    try:
        # Never write a key as typed, whatever put it in the list.
        core.MASTER_KEYS[:] = [k if core._mk_is_hashed(k) else core._mk_hash(k) for k in core.MASTER_KEYS]
        core._atomic_json_write(core.MASTER_KEYS_FILE, {"keys": core.MASTER_KEYS})
        try:
            os.chmod(core.MASTER_KEYS_FILE, 0o600)
        except OSError:
            pass
    except Exception as exc:
        core.log_system_update(f"Failed to save master keys: {type(exc).__name__}")


async def _async_save_config():
    """Run save_config in a thread so it never blocks the async event loop (fsync is slow on RPi SD)."""
    await asyncio.to_thread(core.save_config)


def save_config():
    # NOTE: EMAIL_SENDER_PASS is intentionally excluded —
    # credentials must not be stored in plaintext JSON on disk.
    try:
        cfg = {
            "custom_voice_commands": core.STATE.config.custom_voice_commands,
            "custom_modes": core.STATE.modes.custom,
            "email_recipients": core.STATE.config.email_recipients,
            "email_cooldown": core.STATE.config.email_cooldown,
            "email_sender": core.STATE.config.email_sender,
            "detection_threshold": core.STATE.config.detection_threshold,
            "known_devices": core.STATE.config.known_devices,
            "watch_labels": core.STATE.config.watch_labels,
            "danger_labels": core.STATE.config.danger_labels,
            "night_presence_window": core.STATE.config.night_presence_window,
            "modes": {
                "dnd":       core.STATE.modes.dnd,
                "email_off": core.STATE.modes.email_off,
                "idle":      core.STATE.modes.idle,
                "night":     core.STATE.modes.night,
                "emergency": core.STATE.modes.emergency,
                "privacy":   core.STATE.modes.privacy,
            },
            "mode_schedule": core.STATE.modes.schedule,
        }
        core._atomic_json_write(core.CONFIG_FILE, cfg)
    except Exception as e:
        core.log_system_update(f"Failed to save config: {e}")
