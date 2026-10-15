"""Text as it should be said: what Narada sends to the voice, never the chat.

The voice (Raju, an Indian English voice on eleven_v4_turbo) reads bare
digits in Hindi ("45" came out as "paintalis"), spells out "eg" and "Oct",
and says "tilde" for "~". The model writes for the screen, and the screen
should keep "Oct 16, ~45%". So the words are written out here, between the
model and ElevenLabs, for every sentence the voice says: numbers become
English words, and abbreviations and symbols become what a person would say.

Chosen over asking the model to write for the ear (it ignores such rules
often enough, and the chat would lose the short forms) and over ElevenLabs'
own text normalisation (it adds latency to every reply, and it is what read
the numbers in Hindi in the first place).
"""
import re

_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
         "fourteen fifteen sixteen seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
_SCALES = ((10 ** 12, "trillion"), (10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand"))
_ORDINAL = {"one": "first", "two": "second", "three": "third", "five": "fifth",
            "eight": "eighth", "nine": "ninth", "twelve": "twelfth"}
_MONTHS = ("January February March April May June July August September "
           "October November December").split()
_MONTH_ABBR = {"jan": "January", "feb": "February", "mar": "March", "apr": "April",
               "jun": "June", "jul": "July", "aug": "August", "sep": "September",
               "sept": "September", "oct": "October", "nov": "November", "dec": "December"}
_DAY_ABBR = {"mon": "Monday", "tue": "Tuesday", "tues": "Tuesday", "wed": "Wednesday",
             "thu": "Thursday", "thur": "Thursday", "thurs": "Thursday", "fri": "Friday",
             "sat": "Saturday", "sun": "Sunday"}
# (singular, plural), after a number only. Longest first, so "ms" wins over "m".
_UNITS = {
    "%": ("percent", "percent"), "°C": ("degree Celsius", "degrees Celsius"),
    "°F": ("degree Fahrenheit", "degrees Fahrenheit"), "°": ("degree", "degrees"),
    "ms": ("millisecond", "milliseconds"), "s": ("second", "seconds"),
    "sec": ("second", "seconds"), "secs": ("second", "seconds"),
    "min": ("minute", "minutes"), "mins": ("minute", "minutes"),
    "h": ("hour", "hours"), "hr": ("hour", "hours"), "hrs": ("hour", "hours"),
    "km/h": ("kilometre per hour", "kilometres per hour"), "km": ("kilometre", "kilometres"),
    "cm": ("centimetre", "centimetres"), "mm": ("millimetre", "millimetres"),
    "kg": ("kilogram", "kilograms"), "g": ("gram", "grams"),
    "KB": ("kilobyte", "kilobytes"), "MB": ("megabyte", "megabytes"),
    "GB": ("gigabyte", "gigabytes"), "TB": ("terabyte", "terabytes"),
    "kWh": ("kilowatt hour", "kilowatt hours"), "kW": ("kilowatt", "kilowatts"),
    "W": ("watt", "watts"), "V": ("volt", "volts"), "mA": ("milliamp", "milliamps"),
    "Hz": ("hertz", "hertz"), "kHz": ("kilohertz", "kilohertz"),
    "MHz": ("megahertz", "megahertz"), "GHz": ("gigahertz", "gigahertz"),
    "fps": ("frame per second", "frames per second"), "x": ("times", "times"),
}
_NUM = r"\d+(?:\.\d+)?"
# A short form's dot, taken only mid-sentence: before a capital or at the end
# it is also the sentence's full stop, and the voice needs it for its pause.
_DOT = r"(?:\.(?!\s*$|\s+[A-Z]))?"


def number_words(n):
    """45 -> "forty-five"; 1203 -> "one thousand two hundred and three"."""
    n = int(n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else "")
    if n < 1000:
        rest = n % 100
        return _ONES[n // 100] + " hundred" + (" and " + number_words(rest) if rest else "")
    for size, name in _SCALES:
        if n >= size:
            head, rest = divmod(n, size)
            tail = ""
            if rest:
                tail = (" and " if rest < 100 else " ") + number_words(rest)
            return number_words(head) + " " + name + tail
    return " ".join(_ONES[int(d)] for d in str(n))


def ordinal_words(n):
    """16 -> "sixteenth"; 21 -> "twenty-first"."""
    words = number_words(n)
    head, sep, last = words.rpartition("-") if "-" in words else words.rpartition(" ")
    if last in _ORDINAL:
        last = _ORDINAL[last]
    elif last.endswith("y"):
        last = last[:-1] + "ieth"
    else:
        last += "th"
    return head + sep + last


def year_words(n):
    """2026 -> "twenty twenty-six"; 2005 -> "two thousand and five"."""
    n = int(n)
    if 2000 <= n < 2010 or n % 1000 == 0:
        return number_words(n)
    hi, lo = divmod(n, 100)
    if lo == 0:
        return number_words(hi) + " hundred"
    return number_words(hi) + (" oh " if lo < 10 else " ") + number_words(lo)


def _digits(s):
    return " ".join(_ONES[int(d)] for d in s if d.isdigit())


def _decimal(s):
    whole, _, frac = s.partition(".")
    return number_words(whole) + (" point " + _digits(frac) if frac else "")


def _like(word, model):
    """`word` with the case of `model`'s first letter (sentence starts)."""
    return word[0].upper() + word[1:] if model[:1].isupper() else word


def _time(m):
    h, mins, ampm = int(m.group(1)), int(m.group(2)), (m.group(4) or "").replace(".", "").upper()
    if h > 23 or mins > 59:
        return m.group(0)
    if not ampm and h > 12:
        h, ampm = h - 12, "PM"
    elif not ampm and h == 0:
        h, ampm = 12, "AM"
    said = number_words(h)
    if mins:
        said += (" oh " if mins < 10 else " ") + number_words(mins)
    elif not ampm:
        said += " o'clock"
    return said + (" " + ampm if ampm else "")


def _unit(m):
    value, unit = m.group(1), m.group(2)
    one, many = _UNITS[unit]
    return (_decimal(value) if "." in value else number_words(value)) + " " + (one if value == "1" else many)


def _glued(num):
    """A number written onto letters: "camera2" -> "camera two", "H264" -> "H two six four"."""
    return number_words(num) if len(num) <= 2 else _digits(num)


_MONTH_RE = "|".join(_MONTHS)
_ABBR_RE = "|".join(sorted(_MONTH_ABBR, key=len, reverse=True))
_UNIT_RE = "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True))

_RULES = [
    # Arrows and symbols that are said as a word.
    (re.compile(r"\s*(?:->|→|=>)\s*"), " to "),
    (re.compile(r"≈\s*|~\s*(?=[\d₹$])"), "about "),
    (re.compile(r"\s*~\s*"), " "),
    (re.compile(r"\s*&\s*"), " and "),
    (re.compile(r"±\s*"), "plus or minus "),
    (re.compile(r"#\s*(?=\d)"), "number "),
    (re.compile(r"\b[Nn]o\.\s*(?=\d)"), "number "),
    (re.compile(r"(?<=\w)\s*@\s*(?=\w)"), " at "),
    (re.compile(r"[*_`|•#]+"), " "),
    # Common short forms. The model writes them; nobody says them.
    (re.compile(r"\b(e)\.\s?g\b\.?|\b(e)g\b\.?(?=[\s,:;]|$)", re.I),
     lambda m: _like("for example", m.group(0))),
    (re.compile(r"\b(i)\.\s?e\b\.?|\b(i)e\b\.?(?=[\s,:;]|$)", re.I),
     lambda m: _like("that is", m.group(0))),
    (re.compile(r"\betc\.(?=\s*$)", re.I), "and so on."),
    (re.compile(r"\betc\b\.?", re.I), "and so on"),
    (re.compile(r"\bvs\b" + _DOT, re.I), "versus"),
    (re.compile(r"\bapprox\b" + _DOT, re.I), lambda m: _like("about", m.group(0))),
    (re.compile(r"\bw/o\b", re.I), "without"),
    (re.compile(r"\bw/(?=\s)", re.I), "with"),
    (re.compile(r"\bavg\b" + _DOT, re.I), lambda m: _like("average", m.group(0))),
    (re.compile(r"\bpls\b\.?|\bplz\b", re.I), lambda m: _like("please", m.group(0))),
    # Month and day short forms: always when capitalised ("Oct"), and in
    # lower case only next to a number ("oct 16"), where they cannot be words.
    (re.compile(r"\b(" + _ABBR_RE.title() + r")\b" + _DOT),
     lambda m: _MONTH_ABBR[m.group(1).lower()]),
    (re.compile(r"\b(" + _ABBR_RE + r")\b\.?(?=\s*\d)|(?<=\d)(\s+)(" + _ABBR_RE + r")\b" + _DOT),
     lambda m: (m.group(2) or "") + _MONTH_ABBR[(m.group(1) or m.group(3)).lower()]),
    (re.compile(r"\b(Mon|Tues?|Wed|Thu(?:rs?)?|Fri|Sat|Sun)\b\.?(?=,?\s*(?:\d|" + _MONTH_RE + r"))"),
     lambda m: _DAY_ABBR[m.group(1).lower()]),
    # Dates: 2026-10-16, 16/10/2026 (day first, as written in India), Oct 16, 16th Oct.
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"),
     lambda m: (f"{_MONTHS[int(m.group(2)) - 1]} {ordinal_words(m.group(3))}, {year_words(m.group(1))}"
                if 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(3)) <= 31 else m.group(0))),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"),
     lambda m: (f"{ordinal_words(m.group(1))} {_MONTHS[int(m.group(2)) - 1]} {year_words(m.group(3))}"
                if 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(1)) <= 31 else m.group(0))),
    (re.compile(r"\b(" + _MONTH_RE + r")\s+(\d{1,2})(?:st|nd|rd|th)?\b(?![:.]\d)"),
     lambda m: m.group(1) + " " + ordinal_words(m.group(2))),
    (re.compile(r"(?:\bthe\s+)?\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(" + _MONTH_RE + r")\b"),
     lambda m: "the " + ordinal_words(m.group(1)) + " of " + m.group(2)),
    # Clock times: 16:30 -> four thirty PM; 9:05 am -> nine oh five AM.
    (re.compile(r"\b(0\d|(?<=\bat )[01]?\d|(?<=\bat )2[0-3])\.([0-5]\d)\b(?!\.\d)"), lambda m: m.group(1) + ":" + m.group(2)),
    (re.compile(r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\s*([AaPp]\.[Mm]\.|[AaPp][Mm]\b))?"), _time),
    # Thousands separators, Western (1,234,567) and Indian (12,34,567).
    (re.compile(r"\b\d{1,3}(?:,\d{2,3})*,\d{3}\b"), lambda m: m.group(0).replace(",", "")),
    # Money.
    (re.compile(r"(?:₹|\bRs\.?|\bINR)\s*(" + _NUM + r")"), r"\1 rupees"),
    (re.compile(r"\$\s*(" + _NUM + r")"), r"\1 dollars"),
    # Ranges, scores and signs.
    (re.compile(r"(?<=\d)\s?[-–]\s?(?=\d)"), " to "),
    (re.compile(r"\b24\s?/\s?7\b"), "twenty-four seven"),
    (re.compile(r"\b(\d+)\s?/\s?(\d+)\b"), r"\1 out of \2"),
    (re.compile(r"(?<![\w.])[-−](?=\d)"), "minus "),
    (re.compile(r"(?<=\d)\+"), " plus"),
    # Numbers with a unit: 45% -> forty-five percent; 1 s -> one second.
    (re.compile(r"(" + _NUM + r")\s?(" + _UNIT_RE + r")(?![A-Za-z])"), _unit),
    # Addresses and versions: 192.168.1.4 -> one nine two dot ...
    (re.compile(r"\b\d+(?:\.\d+){2,}\b"), lambda m: " dot ".join(_digits(p) for p in m.group(0).split("."))),
    (re.compile(r"\b(\d+)(?:st|nd|rd|th)\b"), lambda m: ordinal_words(m.group(1))),
    (re.compile(r"\b\d+\.\d+\b"), lambda m: _decimal(m.group(0))),
    # Years, said as years.
    (re.compile(r"\b(?:19|20)\d{2}\b(?!\.\d)"), lambda m: year_words(m.group(0))),
    # Phone numbers, codes, anything long or with a leading zero: digit by digit.
    (re.compile(r"\b0\d+\b|\b\d{7,}\b"), lambda m: _digits(m.group(0))),
    (re.compile(r"(?<=[A-Za-z])(\d+)"), lambda m: " " + _glued(m.group(1))),
    (re.compile(r"(\d+)(?=[A-Za-z])"), lambda m: _glued(m.group(1)) + " "),
    (re.compile(r"\d+"), lambda m: number_words(m.group(0))),
]


def for_speech(text):
    """`text` as the voice should say it. Leading and trailing spaces are kept
    (sentences are streamed with their trailing space; a lone " " keeps
    ElevenLabs waiting and must stay a lone space)."""
    if not text or not text.strip():
        return text
    lead = text[:len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    body = text.strip()
    for pattern, repl in _RULES:
        body = pattern.sub(repl, body)
    body = re.sub(r"[ \t]{2,}", " ", body)
    body = re.sub(r" +([,.!?;:])", r"\1", body).strip()
    return lead + body + trail
