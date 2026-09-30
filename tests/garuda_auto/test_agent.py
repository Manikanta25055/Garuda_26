"""The decision engine, the NIM client's model fallback, and the home agent."""
import json
import time

import pytest
import requests

from basic_pipelines import drishti_api
from basic_pipelines.garuda_auto.agent import HomeAgent
from basic_pipelines.garuda_auto.decision import DecisionEngine, JevBackend, LocalBackend
from basic_pipelines.garuda_auto.home import HomeServices
from basic_pipelines.garuda_auto.llm import NimChat, NimUnavailable, parse_models
from basic_pipelines.garuda_auto.runtime import DrishtiRuntime

LAMP = {"id": "lamp", "name": "Lamp", "type": "light", "room": "study",
        "transport": {"kind": "relay", "channel": 1}}
FAN = {"id": "fan", "name": "Fan", "type": "fan", "room": "study",
       "transport": {"kind": "relay", "channel": 2}}


class Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def completion(content="", tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg, "finish_reason": "stop"}],
            "usage": {"total_tokens": 10}}


def call(name, args, cid="c1"):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


class ScriptedChat(NimChat):
    """A NimChat whose HTTP layer replays scripted completions."""

    def __init__(self, script):
        self.requests = []
        replies = iter(script)

        def post(url, headers=None, json=None, timeout=None):
            self.requests.append(json)
            return Resp(200, next(replies))
        super().__init__("key", ["m1"], post=post)


@pytest.fixture
def house(tmp_path):
    ctx = drishti_api.build_context(data_dir=str(tmp_path), relay_channels=(1, 2, 3),
                                    channel_to_pin={1: 17, 2: 27, 3: 22})
    for d in (LAMP, FAN):
        ctx.registry.add(d)
    ctx.rebuild()
    runtime = DrishtiRuntime(ctx)
    home = HomeServices(ctx, runtime, str(tmp_path))
    try:
        yield ctx, home
    finally:
        ctx.relay_bank.close()


def engine(ctx, home, jev=None):
    return DecisionEngine(LocalBackend(lambda: ctx.registry.devices, lambda: home.scenes.scenes),
                          jev, threshold=0.85)


def agent(ctx, home, chat=None, jev=None, **kw):
    return HomeAgent(ctx, home, chat, engine(ctx, home, jev), **kw)


# ── decision engine ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, intent", [
    ("lamp off", "device_control"),
    ("turn the fan on", "device_control"),
    ("turn everything off", "all_off"),
    ("when the room is empty turn the lamp off", "automation_rule"),
    ("turn the lamp on in 20 minutes", "timer"),
    ("is the fan on?", "state_query"),
    ("why did the lamp turn on", "explain"),
    ("tell me a story about dragons", "other"),
    ("arm night mode and switch everything off", "other"),
    ("turn the lamp on and the fan off", "other"),
])
def test_local_routing(house, text, intent):
    ctx, home = house
    assert engine(ctx, home).route(text, ctx.registry.devices, [])["intent"].value == intent


def test_a_long_sentence_is_not_confident_enough_to_act_on(house):
    ctx, home = house
    route = engine(ctx, home).route(
        "so I was thinking maybe later we could have the lamp on for reading", ctx.registry.devices, [])
    assert route["intent"].confidence < 0.85


def test_jev_answers_when_configured(house):
    ctx, home = house
    sent = {}

    def post(url, headers=None, json=None, timeout=None):
        sent.update(url=url, body=json)
        return Resp(200, {"answers": {
            "intent": {"choice": "device_control", "probabilities": {"device_control": 0.97},
                       "confidence": 0.97},
            "device": {"choice": "fan", "confidence": 0.99},
            "action": {"choice": "off", "confidence": 0.99},
            "scene": {"choice": "none", "confidence": 0.9}}})
    jev = JevBackend("jk", "https://jev.test", post=post)
    route = engine(ctx, home, jev).route("kill the breeze", ctx.registry.devices, [])
    assert sent["url"] == "https://jev.test/v1/systemone"
    assert "kill the breeze" in sent["body"]["state"]
    assert route["device"].value == "fan" and route["intent"]["backend"] == "jev"


