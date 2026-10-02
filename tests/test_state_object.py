"""The state object and the flat names that forward to it are one value, not two."""
import pytest

import Garuda_web as gw

MODES = {"MODE_DND": "dnd", "MODE_EMAIL_OFF": "email_off", "MODE_IDLE": "idle",
         "MODE_NIGHT": "night", "MODE_EMERGENCY": "emergency", "MODE_PRIVACY": "privacy"}


@pytest.mark.parametrize("old,field", sorted(MODES.items()))
def test_flat_mode_name_and_state_field_are_the_same_value(old, field, monkeypatch):
    assert old not in vars(gw), "a module global would shadow nothing but go stale"
    before = getattr(gw.STATE.modes, field)
    monkeypatch.setattr(gw, old, not before)
    assert getattr(gw.STATE.modes, field) is (not before)
    monkeypatch.undo()
    assert getattr(gw.STATE.modes, field) is before
    monkeypatch.setattr(gw.STATE.modes, field, not before)
    assert getattr(gw, old) is (not before)


def test_schedule_custom_and_lock_forward(monkeypatch):
    monkeypatch.setattr(gw, "MODE_SCHEDULE", {"night": {"start": "22:00", "end": "06:00"}})
    monkeypatch.setattr(gw, "CUSTOM_MODES", {"movie": {}})
    assert gw.STATE.modes.schedule == {"night": {"start": "22:00", "end": "06:00"}}
    assert gw.STATE.modes.custom == {"movie": {}}
    assert gw._mode_lock is gw.STATE.modes.lock


def test_unknown_mode_is_refused():
    with pytest.raises(KeyError):
        gw.STATE.modes.set("MODE_DND", True)      # field names only, not the old globals
    with pytest.raises(KeyError):
        gw.STATE.modes.get("lock")


CONFIG = {"EMAIL_SENDER": "email_sender", "EMAIL_SENDER_PASS": "email_sender_pass",
          "EMAIL_RECIPIENTS": "email_recipients", "EMAIL_COOLDOWN": "email_cooldown",
          "DETECTION_THRESHOLD": "detection_threshold", "DANGER_LABELS": "danger_labels",
          "WATCH_LABELS": "watch_labels", "KNOWN_DEVICES": "known_devices",
          "NIGHT_PRESENCE_WINDOW": "night_presence_window",
          "CUSTOM_VOICE_COMMANDS": "custom_voice_commands"}


@pytest.mark.parametrize("old,field", sorted(CONFIG.items()))
def test_flat_config_name_and_state_field_are_the_same_value(old, field, monkeypatch):
    assert old not in vars(gw)
    marker = object()
    monkeypatch.setattr(gw, old, marker)
    assert getattr(gw.STATE.config, field) is marker
    monkeypatch.undo()
    assert getattr(gw.STATE.config, field) is getattr(gw, old) is not marker


def test_mail_wrapper_reads_the_state(monkeypatch):
    sent = {}
    monkeypatch.setattr(gw._mailer, "send", lambda subject, body, **kw: sent.update(kw))
    monkeypatch.setattr(gw, "EMAIL_SENDER", "pi@example.com")
    monkeypatch.setattr(gw.STATE.config, "email_sender_pass", "secret")
    monkeypatch.setattr(gw, "EMAIL_RECIPIENTS", ["a@example.com"])
    gw._send_mail("s", "b")
    assert sent == {"sender": "pi@example.com", "password": "secret", "to": ["a@example.com"]}
