"""The 2026-11 architecture audit: each defect found, pinned by a test.

Stores that lost their contents to one damaged byte, a rule that never
retried a switch that failed, a relay bank that stopped switching off at the
first pin that raised, an activity log that could fail a switch which had
already happened, a home loop one bad schedule could stop, an alert email
lost to one mail-server hiccup, and a frame callback whose exception dropped
the picture.
"""
import glob
import json
import sys
import threading
import types

import pytest

import Garuda_web as gw
from basic_pipelines.garuda_auto import actuation_log, actuators, jsonfile
from basic_pipelines.garuda_auto.device_registry import DeviceRegistry
from basic_pipelines.garuda_auto.home import HomeServices
from basic_pipelines.garuda_auto.pending_store import PendingStore
from basic_pipelines.garuda_auto.rule_engine import RETRY_FIRST_S, RETRY_MAX_S, RuleEngine
from basic_pipelines.garuda_auto.rule_schema import build_schema
from basic_pipelines.garuda_auto.rule_store import RuleStore
from basic_pipelines.garuda_auto.runtime import DrishtiRuntime
from basic_pipelines.garuda_auto.scenes import SceneStore
from basic_pipelines.garuda_auto.schedules import ScheduleStore
from basic_pipelines.garuda_auto.switch_guard import SwitchGuard
from basic_pipelines.garuda_auto.transports import DeviceRouter

LAMP = {"id": "lamp", "name": "Lamp", "type": "light", "room": "study",
        "transport": {"kind": "relay", "channel": 1}}
FAN = {"id": "fan", "name": "Fan", "type": "light", "room": "study",
       "transport": {"kind": "relay", "channel": 2}}
RULE = {"id": "r1", "source_utterance": "lamp on when occupied",
        "when": {"all": [{"field": "occupancy", "op": "==", "value": "occupied"}]},
        "then": [{"device": "lamp", "action": "on"}], "cooldown_s": 0}


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def _registry(tmp_path, devices=(LAMP,)):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps(list(devices)))
    return DeviceRegistry(str(path), {1, 2})


def _copies(path):
    return glob.glob(f"{path}.corrupt-*")


# ── stores: a damaged file is kept, not erased by the next save ──────────────

def test_damaged_json_is_copied_aside(tmp_path):
    path = tmp_path / "scenes.json"
    path.write_text('[{"id": "movie", "name": "Mov')          # a write cut short
    assert jsonfile.load(str(path), []) == []
    copies = _copies(path)
    assert len(copies) == 1
    assert open(copies[0]).read() == '[{"id": "movie", "name": "Mov'


def test_missing_or_empty_file_is_not_quarantined(tmp_path):
    path = tmp_path / "nothing.json"
    assert jsonfile.load(str(path), {}) == {}
    path.write_text("")
    assert jsonfile.load(str(path), {}) == {}
    assert _copies(path) == []


def test_quarantine_keeps_only_the_newest_copies(tmp_path, monkeypatch):
    path = tmp_path / "rules.json"
    path.write_text("{broken")
    stamps = iter(f"20261111-0000{n:02d}" for n in range(10))
    monkeypatch.setattr(jsonfile.time, "strftime", lambda fmt: next(stamps))
    for _ in range(6):
        jsonfile.quarantine(str(path))
    assert len(_copies(path)) == jsonfile.QUARANTINE_KEEP


def test_rule_store_keeps_a_damaged_rule_base(tmp_path):
    registry = _registry(tmp_path)
    path = tmp_path / "rules.json"
    path.write_text(json.dumps([RULE])[:-9])                   # truncated
    store = RuleStore(str(path), build_schema(registry))
    assert store.rules == []
    store.save()                                               # what used to erase it
    assert len(_copies(path)) == 1


def test_rule_store_counts_fires_and_saves_without_deadlock(tmp_path):
    registry = _registry(tmp_path)
    store = RuleStore(str(tmp_path / "rules.json"), build_schema(registry))
    assert store.add(dict(RULE)) == (True, "")
    assert store.record_fire("r1", 1234.9) is True
    assert store.record_fire("nope", 1.0) is False
    store.rebind(build_schema(registry))                       # saves while holding the lock
    saved = json.loads((tmp_path / "rules.json").read_text())
    assert saved[0]["fired_count"] == 1 and saved[0]["last_fired"] == 1234


