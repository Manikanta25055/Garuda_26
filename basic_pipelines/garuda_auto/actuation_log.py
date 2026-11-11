"""Every actuation, with the rule and the conditions that caused it.

This is what lets the system answer "why did the fan turn on" locally, with
no model involved: the answer is already written down.

Newline-delimited JSON so an append is one write and a partial line cannot
corrupt what came before it.
"""
import json
import logging
import os
import threading
import time

log = logging.getLogger(__name__)

# The rule loop, the home loop and every request thread write here. Without
# this, a trim (read all, rewrite, rename) threw away whatever another thread
# appended meanwhile, and two trims shared one temp file.
_write_lock = threading.Lock()
_warned_at = 0.0

MAX_LINES = 20_000
# The file itself had no limit: only the read was capped, and every read
# loaded all of it. Past this size the oldest lines are dropped.
MAX_BYTES = 4 * 1024 * 1024


def record(path, *, device, action, rule_id, matched, ok, reason="", clock=time.time,
           source="", actor=""):
    entry = {
        "ts": clock(),
        "device": device,
        "action": action,
        "rule_id": rule_id,
        "matched": matched,
        "ok": ok,
        "reason": reason,
    }
    # Who asked: "rule", "manual", "scene:<id>", "schedule:<id>", "away",
    # "assistant". Usage stats and habit suggestions need to tell a person's
    # own switching apart from the house acting on its own.
    if source:
        entry["source"] = source
    if actor:
        entry["actor"] = actor
    # The switch has already happened by the time it is written down. A full
    # disk or a read-only card must not turn that into an exception: it used
    # to end the rule loop's tick halfway and answer a button press with an
    # error after the lamp had come on. Returns whether the line was written.
    global _warned_at
    try:
        line = json.dumps(entry, default=str) + "\n"
        with _write_lock:
            directory = os.path.dirname(path) or "."
            os.makedirs(directory, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
            _trim(path)
        return True
    except (OSError, ValueError, TypeError) as exc:
        now = time.monotonic()
        if now - _warned_at >= 60 or not _warned_at:
            _warned_at = now
            log.error("activity log %s could not be written: %s: %s",
                      path, type(exc).__name__, exc)
        return False


def _trim(path):
    try:
        if os.path.getsize(path) <= MAX_BYTES:
            return
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()[-MAX_LINES // 2:]
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.writelines(lines)
        os.replace(tmp, path)
    except OSError:
        pass


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()[-MAX_LINES:]
    except OSError:
        return []
    entries = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        # Every reader takes an entry to be an object that names its device
        # (the Activity page, usage, habits, Narada); a line that was valid
        # JSON but not that made each of them fail for as long as it stayed
        # in the file.
        if isinstance(entry, dict) and isinstance(entry.get("device"), str):
            entries.append(entry)
    return entries


def recent(path, limit=200):
    return list(reversed(_read(path)))[:limit]


def last_for(path, device):
    for entry in reversed(_read(path)):
        if entry.get("device") == device:
            return entry
    return None
