"""The planner lane: who is sent to it, what it is offered, what it leaves behind."""
import json

from basic_pipelines.garuda_auto import agent as agent_mod
from basic_pipelines.garuda_auto.artifacts import ArtifactStore

from test_agent import ScriptedChat, agent, call, completion, house  # noqa: F401

PAGE = "<!doctype html><html><head><title>t</title></head><body><svg></svg></body></html>"


def names(request):
    return [t["function"]["name"] for t in request["tools"]]


def test_without_a_planner_the_quick_model_has_no_way_to_one(house):
    ctx, home = house
    chat = ScriptedChat([completion("Hello.")])
    agent(ctx, home, chat).handle("hi", user="mani", role="admin")
    assert "hand_to_planner" not in names(chat.requests[0])


def test_the_quick_model_hands_over_and_the_planner_starts_from_the_persons_words(house):
    ctx, home = house
    quick = ScriptedChat([completion(tool_calls=[call("hand_to_planner", {"why": "a chart"})])])
    planner = ScriptedChat([
        completion(tool_calls=[call("show_artifact", {"title": "Energy", "html": PAGE})]),
        completion("Here is the chart.")])
    events = []
    a = agent(ctx, home, quick, planner=planner, artifacts=ArtifactStore())
    result = a.handle("chart the energy use", user="mani", role="admin", progress=events.append)
    assert "hand_to_planner" in names(quick.requests[0])
    offered = names(planner.requests[0])
    assert "hand_to_planner" not in offered and {"show_artifact", "create_shortcut",
                                                 "delete_device"} <= set(offered)
    # (The scripted chat's requests share one growing list of messages.)
    assert {"role": "user", "content": "chart the energy use"} in planner.requests[0]["messages"]
    assert "working as the planner" in planner.requests[0]["messages"][0]["content"]
    assert result["planner"] and result["reply"] == "Here is the chart."
    assert result["artifacts"][0]["title"] == "Energy"
    assert a.artifacts.html(result["artifacts"][0]["id"]) == PAGE
    assert [e.get("type") for e in events] == ["lane", "step", "step"]
    assert events[0]["lane"] == "planner" and events[2]["ok"] is True
    assert result["steps"] == [{"tool": "show_artifact", "ok": True, "waiting": False}]
    assert a.stats["planner"] == 1 and a.live_for("mani") is None


def test_the_page_is_not_carried_into_the_next_conversation(house):
    ctx, home = house
    planner = ScriptedChat([
        completion(tool_calls=[call("show_artifact", {"title": "Energy", "html": PAGE})]),
        completion("Done."), completion("Yes.")])
    a = agent(ctx, home, planner, planner=planner, artifacts=ArtifactStore())
    a._wants_planner = lambda route: True
    a.handle("chart it", user="mani", role="admin")
    a.handle("and again?", user="mani", role="admin")
    remembered = json.dumps(planner.requests[2]["messages"])
    assert "<svg>" not in remembered and "the page you wrote" in remembered


def test_the_routing_models_word_sends_a_request_straight_to_the_planner(house):
    ctx, home = house
    planner = ScriptedChat([completion("Planned.")])
    a = agent(ctx, home, ScriptedChat([]), planner=planner)
    sure = {"value": "build", "confidence": 0.97, "backend": "router"}
    assert a._wants_planner({"intent": sure})
    assert not a._wants_planner({"intent": {**sure, "confidence": 0.5}})
    assert not a._wants_planner({"intent": {**sure, "backend": "local"}})
    assert not a._wants_planner({"intent": {**sure, "value": "device_control"}})
    a.decision.route = lambda *args: {"intent": sure}
    assert a.handle("build me a morning routine", user="mani", role="admin")["planner"]


def test_an_artifact_is_refused_in_a_spoken_turn_and_in_a_poisoned_one(house):
    ctx, home = house
    a = agent(ctx, home, ScriptedChat([]), artifacts=ArtifactStore())
    a._turn.voice, a._turn.artifacts = True, []
    assert "spoken" in a._run_tool("show_artifact", {"title": "t", "html": PAGE}, "mani", "admin")["error"]
    assert "show_artifact" in agent_mod.CHANGING_TOOLS      # refused once a turn has read injected text
    a._turn.voice = False
    assert "html" in a._run_tool("show_artifact", {"title": "t", "html": ""}, "mani", "admin")["error"]
    assert "large" in a._run_tool("show_artifact", {"title": "t", "html": "x" * 300_000},
                                  "mani", "admin")["error"]


def test_the_planner_is_told_the_facts_a_shortcut_can_test(house):
    ctx, home = house
    planner = ScriptedChat([completion("ok")])
    a = agent(ctx, home, planner, planner=planner, facts_fn=lambda: {"occupancy": "empty"})
    a._wants_planner = lambda route: True
    a.handle("make a routine", user="mani", role="admin")
    assert '{"occupancy": "empty"}' in planner.requests[0]["messages"][0]["content"]


def test_a_page_that_loads_something_from_outside_is_refused(house):
    store = ArtifactStore()
    for bad in ('<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>',
                "<link rel=stylesheet href=//fonts.example/x.css>",
                "<style>@import url(https://x.example/a.css);</style>",
                '<style>body{background:url("http://x.example/a.png")}</style>'):
        entry, reason = store.add("t", "<html><body>" + bad + "</body></html>", by="a")
        assert entry is None and "outside" in reason, bad
    ok, _ = store.add("t", '<svg xmlns="http://www.w3.org/2000/svg"></svg><a href="https://x.example">x</a>',
                      by="a")
    assert ok is not None


def test_saved_shortcuts_are_in_the_state_the_model_reads(house):
    ctx, home = house
    chat = ScriptedChat([completion("ok")])
    a = agent(ctx, home, chat)
    a.shortcuts_fn = lambda: [{"id": "ab12", "name": "Good night", "when": "At 22:30 every day"}]
    a.handle("hello", user="mani", role="admin")
    assert "Good night" in chat.requests[0]["messages"][0]["content"]
    assert "run_shortcut" in names(chat.requests[0])
