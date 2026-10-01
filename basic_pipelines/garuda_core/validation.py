"""Shape checks for values that arrive from a client.

Pure functions and patterns. Moved out of Garuda_web.py unchanged (2026-10);
that module still exports every name.
"""
import re

def _time_in_range(start: str, end: str, current: str) -> bool:
    """Return True if current (HH:MM) is in [start, end] — handles midnight wrap."""
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


# A real time of day: "\d{2}:\d{2}" also accepted 99:99, which then never matched.
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _clean_labels(labels, limit: int = 50) -> list:
    out = []
    for label in labels or []:
        label = str(label).strip()[:64]
        if label and label not in out:
            out.append(label)
    return out[:limit]


_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
