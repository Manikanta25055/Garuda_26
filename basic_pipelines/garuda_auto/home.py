"""The house as one object: control, scenes, schedules, presence and notices.

Every change to a device goes through `HomeServices.set`, whoever asked --
the dashboard, the assistant, a scene, a schedule, the away check. That keeps
three things true everywhere: the action is logged with who and why, the rule
base sees the new state at once, and a person watching the dashboard sees it
on the next state push.

A background thread (1 s) runs what is time-driven: schedules, the away
check, vacation lighting and the daily digest. Rules keep their own faster
loop in runtime.py.
"""
import logging
import random
import threading
import time
import uuid

from . import actuation_log, habits, jsonfile, usage
from .device_types import is_actuator
from .scenes import SceneStore
from .schedules import ScheduleStore, validate_target

log = logging.getLogger(__name__)

OWNER_EVENT_WINDOW_S = 120
MAX_NOTICES = 20

DEFAULT_SETTINGS = {
    # Away: what to do when the last known phone leaves and loads are on.
    "away_auto_off": False,       # False: send a notice with a one-tap button
    "away_notify": True,
    # Left-on watchdog: a load on for this long while nobody is home.
    "left_on_minutes": 120,       # 0 disables
    # Vacation: lights come and go in the evening while the house is empty.
    "vacation_mode": False,
    "vacation_start": "19:00",
    "vacation_end": "23:30",
    "vacation_devices": [],       # empty: every light
    # Daily digest.
    "digest_email": False,
    "digest_time": "21:30",
    "tariff_per_kwh": 0.0,
    "dismissed_suggestions": [],
}
_BOOL_KEYS = ("away_auto_off", "away_notify", "vacation_mode", "digest_email")
_TIME_KEYS = ("vacation_start", "vacation_end", "digest_time")


def _hhmm_minutes(value):
    hh, mm = value.split(":")
    return int(hh) * 60 + int(mm)


def _in_window(now_min, start, end):
    s, e = _hhmm_minutes(start), _hhmm_minutes(end)
    return s <= now_min < e if s <= e else now_min >= s or now_min < e


