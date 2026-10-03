"""The permanent logs, read back: every day that is on disk, not only the newest lines.

The system, detection and voice logs are text files of "[YYYY-MM-DD HH:MM:SS]
message" lines, rotated by size into path.1, path.2 ... (path.1 the newest of
the old ones). Until 2026-10 nothing read them back except the last 500 lines
at startup, so the dashboard's Activity card and Narada saw the last few hours
at most, while weeks sat on the disk.

Here each file is indexed once (lines per day, first and last stamp) and the
index is kept by inode, so a rotated file is not read again and the live one is
read only from where it last ended. A question for a day or an hour then opens
only the files that cover it.

parse_moment() turns what a person writes or copies ("[2026-10-02 14:33:05]",
"14:33", "2 Oct 2:33 pm", "yesterday 9pm") into a time, so the website and
Narada read a time the same way.
"""
import datetime
import os
import re
import threading
from collections import Counter

STAMP_FMT = "%Y-%m-%d %H:%M:%S"
_STAMP = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s?")

# ── reading the files ────────────────────────────────────────────────────────


def generations(path, keep):
    """The files of one log that exist, oldest first: path.keep ... path.1, path."""
    out = [f"{path}.{i}" for i in range(keep, 0, -1)] + [path]
    return [p for p in out if os.path.exists(p)]


class _Index:
    __slots__ = ("size", "mtime", "days", "first", "last")

    def __init__(self):
        self.size, self.mtime = 0, 0.0
        self.days, self.first, self.last = Counter(), "", ""


_cache = {}             # (st_dev, st_ino) -> _Index
_cache_lock = threading.Lock()


def _scan(fh, index):
    for raw in fh:
        m = _STAMP.match(raw.decode("utf-8", "replace"))
        if not m:
            continue
        ts = m.group(1)
        index.days[ts[:10]] += 1
        index.first = index.first or ts
        index.last = ts


