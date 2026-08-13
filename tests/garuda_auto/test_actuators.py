import basic_pipelines.garuda_auto.actuators as actuators


class FakeOutput:
    instances = []

    def __init__(self, pin, active_high=True, initial_value=False):
        self.pin = pin
        self.active_high = active_high
        self.value = initial_value
        self.closed = False
        FakeOutput.instances.append(self)

    def on(self):
        self.value = True

    def off(self):
        self.value = False

    def close(self):
        self.closed = True


def _bank(monkeypatch):
    FakeOutput.instances = []
    monkeypatch.setattr(actuators, "OutputDevice", FakeOutput)
    monkeypatch.setattr(actuators, "GPIO_AVAILABLE", True)
    return actuators.RelayBank({"lamp": 17, "fan": 27})


def test_devices_start_off(monkeypatch):
    bank = _bank(monkeypatch)
    assert bank.state("lamp") == "off"
    assert bank.state("fan") == "off"


def test_set_on_drives_the_pin_and_updates_state(monkeypatch):
    bank = _bank(monkeypatch)
    assert bank.set("lamp", "on") is True
    assert bank.state("lamp") == "on"
    assert FakeOutput.instances[0].value is True


def test_opto_boards_are_configured_active_low(monkeypatch):
    bank = _bank(monkeypatch)
    bank.set("lamp", "on")
    assert FakeOutput.instances[0].active_high is False


def test_no_pin_is_claimed_until_first_actuation(monkeypatch):
    # A gpiozero reservation is exclusive and process-wide. Claiming pins in
    # __init__ meant importing Garuda_web took GPIO 17 away from whatever else
    # held it, the running service included.
    _bank(monkeypatch)
    assert FakeOutput.instances == []


def test_actuating_one_device_does_not_claim_the_others(monkeypatch):
    bank = _bank(monkeypatch)
    bank.set("lamp", "on")
    assert [o.pin for o in FakeOutput.instances] == [17]


def test_all_off_does_not_claim_unused_pins(monkeypatch):
    bank = _bank(monkeypatch)
    bank.all_off()
    assert FakeOutput.instances == []
    assert bank.state("lamp") == "off"


def test_the_pin_is_reused_across_actuations(monkeypatch):
    bank = _bank(monkeypatch)
    bank.set("lamp", "on")
    bank.set("lamp", "off")
    bank.set("lamp", "on")
    assert len(FakeOutput.instances) == 1


def test_unknown_device_is_refused(monkeypatch):
    bank = _bank(monkeypatch)
    assert bank.set("front_door_lock", "on") is False


def test_unknown_action_is_refused(monkeypatch):
    bank = _bank(monkeypatch)
    assert bank.set("lamp", "explode") is False


def test_all_off_clears_every_device(monkeypatch):
    bank = _bank(monkeypatch)
    bank.set("lamp", "on")
    bank.set("fan", "on")
    bank.all_off()
    assert bank.state("lamp") == "off"
    assert bank.state("fan") == "off"


def test_works_without_gpio_present(monkeypatch):
    monkeypatch.setattr(actuators, "GPIO_AVAILABLE", False)
    bank = actuators.RelayBank({"lamp": 17})
    assert bank.set("lamp", "on") is True
    assert bank.state("lamp") == "on"