def test_rule_store_save_survives_counters_changing(tmp_path):
    registry = _registry(tmp_path)
    store = RuleStore(str(tmp_path / "rules.json"), build_schema(registry))
    for n in range(20):
        assert store.add({**RULE, "id": f"r{n}"})[0]
    stop, errors = threading.Event(), []

    def fire():
        n = 0
        while not stop.is_set():
            store.record_fire(f"r{n % 20}", n)
            n += 1

    thread = threading.Thread(target=fire, daemon=True)
    thread.start()
    try:
        for _ in range(40):
            try:
                store.save()
            except RuntimeError as exc:                        # dict changed size during iteration
                errors.append(exc)
    finally:
        stop.set()
        thread.join(2)
    assert errors == []


def test_registry_refuses_two_devices_on_one_channel_from_disk(tmp_path):
    clash = {**FAN, "transport": {"kind": "relay", "channel": 1}}
    registry = _registry(tmp_path, (LAMP, clash))
    assert [d["id"] for d in registry.devices] == ["lamp"]
    assert len(_copies(tmp_path / "devices.json")) == 1        # the dropped one is recoverable


def test_pending_store_ignores_malformed_proposals(tmp_path):
    path = tmp_path / "pending.json"
    path.write_text(json.dumps([{"id": "a"}, "junk", {"id": "b", "rule": {}, "created_at": 9e12}]))
    store = PendingStore(str(path))
    assert [p["id"] for p in store.all()] == ["b"]             # used to raise KeyError


def test_schedule_and_scene_stores_drop_malformed_entries(tmp_path):
    sched = tmp_path / "schedules.json"
    good = {"id": "s1", "kind": "daily", "time": "19:00", "days": [0, 1],
            "target": {"device": "lamp", "action": "on"}, "enabled": True, "last_run": None}
    sched.write_text(json.dumps([good, {"id": "s2"}, {"id": "s3", "kind": "once", "at": "soon",
                                                        "target": {"scene": "x"}}, 7]))
    store = ScheduleStore(str(sched))
    assert [e["id"] for e in store.entries] == ["s1"]
    assert store.due() == [] or store.due() is not None        # does not raise
    assert len(_copies(sched)) == 1

    scenes = tmp_path / "scenes.json"
    scenes.write_text(json.dumps([{"id": "ok", "name": "Ok", "actions": [{"device": "lamp", "action": "on"}]},
                                  {"id": "bad", "name": "Bad"}, None]))
    scene_store = SceneStore(str(scenes), _registry(tmp_path))
    assert [s["id"] for s in scene_store.scenes] == ["ok"]
    assert scene_store.find("bad") is None


# ── rules: a switch that failed is tried again ──────────────────────────────

def test_rule_is_retried_after_a_failed_switch():
    clock = Clock()
    store = types.SimpleNamespace(rules=[dict(RULE)])
    engine = RuleEngine(store, clock=clock, once=True)
    occupied = {"occupancy": "occupied"}

    assert len(engine.evaluate(occupied)) == 1
    assert engine.evaluate(occupied) == []                     # held: already acted on
    assert engine.release("r1") == RETRY_FIRST_S               # ...but the switch failed
    assert engine.evaluate(occupied) == []                     # not at once
    clock.now += RETRY_FIRST_S + 1
    assert len(engine.evaluate(occupied)) == 1                 # tried again

    assert engine.release("r1") == RETRY_FIRST_S * 2           # the pause grows
    for _ in range(20):
        last = engine.release("r1")
    assert last == RETRY_MAX_S

    engine.evaluate({"occupancy": "empty"})                    # condition went false
    assert len(engine.evaluate(occupied)) == 1                 # a fresh start, no pause


class _FlakyRouter:
    def __init__(self):
        self.fail = True
        self.states = {"lamp": "off"}
        self.calls = 0

    def state(self, device_id):
        return self.states.get(device_id)

    def set(self, device_id, action):
        self.calls += 1
        if self.fail == "raise":
            raise OSError("pin busy")
        if self.fail:
            return False, "device 'lamp' is unreachable"
        self.states[device_id] = action
        return True, ""


