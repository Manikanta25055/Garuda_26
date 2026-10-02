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
