"""Switch guard: a device is never switched on faster than its hardware can take.

Every switch -- a button, a scene, a schedule, a rule, a shortcut, Narada, a
page Narada wrote -- ends in DeviceRouter.set, and nothing there limited how
often a device could be turned on. Two rules that undo each other, a shortcut
with a loop in it or a page calling set_device on a timer could click a relay
several times a second. A mechanical relay's contacts wear with every switch,
and a compressor or a motor behind one can be damaged by being restarted
before it has stopped.

So, per device:
  * "off" is never refused. It is the safe direction: all-off, the emergency
    stop and shutting the service down must always work. Chatter needs a
    stream of "on"s, so holding those back is enough to stop it.
  * Asking for the state a device is already in is not a switch, and passes.
  * "on" is refused when the device last changed less than MIN_GAP_S ago, or
    when it has been switched on MAX_PER_MINUTE times in the last minute.

A refusal comes back as the reason the switch did not happen (the Activity
page shows it), and is logged at most once a minute per device.

RELAY_MIN_GAP_S and RELAY_MAX_PER_MINUTE in .env change the limits; 0 turns
that limit off.
"""
import collections
import logging
import os
import threading
import time

log = logging.getLogger(__name__)

MIN_GAP_S = 1.0
MAX_PER_MINUTE = 10
_WINDOW_S = 60.0


def _env_number(name, default, cast):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = cast(raw)
    except ValueError:
        log.warning("%s=%r is not a number; using %s", name, raw, default)
        return default
    return max(0, value)


class SwitchGuard:
    def __init__(self, min_gap_s=MIN_GAP_S, max_per_minute=MAX_PER_MINUTE, clock=time.monotonic):
        self.min_gap_s = min_gap_s
        self.max_per_minute = max_per_minute
        self._clock = clock
        self._lock = threading.Lock()
        self._last_change = {}                               # device -> time of its last change
        self._ons = collections.defaultdict(collections.deque)   # device -> times it went on
        self._warned = {}                                    # device -> time of the last warning

    @classmethod
    def from_env(cls):
        return cls(min_gap_s=_env_number("RELAY_MIN_GAP_S", MIN_GAP_S, float),
                   max_per_minute=_env_number("RELAY_MAX_PER_MINUTE", MAX_PER_MINUTE, int))

    def check(self, device_id, action, current):
        """None when `action` may go ahead, else the reason it may not."""
        if action != "on" or current == "on":
            return None
        now = self._clock()
        with self._lock:
            last = self._last_change.get(device_id)
            if self.min_gap_s and last is not None and now - last < self.min_gap_s:
                return self._refuse(device_id, now, f"switched {now - last:.1f} s ago; wait "
                                    f"{self.min_gap_s:g} s between switches to protect the hardware")
            ons = self._ons[device_id]
            while ons and now - ons[0] >= _WINDOW_S:
                ons.popleft()
            if self.max_per_minute and len(ons) >= self.max_per_minute:
                wait = _WINDOW_S - (now - ons[0])
                return self._refuse(device_id, now, f"switched on {len(ons)} times in the last minute; "
                                    f"held back for {wait:.0f} s to protect the hardware")
        return None

    def record(self, device_id, action, previous):
        """Note a switch that happened. Only real changes count."""
        if action == previous:
            return
        now = self._clock()
        with self._lock:
            self._last_change[device_id] = now
            if action == "on":
                self._ons[device_id].append(now)

    def _refuse(self, device_id, now, reason):
        # Called with the lock held.
        if now - self._warned.get(device_id, float("-inf")) >= _WINDOW_S:
            self._warned[device_id] = now
            log.warning("switch guard: %s held back: %s", device_id, reason)
        return reason