def _runtime(tmp_path, clock):
    registry = _registry(tmp_path)
    store = RuleStore(str(tmp_path / "rules.json"), build_schema(registry))
    assert store.add(dict(RULE))[0]
    router = _FlakyRouter()
    ctx = types.SimpleNamespace(registry=registry, store=store, device_router=router,
                                log_path=str(tmp_path / "activity.jsonl"), descriptor={})
    return DrishtiRuntime(ctx, clock=clock), router


def test_runtime_retries_a_rule_whose_device_did_not_switch(tmp_path):
    clock = Clock()
    runtime, router = _runtime(tmp_path, clock)
    runtime.observe([{"label": "person", "bbox": (0.1, 0.1, 0.3, 0.9)}], luma=80)

    assert runtime.tick()[0]["ok"] is False
    assert runtime.tick() == []                                # waiting, not hammering
    router.fail = False
    clock.now += RETRY_FIRST_S + 1
    assert runtime.tick()[0]["ok"] is True                     # used to never happen
    assert router.states["lamp"] == "on"


def test_runtime_tick_survives_a_router_that_raises(tmp_path):
    clock = Clock()
    runtime, router = _runtime(tmp_path, clock)
    router.fail = "raise"
    runtime.observe([{"label": "person", "bbox": (0.1, 0.1, 0.3, 0.9)}], luma=80)
    performed = runtime.tick()
    assert performed[0]["ok"] is False and "OSError" in performed[0]["reason"]
    assert actuation_log.recent(runtime.ctx.log_path)[0]["ok"] is False   # and it was written down


# ── relays ──────────────────────────────────────────────────────────────────

class _Pin:
    def __init__(self, broken=False):
        self.broken, self.value, self.closed = broken, False, False

    def on(self):
        self.value = True

    def off(self):
        if self.broken:
            raise OSError("gpio gone")
        self.value = False

    def close(self):
        self.closed = True


def _bank(monkeypatch, broken=()):
    pins = {}

    def make(pin, active_high=False, initial_value=False):
        pins[pin] = _Pin(broken=pin in broken)
        return pins[pin]

    monkeypatch.setattr(actuators, "GPIO_AVAILABLE", True)
    monkeypatch.setattr(actuators, "OutputDevice", make)
    return actuators.RelayBank({"lamp": 17, "fan": 27}), pins


def test_all_off_reaches_every_relay_past_one_that_fails(monkeypatch):
    bank, pins = _bank(monkeypatch, broken={17})
    bank.set("lamp", "on")
    bank.set("fan", "on")
    assert bank.all_off() == ["lamp"]
    assert pins[27].value is False                             # used to be left on
    assert bank.state("fan") == "off" and bank.state("lamp") == "on"


def test_a_shut_down_bank_cannot_be_switched_on_again(monkeypatch):
    bank, pins = _bank(monkeypatch)
    bank.set("lamp", "on")
    bank.close()                                               # gives the pins back, no more
    assert pins[17].value is False and bank.set("lamp", "on") is True
    bank.shutdown()
    assert pins[17].value is False and pins[17].closed
    assert bank.set("lamp", "on") is False                     # a late loop after shutdown
    assert bank.set("lamp", "off") is True
    assert not bank.claimed("lamp")
    assert bank.settle() == []                                 # the service starting again
    assert bank.set("lamp", "on") is True


def test_router_turns_a_transport_exception_into_a_refusal(tmp_path):
    class Boom:
        def state(self, device_id):
            return "off"

        def set(self, device_id, action):
            raise RuntimeError("GPIO busy")

    router = DeviceRouter(_registry(tmp_path), Boom(), None, guard=SwitchGuard(0, 0))
    ok, reason = router.set("lamp", "on")
    assert ok is False and "GPIO busy" in reason
    assert router.set("lamp", "on")[0] is False                # the lock was released


# ── activity log ────────────────────────────────────────────────────────────

def test_activity_log_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    path = str(blocker / "activity.jsonl")                     # a directory that cannot exist
    assert actuation_log.record(path, device="lamp", action="on", rule_id="", matched=[], ok=True) is False
    good = str(tmp_path / "activity.jsonl")
    assert actuation_log.record(good, device="lamp", action="on", rule_id="", matched=[{1, 2}], ok=True) is True
    assert actuation_log.recent(good)[0]["device"] == "lamp"


