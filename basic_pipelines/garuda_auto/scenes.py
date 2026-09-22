"""Scenes: a named set of device actions run with one tap or one sentence.

"Leave home" is every load off; "Study" is the lamp on and the fan on. A scene
is checked against the registry when it is saved and again when it runs, so a
device removed later makes that one step fail with a reason instead of the
whole scene silently doing nothing.
"""
import re
import threading

from . import jsonfile
from .device_types import actions_for

MAX_SCENES = 32
MAX_STEPS = 16
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slug(name):
    base = _SLUG_RE.sub("_", str(name).lower()).strip("_")[:32]
    return base if base and base[0].isalpha() else f"s_{base}"[:32]


def validate_scene(name, actions, registry):
    if not isinstance(name, str) or not name.strip() or len(name) > 48:
        return False, "a scene needs a name of 1-48 characters"
    if not isinstance(actions, list) or not actions:
        return False, "a scene needs at least one step"
    if len(actions) > MAX_STEPS:
        return False, f"a scene can have at most {MAX_STEPS} steps"
    seen = set()
    for step in actions:
        if not isinstance(step, dict):
            return False, "each step needs a device and an action"
        device_id, action = step.get("device"), step.get("action")
        device = registry.get(device_id)
        if device is None:
            return False, f"unknown device: {device_id}"
        if action not in actions_for(device["type"]):
            return False, f"{device['name']} cannot be switched {action!r}"
        if device_id in seen:
            return False, f"{device['name']} appears twice"
        seen.add(device_id)
    return True, ""


class SceneStore:
    def __init__(self, path, registry):
        self.path = path
        self.registry = registry
        self._lock = threading.Lock()
        self.scenes = jsonfile.load(path, [])

    def save(self):
        jsonfile.save(self.path, self.scenes)

    def get(self, scene_id):
        for scene in self.scenes:
            if scene["id"] == scene_id:
                return scene
        return None

    def find(self, text):
        """A scene named in free text: exact id, then name contained in text."""
        lowered = str(text).lower()
        hit = self.get(lowered.strip())
        if hit:
            return hit
        for scene in sorted(self.scenes, key=lambda s: -len(s["name"])):
            if scene["name"].lower() in lowered:
                return scene
        return None

    def add(self, name, actions, created_by=""):
        with self._lock:
            if len(self.scenes) >= MAX_SCENES:
                return False, f"scene limit reached ({MAX_SCENES})", None
            ok, reason = validate_scene(name, actions, self.registry)
            if not ok:
                return False, reason, None
            scene_id = slug(name)
            if self.get(scene_id):
                return False, f"a scene called {name!r} already exists", None
            scene = {"id": scene_id, "name": name.strip(),
                     "actions": [{"device": a["device"], "action": a["action"]} for a in actions],
                     "created_by": created_by}
            self.scenes.append(scene)
            self.save()
        return True, "", scene

    def delete(self, scene_id):
        with self._lock:
            before = len(self.scenes)
            self.scenes = [s for s in self.scenes if s["id"] != scene_id]
            if len(self.scenes) == before:
                return False
            self.save()
        return True
