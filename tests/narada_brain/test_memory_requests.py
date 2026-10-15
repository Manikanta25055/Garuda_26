"""2026-10-16: requests, questions and timers were being kept as memories.

The facts in these tests are the ones the owner found in memory after an
evening of testing automations (screenshot), and the one rule kept twice.
"""
import json

import pytest

from basic_pipelines.narada_brain import Brain, gate
from basic_pipelines.narada_brain.memory import MemoryStore


class FakeChat:
    configured = True

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def chat(self, messages, **kw):
        self.calls.append(messages)
        return {"content": self.reply}


def acted(said, tool, answered="Done."):
    """A turn in which Narada called a tool for the person."""
    return [{"role": "user", "content": said},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": tool, "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "{}"},
            {"role": "assistant", "content": answered}]


def chatted(said, answered):
    return [{"role": "user", "content": said}, {"role": "assistant", "content": answered}]


# ── which sentences tell something ────────────────────────────────────────────

@pytest.mark.parametrize("said, acted_, told", [
    ("Dinner for four? My sister Priya is allergic to peanuts.", False,
     "My sister Priya is allergic to peanuts."),
    ("When my phone is detected, turn on this lamp.", True, ""),
    ("Can you turn off email alerts?", True, ""),
    ("How many alerts did I get between midnight and 6 am last week?", False, ""),
    ("Turn the lamp and fan off after one minute from now.", False, ""),
    ("Okay. I sleep at 10, so switch the lights off then.", True,
     "I sleep at 10, so switch the lights off then."),
    ("Remember that I hate loud alarms.", False, "Remember that I hate loud alarms."),
    ("I like the lamp warm.", False, "I like the lamp warm."),
])
def test_statements(said, acted_, told):
    assert gate.statements(said, acted=acted_) == told


@pytest.mark.parametrize("fact", [
    "Admin wants to know how many times they got alerts between 12:00 a.m. and 6:00 a.m. in the last one week.",
    "Admin wants the lamp and fan to turn off after one minute from now.",
    "Admin wants to toggle the lamp and switch after five seconds from now.",
    "Admin wants to check logs for unusual patterns or timing patterns from the past 48 hours.",
    "Admin wants the heater on in 10 minutes.",
])
def test_one_off_facts(fact):
    assert gate.one_off(fact)


@pytest.mark.parametrize("fact", [
    "Admin prefers email alerts off after 10 pm every night.",
    "Admin wants the lamp to turn on for only 10 minutes when they are home and then turn off.",
    "Admin's name is Manikanta.",
])
def test_lasting_facts(fact):
    assert not gate.one_off(fact)


# ── the distiller ─────────────────────────────────────────────────────────────

def test_requests_are_not_distilled_and_the_model_is_not_even_asked(tmp_path):
    # What the model wrote that evening, each traced to the owner's own words.
    chat = FakeChat(json.dumps([
        {"text": "Admin wants lamp and fan to turn on for 10 minutes when their device is "
                 "detected home and then turn off", "category": "routine"},
        {"text": "Admin wants the lamp and fan to turn off after one minute from now", "category": "routine"}]))
    brain = Brain(str(tmp_path), chat, background=False)
    brain.record("admin", acted("When my device is detected home, turn on the lamp and fan "
                                "for 10 minutes and then turn them off.", "create_automation"))
    brain.record("admin", acted("Turn the lamp and fan off after one minute from now.", "schedule_action"))
    brain.record("admin", acted("How many alerts did I get between 12 am and 6 am last week?",
                                "search_history"))
    assert brain.distill("admin") == []
    assert chat.calls == []                     # nothing was told, so nothing to read back
    assert brain.memory.facts() == []


def test_a_fact_told_beside_a_request_is_still_kept(tmp_path):
    chat = FakeChat(json.dumps([
        {"text": "Admin sleeps at 10 pm", "category": "routine"},
        {"text": "Admin wants the lights off at 10 pm tonight", "category": "routine"}]))
    brain = Brain(str(tmp_path), chat, background=False)
    brain.record("admin", acted("I sleep at 10 pm. Turn the lights off at 10 pm tonight.",
                                "schedule_action"))
    assert [f["text"] for f in brain.distill("admin")] == ["Admin sleeps at 10 pm."]
    sent = chat.calls[0][1]["content"]
    assert "admin: I sleep at 10 pm." in sent


