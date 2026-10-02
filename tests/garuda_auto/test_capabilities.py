"""The table of what Narada may do: complete, consistent, and matched by the agent."""
import json
from pathlib import Path

from basic_pipelines.garuda_auto import capabilities as caps
from basic_pipelines.garuda_auto.agent import CHANGING_TOOLS, SECURITY_TOOLS, HomeAgent

ROUTES = Path(__file__).resolve().parents[1] / "contract" / "routes.json"


def _site_routes():
    return {f"{r['method']} {r['path']}" for r in json.loads(ROUTES.read_text())}


def test_every_route_is_accounted_for_exactly_once():
    groups = [caps.covered_routes(), set(caps.PLANNED), set(caps.NEVER), set(caps.PLUMBING)]
    listed = set().union(*groups)
    site = _site_routes()
    assert site - listed == set(), "a route nobody decided about: add it to capabilities.py"
    assert listed - site == set(), "capabilities.py names a route the site does not have"
    assert sum(len(g) for g in groups) == len(listed), "a route is listed twice"


def test_capabilities_are_well_formed():
    assert len(caps.BY_NAME) == len(caps.CAPABILITIES)
    for c in caps.CAPABILITIES:
        assert c.role in ("user", "admin") and c.tier in caps.TIERS and c.lane in caps.LANES
        assert set(c.required) <= set(c.params) | set(c.typed)
        assert not set(c.typed) & set(c.params), "a typed field must not be the model's to fill"
        assert c.tier == "confirm" or not c.typed
        assert c.routes or c.name in caps.INTERNAL, f"{c.name} stands in for no route"
    for name, role, tier in caps.PLANNED.values():
        assert role in ("user", "admin") and tier in caps.TIERS
        assert name not in caps.BY_NAME


def test_every_capability_can_be_carried_out_one_way():
    handlers = {n[len("_tool_"):] for n in dir(HomeAgent) if n.startswith("_tool_")}
    assert caps.INTERNAL <= handlers
    by_route = {c.name for c in caps.CAPABILITIES if c.call}
    assert handlers | by_route == set(caps.BY_NAME)
    assert handlers & by_route == set(), "a capability has both a handler and a route"
    for c in caps.CAPABILITIES:
        if c.call:
            assert c.routes == (c.call,) and c.lane == "planner"


def test_a_route_backed_capability_asks_for_the_role_its_route_asks_for():
    guards = {f"{r['method']} {r['path']}": r["guard"] for r in json.loads(ROUTES.read_text())}
    for c in caps.CAPABILITIES:
        if c.call:
            wanted = "user" if guards[c.call] == "require_session" else "admin"
            assert c.role == wanted, f"{c.name}: the route wants {guards[c.call]}"


def test_the_quick_model_is_offered_what_it_always_was():
    offered = [t["function"]["name"] for t in HomeAgent._tools(None)]
    assert offered == ["get_security_state", "get_house_state", "set_device", "all_off",
                       "run_scene", "create_scene", "schedule_action", "list_schedules",
                       "create_automation", "list_automations", "set_security_mode",
                       "recent_activity", "energy_usage", "remember_fact", "forget_fact"]
    # With a planner to hand over to, the quick model gets one more tool: the way to it.
    assert [t["function"]["name"] for t in caps.tools()][-1] == "hand_to_planner"
    assert "hand_to_planner" not in [t["function"]["name"] for t in caps.tools("planner")]
    assert [n for n in offered if n in SECURITY_TOOLS] == [
        "get_security_state", "set_security_mode", "remember_fact", "forget_fact"]
    assert {"set_device", "all_off", "run_scene", "create_scene", "schedule_action",
            "create_automation", "set_security_mode", "forget_fact"} <= set(CHANGING_TOOLS)
    assert "remember_fact" not in CHANGING_TOOLS
    # The planner is offered everything, the quick model's tools included.
    assert len(caps.tools("planner")) == len(caps.CAPABILITIES) - 1


def test_nothing_about_sign_in_or_keys_is_planned():
    for route in caps.PLANNED:
        assert not any(word in route for word in ("login", "otp", "master_key", "/ai", "forgot"))
