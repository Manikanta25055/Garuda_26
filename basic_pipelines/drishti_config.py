"""Deployment constants for Drishti, importable without the pipeline.

Garuda_web.py pulls in GStreamer, Hailo and a live relay bank that claims GPIO
pins on import. Anything that only needs to know where the data lives, or which
relay channel maps to which pin, imports this instead — a seeding or migration
script must not have to start the camera, and must not fight the running
service for pin 17.

The relay bank is the 8-channel opto-isolated board. A user assigns a channel,
never a BCM pin: a wrong pin could drive one the Hailo HAT, the camera or the
I2C bus is using. Channels 1 and 2 are the lamp and fan from the Narada-RS
design. The stepper in the gesture prototype moves to 5/6/13/19 so it stops
colliding with this bank.
"""
import os
from pathlib import Path

# DRISHTI_DATA_DIR moves the house's data elsewhere. The test suite sets it
# (conftest.py at the repository root): run on the Pi, the tests used to load
# the live house's rules, schedules and shortcuts, evaluate them on mock pins
# and write their counts back into the live files.
DATA_DIR = os.environ.get("DRISHTI_DATA_DIR") or str(Path(__file__).resolve().parent / "system_logs")

RELAY_CHANNELS = (1, 2, 3, 4, 5, 6, 7)
CHANNEL_TO_PIN = {1: 17, 2: 27, 3: 22, 4: 23, 5: 24, 6: 25, 7: 26}
