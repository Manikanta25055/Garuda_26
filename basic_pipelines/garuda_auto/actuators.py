"""Relay control via gpiozero.

gpiozero only -- RPi.GPIO does not work on the Pi 5. When gpiozero is absent
(laptop, CI) the bank degrades to bookkeeping so the rest of the system can be
exercised without hardware.
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
        self._state = {device: "off" for device in self.pin_map}
        self._outputs = {}
        if GPIO_AVAILABLE:
            for device, pin in self.pin_map.items():
                self._outputs[device] = OutputDevice(
                    pin, active_high=active_high, initial_value=False
                )
        else:
            log.warning("gpiozero unavailable -- relay bank running in no-op mode")

    def state(self, device):
        return self._state.get(device, "off")

    def set(self, device, action):
        if device not in self.pin_map:
            log.warning("refusing unknown device: %s", device)
            return False
        if action not in ("on", "off"):
            log.warning("refusing unknown action: %s", action)
            return False
        output = self._outputs.get(device)
        if output is not None:
            output.on() if action == "on" else output.off()
        self._state[device] = action
        return True

    def all_off(self):
        for device in self.pin_map:
            self.set(device, "off")

    def close(self):
        self.all_off()
        for output in self._outputs.values():
            try:
                output.close()
            except Exception:
                pass
        self._outputs.clear()
