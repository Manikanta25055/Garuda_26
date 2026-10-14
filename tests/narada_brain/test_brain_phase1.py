"""Narada's brain, phase 1: the persona, the saved conversation and the leak guard."""
import json

from basic_pipelines.narada_brain import Brain, budget, guards, persona
from basic_pipelines.narada_brain import conversation as conv
from basic_pipelines.narada_brain.conversation import Conversations


def turn(question, answer, tool=None):
    messages = [{"role": "user", "content": question}]
    if tool:
        messages.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": tool, "arguments": "{}"}}]})
        messages.append({"role": "tool", "tool_call_id": "c1", "content": "x" * 5000})
    messages.append({"role": "assistant", "content": answer})
    return messages


# ── persona ───────────────────────────────────────────────────────────────────

def test_persona_is_a_general_assistant_first_and_keeps_the_house_rules():
    home = persona.system_prompt("mani", "admin", scope="home", now=0)
    assert "general assistant" in home.split("The house:")[0]
    assert "Never refuse or deflect a question because it is not about the house" in home
    assert "Nothing changes unless you call a tool in this turn" in home
    assert "talking to mani (role: admin)" in home


def test_security_persona_has_no_device_rules():
    text = persona.system_prompt("mani", "admin", scope="security", now=0)
    assert "set_security_mode" in text and "create_automation" not in text
    assert "home automation lives in the Drishti app" in text


# ── budget ────────────────────────────────────────────────────────────────────

def test_fit_turns_keeps_the_newest_whole_turns():
    turns = [turn(f"question {i} " + "w" * 400, f"answer {i}") for i in range(10)]
    kept, dropped = budget.fit_turns(turns, budget=350)
    assert dropped == 10 - len(kept) and 0 < len(kept) < 10
    assert kept[-1][0]["content"].startswith("question 9")
    assert all(t[0]["role"] == "user" and t[-1]["role"] == "assistant" for t in kept)


def test_a_turn_larger_than_the_budget_is_left_out_not_cut():
    kept, dropped = budget.fit_turns([turn("q" * 40000, "a")], budget=100)
    assert kept == [] and dropped == 1


# ── conversation ──────────────────────────────────────────────────────────────

def test_conversation_survives_a_restart(tmp_path):
    path = str(tmp_path / "c.json")
    Conversations(path).record("mani", turn("I want to go to Hampi", "Nice choice."))
    again = Conversations(path)
    contents = [m["content"] for m in again.history("mani")]
    assert contents == ["I want to go to Hampi", "Nice choice."]


def test_tool_results_are_stored_short_and_turns_stay_whole(tmp_path):
    c = Conversations(str(tmp_path / "c.json"))
    c.record("mani", turn("lamp on", "Lamp is on.", tool="set_device"))
    roles = [m["role"] for m in c.history("mani")]
    assert roles == ["user", "assistant", "tool", "assistant"]
    stored = json.loads((tmp_path / "c.json").read_text())
    tool = stored["conversations"]["mani"]["turns"][0]["messages"][2]
    assert len(tool["content"]) == conv.TOOL_RESULT_CHARS


def test_keys_do_not_share_a_conversation(tmp_path):
    c = Conversations(str(tmp_path / "c.json"))
    c.record("mani", turn("home thing", "ok"))
    c.record("security:mani", turn("security thing", "ok"))
    assert [m["content"] for m in c.history("security:mani")][0] == "security thing"
    c.forget("mani")
    assert c.history("mani") == [] and c.history("security:mani")


def test_old_turns_are_folded_into_the_summary(tmp_path):
    seen = []

    def summarise(previous, transcript):
        seen.append((previous, transcript))
        return "They planned a trip to Hampi."
    c = Conversations(str(tmp_path / "c.json"), summarise=summarise, background=False)
    for i in range(conv.FOLD_AT):
        c.record("mani", turn(f"q{i}", f"a{i}"))
    assert len(seen) == 1 and "Person: q0" in seen[0][1] and "Narada: a0" in seen[0][1]
    kept = [m["content"] for m in c.history("mani") if m["role"] == "user"]
    assert kept == [f"q{i}" for i in range(conv.FOLD_AT - conv.KEEP_RECENT, conv.FOLD_AT)]
    assert c.summary("mani") == "They planned a trip to Hampi."
    assert "They planned a trip to Hampi." in Conversations(str(tmp_path / "c.json")).summary_block("mani")


def test_a_failed_summary_loses_nothing(tmp_path):
    def summarise(previous, transcript):
        raise RuntimeError("model down")
    c = Conversations(str(tmp_path / "c.json"), summarise=summarise, background=False)
    for i in range(conv.FOLD_AT + 2):
        c.record("mani", turn(f"q{i}", f"a{i}"))
    assert len([m for m in c.history("mani", token_budget=10**6) if m["role"] == "user"]) == conv.FOLD_AT + 2
    assert c.summary("mani") == ""


def test_an_empty_summary_is_a_failed_summary(tmp_path):
    c = Conversations(str(tmp_path / "c.json"), summarise=lambda previous, transcript: "  ", background=False)
    for i in range(conv.FOLD_AT):
        c.record("mani", turn(f"q{i}", f"a{i}"))
    assert len([m for m in c.history("mani", token_budget=10**6) if m["role"] == "user"]) == conv.FOLD_AT
    assert c.summary("mani") == ""


def test_the_file_is_bounded_even_without_a_summariser(tmp_path):
    c = Conversations(str(tmp_path / "c.json"), background=False)
    for i in range(conv.HARD_CAP + 15):
        c.record("mani", turn(f"q{i}", f"a{i}"))
    stored = json.loads((tmp_path / "c.json").read_text())["conversations"]["mani"]["turns"]
    assert len(stored) <= conv.HARD_CAP


