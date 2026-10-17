"""Replies checked against the house (garuda_auto.claims)."""
import pytest

from basic_pipelines.garuda_auto.claims import correction, false_claims

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
