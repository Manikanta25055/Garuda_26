"""E4: decision-to-contact time on the real relay, through the production path.

Wiring (relay channel 2, the "fan", turned into a dry contact for the test):
    channel 2 COM -> Pi GND (pin 20), channel 2 NO -> GPIO24 (pin 18).
    Nothing else on channel 2's COM or NO. GPIO24 is pulled up, so the contact
    closing pulls it low. IN2 stays on GPIO27 as in service.

garuda-web must be stopped: it holds GPIO17/27.

    sudo systemctl stop garuda-web
    python3 -m evaluation.narada_rs.relay_latency --cycles 200
    sudo systemctl start garuda-web

Each cycle: a descriptor that makes the rule hold, RuleEngine.evaluate (as the
house loop runs it), DeviceRouter.set (switch guard included), RelayBank on
GPIO27; then the room empties and the opposite rule switches it off. GPIO24's
edges are read with kernel timestamps on the same monotonic clock as the
software marks, so every interval is end to end on one clock.
"""
import argparse
import os
import sys
import tempfile
import threading
import time

import gpiod

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from basic_pipelines import drishti_api                                  # noqa: E402
from basic_pipelines.garuda_auto.rule_engine import RuleEngine           # noqa: E402
from evaluation.narada_rs.run_eval import _dist, _write                  # noqa: E402

SENSE_PIN = 24
FAN = {"id": "fan", "name": "Fan", "type": "fan", "room": "study",
       "transport": {"kind": "relay", "channel": 2}}
RULES = [
    {"id": "on", "source_utterance": "fan on when someone is here", "cooldown_s": 0,
     "when": {"all": [{"field": "occupancy", "op": "==", "value": "occupied"}]},
     "then": [{"device": "fan", "action": "on"}]},
    {"id": "off", "source_utterance": "fan off when the room is empty", "cooldown_s": 0,
     "when": {"all": [{"field": "occupancy", "op": "==", "value": "empty"}]},
     "then": [{"device": "fan", "action": "off"}]},
]


def _header_chip():
    for i in range(32):
        try:
            chip = gpiod.Chip(f"gpiochip{i}")
        except (OSError, FileNotFoundError):
            continue
        if chip.label() == "pinctrl-rp1":
            return chip
        chip.close()
    sys.exit("no pinctrl-rp1 GPIO chip: not a Pi 5?")


