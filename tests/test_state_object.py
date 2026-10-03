"""The state object is the only home of live state; the flat globals it replaced are gone."""
import pytest

import Garuda_web as gw
from garuda_core.state import RETIRED_NAMES, State


def test_every_retired_name_points_at_a_real_field():
    fresh = State()
    assert len(RETIRED_NAMES) == 91
    for old, (group, field) in RETIRED_NAMES.items():
        assert hasattr(getattr(fresh, group), field), (old, group, field)
        assert hasattr(getattr(gw.STATE, group), field), (old, group, field)


@pytest.mark.parametrize("old", sorted(RETIRED_NAMES))
def test_retired_name_cannot_be_read_or_set(old, monkeypatch):
    group, field = RETIRED_NAMES[old]
    assert old not in vars(gw)
    with pytest.raises(AttributeError, match=f"moved to STATE.{group}.{field}"):
        getattr(gw, old)
    with pytest.raises(AttributeError, match="moved to STATE"):
        setattr(gw, old, object())
    with pytest.raises(AttributeError):
        monkeypatch.setattr(gw, old, object())
    assert old not in vars(gw), "a failed write must not leave a stale copy behind"


def test_unknown_mode_is_refused():
    with pytest.raises(KeyError):
        gw.STATE.modes.set("MODE_DND", True)      # field names only, not the old globals
    with pytest.raises(KeyError):
        gw.STATE.modes.get("lock")


def test_mode_set_and_get_by_name(monkeypatch):
    monkeypatch.setattr(gw.STATE.modes, "night", False)
    gw.STATE.modes.set("night", True)
    assert gw.STATE.modes.get("night") is True and gw.STATE.modes.night is True


def test_mail_wrapper_reads_the_state(monkeypatch):
    sent = {}
    monkeypatch.setattr(gw._mailer, "send", lambda subject, body, **kw: sent.update(kw))
    monkeypatch.setattr(gw.STATE.config, "email_sender", "pi@example.com")
    monkeypatch.setattr(gw.STATE.config, "email_sender_pass", "secret")
    monkeypatch.setattr(gw.STATE.config, "email_recipients", ["a@example.com"])
    gw._send_mail("s", "b")
    assert sent == {"sender": "pi@example.com", "password": "secret", "to": ["a@example.com"]}
