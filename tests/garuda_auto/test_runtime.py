import json

import pytest

from basic_pipelines import drishti_api
from basic_pipelines.garuda_auto import actuation_log
from basic_pipelines.garuda_auto.runtime import DEFAULT_ZONES, DrishtiRuntime

pytestmark = pytest.mark.integration

LAMP = {"id": "lamp", "name": "Lamp", "type": "light", "room": "study",
        "transport": {"kind": "relay", "channel": 1}}
FAN = {"id": "fan", "name": "Fan", "type": "fan", "room": "study",
       "transport": {"kind": "relay", "channel": 2}}

EMPTY_ROOM_LAMP_OFF = {
    "id": "r_1",
    "source_utterance": "turn the lamp off when the room is empty",
    "when": {"all": [{"field": "occupancy", "op": "==", "value": "empty"}]},
    "then": [{"device": "lamp", "action": "off"}],
    "enabled": True,
    "cooldown_s": 0,
}

PERSON = {"label": "person", "bbox": (0.05, 0.1, 0.25, 0.9), "posture": "seated"}
DOORWAY = {"label": "person", "bbox": (0.75, 0.1, 0.95, 0.9), "posture": "standing"}


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def rt(tmp_path):
    ctx = drishti_api.build_context(
        data_dir=str(tmp_path), relay_channels=(1, 2, 3),
        channel_to_pin={1: 17, 2: 27, 3: 22})
    ctx.registry.add(dict(LAMP))
    ctx.registry.add(dict(FAN))
    ctx.rebuild()
    clock = Clock()
    runtime = DrishtiRuntime(ctx, clock=clock)
    runtime.rebind()
    try:
        yield runtime, ctx, clock
    finally:
        runtime.stop()
        ctx.relay_bank.close()


# ── perception ────────────────────────────────────────────────────────────────

def test_a_descriptor_exists_before_the_first_frame(rt):
    _, ctx, _ = rt
    # Without this the local lane answers "I have no reading yet" to every
    # question asked in the first seconds after a restart.
    assert ctx.descriptor["occupancy"] == "empty"
    assert ctx.descriptor["person_count"] == 0


def test_observe_publishes_the_descriptor_on_the_context(rt):
    runtime, ctx, _ = rt
    runtime.observe([PERSON], luma=120)
    assert ctx.descriptor["occupancy"] == "occupied"
    assert ctx.descriptor["person_count"] == 1
    assert ctx.descriptor["ambient_luma"] == 120


def test_the_zone_comes_from_where_the_person_is(rt):
    runtime, ctx, _ = rt
    runtime.observe([PERSON], luma=10)
    assert ctx.descriptor["zone"] == "desk"
    runtime.observe([DOORWAY], luma=10)
    assert ctx.descriptor["zone"] == "door"


def test_the_zones_tile_the_frame_without_overlapping(rt):
    edges = sorted((x0, x1) for x0, _, x1, _ in DEFAULT_ZONES.values())
    assert edges[0][0] == 0.0
    assert edges[-1][1] == 1.0
    for (_, end), (start, _) in zip(edges, edges[1:]):
        assert end == start


def test_every_device_state_reaches_the_descriptor(rt):
    runtime, ctx, _ = rt
    runtime.observe([], luma=0)
    assert ctx.descriptor["lamp_state"] == "off"
    assert ctx.descriptor["fan_state"] == "off"


def test_occupancy_duration_grows_while_the_state_holds(rt):
    runtime, ctx, clock = rt
    runtime.observe([PERSON], luma=0)
    clock.t += 300
    runtime.observe([PERSON], luma=0)
    assert ctx.descriptor["occupancy_duration_s"] == 300


def test_occupancy_duration_resets_when_the_state_changes(rt):
    runtime, ctx, clock = rt
    runtime.observe([PERSON], luma=0)
    clock.t += 300
    runtime.observe([], luma=0)
    assert ctx.descriptor["occupancy_duration_s"] == 0


# ── action ────────────────────────────────────────────────────────────────────

def test_a_matching_rule_fires_and_drives_the_device(rt):
    runtime, ctx, _ = rt
    ctx.store.add(dict(EMPTY_ROOM_LAMP_OFF))
    runtime.observe([PERSON], luma=0)
    ctx.device_router.set("lamp", "on")

    runtime.observe([], luma=0)
    performed = runtime.tick()

    assert performed == [{"device": "lamp", "action": "off", "rule_id": "r_1",
                          "ok": True, "reason": ""}]
    assert ctx.device_router.state("lamp") == "off"


def test_nothing_happens_when_no_rule_matches(rt):
    runtime, ctx, _ = rt
    ctx.store.add(dict(EMPTY_ROOM_LAMP_OFF))
    runtime.observe([PERSON], luma=0)
    assert runtime.tick() == []


def test_a_disabled_rule_does_not_fire(rt):
    runtime, ctx, _ = rt
    ctx.store.add({**EMPTY_ROOM_LAMP_OFF, "enabled": False})
    runtime.observe([], luma=0)
    assert runtime.tick() == []