def test_activity_log_keeps_every_line_written_at_once(tmp_path, monkeypatch):
    path = str(tmp_path / "activity.jsonl")
    monkeypatch.setattr(actuation_log, "MAX_BYTES", 2000)      # trims constantly
    monkeypatch.setattr(actuation_log, "MAX_LINES", 100_000)

    def write(n):
        for i in range(50):
            actuation_log.record(path, device=f"d{n}", action="on", rule_id=str(i), matched=[], ok=True)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    lines = open(path).read().splitlines()
    assert lines and all(json.loads(line)["action"] == "on" for line in lines)   # no torn lines


# ── home loop ───────────────────────────────────────────────────────────────

def test_home_tick_runs_every_part_when_one_fails(tmp_path):
    registry = _registry(tmp_path)
    router = _FlakyRouter()
    ctx = types.SimpleNamespace(registry=registry, device_router=router,
                                log_path=str(tmp_path / "activity.jsonl"), descriptor={})
    home = HomeServices(ctx, runtime=types.SimpleNamespace(note_state=lambda *a: None),
                        data_dir=str(tmp_path))
    asked = []
    home.presence_fn = lambda: asked.append(1) or True
    home.schedules.due = lambda now: (_ for _ in ()).throw(KeyError("kind"))

    with pytest.raises(KeyError):
        home.tick()
    assert asked == [1]                                        # presence was still checked
    assert home.last_error.startswith("schedules: KeyError")


# ── alert email ─────────────────────────────────────────────────────────────

def test_a_failed_alert_email_does_not_use_up_the_cooldown(monkeypatch):
    alerts = sys.modules[gw.send_email_alert.__module__]
    monkeypatch.setattr(gw.STATE.modes, "email_off", False)
    monkeypatch.setattr(gw.STATE.modes, "idle", False)
    monkeypatch.setattr(gw.STATE.config, "email_cooldown", 300)
    monkeypatch.setattr(gw.STATE.alerts, "last_email_sent_time", 0.0)
    monkeypatch.setattr(gw._cloud, "publish_alert_async", lambda *a, **k: None)
    sent = []

    def mail(subject, body, *a, **k):
        if not sent:
            sent.append("failed")
            raise OSError("smtp down")
        sent.append(subject)

    monkeypatch.setattr(gw, "_send_mail", mail)
    clock = Clock()
    monkeypatch.setattr(alerts.time, "time", clock)

    gw.send_email_alert(label="Knife")
    assert sent == ["failed"]
    gw.send_email_alert(label="Knife")
    assert sent == ["failed"]                                  # not retried at once
    clock.now += alerts.EMAIL_RETRY_S + 1
    gw.send_email_alert(label="Knife")
    assert len(sent) == 2 and "Knife" in sent[1]               # used to wait the full 300 s
    clock.now += 60
    gw.send_email_alert(label="Knife")
    assert len(sent) == 2                                      # a sent email still starts the cooldown


# ── frame callback ──────────────────────────────────────────────────────────

def test_frame_callback_passes_the_buffer_on_when_it_fails(monkeypatch):
    detection = sys.modules[gw.app_callback.__module__]
    logged = []
    monkeypatch.setattr(detection, "_process_frame",
                        lambda *a: (_ for _ in ()).throw(ValueError("bad zone")))
    monkeypatch.setattr(detection, "_callback_failures", {"count": 0, "logged_at": 0.0})
    monkeypatch.setattr(gw, "log_system_update", logged.append)
    for _ in range(5):
        assert gw.app_callback(None, None, None) == gw.Gst.PadProbeReturn.OK
    assert len(logged) == 1 and "ValueError: bad zone" in logged[0]     # once a minute, not per frame
    assert detection._callback_failures["count"] == 5


# ── config ──────────────────────────────────────────────────────────────────

def test_config_values_of_the_wrong_type_are_not_loaded():
    typed = sys.modules[gw.load_config.__module__]._typed
    assert typed({"email_cooldown": "60"}, "email_cooldown", 60) == 60
    assert typed({"email_cooldown": 90}, "email_cooldown", 60) == 90
    assert typed({"email_cooldown": True}, "email_cooldown", 60) == 60
    assert typed({"detection_threshold": 1}, "detection_threshold", 0.3) == 1
    assert typed({"watch_labels": "person"}, "watch_labels", ["Person"]) == ["Person"]
    assert typed({}, "mode_schedule", {"a": 1}) == {"a": 1}


