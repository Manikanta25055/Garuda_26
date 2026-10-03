import pytest
from basic_pipelines.garuda_auto import local_lane
from basic_pipelines.garuda_auto.device_registry import DeviceRegistry

pytestmark = pytest.mark.unit

LAMP = {"id": "lamp_desk", "name": "Desk lamp", "type": "light", "room": "study",
        "transport": {"kind": "relay", "channel": 1}}

DESCRIPTOR = {
    "occupancy": "occupied", "person_count": 1, "occupancy_duration_s": 120,
    "zone": "desk", "posture": "seated", "ambient_luma": 90,
    "temperature_c": 24.5, "humidity_pct": 48.0, "hour": 19,
    "lamp_desk_state": "off",
}


class FakeRouter:
    def __init__(self):
        self.calls = []

    def set(self, device_id, action):
        self.calls.append((device_id, action))
        return True, ""

    def state(self, device_id):
        return "off"

    def available(self, device_id):
        return True


class FakeStore:
    rules = [{"id": "r_001", "source_utterance": "turn the desk lamp on when I sit down"}]


@pytest.fixture
def parts(tmp_path):
    registry = DeviceRegistry(str(tmp_path / "devices.json"), relay_channels=(1, 2, 3))
    registry.add(LAMP)
    return registry, FakeRouter(), str(tmp_path / "actuations.jsonl")


def call(text, parts):
    registry, router, log_path = parts
    return local_lane.answer(text, registry=registry, descriptor=DESCRIPTOR,
                             router=router, log_path=log_path, store=FakeStore())


def test_answers_whether_anyone_is_home(parts):
    result = call("is anyone home?", parts)
    assert result["kind"] == "state"
    assert result["resolved"] == "on-device"
    assert "1" in result["text"]


def test_answers_the_temperature(parts):
    assert "24.5" in call("what's the temperature?", parts)["text"]


def test_answers_the_humidity(parts):
    assert "48" in call("what's the humidity?", parts)["text"]


def test_answers_a_device_state_by_name(parts):
    result = call("is the desk lamp on?", parts)
    assert result["kind"] == "state"
    assert "off" in result["text"].lower()


def test_turns_a_device_on(parts):
    _, router, _ = parts
    result = call("turn the desk lamp on", parts)
    assert result["kind"] == "control"
    assert router.calls == [("lamp_desk", "on")]


def test_turns_a_device_off(parts):
    _, router, _ = parts
    call("turn off the desk lamp", parts)
    assert router.calls == [("lamp_desk", "off")]


def test_direct_control_is_recorded_in_the_activity_log(parts):
    from basic_pipelines.garuda_auto import actuation_log
    _, _, log_path = parts
    call("turn the desk lamp on", parts)
    entries = actuation_log.recent(log_path)
    assert entries[0]["device"] == "lamp_desk"
    assert entries[0]["rule_id"] == ""


def test_explains_why_a_device_changed(parts):
    from basic_pipelines.garuda_auto import actuation_log
    _, _, log_path = parts
    actuation_log.record(log_path, device="lamp_desk", action="on", rule_id="r_001",
                         matched=[{"field": "posture", "op": "==", "value": "seated"}], ok=True)
    result = call("why did the desk lamp turn on?", parts)
    assert result["kind"] == "why"
    assert "turn the desk lamp on when I sit down" in result["text"]
    assert "posture" in result["text"]


def test_why_with_no_history_says_so(parts):
    assert "no record" in call("why did the desk lamp turn on?", parts)["text"].lower()


def test_declines_a_teaching_instruction(parts):
    assert call("turn the lamp on whenever it gets dark", parts) is None


def test_declines_every_conditional_word(parts):
    for phrase in ("turn the desk lamp on when it gets dark",
                   "turn the desk lamp on if nobody is here",
                   "turn the desk lamp off after five minutes",
                   "turn the desk lamp on every time I sit down"):
        assert call(phrase, parts) is None, phrase


def test_declines_something_it_does_not_understand(parts):
    assert call("book me a flight to Chennai", parts) is None


def test_control_of_an_unknown_device_declines_rather_than_guessing(parts):
    assert call("turn the garage door on", parts) is None


def test_empty_input_declines(parts):
    assert call("   ", parts) is None


def test_an_empty_descriptor_says_so_instead_of_raising(parts):
    """Before the detection pipeline has produced a descriptor, the dict is
    empty. Asking about state must not 500."""
    registry, router, log_path = parts
    for question in ("is anyone home?", "what's the temperature?", "what's the humidity?"):
        result = local_lane.answer(question, registry=registry, descriptor={},
                                   router=router, log_path=log_path, store=FakeStore())
        assert result is not None, question
        assert "don't have" in result["text"], question


def test_an_empty_descriptor_still_allows_direct_control(parts):
    registry, router, log_path = parts
    result = local_lane.answer("turn the desk lamp on", registry=registry, descriptor={},
                               router=router, log_path=log_path, store=FakeStore())
    assert result["kind"] == "control"
    assert router.calls == [("lamp_desk", "on")]


def test_a_refused_actuation_is_reported(tmp_path):
    registry = DeviceRegistry(str(tmp_path / "devices.json"), relay_channels=(1, 2, 3))
    registry.add(LAMP)

    class Refusing(FakeRouter):
        def set(self, device_id, action):
            return False, "device 'lamp_desk' is unreachable"

    result = local_lane.answer(
        "turn the desk lamp on", registry=registry, descriptor=DESCRIPTOR,
        router=Refusing(), log_path=str(tmp_path / "a.jsonl"), store=FakeStore())
    assert result["kind"] == "control"
    assert "unreachable" in result["text"]
