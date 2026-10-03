"""Narada doing what a button does: the site's own endpoint, run as the person."""
import inspect
import typing

from pydantic import BaseModel

import Garuda_web as gw
from basic_pipelines.garuda_auto import capabilities as caps
from basic_pipelines.garuda_auto.site_calls import SiteCaller, trim


def run(name, args=None, user="someone", role="user"):
    return gw.AGENT._run_tool(name, args or {}, user, role)


def test_every_route_backed_capability_fits_its_endpoint():
    site = SiteCaller(gw.fastapi_app)
    for c in caps.CAPABILITIES:
        if not c.call:
            continue
        method, path = c.call.split(" ", 1)
        route = site.route(method, path)
        assert route is not None, c.name
        accepted = set()
        hints = typing.get_type_hints(route.endpoint)
        for name in inspect.signature(route.endpoint).parameters:
            kind = hints.get(name)
            if inspect.isclass(kind) and issubclass(kind, BaseModel):
                accepted |= set(kind.model_fields)
            else:
                accepted.add(name)
        assert set(c.params) <= accepted, f"{c.name}: {set(c.params) - accepted} go nowhere"
        assert set(route.param_convertors) <= set(c.required), f"{c.name}: an id is not required"


def test_a_user_reads_what_a_user_may(app_client):
    out = run("list_suggestions")
    assert "suggestions" in out and "error" not in out and "_action" not in out


def test_an_admin_capability_is_refused_to_a_user(app_client):
    assert run("list_users") == {"error": "only an admin can do that"}
    assert "error" not in run("list_users", role="admin")


def test_logs_stay_behind_the_master_key(app_client):
    assert "master key" in run("read_logs", role="admin")["error"]


def test_a_change_goes_through_the_endpoints_own_checks(app_client):
    lamp = gw.DRISHTI_CTX.registry.get("lamp")
    before = lamp["name"]
    try:
        assert "unknown" in run("edit_device", {"device_id": "nope", "name": "x"}, role="admin")["error"]
        done = run("edit_device", {"device_id": "lamp", "name": "Desk lamp"}, role="admin")
        assert done["ok"] and done["_action"]
        assert gw.DRISHTI_CTX.registry.get("lamp")["name"] == "Desk lamp"
        # The request model is the check: a wrong kind of value is refused with its name.
        assert "watts" in run("edit_device", {"device_id": "lamp", "watts": "plenty"}, role="admin")["error"]
        # A field the capability does not declare never reaches the endpoint.
        run("edit_device", {"device_id": "lamp", "name": "Desk lamp", "type": "fan"}, role="admin")
        assert gw.DRISHTI_CTX.registry.get("lamp")["type"] == "light"
    finally:
        gw.DRISHTI_CTX.registry.update("lamp", {"name": before})


def test_only_its_creator_or_an_admin_removes_a_timer(app_client):
    device = next(d["id"] for d in gw.HOME.devices() if d["actuator"])
    made = run("schedule_action", {"device": device, "action": "off", "in_minutes": 30},
               user="asha")
    sid = made["schedule_id"]
    assert run("delete_schedule", {"schedule_id": sid}, user="ravi")["status"] == 403
    assert gw.HOME.schedules.get(sid) is not None
    assert run("delete_schedule", {"schedule_id": sid}, user="asha")["ok"]
    assert gw.HOME.schedules.get(sid) is None
    assert run("delete_schedule", {})["error"] == "schedule_id is needed"


def test_without_a_site_it_says_so():
    site, gw.AGENT.site = gw.AGENT.site, None
    try:
        assert run("list_suggestions") == {"error": "list_suggestions is not available here"}
    finally:
        gw.AGENT.site = site


def test_long_lists_are_cut_newest_end_for_logs():
    out = trim({"system_log": list(range(100)), "entries": list(range(100))})
    assert out["system_log"][0] == 60 and out["entries"][-1] == 39