def test_jev_failure_falls_back_to_local(house):
    ctx, home = house

    def post(*a, **k):
        raise requests.ConnectionError("down")
    eng = engine(ctx, home, JevBackend("jk", post=post))
    assert eng.route("lamp off", ctx.registry.devices, [])["intent"]["backend"] == "local"
    assert eng.stats["jev_errors"] == 1


def test_jev_answer_outside_the_options_is_rejected(house):
    ctx, home = house
    post = lambda *a, **k: Resp(200, {"intent": {"choice": "launch_rockets", "confidence": 1}})
    eng = engine(ctx, home, JevBackend("jk", post=post))
    assert eng.route("lamp off", ctx.registry.devices, [])["intent"]["backend"] == "local"


# ── NIM model fallback ───────────────────────────────────────────────────────

def test_a_retired_model_falls_through_to_the_next():
    seen = []

    def post(url, headers=None, json=None, timeout=None):
        seen.append(json["model"])
        return Resp(410) if json["model"] == "old" else Resp(200, completion("hi"))
    chat = NimChat("k", ["old", "new"], post=post)
    assert chat.chat([{"role": "user", "content": "x"}])["content"] == "hi"
    assert chat.last_model == "new"
    chat.chat([{"role": "user", "content": "x"}])
    assert seen == ["old", "new", "new"]      # the dead model is not retried


def test_a_rejected_key_is_not_retried_on_other_models():
    seen = []

    def post(url, headers=None, json=None, timeout=None):
        seen.append(json["model"])
        return Resp(401)
    with pytest.raises(NimUnavailable, match="key was rejected"):
        NimChat("k", ["a", "b"], post=post).chat([])
    assert seen == ["a"]


def test_no_key_means_unavailable():
    with pytest.raises(NimUnavailable):
        NimChat("", ["a"]).chat([])


def test_parse_models_keeps_order_and_adds_defaults():
    models = parse_models("x/one", "x/two, x/one ,")
    assert models[:2] == ["x/one", "x/two"] and len(models) == len(set(models))


# ── agent ────────────────────────────────────────────────────────────────────

def test_even_a_confident_command_goes_through_the_model(house):
    # NIM is the only path to an action: no local fast lane.
    ctx, home = house
    chat = ScriptedChat([
        completion(tool_calls=[call("set_device", {"device": "lamp", "action": "on"})]),
        completion("The lamp is on."),
    ])
    out = agent(ctx, home, chat).handle("lamp on", user="mani")
    assert out["lane"] == "agent" and len(chat.requests) == 2
    assert [d["id"] for d in home.on_devices()] == ["lamp"]


