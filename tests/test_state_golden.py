"""Golden tests for the two places state leaves the process: the dashboard
snapshot (get_state_dict) and the saved config.json.

They pin the exact keys, order and values for a fixed state, so that moving
state into objects (STATE.modes, STATE.config, ...) cannot change what the
browser or the disk sees. State is set here through the flat module names on
purpose: those must keep working for as long as the rest of the suite uses them.
"""
import json

import Garuda_web as gw

STATE_KEYS = [
    "modes", "alert_active", "night_presence_alert", "danger_info", "last_alert",
    "uptime", "uptime_seconds", "system_log", "voice_log", "voice_mic", "voice_responses",
    "detection_threshold", "cpu_percent", "cpu_cores", "ram_percent", "ram_used_gb",
    "ram_total_gb", "cpu_temp", "inference_fps", "owner_present", "home", "owner_name",
    "known_devices", "alert_history", "watchdog_ok", "camera_blind", "throttled",
    "disk_percent", "disk_used_gb", "disk_total_gb", "net_connected", "net_iface",
    "detection_log_count", "presence_log_count", "net_online", "pending_sync", "clip_recording",
]

CONFIG_KEYS = [
    "custom_voice_commands", "custom_modes", "email_recipients", "email_cooldown",
    "email_sender", "detection_threshold", "known_devices", "watch_labels", "danger_labels",
    "night_presence_window", "modes", "mode_schedule",
]


def _fix_state(monkeypatch):
    for name, value in {
        "MODE_DND": True, "MODE_EMAIL_OFF": False, "MODE_IDLE": True, "MODE_NIGHT": False,
        "MODE_EMERGENCY": True, "MODE_PRIVACY": False,
        "MODE_SCHEDULE": {"night": {"start": "22:00", "end": "06:00"}},
        "CUSTOM_MODES": {"movie": {"dnd": True}},
        "CUSTOM_VOICE_COMMANDS": {"lights out": "activate dnd"},
        "DETECTION_THRESHOLD": 0.42, "EMAIL_COOLDOWN": 75,
        "EMAIL_SENDER": "pi@example.com", "EMAIL_RECIPIENTS": ["a@example.com", "b@example.com"],
        "KNOWN_DEVICES": [{"name": "Phone", "mac": "AA:BB:CC:DD:EE:FF"}],
        "WATCH_LABELS": ["Person"], "DANGER_LABELS": ["Knife", "Hammer"],
        "NIGHT_PRESENCE_WINDOW": {"start": "01:00", "end": "04:00", "enabled": False},
    }.items():
        monkeypatch.setattr(gw, name, value)


def test_state_dict_golden(app_client, monkeypatch):
    _fix_state(monkeypatch)
    monkeypatch.setattr(gw, "psutil", None)
    monkeypatch.setattr(gw, "_alert_active", True)
    monkeypatch.setattr(gw, "_alert_end_time", 0.0)
    monkeypatch.setattr(gw, "_danger_trigger_info", "Knife (0.91)")
    monkeypatch.setattr(gw, "_last_alert_time", None)
    monkeypatch.setattr(gw, "_night_presence_alert_active", False)
    monkeypatch.setattr(gw, "_owner_present", True)
    monkeypatch.setattr(gw, "_last_arp_cache", "192.168.1.6 0x1 0x2 aa:bb:cc:dd:ee:ff * wlan0\n")
    monkeypatch.setattr(gw, "_net_online", False)
    monkeypatch.setattr(gw, "_blind_alert_sent", True)
    monkeypatch.setattr(gw, "_clip_writer", None)
    monkeypatch.setattr(gw, "_voice_mic_ok", False)
    monkeypatch.setattr(gw, "_voice_mic_detail", "no microphone")
    monkeypatch.setattr(gw, "system_updates_log", ["one", "two"])
    monkeypatch.setattr(gw, "voice_assistant_log", ["heard"])
    monkeypatch.setattr(gw, "voice_responses", ["said"])
    monkeypatch.setattr(gw, "_detection_log", ["d1", "d2", "d3"])
    monkeypatch.setattr(gw, "_presence_log", [{"ts": "t"}])

    state = gw.get_state_dict()

    assert list(state) == STATE_KEYS
    assert list(state["modes"]) == ["dnd", "email_off", "idle", "night", "emergency", "privacy"]
    assert state["modes"] == {"dnd": True, "email_off": False, "idle": True, "night": False,
                              "emergency": True, "privacy": False}
    fixed = {k: state[k] for k in (
        "alert_active", "night_presence_alert", "danger_info", "last_alert", "system_log",
        "voice_log", "voice_mic", "voice_responses", "detection_threshold", "cpu_percent",
        "cpu_cores", "ram_percent", "cpu_temp", "owner_present", "owner_name", "known_devices",
        "camera_blind", "throttled", "disk_percent", "net_connected", "net_iface",
        "detection_log_count", "presence_log_count", "net_online", "clip_recording")}
    assert fixed == {
        "alert_active": True, "night_presence_alert": False, "danger_info": "Knife (0.91)",
        "last_alert": None, "system_log": ["one", "two"], "voice_log": ["heard"],
        "voice_mic": {"ok": False, "detail": "no microphone"}, "voice_responses": ["said"],
        "detection_threshold": 0.42, "cpu_percent": None, "cpu_cores": [], "ram_percent": None,
        "cpu_temp": None, "owner_present": True, "owner_name": "Phone",
        "known_devices": [{"name": "Phone", "mac": "aa:bb:cc:dd:ee:ff", "online": True}],
        "camera_blind": True, "throttled": False, "disk_percent": None, "net_connected": False,
        "net_iface": None, "detection_log_count": 3, "presence_log_count": 1,
        "net_online": False, "clip_recording": False,
    }
    # Whatever it holds, the push to browsers must stay plain JSON.
    json.dumps(state)


