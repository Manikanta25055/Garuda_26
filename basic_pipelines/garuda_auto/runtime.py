"""The loop that makes the rules actually run.

SceneBuilder and RuleEngine were written, tested and never connected to
anything: nothing called them, so `descriptor` stayed empty, no rule had ever
fired, and the activity log had never had a line written to it. This is the
missing half.

Two speeds, on purpose. `observe()` is pure arithmetic over one frame's
detections and is called from the GStreamer callback, which must never block —
the primary pipeline is the thing that must not stall. `tick()` does the I/O
(relay, MQTT, log, store) and runs on its own thread a few times a second, so a
slow relay or a dead MQTT broker cannot back up into the video path.
"""
import threading
import time

from .rule_engine import RuleEngine
from .scene_state import SceneBuilder
from . import actuation_log

# Normalised frame coordinates. A person is placed by the centre of their box.
# Disjoint and left-to-right, because _zone_for returns the first match.
DEFAULT_ZONES = {
    "desk":   (0.00, 0.00, 0.33, 1.00),
    "center": (0.33, 0.00, 0.67, 1.00),
    "door":   (0.67, 0.00, 1.00, 1.00),
}

TICK_INTERVAL_S = 0.5

# Used only until a real sensor is registered. The corpus refers to
# temperature_c and humidity_pct, so the fields have to exist; a rule that
# compares against them simply never matches while these are the values.
NEUTRAL_TEMPERATURE_C = 22.0
NEUTRAL_HUMIDITY_PCT = 50.0


class DrishtiRuntime:
    def __init__(self, ctx, zones=None, clock=time.time):
        self.ctx = ctx
        self._clock = clock
        self._lock = threading.Lock()
        self.scene = SceneBuilder(zones or DEFAULT_ZONES, ctx.registry, clock=clock)
        self.engine = RuleEngine(ctx.store, clock=clock)
        self._stop = threading.Event()
        self._thread = None
        # Observability. A loop nobody can see is a loop nobody can tell has
        # stopped, and this one is the difference between a house that
        # automates and a user interface over a registry.
        self.ticks = 0
        self.fires = 0
        self.last_tick = 0.0
        self.last_error = ""
        # Seed the descriptor so a question asked before the first frame gets
        # "nobody is home" rather than "I have no reading".
        self.observe([], luma=0)

    # ── perception ────────────────────────────────────────────────────────────

    def _sensor_value(self, type_name, default):
        for device in self.ctx.registry.devices:
            if device.get("type") == type_name and device.get("enabled", True):
                value = self.scene._device_state.get(device["id"])
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    return float(value)
        return default

    def observe(self, detections, luma, hour=None):
        """One frame in, one descriptor out. Called from the video callback."""
        with self._lock:
            descriptor = self.scene.update(
                detections,
                luma,
                self._sensor_value("sensor.temperature", NEUTRAL_TEMPERATURE_C),
                self._sensor_value("sensor.humidity", NEUTRAL_HUMIDITY_PCT),
                time.localtime(self._clock()).tm_hour if hour is None else hour,
            )
            self.ctx.descriptor = descriptor
        return descriptor

    # ── action ────────────────────────────────────────────────────────────────

    def _conditions_of(self, rule_id):
        for rule in self.ctx.store.rules:
            if rule.get("id") == rule_id:
                return list(next(iter(rule["when"].items()))[1])
        return []

    def _record_fire(self, rule_id):
        """Count the fire on the rule itself so the card can show it."""
        for rule in self.ctx.store.rules:
            if rule.get("id") == rule_id:
                rule["fired_count"] = rule.get("fired_count", 0) + 1
                rule["last_fired"] = int(self._clock())
                return True
        return False

    def tick(self):
        """Evaluate the rule base once and perform what it asks for."""
        with self._lock:
            descriptor = dict(self.ctx.descriptor)
        self.ticks += 1
        self.last_tick = self._clock()
        actions = self.engine.evaluate(descriptor)
        if not actions:
            return []

        performed, touched = [], set()
        for action in actions:
            ok, reason = self.ctx.device_router.set(action["device"], action["action"])
            if ok:
                # The rule base can now see what it just did, so a rule that
                # reads lamp_state stops fighting the one that writes it.
                with self._lock:
                    self.scene.set_device_state(action["device"], action["action"])
            actuation_log.record(
                self.ctx.log_path,
                device=action["device"],
                action=action["action"],
                rule_id=action["rule_id"],
                matched=self._conditions_of(action["rule_id"]),
                ok=ok,
                reason=reason,
                clock=self._clock,
            )
            if self._record_fire(action["rule_id"]):
                touched.add(action["rule_id"])
            performed.append({**action, "ok": ok, "reason": reason})

        self.fires += len(performed)
        if touched:
            # Fires are rate-limited by each rule's cooldown, so this is not a
            # per-tick write.
            self.ctx.store.save()
        return performed

    # ── registry changes ──────────────────────────────────────────────────────

    def rebind(self):
        with self._lock:
            self.scene.registry = self.ctx.registry
            self.scene.rebind()
        self.engine.store = self.ctx.store

    # ── thread ────────────────────────────────────────────────────────────────

    def start(self, interval=TICK_INTERVAL_S):
        if self._thread is not None:
            return
        self._stop.clear()

        def loop():
            while not self._stop.wait(interval):
                try:
                    self.tick()
                except Exception as exc:
                    # A rule base that throws must not take the loop with it,
                    # or one bad rule silently stops every other rule too.
                    # It is recorded rather than swallowed, so the failure is
                    # visible instead of looking like an idle house.
                    self.last_error = f"{type(exc).__name__}: {exc}"

        self._thread = threading.Thread(target=loop, daemon=True, name="drishti-rules")
        self._thread.start()

    def stop(self):
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)

    def health(self):
        """What the System screen shows about the loop."""
        now = self._clock()
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "ticks": self.ticks,
            "fires": self.fires,
            "last_tick_age_s": round(now - self.last_tick, 1) if self.last_tick else None,
            "rules": len(self.ctx.store.rules),
            "orphaned_rules": len(self.ctx.store.orphaned),
            "devices": len(self.ctx.registry.devices),
            "last_error": self.last_error,
        }
