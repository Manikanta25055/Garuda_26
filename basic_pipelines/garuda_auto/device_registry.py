"""Devices the user has declared.

Entries are user-authored, so every field is checked. A device names a type
from the catalogue and a transport; its capabilities are derived, never
entered. Relay devices name a channel, not a pin -- a user who could enter a
BCM pin could drive one the Hailo HAT, camera or I2C bus is using.
"""
import json
import os
import re
import tempfile
import threading

from .device_types import TYPES, TRANSPORTS, is_actuator

MAX_DEVICES = 32
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_REQUIRED = ("id", "name", "type", "room", "transport")


def validate_device(entry, existing_ids, relay_channels):
    """Return (True, "") when the entry is safe to store, else (False, reason)."""
    if not isinstance(entry, dict):
        return False, "device is not an object"
    for key in _REQUIRED:
        if key not in entry:
            return False, f"missing required key: {key}"

    device_id = entry["id"]
    if not isinstance(device_id, str) or not _ID_RE.match(device_id):
        return False, ("id must be lowercase letters, digits and underscores, "
                       f"starting with a letter: {device_id!r}")
    if device_id in existing_ids:
        return False, f"a device with id {device_id!r} already exists"

    if entry["type"] not in TYPES:
        return False, f"unknown device type: {entry['type']!r}"

    for key in ("name", "room"):
        if not isinstance(entry[key], str) or not entry[key].strip():
            return False, f"{key} must be a non-empty string"
        if len(entry[key]) > 64:
            return False, f"{key} must be at most 64 characters"

    transport = entry["transport"]
    if not isinstance(transport, dict) or transport.get("kind") not in TRANSPORTS:
        return False, f"transport.kind must be one of {list(TRANSPORTS)}"

    if transport["kind"] == "relay":
        channel = transport.get("channel")
        if isinstance(channel, bool) or not isinstance(channel, int):
            return False, "relay transport needs an integer channel"
        if channel not in relay_channels:
            return False, f"channel {channel} is not one of {sorted(relay_channels)}"
    else:
        topic = transport.get("topic_base")
        if not isinstance(topic, str) or not topic.strip():
            return False, "mqtt transport needs a topic_base"
        if len(topic) > 128 or any(c in topic for c in "+#"):
            return False, "topic_base must be a literal topic under 128 characters"

    return True, ""


class DeviceRegistry:
    def __init__(self, path, relay_channels):
        self.path = path
        self.relay_channels = frozenset(relay_channels)
        self._lock = threading.Lock()
        self.devices = []
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            self.devices = []
            return
        if not isinstance(data, list):
            self.devices = []
            return
        kept, seen = [], set()
        for entry in data:
            ok, _ = validate_device(entry, seen, self.relay_channels)
            if ok:
                kept.append(entry)
                seen.add(entry["id"])
        self.devices = kept

    def save(self):
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.devices, fh, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def _channel_taken(self, channel):
        return any(d["transport"].get("channel") == channel
                   for d in self.devices if d["transport"]["kind"] == "relay")

    def add(self, entry):
        with self._lock:
            if len(self.devices) >= MAX_DEVICES:
                return False, f"device limit reached ({MAX_DEVICES})"
            ok, reason = validate_device(
                entry, {d["id"] for d in self.devices}, self.relay_channels)
            if not ok:
                return False, reason
            transport = entry["transport"]
            if transport["kind"] == "relay" and self._channel_taken(transport["channel"]):
                return False, f"channel {transport['channel']} is already in use"
            entry = dict(entry)
            entry.setdefault("enabled", True)
            self.devices.append(entry)
            self.save()
        return True, ""

    def delete(self, device_id):
        with self._lock:
            before = len(self.devices)
            self.devices = [d for d in self.devices if d["id"] != device_id]
            if len(self.devices) == before:
                return False
            self.save()
        return True

    def get(self, device_id):
        for device in self.devices:
            if device["id"] == device_id:
                return device
        return None

    def actuators(self):
        return [d for d in self.devices if is_actuator(d["type"])]

    def sensors(self):
        return [d for d in self.devices if not is_actuator(d["type"])]
