"""Keep the whole test suite off real GPIO and out of the live house's data.

On a laptop gpiozero is absent and RelayBank degrades to bookkeeping, so the
tests are harmless. On the Pi gpiozero is present and RelayBank constructs a
real OutputDevice -- running the suite there drives actual pins, and on a
board wired to relays that clicks them.

This lives at the repository root rather than under tests/garuda_auto/ because
the Drishti API tests build a RelayBank too, from a different directory.

gpiozero resolves its pin factory from this variable when a device is first
created, so setting it here covers every test regardless of import order.

The house's data (devices, rules, schedules, scenes, shortcuts, the action log)
lives in one directory that Garuda_web reads when it is imported. Run on the
Pi, the suite used to open the live one: it evaluated the house's real rules
on mock pins, appended to the real action log and rewrote the real rule file
with its own counts, next to the running service. The tests get a directory of
their own, seeded with the same two devices and nothing else.
"""
import json
import os
import tempfile

os.environ.setdefault("GPIOZERO_PIN_FACTORY", "mock")

_DATA = tempfile.mkdtemp(prefix="garuda-test-house-")
with open(os.path.join(_DATA, "devices.json"), "w") as _f:
    json.dump([
        {"id": "lamp", "name": "Lamp", "type": "light", "room": "study",
         "transport": {"kind": "relay", "channel": 1}, "enabled": True},
        {"id": "fan", "name": "Fan", "type": "fan", "room": "study",
         "transport": {"kind": "relay", "channel": 2}, "enabled": True},
    ], _f)
os.environ["DRISHTI_DATA_DIR"] = _DATA
