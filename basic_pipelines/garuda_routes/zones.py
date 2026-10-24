"""Per-object rules and zones: what each thing seen does, when, and where.

Owners mute alarms that go off for a knife on the kitchen counter, and then
miss the one that matters. Two small settings fix that:

    rules   for each label, what it does in each arm state: alert (the
            alarm and the e-mail), log (an Events entry, no alarm) or ignore
    zones   rectangles drawn on the camera picture. A quiet zone turns an
            alert into a log for the labels it names (the counter where the
            knives live); an alert zone turns anything it names into an
            alert (the back door). Quiet wins when both cover a box.

With nothing saved the answer is what it was before: the danger labels
alert, everything else is logged. Off is the arm switch's own business (it
silences alerts further down), so rules cover Home, Night and Away.
"""
import json
import os
import threading
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

try:
    from .arm import current as arm_current
except ImportError:                                # script mode (python Garuda_web.py)
    from basic_pipelines.garuda_routes.arm import current as arm_current

ACTIONS = ("alert", "log", "ignore")
RANK = {"ignore": 0, "log": 1, "alert": 2}
ARMED = ("home", "night", "away")
ZONE_MODES = ("quiet", "alert")
MAX_ZONES = 12


def _inside(rect, box):
    """True when the centre of `box` lies in `rect` (both normalised x1,y1,x2,y2)."""
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return rect[0] <= cx <= rect[2] and rect[1] <= cy <= rect[3]


def decide(data, label, box, state, danger):
    """alert, log or ignore for one detection.

    `data` is the saved rules and zones, `danger` the danger label set (lower
    case) and `state` the arm state.
    """
    label = label.lower()
    action = "alert" if label in danger else "log"
    rule = data.get("rules", {}).get(label)
    if rule and rule.get(state) in ACTIONS:
        action = rule[state]
    if box is None or action == "ignore":
        return action
    hits = [z["mode"] for z in data.get("zones", ())
            if (not z.get("labels") or label in z["labels"]) and _inside(z["rect"], box)]
    if "quiet" in hits:
        return "log" if action == "alert" else action
    if "alert" in hits:
        return "alert"
    return action


class ZoneStore:
    """Rules and zones, kept in one JSON file beside the other data."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = {"rules": {}, "zones": []}
        try:
            with open(path) as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                self.data["rules"] = dict(saved.get("rules") or {})
                self.data["zones"] = list(saved.get("zones") or [])
        except (OSError, ValueError):
            pass

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)

    def replace(self, rules=None, zones=None):
        """Swap in new rules and/or zones as a whole (readers never see half)."""
        with self.lock:
            data = {"rules": self.data["rules"] if rules is None else rules,
                    "zones": self.data["zones"] if zones is None else zones}
            self.data = data
            try:
                self._save()
            except OSError:
                pass            # the rules still apply for this run
            return data


def _clean_rules(raw):
    rules = {}
    for label, per_state in (raw or {}).items():
        label = str(label).strip().lower()[:40]
        if not label or not isinstance(per_state, dict):
            continue
        clean = {s: per_state[s] for s in ARMED if per_state.get(s) in ACTIONS}
        if clean:
            rules[label] = clean
    return rules


def _clean_zones(raw):
    zones = []
    for z in (raw or [])[:MAX_ZONES]:
        try:
            x1, y1, x2, y2 = (min(1.0, max(0.0, float(v))) for v in z["rect"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "Each zone needs a rectangle: four numbers from 0 to 1.")
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        if x2 - x1 < 0.02 or y2 - y1 < 0.02:
            raise HTTPException(400, "A zone is too small to draw on the picture.")
        mode = z.get("mode")
        if mode not in ZONE_MODES:
            raise HTTPException(400, f"Zone mode must be one of {', '.join(ZONE_MODES)}.")
        labels = sorted({str(l).strip().lower()[:40] for l in (z.get("labels") or []) if str(l).strip()})
        zones.append({"id": str(z.get("id") or uuid.uuid4().hex[:8])[:16],
                      "name": str(z.get("name") or "Zone").strip()[:40] or "Zone",
                      "rect": [round(v, 4) for v in (x1, y1, x2, y2)],
                      "mode": mode, "labels": labels})
    return zones


class RulesRequest(BaseModel):
    rules: dict

class ZonesRequest(BaseModel):
    zones: list


def build_zones_router(core, store):
    router = APIRouter()

    def _view():
        d = store.data
        return {"rules": d["rules"], "zones": d["zones"],
                "danger_labels": list(core.STATE.config.danger_labels),
                "watch_labels": list(core.STATE.config.watch_labels),
                "actions": list(ACTIONS), "states": list(ARMED), "zone_modes": list(ZONE_MODES)}

    @router.get("/api/detection/rules")
    async def get_rules(session=Depends(core.require_session)):
        return _view()

    @router.post("/api/detection/rules")
    async def set_rules(data: RulesRequest, session=Depends(core.require_admin)):
        store.replace(rules=_clean_rules(data.rules))
        core.log_system_update(f"[RULES] detection rules changed by {session['username']}")
        return _view()

    @router.post("/api/detection/zones")
    async def set_zones(data: ZonesRequest, session=Depends(core.require_admin)):
        if len(data.zones) > MAX_ZONES:
            raise HTTPException(400, f"At most {MAX_ZONES} zones.")
        store.replace(zones=_clean_zones(data.zones))
        core.log_system_update(f"[RULES] {len(store.data['zones'])} zone(s) saved by {session['username']}")
        return _view()

    return router


class FrameRules:
    """What detection.py asks once per frame: the arm state and data, read once."""

    def __init__(self, core, store, arm_store, danger):
        self.data = store.data
        self.danger = danger
        try:
            self.state = arm_current(core, arm_store)["state"] if arm_store is not None else "home"
        except Exception:
            self.state = "home"

    def __call__(self, label, box=None):
        return decide(self.data, label, box, self.state, self.danger)
