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
        assert set(c.required) <= set(c.params)
        assert c.routes, f"{c.name} stands in for no route"
    for name, role, tier in caps.PLANNED.values():
        assert role in ("user", "admin") and tier in caps.TIERS
        assert name not in caps.BY_NAME


def test_every_capability_has_a_handler_and_every_handler_a_capability():
    handlers = {n[len("_tool_"):] for n in dir(HomeAgent) if n.startswith("_tool_")}
    assert handlers == set(caps.BY_NAME)


def test_the_agent_offers_the_table():
    offered = [t["function"]["name"] for t in HomeAgent._tools(None)]
    assert offered == [c.name for c in caps.CAPABILITIES if c.lane == "fast"]
    assert SECURITY_TOOLS == ("get_security_state", "set_security_mode", "remember_fact",
                              "forget_fact")
    assert set(CHANGING_TOOLS) == {"set_device", "all_off", "run_scene", "create_scene",
                                   "schedule_action", "create_automation", "set_security_mode",
                                   "forget_fact"}


def test_nothing_about_sign_in_or_keys_is_planned():
    for route in caps.PLANNED:
        assert not any(word in route for word in ("login", "otp", "master_key", "/ai", "forgot"))