def test_a_stale_conversation_is_not_replayed(tmp_path):
    now = [1000.0]
    c = Conversations(str(tmp_path / "c.json"), clock=lambda: now[0])
    c.record("mani", turn("yesterday's question", "yesterday's answer"))
    now[0] += conv.STALE_AFTER_S + 1
    assert c.history("mani") == []
    c.record("mani", turn("today", "hello"))
    assert [m["content"] for m in c.history("mani")] == ["today", "hello"]


def test_a_corrupt_file_starts_empty(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{not json")
    c = Conversations(str(path))
    assert c.history("mani") == []
    c.record("mani", turn("q", "a"))
    assert json.loads(path.read_text())["conversations"]["mani"]["turns"]


# ── guards ────────────────────────────────────────────────────────────────────

def test_reciting_the_instructions_is_replaced():
    recited = "```\n" + persona.system_prompt("mani", "admin", now=0) + "\n```"
    reply, leaked = guards.protect(recited, persona.protected_text())
    assert leaked and reply == guards.LEAK_REPLY


def test_an_honest_description_of_itself_is_not_a_leak():
    reply = ("I'm Narada. I can answer questions, chat with you, switch devices, run scenes, "
             "set timers and change security modes such as dnd, night, idle and privacy.")
    assert guards.protect(reply, persona.protected_text()) == (reply, False)


def test_one_quoted_rule_is_not_a_leak_but_two_are():
    one = "Nothing changes unless you call a tool in this turn."
    two = one + " Never invent devices, scenes or readings; call get_house_state when unsure."
    assert guards.protect("My rule is: " + one, persona.protected_text())[1] is False
    assert guards.protect("My rules are: " + two, persona.protected_text())[1] is True


def test_paraphrase_is_not_caught():
    """Recorded so nobody mistakes this for a guarantee: it matches verbatim text only."""
    reply = "I am told to answer general questions and to switch devices only by using tools."
    assert guards.protect(reply, persona.protected_text())[1] is False


def test_a_spoken_reply_is_said_whole_and_a_list_is_said_as_sentences():
    # The old 300-character cut took a list's "3." for a sentence and ended
    # answers on a bare number; the owner wants every answer whole.
    reply = ("Sure thing! Did you know the first computer bug was a real moth? "
             + "It was stuck in a relay of the Harvard Mark II in 1947. " * 6).strip()
    assert guards.fit_spoken(reply) == reply
    listed = ("In the last 48 hours there were three things:\n\n1. **Tamper** alerts at 04:11.  \n"
              "2. Knife detections spiked (206 total)\n3. The door opened twice.")
    assert guards.fit_spoken(listed) == ("In the last 48 hours there were three things: Tamper alerts "
                                         "at 04:11. Knife detections spiked (206 total). The door "
                                         "opened twice.")
    assert guards.fit_spoken("Short one.") == "Short one."


# ── brain ─────────────────────────────────────────────────────────────────────

def test_brain_prompt_carries_the_summary_and_checks_replies(tmp_path):
    class Chat:
        def chat(self, messages, **kw):
            return {"content": "Earlier they asked about Hampi."}
    brain = Brain(str(tmp_path), Chat(), background=False)
    for i in range(conv.FOLD_AT):
        brain.record("mani", turn(f"q{i}", f"a{i}"))
    assert "Earlier they asked about Hampi." in brain.system_prompt("mani", "admin", key="mani")
    assert "Earlier they asked about Hampi." not in brain.system_prompt("mani", "admin", key="guest")
    leaked = brain.check_reply(persona.system_prompt("mani", "admin", now=0))
    assert leaked == guards.LEAK_REPLY and brain.status()["leaks_blocked"] == 1


def test_brain_without_a_directory_keeps_nothing_on_disk(tmp_path):
    brain = Brain()
    brain.record("mani", turn("q", "a"))
    assert [m["content"] for m in brain.history("mani")] == ["q", "a"]
    assert list(tmp_path.iterdir()) == []


def test_split_spoken_says_the_opening_and_shows_everything():
    spoken, shown = guards.split_spoken("Two alerts today.\n[[more]]\n1. **Tamper** at 04:11\n2. Knife")
    assert spoken == "Two alerts today."
    assert shown == "Two alerts today.\n\n1. **Tamper** at 04:11\n2. Knife"
    # No marker and short: said whole, shown whole.
    assert guards.split_spoken("The lamp is on.") == ("The lamp is on.", "The lamp is on.")
    # No marker and long: a few sentences, then a pointer to the screen.
    spoken, shown = guards.split_spoken("One. Two. Three. Four. Five.")
    assert spoken == "One. Two. Three. " + guards.ON_SCREEN and shown == "One. Two. Three. Four. Five."
    # A marker with nothing before it: the details are said in short form.
    assert guards.split_spoken("[[more]]\nAll quiet.")[0] == "All quiet."
    assert guards.strip_more("A\n[[ more ]]\nB") == "A\n\nB"


def test_speech_ends_where_a_table_or_list_starts():
    reply = ("Here's a quick comparison of the lamp and fan:\n\n| Device | State |\n|---|---|\n"
             "| Lamp | Off |\n\nBoth are off.")
    spoken, shown = guards.split_spoken(reply)
    assert spoken == "Here's a quick comparison of the lamp and fan: " + guards.ON_SCREEN
    assert shown == reply
    spoken, _ = guards.split_spoken("## Plan\n1. Lamp off\n2. Fan low")
    assert spoken == "Plan. " + guards.ON_SCREEN
    assert guards.is_structure("**Phase 1: Wind Down**") and not guards.is_structure("**Tamper** at 04:11.")