class HomeServices:
    def __init__(self, ctx, runtime, data_dir, *, clock=time.time, rng=None):
        self.ctx = ctx
        self.runtime = runtime
        self._clock = clock
        self._rng = rng or random.Random()
        self._lock = threading.RLock()
        self.scenes = SceneStore(f"{data_dir}/scenes.json", ctx.registry)
        self.schedules = ScheduleStore(f"{data_dir}/schedules.json", clock=clock)
        self._settings_path = f"{data_dir}/home_settings.json"
        self.settings = {**DEFAULT_SETTINGS, **jsonfile.load(self._settings_path, {})}
        self.notices = []
        # Injected by Garuda.
        self.presence_fn = None    # () -> True (home) / False (away) / None (unknown)
        self.security_fn = None    # () -> "clear" | "danger" | "night_presence"
        self.notify_fn = None      # (subject, body) -> None, e.g. email
        self.digest_fn = None      # () -> str, the text of today's digest
        self.on_change = None      # () -> None, e.g. push a websocket update
        # Presence bookkeeping.
        self._owner_home = None
        self._owner_changed_at = 0.0
        self._owner_event = "none"
        self._on_since = {}
        self._left_on_warned = set()
        self._vacation_next = 0.0
        self._vacation_active = False
        self._digest_sent_on = ""
        self._stop = threading.Event()
        self._thread = None
        self.last_error = ""

    # ── control ───────────────────────────────────────────────────────────────

    def set(self, device_id, action, *, source="manual", actor="", rule_id=""):
        ok, reason = self.ctx.device_router.set(device_id, action)
        if ok:
            self.runtime.note_state(device_id, action)
            with self._lock:
                if action == "on":
                    self._on_since.setdefault(device_id, self._clock())
                else:
                    self._on_since.pop(device_id, None)
                    self._left_on_warned.discard(device_id)
        actuation_log.record(self.ctx.log_path, device=device_id, action=action,
                             rule_id=rule_id, matched=[], ok=ok, reason=reason,
                             clock=self._clock, source=source, actor=actor)
        self._changed()
        return ok, reason

    def devices(self):
        router = self.ctx.device_router
        return [{**d, "state": router.state(d["id"]), "available": router.available(d["id"]),
                 "actuator": is_actuator(d["type"])}
                for d in self.ctx.registry.devices]

    def on_devices(self):
        return [d for d in self.devices() if d["actuator"] and d["state"] == "on"]

    def all_off(self, *, source="manual", actor="", room=None):
        done, failed = [], []
        for d in self.devices():
            if not d["actuator"] or d["state"] == "off" or not d["available"]:
                continue
            if room and d.get("room", "").lower() != room.lower():
                continue
            ok, reason = self.set(d["id"], "off", source=source, actor=actor)
            (done if ok else failed).append(d["name"] if ok else f"{d['name']}: {reason}")
        return done, failed

    def run_scene(self, scene_id, *, actor="", source=None):
        scene = self.scenes.get(scene_id)
        if scene is None:
            return False, f"unknown scene: {scene_id}", []
        results = []
        for step in scene["actions"]:
            ok, reason = self.set(step["device"], step["action"],
                                  source=source or f"scene:{scene_id}", actor=actor)
            results.append({**step, "ok": ok, "reason": reason})
        failed = [r for r in results if not r["ok"]]
        return not failed, "; ".join(r["reason"] for r in failed), results

    def run_target(self, target, *, source, actor=""):
        if "scene" in target:
            ok, reason, _ = self.run_scene(target["scene"], actor=actor, source=source)
            return ok, reason
        return self.set(target["device"], target["action"], source=source, actor=actor)

    def describe_target(self, target):
        if "scene" in target:
            scene = self.scenes.get(target["scene"])
            return f"run {scene['name'] if scene else target['scene']}"
        device = self.ctx.registry.get(target.get("device"))
        name = device["name"] if device else target.get("device")
        return f"{name} {target.get('action')}"

    # ── schedules ─────────────────────────────────────────────────────────────

    def add_schedule(self, target, *, time_hhmm=None, days=None, at=None,
                     label="", created_by=""):
        ok, reason = validate_target(target, self.ctx.registry, self.scenes)
        if not ok:
            return False, reason, None
        if at is not None:
            result = self.schedules.add_once(target, at, label=label, created_by=created_by)
        else:
            result = self.schedules.add_daily(target, time_hhmm, days, label=label,
                                              created_by=created_by)
        self._changed()
        return result

    def schedule_view(self):
        now = self._clock()
        return [{**e, "describe": self.describe_target(e["target"]),
                 "next_run": self.schedules.next_run(e, now)}
                for e in self.schedules.entries]

    # ── settings ──────────────────────────────────────────────────────────────

    def update_settings(self, fields):
        clean = {}
        for key, value in fields.items():
            if key not in DEFAULT_SETTINGS or key == "dismissed_suggestions":
                return False, f"unknown setting: {key}"
            if key in _BOOL_KEYS:
                if not isinstance(value, bool):
                    return False, f"{key} must be true or false"
            elif key in _TIME_KEYS:
                if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
                    return False, f"{key} must be HH:MM"
                try:
                    if not 0 <= _hhmm_minutes(value) < 1440:
                        raise ValueError
                except ValueError:
                    return False, f"{key} must be HH:MM"
            elif key == "left_on_minutes":
                if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1440:
                    return False, "left_on_minutes must be 0-1440"
            elif key == "tariff_per_kwh":
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1000:
                    return False, "tariff_per_kwh must be 0-1000"
            elif key == "vacation_devices":
                if not isinstance(value, list) or any(self.ctx.registry.get(v) is None for v in value):
                    return False, "vacation_devices must list known devices"
            clean[key] = value
        with self._lock:
            self.settings.update(clean)
            jsonfile.save(self._settings_path, self.settings)
        self._changed()
        return True, ""

    def dismiss_suggestion(self, suggestion_id):
        with self._lock:
            dismissed = self.settings.setdefault("dismissed_suggestions", [])
            if suggestion_id not in dismissed:
                dismissed.append(suggestion_id)
                del dismissed[:-200]
                jsonfile.save(self._settings_path, self.settings)

    # ── insight ───────────────────────────────────────────────────────────────

    def log_entries(self, limit=5000):
        return list(reversed(actuation_log.recent(self.ctx.log_path, limit=limit)))

    def usage(self, days=7):
        return usage.summary(self.log_entries(),
                             [d for d in self.ctx.registry.devices if is_actuator(d["type"])],
                             days=days, now=self._clock(),
                             tariff_per_kwh=float(self.settings.get("tariff_per_kwh") or 0))

    def suggestions(self):
        return habits.suggest(self.log_entries(), self.ctx.registry, self.schedules,
                              dismissed=set(self.settings.get("dismissed_suggestions", [])),
                              now=self._clock())

    # ── notices ───────────────────────────────────────────────────────────────

    def notice(self, kind, text, *, actions=(), key=None, email=False):
        """Something the owner should see. `key` de-duplicates a live notice."""
        with self._lock:
            if key and any(n.get("key") == key for n in self.notices):
                return None
            item = {"id": uuid.uuid4().hex[:8], "ts": self._clock(), "kind": kind,
                    "text": text, "actions": list(actions), "key": key}
            self.notices.insert(0, item)
            del self.notices[MAX_NOTICES:]
        if email and self.notify_fn is not None:
            # On its own thread: this is called from the one-second home loop,
            # and a mail server that takes ten seconds to answer held up every
            # schedule and timer due in the meantime.
            threading.Thread(target=self._send_notice_email, args=(text,),
                             daemon=True, name="home-notice").start()
        self._changed()
        return item

    def _send_notice_email(self, text):
        try:
            self.notify_fn(f"Garuda Home: {text[:60]}", text)
        except Exception as exc:
            log.warning("notice email failed: %s", exc)

    def dismiss_notice(self, notice_id):
        with self._lock:
            before = len(self.notices)
            self.notices = [n for n in self.notices if n["id"] != notice_id]
            changed = len(self.notices) != before
        if changed:
            self._changed()
        return changed

    # ── house context for the rule base ───────────────────────────────────────

    def context(self):
        now = self._clock()
        home = self._owner_home
        event = self._owner_event if now - self._owner_changed_at <= OWNER_EVENT_WINDOW_S else "none"
        security = "clear"
        if self.security_fn is not None:
            try:
                security = self.security_fn() or "clear"
            except Exception:
                security = "clear"
        return {"owner_presence": "away" if home is False else "home",
                "owner_event": event, "security": security}

    # ── background loop ───────────────────────────────────────────────────────

    def tick(self):
        now = self._clock()
        for entry in self.schedules.due(now):
            ok, reason = self.run_target(entry["target"], source=f"schedule:{entry['id']}",
                                         actor=entry.get("created_by", ""))
            if not ok:
                self.notice("schedule", f"Scheduled {self.describe_target(entry['target'])} "
                                        f"failed: {reason}")
        self._check_presence(now)
        self._check_left_on(now)
        self._vacation(now)
        self._digest(now)

    def _check_presence(self, now):
        if self.presence_fn is None:
            return
        try:
            home = self.presence_fn()
        except Exception:
            return
        if home is None or home == self._owner_home:
            return
        previous, self._owner_home = self._owner_home, home
        if previous is None:
            return          # first reading after start-up is not a trip
        self._owner_changed_at = now
        self._owner_event = "arrived" if home else "left"
        if home:
            return
        on = self.on_devices()
        if not on:
            return
        names = ", ".join(d["name"] for d in on)
        if self.settings.get("away_auto_off"):
            done, failed = self.all_off(source="away")
            text = f"You left home. Turned off {', '.join(done) or 'nothing'}."
            if failed:
                text += f" Could not turn off: {'; '.join(failed)}."
            self.notice("away", text, email=self.settings.get("away_notify", True))
        elif self.settings.get("away_notify", True):
            self.notice("away", f"You left home with {names} on.",
                        actions=[{"label": "Turn all off", "call": "all_off"}],
                        key="away-left-on", email=True)

    def _check_left_on(self, now):
        minutes = int(self.settings.get("left_on_minutes") or 0)
        if not minutes or self._owner_home is not False:
            return
        occupied = self.ctx.descriptor.get("occupancy") == "occupied"
        if occupied:
            return
        for d in self.on_devices():
            since = self._on_since.setdefault(d["id"], now)
            if now - since >= minutes * 60 and d["id"] not in self._left_on_warned:
                self._left_on_warned.add(d["id"])
                self.notice("left_on",
                            f"{d['name']} has been on for {int((now - since) // 60)} min "
                            "and nobody is home.",
                            actions=[{"label": f"Turn {d['name']} off", "call": "device_off",
                                      "device": d["id"]}],
                            key=f"left-on-{d['id']}", email=True)

    def _vacation_targets(self):
        chosen = self.settings.get("vacation_devices") or []
        return [d for d in self.devices()
                if d["actuator"] and d["available"]
                and (d["id"] in chosen if chosen else d["type"] == "light")]

    def _vacation(self, now):
        s = self.settings
        lt = time.localtime(now)
        active = (s.get("vacation_mode") and self._owner_home is False
                  and _in_window(lt.tm_hour * 60 + lt.tm_min, s["vacation_start"], s["vacation_end"]))
        if not active:
            if self._vacation_active:
                self._vacation_active = False
                for d in self._vacation_targets():
                    if d["state"] == "on":
                        self.set(d["id"], "off", source="vacation")
            return
        self._vacation_active = True
        if now < self._vacation_next:
            return
        targets = self._vacation_targets()
        if targets:
            d = self._rng.choice(targets)
            self.set(d["id"], "off" if d["state"] == "on" else "on", source="vacation")
        # Irregular on purpose: a light that flips every 30:00 exactly is a tell.
        self._vacation_next = now + self._rng.uniform(15, 50) * 60

    def _digest(self, now):
        s = self.settings
        if not s.get("digest_email") or self.digest_fn is None or self.notify_fn is None:
            return
        today = time.strftime("%Y-%m-%d", time.localtime(now))
        if self._digest_sent_on == today or time.strftime("%H:%M", time.localtime(now)) != s["digest_time"]:
            return
        self._digest_sent_on = today
        threading.Thread(target=self._send_digest, daemon=True, name="home-digest").start()

    def _send_digest(self):
        try:
            self.notify_fn("Garuda Home: today's summary", self.digest_fn())
        except Exception as exc:
            log.warning("digest failed: %s", exc)

    def _changed(self):
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                pass

    def start(self, interval=1.0):
        if self._thread is not None:
            return
        self._stop.clear()
        # Devices already on at start-up count from now, not from never.
        for d in self.on_devices():
            self._on_since.setdefault(d["id"], self._clock())

        def loop():
            while not self._stop.wait(interval):
                try:
                    self.tick()
                except Exception as exc:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    log.exception("home loop")

        self._thread = threading.Thread(target=loop, daemon=True, name="home-services")
        self._thread.start()

    def stop(self):
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)

    def summary(self):
        devices = self.devices()
        ctx = self.context()
        return {
            # Compact states, so open pages update from the websocket push
            # instead of polling (the API allows 30 requests a minute per IP).
            "states": {d["id"]: [d["state"], d["available"]] for d in devices},
            "devices_on": sum(1 for d in devices if d["actuator"] and d["state"] == "on"),
            "devices": len(devices),
            "owner_presence": ctx["owner_presence"],
            "security": ctx["security"],
            "vacation": bool(self.settings.get("vacation_mode")),
            "notices": self.notices[:5],
            "pending_proposals": len(self.ctx.pending.all()),
            "loop_running": self._thread is not None and self._thread.is_alive(),
        }
