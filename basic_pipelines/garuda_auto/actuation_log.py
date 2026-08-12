"""Every actuation, with the rule and the conditions that caused it.

This is what lets the system answer "why did the fan turn on" locally, with
no model involved: the answer is already written down.

Newline-delimited JSON so an append is one write and a partial line cannot
corrupt what came before it.
"""
import json
import os
import time

MAX_LINES = 20_000


def record(path, *, device, action, rule_id, matched, ok, reason="", clock=time.time):
    entry = {
        "ts": clock(),
        "device": device,
        "action": action,
        "rule_id": rule_id,
        "matched": matched,
        "ok": ok,
        "reason": reason,
    }
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()[-MAX_LINES:]
    except OSError:
        return []
    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries


def recent(path, limit=200):
    return list(reversed(_read(path)))[:limit]


def last_for(path, device):
    for entry in reversed(_read(path)):
        if entry.get("device") == device:
            return entry
    return None
