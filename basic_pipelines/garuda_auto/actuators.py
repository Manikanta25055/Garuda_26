"""Relay control via gpiozero.

gpiozero only -- RPi.GPIO does not work on the Pi 5. When gpiozero is absent
(laptop, CI) the bank degrades to bookkeeping so the rest of the system can be
exercised without hardware.

Pins are claimed on first actuation, not on construction. A gpiozero pin
reservation is exclusive and process-wide, so a bank that opened every pin in
__init__ turned merely *importing* Garuda_web into seizing GPIO 17 -- which
would take the pin away from the running service, and made two import paths
for the same module collide during test collection. Nothing here touches
hardware until something asks it to.
"""
import logging

log = logging.getLogger(__name__)

try:
    from gpiozero import OutputDevice
    GPIO_AVAILABLE = True
except Exception:
    OutputDevice = None
    GPIO_AVAILABLE = False

# Most opto-isolated relay boards pull the input low to energise the coil.
ACTIVE_HIGH = False


class RelayBank:
    def __init__(self, pin_map, active_high=ACTIVE_HIGH):
        self.pin_map = dict(pin_map)
        self.active_high = active_high
        self._state = {device: "off" for device in self.pin_map}
        self._outputs = {}
        if not GPIO_AVAILABLE:
            log.warning("gpiozero unavailable -- relay bank running in no-op mode")

    def _output(self, device):
        """The gpiozero device for a pin, opening it the first time it is used."""
        if not GPIO_AVAILABLE:
            return None
        output = self._outputs.get(device)
        if output is None:
            output = OutputDevice(
                self.pin_map[device], active_high=self.active_high,
                initial_value=False,
            )
            self._outputs[device] = output
        return output

    def state(self, device):
        return self._state.get(device, "off")

    def set(self, device, action):
        if device not in self.pin_map:
            log.warning("refusing unknown device: %s", device)
            return False
        if action not in ("on", "off"):
            log.warning("refusing unknown action: %s", action)
            return False
        output = self._output(device)
        if output is not None:
            output.on() if action == "on" else output.off()
        self._state[device] = action
        return True

    def all_off(self):
        # Only drive pins this bank has already opened. A pin it never touched
        # is not energised by us, and opening it here just to write the value
        # it already has would claim it for no reason.
        for device in self.pin_map:
            output = self._outputs.get(device)
            if output is not None:
                output.off()
            self._state[device] = "off"

    def close(self):
        self.all_off()
        for output in self._outputs.values():
            try:
                output.close()
            except Exception:
                pass
        self._outputs.clear()