def test_the_agent_uses_tools_and_reports(house):
    ctx, home = house
    chat = ScriptedChat([
        completion(tool_calls=[call("set_device", {"device": "fan", "action": "on"}),
                               call("get_house_state", {}, "c2")]),
        completion("The fan is on and nobody is in the room."),
    ])
    out = agent(ctx, home, chat).handle("could you get some air moving in here", user="mani")
    assert out["lane"] == "agent"
    assert out["reply"].startswith("The fan is on")
    assert [d["id"] for d in home.on_devices()] == ["fan"]
    tool_msgs = [m for m in chat.requests[1]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 2 and "devices" in tool_msgs[1]["content"]
    assert home.log_entries()[-1]["source"] == "assistant"


def test_a_user_cannot_create_recurring_schedules_through_the_agent(house):
    ctx, home = house
    chat = ScriptedChat([
        completion(tool_calls=[call("schedule_action", {"device": "lamp", "action": "on",
                                                        "time": "19:00", "repeat": True})]),
        completion("I can't set that up for you."),
    ])
    agent(ctx, home, chat).handle("lamp on at seven every evening please", user="u", role="user")
    assert home.schedules.entries == []
    assert "only an admin" in chat.requests[1]["messages"][-1]["content"]


def test_a_timer_through_the_agent(house):
    ctx, home = house
    chat = ScriptedChat([
        completion(tool_calls=[call("schedule_action", {"device": "fan", "action": "off",
                                                        "in_minutes": 30})]),
        completion("Fan off in 30 minutes."),
    ])
    out = agent(ctx, home, chat).handle("switch the fan off in half an hour", user="u")
    assert home.schedules.entries[0]["kind"] == "once"
    assert out["actions"]


def test_automations_become_proposals_not_rules(house, monkeypatch):
    ctx, home = house
    rule = {"source_utterance": "x", "when": {"all": [{"field": "occupancy", "op": "==",
                                                        "value": "empty"}]},
            "then": [{"device": "lamp", "action": "off"}], "cooldown_s": 60}
    monkeypatch.setattr(ctx.nim, "synthesize", lambda *a: (dict(rule), ""))
    chat = ScriptedChat([
        completion(tool_calls=[call("create_automation",
                                    {"instruction": "when the room is empty turn the lamp off"})]),
        completion("Drafted it; confirm on the Automations page."),
    ])
    # A conditional routes to the agent: the fast lane never compiles rules.
    out = agent(ctx, home, chat).handle("when the room is empty turn the lamp off", user="u")
    assert out["proposal"]["rendered"]["then"] == "lamp → off"
    assert ctx.store.rules == [] and len(ctx.pending.all()) == 1


def test_mode_changes_go_through_the_injected_switch(house):
    ctx, home = house
    changed = []
    chat = ScriptedChat([
        completion(tool_calls=[call("set_security_mode", {"mode": "night", "on": True})]),
        completion("Night mode is on."),
    ])
    agent(ctx, home, chat, set_mode_fn=lambda m, v, a: changed.append((m, v, a)) or "ok"
          ).handle("going to bed, arm night mode for me please", user="mani")
    assert changed == [("night", True, "mani")]


def test_without_nim_nothing_is_changed(house):
    ctx, home = house
    a = agent(ctx, home, None)
    for text in ("lamp on", "turn everything off", "is the lamp on?"):
        out = a.handle(text)
        assert out["lane"] == "unavailable" and "Nothing was changed" in out["reply"]
    assert home.on_devices() == []


def test_an_unreachable_model_changes_nothing(house):
    ctx, home = house

    def post(*a, **k):
        raise requests.ConnectionError("offline")
    out = agent(ctx, home, NimChat("k", ["m"], post=post)).handle("lamp on")
    assert out["lane"] == "unavailable" and "unavailable" in out["reply"]
    assert home.on_devices() == []


def test_history_is_kept_per_user(house):
    ctx, home = house
    chat = ScriptedChat([completion("one"), completion("two")])
    a = agent(ctx, home, chat)
    a.handle("remember that I like it cool", user="mani")
    a.handle("what did I say", user="mani")
    contents = [m["content"] for m in chat.requests[1]["messages"]]
    assert "remember that I like it cool" in contents and "one" in contents


def test_security_scope_never_touches_devices(house):
    ctx, home = house
    chat = ScriptedChat([
        completion(tool_calls=[call("set_device", {"device": "lamp", "action": "on"})]),
        completion("Home automation lives in the Drishti app."),
    ])
    out = agent(ctx, home, chat).handle("lamp on", user="mani", scope="security")
    assert out["lane"] == "agent"                       # no fast lane on Garuda
    assert home.on_devices() == []
    offered = {t["function"]["name"] for t in chat.requests[0]["tools"]}
    assert offered == {"get_security_state", "set_security_mode"}
    assert "not available in Garuda" in chat.requests[1]["messages"][-1]["content"]


def test_security_scope_without_nim_changes_nothing(house):
    ctx, home = house
    out = agent(ctx, home, None).handle("activate dnd", user="mani", scope="security")
    assert out["lane"] == "unavailable" and home.on_devices() == []


def test_voice_asks_the_model_for_a_short_spoken_reply(house):
    ctx, home = house
    chat = ScriptedChat([completion("All quiet.")])
    agent(ctx, home, chat).handle("anything happening", user="mani", voice=True)
    assert "speaking out loud" in chat.requests[0]["messages"][0]["content"]
