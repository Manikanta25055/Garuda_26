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


def test_the_planner_cannot_run_a_tool_it_was_not_offered(house):
    ctx, home = house
    planner = ScriptedChat([
        completion(tool_calls=[call("create_automation", {"instruction": "when empty turn off the lamp"})]),
        completion("I will use a shortcut instead.")])
    a = agent(ctx, home, planner, planner=planner)
    a._wants_planner = lambda route: True
    result = a.handle("when the room is empty turn off the lamp", user="mani", role="admin")
    assert result["steps"] == [{"tool": "create_automation", "ok": False, "waiting": False}]
    assert result["proposal"] is None and len(ctx.pending.all()) == 0
    assert "create_shortcut" in json.dumps(planner.requests[1]["messages"][-1])


def test_reaching_for_a_tool_it_does_not_have_is_a_handover_too(house):
    ctx, home = house
    quick = ScriptedChat([completion(tool_calls=[call("set_device_name", {"device": "lamp", "name": "Reading lamp"})])])
    planner = ScriptedChat([completion("A card is waiting.")])
    a = agent(ctx, home, quick, planner=planner)
    result = a.handle("rename the lamp to reading lamp", user="mani", role="admin")
    assert result["planner"] and result["reply"] == "A card is waiting."
    assert "call hand_to_planner" in quick.requests[0]["messages"][0]["content"]
    # Without a planner the made-up tool is simply an unknown tool, as before.
    alone = ScriptedChat([completion(tool_calls=[call("set_device_name", {})]), completion("I can't do that.")])
    b = agent(ctx, home, alone)
    assert not b.handle("rename the lamp", user="mani", role="admin")["planner"]
    assert "call hand_to_planner" not in alone.requests[0]["messages"][0]["content"]


def test_asking_for_the_cameras_view_shows_a_picture_the_model_never_sees(house):
    ctx, home = house
    chat = ScriptedChat([completion(tool_calls=[call("take_snapshot", {})]), completion("Here it is.")])
    a = agent(ctx, home, chat, security_fn=lambda: {"camera_live": True})
    result = a.handle("show me the camera", user="mani", role="user")
    assert result["images"] and result["images"][0]["kind"] == "snapshot"
    assert "cannot see it" in json.dumps(chat.requests[1]["messages"][-1])
    down = agent(ctx, home, ScriptedChat([]), security_fn=lambda: {"camera_live": False})
    down._turn.voice = False
    assert "not delivering" in down._run_tool("take_snapshot", {}, "mani", "user")["error"]


class Dead(ScriptedChat):
    """A planner that never answers."""

    def __init__(self):
        super().__init__([])

    def chat(self, *a, **kw):
        from basic_pipelines.garuda_auto.llm import NimUnavailable
        raise NimUnavailable("the model service is unavailable (m1: ReadTimeout)")


def test_when_the_planner_does_not_answer_the_quick_model_still_does(house):
    ctx, home = house
    quick = ScriptedChat([completion("I can switch things, but building that needs the planner.")])
    a = agent(ctx, home, quick, planner=Dead())
    a._wants_planner = lambda route: True
    result = a.handle("make me a bedtime routine", user="mani", role="admin")
    assert result["lane"] == "agent" and "planner model did not answer" in result["reply"]
    # No way back to the planner in that turn, and it is whole again for the next.
    assert "hand_to_planner" not in names(quick.requests[0])
    assert a._turn.no_planner and a.live_for("mani") is None


def test_a_planner_that_stops_part_way_says_what_was_done(house):
    ctx, home = house

    class Half(ScriptedChat):
        def chat(self, messages, **kw):
            from basic_pipelines.garuda_auto.llm import NimUnavailable
            if len(self.requests) >= 1:
                raise NimUnavailable("gone")
            return super().chat(messages, **kw)
    planner = Half([completion(tool_calls=[call("set_device", {"device": "lamp", "action": "on"})])])
    a = agent(ctx, home, ScriptedChat([]), planner=planner)
    a._wants_planner = lambda route: True
    result = a.handle("lamp on then build a routine", user="mani", role="admin")
    assert "part-way" in result["reply"] and result["actions"] == ["Lamp on"]
    assert [d["id"] for d in home.on_devices()] == ["lamp"]


def test_the_planner_is_not_offered_what_this_person_may_not_do(house):
    ctx, home = house
    planner = ScriptedChat([completion("ok"), completion("ok")])
    a = agent(ctx, home, planner, planner=planner)
    a._wants_planner = lambda route: True
    a.handle("build something", user="asha", role="user")
    a.handle("build something", user="mani", role="admin")
    as_user, as_admin = set(names(planner.requests[0])), set(names(planner.requests[1]))
    assert {"create_shortcut", "set_device", "show_artifact"} <= as_user
    assert not {"delete_device", "add_user", "create_backup", "list_users"} & as_user
    assert {"delete_device", "add_user", "create_backup", "list_users"} <= as_admin


def test_a_planner_job_has_a_time_budget(house, monkeypatch):
    ctx, home = house
    from basic_pipelines.garuda_auto import agent as mod
    monkeypatch.setattr(mod, "PLANNER_BUDGET_S", 0)
    planner = ScriptedChat([completion("never asked")])
    a = agent(ctx, home, planner, planner=planner)
    a._wants_planner = lambda route: True
    result = a.handle("build something", user="mani", role="admin")
    assert planner.requests == [] and "too long" in result["reply"]


def test_nothing_narada_says_to_the_model_reads_as_an_injected_instruction():
    """A tool result is scanned for instructions hiding in data (guards.injected).
    The agent's own messages go through the same scan: one of them said "Use the
    tools you have", was taken for an attack, and the rest of that turn refused
    to change anything."""
    import ast
    from pathlib import Path
    from basic_pipelines.narada_brain import guards
    root = Path(__file__).resolve().parents[2] / "basic_pipelines"
    hits = []
    for name in ("garuda_auto/agent.py", "garuda_auto/site_calls.py", "garuda_auto/shortcuts.py",
                 "garuda_auto/artifacts.py", "garuda_auto/confirmations.py",
                 "garuda_auto/capabilities.py", "garuda_auto/home.py", "garuda_auto/schedules.py",
                 "garuda_auto/scenes.py", "garuda_routes/shortcuts.py", "garuda_routes/artifacts.py",
                 "garuda_routes/narada.py", "home_api.py"):
        tree = ast.parse((root / name).read_text())
        docs = {id(n.body[0].value) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and n.body and isinstance(n.body[0], ast.Expr)
                and isinstance(getattr(n.body[0], "value", None), ast.Constant)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
                text = node.value
            elif isinstance(node, ast.JoinedStr):
                text = " ".join(v.value if isinstance(v, ast.Constant) else "X" for v in node.values)
            else:
                continue
            if guards.injected(text):
                hits.append((name, node.lineno, guards.injected(text)))
    assert hits == []


def test_a_refused_tool_does_not_poison_the_rest_of_the_turn(house):
    ctx, home = house
    planner = ScriptedChat([
        completion(tool_calls=[call("make_coffee", {})]),
        completion(tool_calls=[call("set_device", {"device": "lamp", "action": "on"})]),
        completion("The lamp is on.")])
    a = agent(ctx, home, planner, planner=planner)
    a._wants_planner = lambda route: True
    result = a.handle("lamp on", user="mani", role="admin")
    assert not result["injection"] and result["actions"] == ["Lamp on"]
