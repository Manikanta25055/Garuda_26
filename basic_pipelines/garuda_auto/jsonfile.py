"""Atomic JSON persistence for the small stores under system_logs/.

A power cut halfway through a write must leave the previous file intact, so
every save goes to a temp file in the same directory and is renamed over.
"""
import glob
import json
import logging
import os
import shutil
import tempfile
import time

log = logging.getLogger(__name__)

# How many set-aside copies of one damaged file are kept.
QUARANTINE_KEEP = 3


def quarantine(path, why="unreadable"):
    """Keep a copy of a file that could not be used, and say so.

    A store that cannot read its file starts empty, and its next save writes
    that emptiness over the file: one damaged byte in rules.json cost every
    rule the house had learned, with nothing in the log. The copy
    (`<name>.corrupt-<time>`) is what a person restores from. Returns the
    copy's path, or None when there was nothing to copy. Never raises.
    """
    try:
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            return None
        copy = f"{path}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(path, copy)
        for old in sorted(glob.glob(glob.escape(path) + ".corrupt-*"))[:-QUARANTINE_KEEP]:
            try:
                os.unlink(old)
            except OSError:
                pass
        log.error("%s is %s; a copy was kept as %s", path, why, os.path.basename(copy))
        return copy
    except OSError as exc:
        log.error("%s is %s and could not be copied aside: %s", path, why, exc)
        return None


def load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return default
    except OSError:
        return default
    except ValueError:
        quarantine(path, "not valid JSON")
        return default
    if not isinstance(data, type(default)):
        quarantine(path, f"not a {type(default).__name__}")
        return default
    return data


def save(path, data):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            # Without this the rename can reach the disk before the data: a
            # power cut then leaves an empty file where the old one was.
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
