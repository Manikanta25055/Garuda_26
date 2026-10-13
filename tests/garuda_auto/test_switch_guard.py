"""The switch guard: no device is switched on faster than its hardware can take."""
import pytest

from basic_pipelines import drishti_api
from basic_pipelines.garuda_auto.actuators import RelayBank
from basic_pipelines.garuda_auto.device_registry import DeviceRegistry
from basic_pipelines.garuda_auto.switch_guard import SwitchGuard
from basic_pipelines.garuda_auto.transports import DeviceRouter, MqttBank

pytestmark = pytest.mark.unit

LAMP = {"id": "lamp_desk", "name": "Desk lamp", "type": "light", "room": "study",
        "transport": {"kind": "relay", "channel": 1}}


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def router(tmp_path):
    registry = DeviceRegistry(str(tmp_path / "devices.json"), relay_channels=(1, 2, 3))
    registry.add(LAMP)
    clock = Clock()
    guard = SwitchGuard(min_gap_s=1.0, max_per_minute=10, clock=clock)
    router = DeviceRouter(registry, RelayBank({"lamp_desk": 17}), MqttBank("localhost"), guard=guard)
    return router, clock


def test_off_is_never_held_back(router):
    router, clock = router
    router.set("lamp_desk", "on")
    for _ in range(20):                       # the safe direction, however fast
        assert router.set("lamp_desk", "off") == (True, "")


def test_asking_for_the_state_it_is_in_is_not_a_switch(router):
    router, clock = router
    assert router.set("lamp_desk", "on") == (True, "")
    for _ in range(20):
        assert router.set("lamp_desk", "on") == (True, "")


def test_on_straight_after_a_switch_is_held_back_and_the_relay_stays_off(router):
    router, clock = router
    router.set("lamp_desk", "on")
    clock.t += 0.3
    router.set("lamp_desk", "off")
    clock.t += 0.3
    ok, reason = router.set("lamp_desk", "on")
    assert not ok
    assert reason.startswith("Desk lamp switched 0.3 s ago") and "protect the hardware" in reason
    assert router.state("lamp_desk") == "off"
    clock.t += 1.0
    assert router.set("lamp_desk", "on") == (True, "")


def test_a_device_switched_on_too_often_in_a_minute_is_held_until_the_minute_frees(router):
    router, clock = router
    for _ in range(10):                       # a loop well spaced out, but endless
        assert router.set("lamp_desk", "on") == (True, "")
        clock.t += 1.5
        router.set("lamp_desk", "off")
        clock.t += 1.5
    ok, reason = router.set("lamp_desk", "on")
    assert not ok and "10 times in the last minute" in reason
    assert router.state("lamp_desk") == "off"
    clock.t += 31                             # the first "on" leaves the window
    assert router.set("lamp_desk", "on") == (True, "")


def test_a_relay_chattering_from_a_loop_is_stopped():
    clock = Clock()
    guard = SwitchGuard(clock=clock)
    switched = 0
    state = "off"
    for _ in range(200):                      # a loop asking ten times a second for a minute
        clock.t += 0.1
        want = "off" if state == "on" else "on"
        if guard.check("pump", want, state) is None:
            guard.record("pump", want, state)
            state = want
            switched += 1
    assert switched <= 2 * 10                 # at most ten "on"s, each with its "off"


def test_the_limits_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("RELAY_MIN_GAP_S", "0")
    monkeypatch.setenv("RELAY_MAX_PER_MINUTE", "3")
    guard = SwitchGuard.from_env()
    assert guard.min_gap_s == 0 and guard.max_per_minute == 3
    monkeypatch.setenv("RELAY_MIN_GAP_S", "soon")
    assert SwitchGuard.from_env().min_gap_s == 1.0     # a bad value keeps the default


def test_editing_a_device_does_not_forget_the_last_switch(tmp_path):
    ctx = drishti_api.build_context(data_dir=str(tmp_path), relay_channels=(1, 2, 3),
                                    channel_to_pin={1: 17, 2: 27, 3: 22})
    ctx.registry.add(dict(LAMP))
    ctx.rebuild()
    guard = ctx.device_router.guard
    ctx.device_router.set("lamp_desk", "on")
    ctx.device_router.set("lamp_desk", "off")
    ctx.rebuild()                             # what a registry change does
    assert ctx.device_router.guard is guard
    assert not ctx.device_router.set("lamp_desk", "on")[0]