def _index(path):
    """The day index of one file, brought up to date. None if it cannot be read."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (st.st_dev, st.st_ino)
    with _cache_lock:
        index = _cache.get(key)
        if index is not None and st.st_size == index.size and st.st_mtime == index.mtime:
            return index
        start = index.size if index is not None and st.st_size > index.size else 0
        if start == 0:
            index = _Index()
        try:
            with open(path, "rb") as fh:
                fh.seek(start)
                _scan(fh, index)
        except OSError:
            return None
        index.size, index.mtime = st.st_size, st.st_mtime
        _cache[key] = index
        if len(_cache) > 256:              # files that no longer exist
            for stale in list(_cache)[:-128]:
                _cache.pop(stale, None)
        return index


def day_counts(path, keep):
    """{"YYYY-MM-DD": lines} over every generation of one log."""
    total = Counter()
    for p in generations(path, keep):
        index = _index(p)
        if index is not None:
            total.update(index.days)
    return dict(total)


def _read_file(p, start, end):
    """[stamp, text] of one file's lines with start <= stamp < end, in file order.

    A line without a stamp of its own (a message that held a newline) belongs
    to the line before it.
    """
    out = []
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            current = None
            for raw in fh:
                line = raw.rstrip("\n")
                m = _STAMP.match(line)
                if m is None:
                    if current is not None and line:
                        current[1] += "\n" + line
                    continue
                ts = m.group(1)
                if start <= ts < end:
                    current = [ts, line[m.end():]]
                    out.append(current)
                else:
                    current = None
    except OSError:
        return []
    return out


def _covering(path, keep, start, end):
    """The generations that hold a line in [start, end), oldest first."""
    out = []
    for p in generations(path, keep):
        index = _index(p)
        if index is None or not index.first or index.last < start or index.first >= end:
            continue
        out.append(p)
    return out


def read_lines(path, keep, start="", end="9999", accept=None, newest=None):
    """(stamp, text) for the lines with start <= stamp < end, oldest first.

    accept(stamp, text) narrows them further. With newest=N only the newest N
    accepted lines are wanted: the files are read newest first and reading
    stops once there are more than N (so the caller can tell there were more),
    instead of reading every old file for a page of fifty lines.
    """
    out = []
    files = _covering(path, keep, start, end)
    for p in (reversed(files) if newest is not None else files):
        lines = [(ts, text) for ts, text in _read_file(p, start, end)
                 if accept is None or accept(ts, text)]
        out = lines + out if newest is not None else out + lines
        if newest is not None and len(out) > newest:
            break
    out.sort(key=lambda e: e[0])            # stable: lines of one second keep their order
    return out[-(newest + 1):] if newest is not None else out


# ── choosing what to show ────────────────────────────────────────────────────


def matches(text, query):
    """Every word of the query appears in the text, in any case."""
    if not query:
        return True
    low = text.lower()
    return all(word in low for word in query.lower().split())


def pick(entries, limit, at=""):
    """At most `limit` of chronological entries: those nearest `at`, or else the newest.

    Returns (kept, omitted_before, omitted_after).
    """
    if len(entries) <= limit:
        return entries, 0, 0
    if not at:
        return entries[-limit:], len(entries) - limit, 0
    # The entry closest to the moment, then outwards on both sides.
    centre = min(range(len(entries)), key=lambda i: abs(_seconds(entries[i]["ts"]) - _seconds(at)))
    lo = max(0, min(centre - limit // 2, len(entries) - limit))
    return entries[lo:lo + limit], lo, len(entries) - lo - limit


def _seconds(stamp):
    try:
        return datetime.datetime.strptime(stamp[:19], STAMP_FMT).timestamp()
    except ValueError:
        return 0.0


def stamp(dt):
    return dt.strftime(STAMP_FMT)


# ── reading a time from what a person wrote ──────────────────────────────────

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_MON = (r"(january|february|march|april|may|june|july|august|september|october|november|"
        r"december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\b\.?")
_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_ISO_DATE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T]+(\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?)?")
# 2/10, 2/10/2026, 02.10.2026 (a dotted pair alone is a number, as in conf=0.87).
_NUM_DATE = re.compile(r"\b(\d{1,2})(?:/(\d{1,2})(?:/(\d{2,4}))?|\.(\d{1,2})\.(\d{2,4}))\b")
_DAY_MON = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?\s+" + _MON + r"(?:,?\s+(\d{4}))?", re.I)
_MON_DAY = re.compile(r"\b" + _MON + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(\d{4}))?", re.I)
_REL_DAY = re.compile(r"\b(day before yesterday|yesterday|today|tonight|this morning|"
                      r"this evening|this afternoon|(?:last\s+)?(?:" + "|".join(_DAYS)
                      + r")|(?:last\s+)?(?:mon|tue|tues|wed|thu|thur|thurs|fri))\b", re.I)
_CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s*(am|pm|a\.m\.|p\.m\.)?", re.I)
_HOUR = re.compile(r"\b(\d{1,2})\s*(am|pm|a\.m\.|p\.m\.)", re.I)
_WORDS = re.compile(r"\b(noon|midday|midnight)\b", re.I)
# "sat" and "sun" are words too often to be days.
_SHORT_DAYS = {"mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3,
               "fri": 4}


class Moment:
    """A time read from text, and how exact it was: precision is in seconds
    (1 for 14:33:05, 60 for 14:33, 3600 for 2 pm, 86400 for a day alone)."""
    __slots__ = ("dt", "precision", "has_date", "has_time")

    def __init__(self, dt, precision, has_date, has_time):
        self.dt, self.precision, self.has_date, self.has_time = dt, precision, has_date, has_time

    def __repr__(self):
        return f"Moment({stamp(self.dt)}, ±{self.precision}s)"


def _hour24(hour, ampm):
    if not ampm:
        return hour
    pm = ampm.lower().startswith("p")
    if hour == 12:
        return 12 if pm else 0
    return hour + 12 if pm else hour


def _find_date(text, now):
    """(date, matched span) or (None, None)."""
    today = now.date()
    for rx in (_ISO_DATE, _DAY_MON, _MON_DAY, _NUM_DATE):
        for m in rx.finditer(text):
            try:
                if rx is _ISO_DATE:
                    return datetime.date(int(m[1]), int(m[2]), int(m[3])), m
                if rx is _DAY_MON:
                    day, mon, year = int(m[1]), _MONTHS[m[2].lower()[:3]], m[3]
                elif rx is _MON_DAY:
                    mon, day, year = _MONTHS[m[1].lower()[:3]], int(m[2]), m[3]
                else:
                    # Day first, as dates are written here (2/10 is the 2nd of October).
                    day = int(m[1])
                    mon, year = (int(m[2]), m[3]) if m[2] else (int(m[4]), m[5])
                    if year and len(year) == 2:
                        year = "20" + year
                d = datetime.date(int(year) if year else today.year, mon, day)
            except (ValueError, KeyError):
                continue                    # 31/02, or numbers that only look like a date
            if not year and d > today:
                d = d.replace(year=d.year - 1)
            return d, m
    m = _REL_DAY.search(text)
    if m:
        word = m[1].lower()
        if word == "day before yesterday":
            return today - datetime.timedelta(days=2), m
        if word == "yesterday":
            return today - datetime.timedelta(days=1), m
        if word in ("today", "tonight") or word.startswith("this "):
            return today, m
        name = word.replace("last", "").strip()
        target = _DAYS.index(name) if name in _DAYS else _SHORT_DAYS[name]
        back = (today.weekday() - target) % 7
        if back == 0 and word.startswith("last"):
            back = 7
        return today - datetime.timedelta(days=back), m
    return None, None


def parse_moment(text, now=None):
    """A Moment from what a person wrote or copied from a log, or None."""
    if not text or not isinstance(text, str):
        return None
    now = now or datetime.datetime.now()
    text = text.strip().strip("[]\"'`").strip()
    day, m = _find_date(text, now)
    rest = text
    if m is not None:
        if m.re is _ISO_DATE and m[4] is not None:
            # The date carried its own time.
            sec = m[6]
            dt = datetime.datetime.combine(day, datetime.time(int(m[4]), int(m[5]), int(sec or 0)))
            return Moment(dt, 1 if sec is not None else 60, True, True)
        rest = text[:m.start()] + " " + text[m.end():]
    hour = minute = second = None
    precision = None
    c = _CLOCK.search(rest)
    if c:
        hour, minute = _hour24(int(c[1]), c[4]), int(c[2])
        second = int(c[3]) if c[3] is not None else 0
        precision = 1 if c[3] is not None else 60
    else:
        h = _HOUR.search(rest)
        if h:
            hour, minute, second, precision = _hour24(int(h[1]), h[2]), 0, 0, 3600
        else:
            w = _WORDS.search(rest)
            if w:
                hour = 12 if w[1].lower() in ("noon", "midday") else 0
                minute, second, precision = 0, 0, 60
    if hour is None:
        if day is None:
            return None
        return Moment(datetime.datetime.combine(day, datetime.time()), 86400, True, False)
    try:
        clock = datetime.time(hour, minute, second)
    except ValueError:
        return None
    if day is None:
        # A time alone is the most recent one: 23:10 asked at 09:00 is last night.
        dt = datetime.datetime.combine(now.date(), clock)
        if dt > now + datetime.timedelta(minutes=1):
            dt -= datetime.timedelta(days=1)
        return Moment(dt, precision, False, True)
    return Moment(datetime.datetime.combine(day, clock), precision, True, True)