# ── second pass: shortcuts, zones, phone alerts, logs, backups ──────────────

from basic_pipelines.garuda_auto import shortcuts as shortcuts_mod      # noqa: E402
from basic_pipelines.garuda_core.backup import STATE_FILES              # noqa: E402

zones_mod = sys.modules[gw.ZoneStore.__module__]
push_mod = sys.modules[gw.PushStore.__module__]


def _shortcut(sid, trigger):
    return {"id": sid, "name": sid, "trigger": trigger, "steps": [{"stop": True}], "enabled": True,
            "created_by": "admin", "last_run": None, "runs": []}


def test_one_unreadable_shortcut_does_not_stop_the_others():
    clock = Clock()
    store = shortcuts_mod.ShortcutStore(None)
    store.items = [_shortcut("broken", {"type": "time"}),            # no "at": raises when read
                   _shortcut("good", {"type": "every", "minutes": 1})]
    engine = shortcuts_mod.ShortcutEngine(store, do_fn=lambda *a: {}, facts_fn=dict,
                                          role_of=lambda user: "admin", clock=clock)
    ran = []
    engine.run = lambda sid, **kw: ran.append(sid)
    engine.tick()                                              # used to raise here, every second
    clock.now += 61
    engine.tick()
    assert ran == ["good"]
    assert engine.last_error.startswith("broken: KeyError")


def test_shortcut_store_drops_entries_it_cannot_read(tmp_path):
    path = tmp_path / "shortcuts.json"
    path.write_text(json.dumps([_shortcut("ok", {"type": "manual"}), {"id": "x"}, "junk",
                                {"id": "y", "name": "Y", "steps": [], "trigger": "manual"}]))
    (tmp_path / "shortcuts_waits.json").write_text(json.dumps(
        {"ok": {"by": "admin", "cursor": [0]}, "bad": {"by": "admin"}, "worse": 3}))
    store = shortcuts_mod.ShortcutStore(str(path))
    assert [s["id"] for s in store.all()] == ["ok"]
    assert store.find("y") is None                             # used to raise on the entry without a name
    assert list(store.waits()) == ["ok"]
    assert len(_copies(path)) == 1


def test_zones_from_a_damaged_file_cannot_break_the_frame_callback(tmp_path):
    path = tmp_path / "detection_rules.json"
    path.write_text(json.dumps({
        "rules": {"Knife": {"home": "log", "night": "explode"}, "bad": "alert"},
        "zones": [{"rect": [0, 0, 0.5, 0.5], "mode": "quiet", "labels": ["knife"]},
                  {"rect": [0, 0, 1]}, {"mode": "alert"}, "junk"]}))
    store = zones_mod.ZoneStore(str(path))
    assert store.data["rules"] == {"knife": {"home": "log"}}
    assert len(store.data["zones"]) == 1
    for label in ("knife", "person"):                          # used to raise KeyError: 'rect'
        assert zones_mod.decide(store.data, label, (0.1, 0.1, 0.2, 0.2), "night", {"knife"}) in zones_mod.ACTIONS
    assert len(_copies(path)) == 1
    with pytest.raises(zones_mod.HTTPException):
        zones_mod._clean_zones(["junk"])                       # a 400, not a 500


@pytest.mark.parametrize("url,ok", [
    ("https://fcm.googleapis.com/fcm/send/abc", True),
    ("https://updates.push.services.mozilla.com/wpush/v2/x", True),
    ("http://fcm.googleapis.com/fcm/send/abc", False),         # not https
    ("https://192.168.1.1/admin", False),                      # the router
    ("https://[::1]/x", False),
    ("https://localhost/x", False),
    ("https://nas.local/x", False),
    ("https://printer/x", False),
    ("https://user:pw@push.example.com/x", False),
    ("https://push.example.com:8443/x", False),
    ("https://push.example.com/" + "a" * 2000, False),
    (None, False),
])
def test_push_endpoint_must_be_a_public_push_service(url, ok):
    assert push_mod.endpoint_ok(url) is ok


