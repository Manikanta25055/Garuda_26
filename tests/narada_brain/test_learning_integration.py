"""Phase 4 end to end without a model: the log, the agent's offer, and the page's answer."""
import time

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from basic_pipelines import drishti_api
from basic_pipelines.garuda_auto import actuation_log
from basic_pipelines.garuda_auto.agent import HomeAgent
from basic_pipelines.garuda_auto.decision import DecisionEngine, LocalBackend
from basic_pipelines.garuda_auto.digest import Digest
from basic_pipelines.garuda_auto.home import HomeServices
from basic_pipelines.garuda_auto.llm import NimChat
from basic_pipelines.garuda_auto.runtime import DrishtiRuntime
from basic_pipelines.home_api import build_home_router
from basic_pipelines.narada_brain import Brain

pytestmark = pytest.mark.integration

LAMP = {"id": "lamp", "name": "Lamp", "type": "light", "room": "study",
        "transport": {"kind": "relay", "channel": 1}}


class Resp:
    status_code = 200

    def json(self):
        return {"choices": [{"message": {"role": "assistant", "content": "Hello."},
                             "finish_reason": "stop"}], "usage": {"total_tokens": 5}}

    def raise_for_status(self):
        pass


def chat():
    return NimChat("key", ["m1"], post=lambda *a, **k: Resp())


@pytest.fixture
def house(tmp_path):
    ctx = drishti_api.build_context(data_dir=str(tmp_path), relay_channels=(1, 2),
                                    channel_to_pin={1: 17, 2: 27})
    ctx.registry.add(dict(LAMP))
    ctx.rebuild()
    home = HomeServices(ctx, DrishtiRuntime(ctx), str(tmp_path))
    today = time.localtime()
    for back in range(1, 7):                    # switched on by hand at about 19:05, six days running
        stamp = time.mktime((today.tm_year, today.tm_mon, today.tm_mday - back, 19, 5 + back % 3, 0, 0, 0, -1))
        actuation_log.record(ctx.log_path, device="lamp", action="on", rule_id=None, matched=[],
                             ok=True, clock=lambda s=stamp: s, source="manual", actor="mani")
    brain = Brain(str(tmp_path), background=False)
    decision = DecisionEngine(LocalBackend(lambda: ctx.registry.devices, lambda: home.scenes.scenes))
    agent = HomeAgent(ctx, home, chat(), decision, brain=brain)
    yield ctx, home, agent, brain
    ctx.relay_bank.close()


def test_the_house_noticed_the_routine(house):
    ctx, home, agent, brain = house
    assert [(s["device"], s["action"], s["time"]) for s in home.suggestions()] == [("lamp", "on", "19:05")]


def test_an_admin_is_offered_it_once_and_narada_knows_it(house):
    ctx, home, agent, brain = house
    first = agent.handle("hello", user="mani", role="admin")
    assert first["offer"]["device"] == "lamp" and first["offer"]["time"] == "19:05"
    assert "offer" not in agent.handle("hello again", user="mani", role="admin")
    assert "usually turned on by hand around 19:05" in brain.system_prompt("mani", "admin")


@pytest.mark.parametrize("kwargs", [{"role": "user"}, {"role": "admin", "voice": True},
                                    {"role": "admin", "scope": "security"}])
def test_no_offer_to_a_user_in_speech_or_on_the_security_product(house, kwargs):
    ctx, home, agent, brain = house
    assert "offer" not in agent.handle("hello", user="mani", **kwargs)


def _client(ctx, home, agent, brain):
    def session(request: Request):
        role = request.headers.get("X-Role")
        if not role:
            raise HTTPException(401, "Not authenticated")
        return {"username": "mani", "role": role}

    def admin(request: Request):
        s = session(request)
        if s["role"] != "admin":
            raise HTTPException(403, "Admin access required")
        return s
    app = FastAPI()
    app.include_router(build_home_router(
        ctx, home, agent, Digest(home), session_dep=session, admin_dep=admin,
        suggestion_decided=lambda s, accepted, by: brain.routine_decided(
            s, ctx.registry.get(s["device"])["name"], accepted, by=by)))
    return TestClient(app)


def test_yes_on_the_page_makes_a_schedule_and_is_remembered(house):
    ctx, home, agent, brain = house
    offer = agent.handle("hello", user="mani", role="admin")["offer"]
    r = _client(ctx, home, agent, brain).post(f"/api/home/suggestions/{offer['id']}/accept",
                                               headers={"X-Role": "admin"})
    assert r.status_code == 200 and r.json()["schedule"]["time"] == "19:05"
    facts = [f["text"] for f in brain.memory.facts()]
    assert facts == ["The household chose to have the Lamp turned on automatically at 19:05 every day."] \
        or facts == ["The household chose to have the Lamp turned on automatically at 19:05 on weekdays."]
    assert brain.memory.facts()[0]["by"] == "mani" and home.suggestions() == []
    assert "offer" not in agent.handle("hello", user="mani", role="admin")


