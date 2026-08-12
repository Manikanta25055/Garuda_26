"""Keep the unit suite off real GPIO.

On a laptop gpiozero is absent and RelayBank degrades to bookkeeping, so the
tests are harmless. On the Pi gpiozero is present and RelayBank constructs a
real OutputDevice -- running the suite there drives actual pins, and on a
board wired to relays that clicks them.

gpiozero resolves its pin factory from this variable when a device is first
created, so setting it here covers every test in this package regardless of
import order.
"""
import os

os.environ.setdefault("GPIOZERO_PIN_FACTORY", "mock")