def test_push_store_limits_and_ignores_bad_saved_entries(tmp_path):
    path = tmp_path / "push.json"
    good = {"sub": {"endpoint": "https://push.example.com/1", "keys": {"p256dh": "a", "auth": "b"}},
            "user": "ravi", "kinds": ["alert"]}
    path.write_text(json.dumps({"subs": {"https://push.example.com/1": good,
                                         "https://10.0.0.5/x": {**good, "sub": {"endpoint": "https://10.0.0.5/x", "keys": {}}},
                                         "junk": 5}}))
    store = push_mod.PushStore(str(path))
    assert list(store.subs) == ["https://push.example.com/1"]
    assert [t["user"] for t in store.targets("alert")] == ["ravi"]
    for n in range(2, push_mod.MAX_SUBS_PER_USER + 1):
        assert store.add({"endpoint": f"https://push.example.com/{n}", "keys": {}}, "ravi", ["alert"]) == ""
    assert "too many" in store.add({"endpoint": "https://push.example.com/99", "keys": {}}, "ravi", ["alert"])
    assert store.add({"endpoint": "https://push.example.com/1", "keys": {}}, "ravi", ["alert"]) == ""   # a renewal


def test_activity_log_readers_skip_lines_they_cannot_use(tmp_path):
    path = tmp_path / "activity.jsonl"
    path.write_text('5\n{"device": "lamp"}\n"text"\n[1]\n')
    actuation_log.record(str(path), device="lamp", action="on", rule_id="", matched=[], ok=True)
    assert [e["device"] for e in actuation_log.recent(str(path))] == ["lamp"]
    assert actuation_log.last_for(str(path), "lamp")["action"] == "on"       # used to raise on the 5


def test_log_lines_wait_for_the_next_flush_when_the_disk_refuses(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    path = str(blocker / "system.log")                         # cannot be created
    try:
        with gw._log_buffer_lock:                              # the suite makes _perm_write a no-op
            gw._log_buffer[path].extend(["first", "second"])
        gw._do_flush_logs()
        assert gw._log_buffer[path] == ["first", "second"]     # used to be dropped
    finally:
        with gw._log_buffer_lock:
            gw._log_buffer.pop(path, None)


def test_backups_cover_the_arm_state_zones_and_phone_subscriptions():
    for name in ("arm.json", "detection_rules.json", "push_subscriptions.json"):
        assert name in STATE_FILES


# ── camera watch ────────────────────────────────────────────────────────────

def test_a_camera_that_stops_is_reported_once_and_again_when_back(monkeypatch):
    monitors = sys.modules[gw._schedule_monitor.__module__]
    assert monitors.camera_stall(0, 100.0, 100.0 + monitors.CAMERA_START_S - 1) == 0      # still starting
    assert monitors.camera_stall(0, 100.0, 100.0 + monitors.CAMERA_START_S + 1) > 0       # never started
    assert monitors.camera_stall(500.0, 100.0, 505.0) == 0                                # live
    assert monitors.camera_stall(500.0, 100.0, 500.0 + monitors.CAMERA_STALL_S + 1) > 0   # stopped

    clock = Clock(1000.0)
    logged, mails = [], []
    monkeypatch.delenv("GARUDA_CAMERA_STALL_ALERT", raising=False)
    monkeypatch.setattr(gw, "log_system_update", logged.append)
    monkeypatch.setattr(gw, "_append_detection_perm", lambda *a, **k: None)
    monkeypatch.setattr(gw, "push_urgent_ws", lambda: None)
    monkeypatch.setattr(monitors, "_camera_stopped_email", mails.append)
    monkeypatch.setattr(gw.STATE.camera, "frame_ts", 990.0)

    def step(seconds):
        clock.now += seconds

    monitors.camera_watch(every=20.0, sleep=step, clock=clock, rounds=4)   # at 1000, 1020, 1040, 1060
    for _ in range(50):
        if mails:
            break
        threading.Event().wait(0.02)
    assert [l for l in logged if "No frames" in l].__len__() == 1          # once, not every pass
    assert len(mails) == 1

    monkeypatch.setenv("GARUDA_CAMERA_STALL_ALERT", "0")
    logged.clear()
    monitors.camera_watch(every=20.0, sleep=step, clock=clock, rounds=3)
    assert logged == []                                                    # switched off
