"""One arm switch: Off, Home, Night or Away.

People mix up a panel of separate mode toggles, so the page shows one
switch. Each state is a setting of the mode flags the detector already reads,
so nothing below this module had to change:

    off    idle on             nothing alerts (admin only, like Idle)
    home   idle off, night off alerts as before
    night  idle off, night on  higher priority alerts
    away   idle off, night on  and a person anywhere raises the presence alarm

Night and Away set the same flags; which one was chosen is kept in a small
JSON file beside the other data. A flag changed somewhere else (the old mode
API, a schedule, Narada) wins: the state is read back from the flags, and the
file only tells Night from Away.

Auto-arm follows the owner's phone: when it leaves the network the house
arms Away, and when it comes back an Away that auto-arm set returns to Home.
An arm state a person chose by hand is never undone by it.
"""
import json
import os
import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel


STATES = ("off", "home", "night", "away")
FLAGS = {
    "off":   {"idle": True,  "night": False},
    "home":  {"idle": False, "night": False},
    "night": {"idle": False, "night": True},
    "away":  {"idle": False, "night": True},
}


def state_from_flags(idle, night, chosen):
    """The arm state the flags describe; `chosen` only breaks the Night/Away tie."""
    if idle:
        return "off"
    if night:
        return "away" if chosen == "away" else "night"
    return "home"


class ArmStore:
    """The last arm state chosen, who chose it and whether auto-arm is on."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = {"chosen": "home", "by": "", "at": 0.0, "auto": True, "auto_set": False}
        try:
            with open(path) as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                self.data.update({k: saved[k] for k in self.data if k in saved})
        except (OSError, ValueError):
            pass

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)

    def update(self, **fields):
        with self.lock:
            self.data.update(fields)
            try:
                self.save()
            except OSError:
                pass            # the switch still works for this run
            return dict(self.data)


class ArmRequest(BaseModel):
    state: str

class AutoArmRequest(BaseModel):
    enabled: bool


def current(core, store):
    with core.STATE.modes.lock:
        idle, night = core.STATE.modes.idle, core.STATE.modes.night
    d = dict(store.data)
    return {"state": state_from_flags(idle, night, d["chosen"]), "by": d["by"], "at": d["at"],
            "auto": d["auto"], "auto_set": d["auto_set"],
            "owner_present": bool(core.STATE.presence.owner_present)}


def apply(core, store, state, actor, auto=False):
    """Set the flags for `state` and remember the choice. Returns the new state."""
    with core.STATE.modes.lock:
        for name, value in FLAGS[state].items():
            core.STATE.modes.set(name, value)
    core.save_config()
    store.update(chosen=state, by=actor, at=time.time(), auto_set=auto)
    core.log_system_update(f"Armed {state.capitalize()} by {actor}" + (" (auto-arm)" if auto else ""))
    core.push_urgent_ws()
    return current(core, store)


def person_alert_wanted(core, store):
    """True while the house is armed Away: then any person is worth the alarm."""
    return current(core, store)["state"] == "away"


def auto_arm_loop(core, store, every=15.0, sleep=time.sleep):
    """Arm Away when the owner's phone leaves, Home when it returns.

    Acts on a change only, never on the first look, so a restart does not
    re-arm a house a person set by hand.
    """
    last = None
    while True:
        present = bool(core.STATE.presence.owner_present)
        if last is not None and present != last and store.data.get("auto"):
            state = current(core, store)["state"]
            if not present and state == "home":
                apply(core, store, "away", "auto-arm", auto=True)
            elif present and state == "away" and store.data.get("auto_set"):
                apply(core, store, "home", "auto-arm", auto=True)
        last = present
        sleep(every)


def build_arm_router(core, store):
    router = APIRouter()

    @router.get("/api/arm")
    async def get_arm(session=Depends(core.require_session)):
        return current(core, store)

    @router.post("/api/arm")
    async def set_arm(data: ArmRequest, session=Depends(core.require_session)):
        if data.state not in STATES:
            raise HTTPException(400, f"Unknown arm state: {data.state}")
        if data.state == "off" and session["role"] != "admin":
            # Off silences every alert, as Idle did: the same admin-only rule.
            raise HTTPException(403, "Only an admin can turn the system off: it silences alerts.")
        return apply(core, store, data.state, session["username"])

    @router.post("/api/arm/auto")
    async def set_auto_arm(data: AutoArmRequest, session=Depends(core.require_admin)):
        store.update(auto=bool(data.enabled))
        core.log_system_update(f"Auto-arm {'on' if data.enabled else 'off'} by {session['username']}")
        return current(core, store)

    return router