def test_state_for_user_drops_admin_only_keys(app_client, monkeypatch):
    _fix_state(monkeypatch)
    monkeypatch.setattr(gw, "psutil", None)
    slim = gw._state_for_role(gw.get_state_dict(), "user")
    assert [k for k in STATE_KEYS if k not in slim] == ["voice_log", "voice_responses",
                                                         "cpu_cores", "known_devices"]


def test_saved_config_golden(app_client, monkeypatch):
    _fix_state(monkeypatch)
    gw.save_config()
    with open(gw.CONFIG_FILE) as f:
        saved = json.load(f)
    assert list(saved) == CONFIG_KEYS
    assert saved == {
        "custom_voice_commands": {"lights out": "activate dnd"},
        "custom_modes": {"movie": {"dnd": True}},
        "email_recipients": ["a@example.com", "b@example.com"],
        "email_cooldown": 75,
        "email_sender": "pi@example.com",
        "detection_threshold": 0.42,
        "known_devices": [{"name": "Phone", "mac": "AA:BB:CC:DD:EE:FF"}],
        "watch_labels": ["Person"],
        "danger_labels": ["Knife", "Hammer"],
        "night_presence_window": {"start": "01:00", "end": "04:00", "enabled": False},
        "modes": {"dnd": True, "email_off": False, "idle": True, "night": False,
                  "emergency": True, "privacy": False},
        "mode_schedule": {"night": {"start": "22:00", "end": "06:00"}},
    }


def test_config_round_trip(app_client, monkeypatch):
    """What save_config writes, load_config reads back into the same names."""
    _fix_state(monkeypatch)
    gw.save_config()
    for name, value in {
        "MODE_DND": False, "MODE_EMAIL_OFF": True, "MODE_IDLE": False, "MODE_NIGHT": True,
        "MODE_EMERGENCY": False, "MODE_PRIVACY": True, "MODE_SCHEDULE": {}, "CUSTOM_MODES": {},
        "CUSTOM_VOICE_COMMANDS": {}, "DETECTION_THRESHOLD": 0.9, "EMAIL_COOLDOWN": 1,
        "EMAIL_SENDER": "", "EMAIL_RECIPIENTS": [], "KNOWN_DEVICES": [], "WATCH_LABELS": [],
        "DANGER_LABELS": [], "NIGHT_PRESENCE_WINDOW": {},
    }.items():
        monkeypatch.setattr(gw, name, value)
    gw.load_config()
    assert (gw.MODE_DND, gw.MODE_EMAIL_OFF, gw.MODE_IDLE, gw.MODE_NIGHT, gw.MODE_EMERGENCY,
            gw.MODE_PRIVACY) == (True, False, True, False, True, False)
    assert gw.MODE_SCHEDULE == {"night": {"start": "22:00", "end": "06:00"}}
    assert gw.CUSTOM_MODES == {"movie": {"dnd": True}}
    assert gw.CUSTOM_VOICE_COMMANDS == {"lights out": "activate dnd"}
    assert (gw.DETECTION_THRESHOLD, gw.EMAIL_COOLDOWN, gw.EMAIL_SENDER) == (0.42, 75, "pi@example.com")
    assert gw.EMAIL_RECIPIENTS == ["a@example.com", "b@example.com"]
    assert gw.KNOWN_DEVICES == [{"name": "Phone", "mac": "AA:BB:CC:DD:EE:FF"}]
    assert (gw.WATCH_LABELS, gw.DANGER_LABELS) == (["Person"], ["Knife", "Hammer"])
    assert gw.NIGHT_PRESENCE_WINDOW == {"start": "01:00", "end": "04:00", "enabled": False}