def test_a_question_only_line_is_marked_for_the_model(tmp_path):
    chat = FakeChat("[]")
    brain = Brain(str(tmp_path), chat, background=False)
    brain.record("admin", chatted("What is a good bedtime?", "Around 10 pm for most people."))
    brain.record("admin", chatted("My daughter goes to bed at 9.", "Noted."))
    brain.distill("admin")
    sent = chat.calls[0][1]["content"]
    assert "admin (asking): What is a good bedtime?" in sent
    assert "admin: My daughter goes to bed at 9." in sent


# ── saves made during the chat ────────────────────────────────────────────────

def test_a_request_restated_as_a_fact_during_the_chat_is_held_not_saved(tmp_path):
    brain = Brain(str(tmp_path), FakeChat("[]"), background=False)
    out = brain.remember_fact("Admin wants the lamp on when their phone is detected.",
                              said="When my phone is detected, turn on the lamp.", user="admin")
    assert out["status"] == "pending"
    out = brain.remember_fact("Admin likes the lamp warm.", said="I like the lamp warm.", user="admin")
    assert out["status"] == "saved"


def test_a_one_off_from_the_model_is_refused_but_the_owner_may_write_anything(tmp_path):
    store = MemoryStore(str(tmp_path / "m.json"))
    assert store.remember("Admin wants the fan off from now.", origin="noticed")["status"] == "rejected"
    assert store.remember("Admin wants to know the weather today.", origin="distilled")["status"] == "rejected"
    assert store.remember("Check the filter today.", origin="manual")["status"] == "saved"


# ── one rule, kept once ───────────────────────────────────────────────────────

def test_a_newer_version_of_a_rule_replaces_the_older(tmp_path):
    store = MemoryStore(str(tmp_path / "m.json"))
    store.remember("Admin wants the lamp to turn on for only 10 minutes when they are home "
                   "and then turn off.", origin="distilled")
    out = store.remember("Admin wants lamp and fan to turn on for 10 minutes when their device "
                         "is detected home and then turn off.", origin="distilled")
    assert out["status"] == "updated" and "only 10 minutes" in out["replaced"]["text"]
    assert [f["text"][:24] for f in store.facts()] == ["Admin wants lamp and fan"]
    assert len(store.archived()) == 1                  # the old one can be restored


@pytest.mark.parametrize("first, second", [
    ("Admin wants the lamp on when they come home.", "Admin wants the lamp off when they leave home."),
    ("Admin wants the lamp on when they get home.", "Admin wants the fan on when they get home."),
    ("Admin wants the porch light on every morning.", "Admin wants the porch light on every night."),
])
def test_different_rules_are_both_kept(tmp_path, first, second):
    store = MemoryStore(str(tmp_path / "m.json"))
    store.remember(first, origin="distilled")
    assert store.remember(second, origin="distilled")["status"] == "saved"
    assert len(store.facts()) == 2


# ── not clear: ask (owner's suggestion) ───────────────────────────────────────

def test_a_wish_nothing_came_of_is_put_to_the_person(tmp_path):
    chat = FakeChat(json.dumps([{"text": "Admin wants the lamp on when they get home",
                                 "category": "routine"}]))
    brain = Brain(str(tmp_path), chat, background=False)
    brain.record("admin", chatted("When I get home, put the lamp on.", "I can make a rule for that."))
    held = brain.distill("admin")
    assert [f["text"] for f in held] == ["Admin wants the lamp on when they get home."]
    assert held[0]["pending"] and brain.memory.facts() == []
    assert [e["status"] for e in brain.events_since(0)] == ["pending"]


def test_a_wish_that_was_carried_out_is_not_asked_about(tmp_path):
    chat = FakeChat(json.dumps([{"text": "Admin wants the lamp on when they get home",
                                 "category": "routine"}]))
    brain = Brain(str(tmp_path), chat, background=False)
    brain.record("admin", acted("When I get home, put the lamp on.", "create_automation"))
    assert brain.distill("admin") == [] and brain.memory.pending() == []
