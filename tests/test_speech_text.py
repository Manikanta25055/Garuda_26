"""What the voice is given to say: numbers in English words, no short forms."""
import pytest

from garuda_auto.speech_text import for_speech, number_words, ordinal_words, year_words


@pytest.mark.parametrize("n, said", [
    (0, "zero"), (7, "seven"), (45, "forty-five"), (100, "one hundred"),
    (1203, "one thousand two hundred and three"), (2500000, "two million five hundred thousand"),
])
def test_number_words(n, said):
    assert number_words(n) == said


def test_ordinals_and_years():
    assert [ordinal_words(n) for n in (1, 2, 3, 12, 16, 20, 21)] == [
        "first", "second", "third", "twelfth", "sixteenth", "twentieth", "twenty-first"]
    assert [year_words(n) for n in (2026, 2005, 1998, 1900)] == [
        "twenty twenty-six", "two thousand and five", "nineteen ninety-eight", "nineteen hundred"]


@pytest.mark.parametrize("text, said", [
    # The owner's three: eg, Oct and ~.
    ("Use ~3 cameras, eg front and back.", "Use about three cameras, for example front and back."),
    ("E.g. the door on Oct 16.", "For example the door on October sixteenth."),
    ("It is ~ fine.", "It is fine."),
    # Digits never reach the voice (Raju read them in Hindi).
    ("45 alerts at 16:30.", "forty-five alerts at four thirty PM."),
    ("Back at 9:05 am.", "Back at nine oh five AM."),
    ("Battery at 87%, 23.5°C.", "Battery at eighty-seven percent, twenty-three point five degrees Celsius."),
    ("1,250 events, 3-5 per hour.", "one thousand two hundred and fifty events, three to five per hour."),
    ("Score 37/38 on 2026-10-04.", "Score thirty-seven out of thirty-eight on October fourth, twenty twenty-six."),
    ("About ₹1,500 a month.", "About one thousand five hundred rupees a month."),
    ("Camera2 saw 1 person in 3 s.", "Camera two saw one person in three seconds."),
    ("The 3rd vs #4, etc.", "The third versus number four, and so on."),
    ("On the 16th of October.", "On the sixteenth of October."),
    ("Fri, Oct 16 at 10:00.", "Friday, October sixteenth at ten o'clock."),
    ("Dropped to -5 degrees.", "Dropped to minus five degrees."),
    # A short form's dot is also the sentence's full stop at the end.
    ("Tamper alerts at 04.11 on 13 Oct. ", "Tamper alerts at four eleven on the thirteenth of October. "),
    ("Is it 3 vs. 4?", "Is it three versus four?"),
    ("Approx. 5 min.", "About five minutes."),
])
def test_for_speech(text, said):
    assert for_speech(text) == said


def test_words_and_spacing_are_kept():
    assert for_speech(" ") == " "                          # the keep-alive chunk stays blank
    assert for_speech("I'm doing great. ") == "I'm doing great. "
    assert for_speech("Mark the march route.") == "Mark the march route."   # not a month