class EdgeLog:
    """Every edge on the sense pin, kernel timestamped (CLOCK_MONOTONIC)."""

    def __init__(self, line):
        self.line, self.edges, self._stop = line, [], threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            if self.line.event_wait(sec=0, nsec=200_000_000):
                ev = self.line.event_read()
                low = ev.type == gpiod.LineEvent.FALLING_EDGE
                self.edges.append((ev.sec * 1_000_000_000 + ev.nsec, "closed" if low else "open"))

    def stop(self):
        self._stop.set()
        self._thread.join()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=200)
    ap.add_argument("--settle", type=float, default=1.3, help="seconds between switches (guard: 1 s)")
    ap.add_argument("--window-ms", type=float, default=100.0)
    args = ap.parse_args()

    chip = _header_chip()
    # gpiozero 2.0.1's lgpio factory ignores its chip argument and guesses
    # gpiochip0 or gpiochip4; on some boots the Pi 5 header is numbered higher,
    # so open the chip found by its label.
    import lgpio
    from gpiozero import Device
    from gpiozero.pins.lgpio import LGPIOFactory, LGPIOPin
    from gpiozero.pins.local import LocalPiFactory

    class HeaderFactory(LGPIOFactory):
        def __init__(self, number):
            LocalPiFactory.__init__(self)
            self._handle = lgpio.gpiochip_open(number)
            self._chip = number
            self.pin_class = LGPIOPin

    Device.pin_factory = HeaderFactory(int(chip.name().replace("gpiochip", "")))
    line = chip.get_line(SENSE_PIN)
    line.request(consumer="drishti-e4", type=gpiod.LINE_REQ_EV_BOTH_EDGES,
                 flags=gpiod.LINE_REQ_FLAG_BIAS_PULL_UP)
    time.sleep(0.05)
    if line.get_value() != 1:
        sys.exit("GPIO24 reads low with the relay off: check the wiring (COM to GND, NO to GPIO24)")

    with tempfile.TemporaryDirectory(prefix="drishti-e4-") as data_dir:
        ctx = drishti_api.build_context(data_dir=data_dir, relay_channels=(1, 2),
                                        channel_to_pin={1: 17, 2: 27})
        ok, why = ctx.registry.add(dict(FAN))
        if not ok:
            sys.exit(why)
        ctx.rebuild()
        ctx.store.rules = [dict(r) for r in RULES]
        engine = RuleEngine(ctx.store, once=True)
        router = ctx.device_router
        log = EdgeLog(line)
        marks = []
        try:
            engine.prime({"occupancy": "empty"})
            for cycle in range(args.cycles):
                for occupancy, want in (("occupied", "on"), ("empty", "off")):
                    time.sleep(args.settle)
                    t_decide = time.monotonic_ns()
                    actions = engine.evaluate({"occupancy": occupancy})
                    t_eval = time.monotonic_ns()
                    ok, reason = False, "no action issued"
                    for act in actions:
                        if router.state(act["device"]) == act["action"]:
                            continue
                        ok, reason = router.set(act["device"], act["action"])
                    t_set = time.monotonic_ns()
                    marks.append({"cycle": cycle, "action": want, "t_decide": t_decide,
                                  "t_eval": t_eval, "t_set": t_set,
                                  "issued": [a["action"] for a in actions], "ok": ok, "reason": reason})
                if cycle % 20 == 0:
                    print("cycle", cycle, flush=True)
            time.sleep(0.3)
        finally:
            router.set("fan", "off")
            time.sleep(0.2)
            log.stop()
            ctx.relay_bank.close()
            line.release()

    window = int(args.window_ms * 1e6)
    rows, bad = [], 0
    for m in marks:
        want_state = "closed" if m["action"] == "on" else "open"
        hits = [(t, s) for t, s in log.edges if m["t_set"] - 10_000_000 <= t <= m["t_decide"] + window]
        first = next((t for t, s in hits if s == want_state), None)
        if first is None or not m["ok"]:
            bad += 1
            rows.append({**m, "contact_ns": None})
            continue
        rows.append({**m, "contact_ns": first, "settled_ns": hits[-1][0], "edges_in_window": len(hits),
                     "decide_to_contact_ms": (first - m["t_decide"]) / 1e6,
                     "decide_to_gpio_ms": (m["t_set"] - m["t_decide"]) / 1e6,
                     "evaluate_us": (m["t_eval"] - m["t_decide"]) / 1e3,
                     "bounce_ms": (hits[-1][0] - first) / 1e6})
    out = {"cycles": args.cycles, "switches": len(marks), "unmatched": bad}
    for action in ("on", "off"):
        good = [r for r in rows if r["action"] == action and r.get("contact_ns")]
        out[action] = {
            "decide_to_contact_ms": _dist([r["decide_to_contact_ms"] for r in good]),
            "decide_to_gpio_ms": _dist([r["decide_to_gpio_ms"] for r in good]),
            "evaluate_us": _dist([r["evaluate_us"] for r in good]),
            "bounce_ms": _dist([r["bounce_ms"] for r in good]),
            "bounced": sum(1 for r in good if r["edges_in_window"] > 1),
        }
        d = out[action]
        print(action, "decide->contact ms p50 %.2f p99 %.2f max %.2f | decide->gpio ms p50 %.3f | bounced %d/%d"
              % (d["decide_to_contact_ms"]["p50"], d["decide_to_contact_ms"]["p99"],
                 d["decide_to_contact_ms"]["max"], d["decide_to_gpio_ms"]["p50"], d["bounced"], len(good)))
    print("unmatched", bad)
    _write("relay_latency.json", {**out, "rows": rows, "all_edges": len(log.edges)})


if __name__ == "__main__":
    main()