def test_the_fire_is_written_to_the_activity_log(rt):
    runtime, ctx, _ = rt
    ctx.store.add(dict(EMPTY_ROOM_LAMP_OFF))
    runtime.observe([], luma=0)
    runtime.tick()

    entries = actuation_log.recent(ctx.log_path)
    assert len(entries) == 1
    assert entries[0]["device"] == "lamp"
    assert entries[0]["action"] == "off"
    assert entries[0]["ok"] is True
    # The card explains itself with the conditions that actually matched.
    assert entries[0]["matched"] == [
        {"field": "occupancy", "op": "==", "value": "empty"}]


def test_a_rule_naming_a_device_that_does_not_exist_is_refused(rt):
    _, ctx, _ = rt
    # The validator is what keeps the runtime from ever seeing this.
    ok, reason = ctx.store.add({**EMPTY_ROOM_LAMP_OFF,
                                "then": [{"device": "ghost", "action": "off"}]})
    assert ok is False
    assert "ghost" in reason


def test_a_failed_actuation_is_logged_with_its_reason(rt):
    runtime, ctx, _ = rt
    # An MQTT device whose broker is gone: registered and legal, but the
    # publish cannot land. This is what an unplugged smart plug looks like.
    # (mosquitto runs on this host, so the broker has to be removed
    # explicitly or the publish would succeed.)
    ctx.registry.add({"id": "heater", "name": "Heater", "type": "switch",
                      "room": "study",
                      "transport": {"kind": "mqtt", "topic_base": "drishti/heater"}})
    ctx.rebuild()
    ctx.mqtt_bank._client = None
    runtime.rebind()
    ctx.store.add({**EMPTY_ROOM_LAMP_OFF, "id": "r_2",
                   "source_utterance": "turn the heater on when the room is empty",
                   "then": [{"device": "heater", "action": "on"}]})
    runtime.observe([], luma=0)
    performed = runtime.tick()

    assert performed[0]["ok"] is False
    entry = actuation_log.recent(ctx.log_path)[0]
    assert entry["ok"] is False
    assert "unreachable" in entry["reason"]


def test_the_rule_counts_its_own_fires(rt):
    runtime, ctx, _ = rt
    ctx.store.add(dict(EMPTY_ROOM_LAMP_OFF))
    runtime.observe([], luma=0)
    runtime.tick()
    runtime.tick()

    rule = ctx.store.rules[0]
    assert rule["fired_count"] == 2
    assert rule["last_fired"] > 0


def test_the_fire_count_survives_a_restart(rt, tmp_path):
    runtime, ctx, _ = rt
    ctx.store.add(dict(EMPTY_ROOM_LAMP_OFF))
    runtime.observe([], luma=0)
    runtime.tick()

    on_disk = json.load(open(ctx.store.path))
    assert on_disk[0]["fired_count"] == 1


def test_a_cooldown_holds_the_rule_back(rt):
    runtime, ctx, clock = rt
    ctx.store.add({**EMPTY_ROOM_LAMP_OFF, "cooldown_s": 60})
    runtime.observe([], luma=0)

    assert len(runtime.tick()) == 1
    assert runtime.tick() == []
    clock.t += 61
    assert len(runtime.tick()) == 1


def test_what_a_rule_did_is_visible_to_the_next_evaluation(rt):
    runtime, ctx, _ = rt
    ctx.store.add(dict(EMPTY_ROOM_LAMP_OFF))
    ctx.device_router.set("lamp", "on")
    runtime.observe([], luma=0)
    runtime.tick()

    # Without feeding the action back into the scene, a rule reading
    # lamp_state would never see the change its sibling just made.
    assert runtime.observe([], luma=0)["lamp_state"] == "off"


# ── registry changes ──────────────────────────────────────────────────────────

def test_a_new_device_appears_in_the_descriptor_after_rebind(rt):
    runtime, ctx, _ = rt
    ctx.registry.add({"id": "heater", "name": "Heater", "type": "switch",
                      "room": "study", "transport": {"kind": "relay", "channel": 3}})
    ctx.rebuild()
    runtime.rebind()
    assert "heater_state" in runtime.observe([], luma=0)


def test_a_removed_device_leaves_the_descriptor(rt):
    runtime, ctx, _ = rt
    ctx.registry.delete("fan")
    ctx.rebuild()
    runtime.rebind()
    descriptor = runtime.observe([], luma=0)
    assert "fan_state" not in descriptor
    assert "lamp_state" in descriptor


# ── the thread ────────────────────────────────────────────────────────────────

def test_the_loop_survives_a_rule_base_that_throws(rt):
    runtime, ctx, _ = rt
    ctx.store.rules.append({"id": "bad", "when": {}, "then": [], "enabled": True})
    runtime.observe([], luma=0)
    # One malformed rule must not stop every other rule from ever firing.
    runtime.start(interval=0.01)
    assert runtime._thread.is_alive()
    runtime.stop()


def test_stop_is_safe_to_call_twice(rt):
    runtime, _, _ = rt
    runtime.start(interval=0.01)
    runtime.stop()
    runtime.stop()
