"""Replies checked against the house (garuda_auto.claims)."""
import pytest

from basic_pipelines.garuda_auto.claims import correction, false_claims, from_jev, worth_reading

HOUSE = [{"id": "lamp", "name": "Lamp", "state": "off"},
         {"id": "fan", "name": "Fan", "state": "on"},
         {"id": "tv", "name": "TV", "state": "off"}]


@pytest.mark.parametrize("reply, wrong", [
    ("The lamp is on in the study.", ["Lamp"]),
    ("The lamp is now on.", ["Lamp"]),
    ("I turned on the lamp in the study. The TV stays off.", ["Lamp"]),
    ("I switched the fan off.", ["Fan"]),
    ("The lamp and the TV are on now.", ["Lamp", "TV"]),
    ("I turned off the TV. The lamp and the fan remain on.", ["Lamp"]),
    ("Done: lamp on, fan off.", ["Lamp", "Fan"]),
])
def test_a_false_state_is_caught(reply, wrong):
    assert sorted(n for n, _, _ in false_claims(reply, HOUSE)) == sorted(wrong)


@pytest.mark.parametrize("reply", [
    "The fan is on and the lamp is off.",
    "The fan is already on.",
    "The TV stays off, and the fan is still on.",
    "Want me to turn the lamp on?",
    "I can turn the lamp on for you if you'd like.",
    "The lamp will be on at 7 pm.",
    "The fan will turn off in 10 minutes.",
    "I set the lamp to switch on at 7.",
    "The lamp is not on.",
    "I couldn't switch the lamp on: the switch guard held it back.",
    "Which light should I turn off?",
    "लाइट ऑन है",
    "The lamp was on for three hours.",
    "The lamp turned on at 7:02 and the fan switched off this morning.",
    "## Today\n\n1. **Door** opened\n2. Lamp on\n3. Fan off",
    "Yesterday the lamp was on, and it stayed on 4 hours.",
    "The weather is on my mind.",
    "",
])
def test_true_or_non_claims_pass(reply):
    assert false_claims(reply, HOUSE) == []


def test_a_device_in_an_unknown_state_is_not_judged():
    assert false_claims("The lamp is on.", [{"id": "lamp", "name": "Lamp", "state": "unknown"}]) == []


def test_the_correction_gives_the_true_state():
    note = correction([("Lamp", "on", "off")], HOUSE)
    assert "Lamp is off, not on" in note and "Fan on" in note and "tools" in note


# ── where the pattern cannot vouch: Jev's reading (from_jev, worth_reading) ──

def test_jev_reading_counts_only_when_sure_and_contradicted():
    stated = {"lamp": ("on", 0.93), "fan": ("off", 0.57), "tv": ("off", 0.99)}
    assert from_jev(stated, HOUSE) == [("Lamp", "on", "off")]


def test_jev_reading_leaves_unknown_and_unread_devices_alone():
    house = HOUSE + [{"id": "heater", "name": "Heater", "state": "unknown"}]
    assert from_jev({"heater": ("on", 0.99), "lamp": ("none", 1.0)}, house) == []
    assert from_jev({}, HOUSE) == []


SWITCHED = [{"tool": "set_device", "ok": True, "waiting": False}]
FAILED = [{"tool": "set_device", "ok": False, "waiting": False}]


@pytest.mark.parametrize("reply, steps, intent, read", [
    ("Done.", [], "device_control", True),                 # a command, nothing switched
    ("I turned the lamp on.", FAILED, "device_control", True),   # the switch failed
    ("All set, the fan has been shut down.", [], "all_off", True),
    ("Lamp is on.", SWITCHED, "device_control", False),    # switched, English: the pattern's
    ("లైట్ ఆన్ చేశాను.", SWITCHED, "device_control", True),    # another script
    ("Here is a story about dragons.", [], "other", False),     # not a device turn
    ("Yes, the fan is on.", [], "state_query", False),
    ("1. Lamp on\n2. Fan off", [], "device_control", False),    # a list: past events as likely
    ("", [], "device_control", False),
    ("Done.", [], None, False),                             # no route, no device step
])
def test_what_jev_is_asked_to_read(reply, steps, intent, read):
    assert worth_reading(reply, steps, intent) is read