def test_no_on_the_page_is_remembered_and_not_asked_again(house):
    ctx, home, agent, brain = house
    offer = agent.handle("hello", user="mani", role="admin")["offer"]
    r = _client(ctx, home, agent, brain).post(f"/api/home/suggestions/{offer['id']}/dismiss",
                                               headers={"X-Role": "user"})
    assert r.status_code == 200
    assert [f["text"] for f in brain.memory.facts()] == [
        "The household prefers to turn the Lamp on by hand around 19:05, not automatically."]
    assert home.suggestions() == [] and home.schedules.entries == []


def test_a_user_cannot_accept(house):
    ctx, home, agent, brain = house
    offer = agent.handle("hello", user="mani", role="admin")["offer"]
    r = _client(ctx, home, agent, brain).post(f"/api/home/suggestions/{offer['id']}/accept",
                                               headers={"X-Role": "user"})
    assert r.status_code == 403 and brain.memory.by_key("choice:lamp:on") is None


def test_two_houses_in_one_process_do_not_share_dismissed_routines(tmp_path):
    """The defaults held a list that every HomeServices shared (found by these tests)."""
    made = []
    for name in ("a", "b"):
        d = tmp_path / name
        d.mkdir()
        ctx = drishti_api.build_context(data_dir=str(d), relay_channels=(1,), channel_to_pin={1: 17})
        made.append((ctx, HomeServices(ctx, DrishtiRuntime(ctx), str(d))))
    made[0][1].dismiss_suggestion("abc")
    assert made[1][1].settings["dismissed_suggestions"] == []
    for ctx, _ in made:
        ctx.relay_bank.close()


# ── phase 5: a remark nobody asked for ────────────────────────────────────────

FAN = {"id": "fan", "name": "Fan", "type": "fan", "room": "bedroom",
       "transport": {"kind": "relay", "channel": 2}}


@pytest.fixture
def long_fan(tmp_path):
    """A house whose fan usually runs three hours and has now been on for nine."""
    ctx = drishti_api.build_context(data_dir=str(tmp_path), relay_channels=(1, 2),
                                    channel_to_pin={1: 17, 2: 27})
    ctx.registry.add(dict(FAN))
    ctx.rebuild()
    home = HomeServices(ctx, DrishtiRuntime(ctx), str(tmp_path))
    now = time.time()

    def log(action, ts):
        actuation_log.record(ctx.log_path, device="fan", action=action, rule_id=None, matched=[],
                             ok=True, clock=lambda: ts, source="manual", actor="mani")
    for day in range(2, 7):                       # at a different hour each day: not a routine
        log("on", now - day * 86400 + day * 7200)
        log("off", now - day * 86400 + day * 7200 + 3 * 3600)
    log("on", now - 9 * 3600)
    home.set("fan", "on", source="manual", actor="mani")
    brain = Brain(str(tmp_path), background=False)
    decision = DecisionEngine(LocalBackend(lambda: ctx.registry.devices, lambda: home.scenes.scenes))
    yield HomeAgent(ctx, home, chat(), decision, brain=brain), brain
    ctx.relay_bank.close()


def test_a_typed_reply_carries_one_observation_with_its_rule(long_fan):
    agent, brain = long_fan
    out = agent.handle("hello", user="mani", role="user")
    seen = out["observation"]
    assert seen["rule"] == "running-long" and seen["key"] == "running-long:fan"
    assert seen["title"] == "Running longer than usual"
    assert seen["text"].startswith("The Fan has been on for 9 hours. It usually runs about 3 hours")
    assert out["reply"] == "Hello."                                  # the model's words are untouched
    last = brain.history("mani")[-1]["content"]                   # and Narada remembers showing it
    assert last.startswith("Hello.") and "The Fan has been on for 9 hours" in last
    assert "observation" not in agent.handle("hello again", user="mani")


def test_no_observation_in_speech(long_fan):
    agent, brain = long_fan
    assert "observation" not in agent.handle("hello", user="mani", voice=True)


def test_muting_it_stops_it(long_fan):
    agent, brain = long_fan
    brain.mute_notice("running-long:fan", {"fan": "Fan"}, by="mani")
    assert "observation" not in agent.handle("hello", user="mani")
