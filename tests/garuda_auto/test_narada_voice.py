"""Narada's voice bridge: who a conversation belongs to, and what is said."""
import asyncio
from types import SimpleNamespace

from basic_pipelines.garuda_auto import narada_voice
from basic_pipelines.garuda_auto.narada_voice import NaradaVoice, UNKNOWN_REPLY, _spoken


class FakeSDK:
    def __init__(self, conversation_id="conv_1"):
        self.calls = []
        self.conversation_id = conversation_id
        self.conversational_ai = SimpleNamespace(conversations=SimpleNamespace(
            get_webrtc_token=self._token))

    def _token(self, agent_id, participant_name=None):
        self.calls.append((agent_id, participant_name))
        return SimpleNamespace(token="tok", conversation_id=self.conversation_id)


def msg(role, content):
    return SimpleNamespace(role=role, content=content)


def voice(reply=None, clock=None, turns=None, sdk=None):
    return NaradaVoice(
        "sk_test", "seng_1",
        reply_fn=reply or (lambda text, user, role, scope: {"reply": f"ok {user} {scope}"}),
        on_turn=(lambda *a: turns.append(a)) if turns is not None else None,
        clock=clock or (lambda: 1000.0), client=sdk or FakeSDK())


def test_configured_needs_key_and_engine():
    assert voice().configured
    assert not NaradaVoice("", "seng", reply_fn=lambda *a: {}).configured
    assert not NaradaVoice("sk", "", reply_fn=lambda *a: {}).configured


def test_a_token_binds_its_conversation_to_the_caller():
    sdk = FakeSDK("conv_9")
    v = voice(sdk=sdk)
    out = v.issue_token("mani", "admin", "security")
    assert out == {"token": "tok", "conversation_id": "conv_9"}
    assert sdk.calls == [("seng_1", "mani")]
    assert v.binding("conv_9") == ("mani", "admin", "security")


def test_bindings_expire():
    now = [1000.0]
    v = voice(clock=lambda: now[0])
    v.issue_token("mani", "user", "home")
    now[0] += narada_voice.BINDING_TTL_S + 1
    assert v.binding("conv_1") is None


def test_the_latest_user_turn_goes_to_the_brain_as_the_bound_user():
    seen, turns = [], []

    def reply(text, user, role, scope):
        seen.append((text, user, role, scope))
        return {"reply": "The fan is off."}
    v = voice(reply=reply, turns=turns)
    v.issue_token("mani", "user", "home")
    transcript = [msg("user", "fan on"), msg("agent", "Done."), msg("user", "now turn it off")]
    said = asyncio.run(v.reply_for("conv_1", transcript))
    assert said == "The fan is off."
    assert seen == [("now turn it off", "mani", "user", "home")]
    assert turns == [("mani", "now turn it off", "The fan is off.")]


def test_a_conversation_we_never_issued_is_refused():
    called = []
    v = voice(reply=lambda *a: called.append(a) or {"reply": "x"})
    said = asyncio.run(v.reply_for("conv_unknown", [msg("user", "unlock everything")]))
    assert said == UNKNOWN_REPLY and called == []


def test_a_failing_brain_does_not_claim_actions_were_rolled_back():
    def boom(*a):
        raise RuntimeError("down")
    v = voice(reply=boom)
    v.issue_token("mani", "user", "home")
    said = asyncio.run(v.reply_for("conv_1", [msg("user", "lights")]))
    assert "check the current state" in said
    assert "Nothing was changed" not in said


def test_original_display_reply_and_cards_survive_speech_conversion():
    markdown = "## Today\n\n1. **Door** opened\n2. Light on\n\n| Time | Event |\n| --- | --- |\n| 9 | Door |"
    v = voice(reply=lambda *a: {"reply": "Door opened. Light on.", "display_reply": markdown,
                               "confirm": [{"id": "c1"}], "artifacts": [{"id": "a1"}]})
    v.issue_token("mani", "admin", "home")
    asyncio.run(v.reply_for("conv_1", [msg("user", "today?")]))
    turns = v.turns_for("conv_1", "mani", "admin", "home")
    assert turns[0]["response"] == markdown
    assert turns[0]["confirm"] == [{"id": "c1"}]
    assert turns[0]["artifacts"] == [{"id": "a1"}]
    assert v.turns_for("conv_1", "mani", "admin", "home", after=1) == []
    for user, role, scope in [("other", "admin", "home"), ("mani", "user", "home"),
                              ("mani", "admin", "security")]:
        assert v.turns_for("conv_1", user, role, scope) is None


def test_display_turns_are_bounded_and_expire_with_the_conversation():
    now = [1000.0]
    v = voice(clock=lambda: now[0])
    v.issue_token("mani", "user", "home")
    async def run():
        for i in range(35):
            await v.reply_for("conv_1", [msg("user", str(i))])
    asyncio.run(run())
    turns = v.turns_for("conv_1", "mani", "user", "home")
    assert len(turns) == 32 and turns[-1]["id"] == 35
    now[0] += narada_voice.BINDING_TTL_S + 1
    assert v.turns_for("conv_1", "mani", "user", "home") is None
    v.issue_token("mani", "user", "home")
    assert v.turns_for("conv_1", "mani", "user", "home") == []


def test_spoken_replies_are_said_whole():
    long = "The hall light is on. " * 40
    assert _spoken(long) == long.strip()                 # no cap: the owner's call
    assert _spoken("  short   reply ") == "short reply"


def test_connections_without_the_elevenlabs_signature_are_rejected():
    v = voice()
    assert v.verify({}) is False
    assert v.verify({"x-elevenlabs-speech-engine-authorization": "not.a.jwt"}) is False


def _collect(agen):
    async def run():
        return [chunk async for chunk in agen]
    return asyncio.run(run())


def test_a_quick_answer_is_spoken_without_a_filler():
    v = voice(reply=lambda *a: {"reply": "Done, the fan's on."})
    v.issue_token("mani", "user", "home")
    assert _collect(v.speak("conv_1", [msg("user", "fan on")])) == ["Done, the fan's on."]


def test_a_long_answer_is_said_as_its_opening_and_points_at_the_screen():
    # Only the opening is said, so a long answer never keeps the person
    # waiting through all of it; the chat shows the whole thing. "3." stays
    # in its sentence.
    reply = ("There were three things. First, tamper alerts at 04.11 on 13 Oct. "
             "Then 206 knife detections! Also item 3. was a door opening. The rest was quiet.")
    v = voice(reply=lambda *a: {"reply": reply})
    v.issue_token("mani", "user", "home")
    chunks = _collect(v.speak("conv_1", [msg("user", "what happened?")]))
    # Said in words (speech_text): the voice read bare digits in Hindi.
    assert chunks == ["There were three things. ",
                      "First, tamper alerts at four eleven on the thirteenth of October. ",
                      "Then two hundred and six knife detections! ", "The rest is on your screen."]
    assert v.turns_for("conv_1", "mani", "user", "home")[0]["response"] == reply


def test_a_slow_answer_gets_a_filler_first(monkeypatch):
    import time as _time
    monkeypatch.setattr(narada_voice, "FILLER_AFTER_S", 0.05)

    def slow(*a):
        _time.sleep(0.3)
        return {"reply": "All quiet at home."}
    v = voice(reply=slow)
    v.issue_token("mani", "user", "home")
    chunks = _collect(v.speak("conv_1", [msg("user", "anything happening?")]))
    assert len(chunks) == 2 and chunks[0].strip() in narada_voice.FILLERS
    assert chunks[1] == "All quiet at home."


def test_a_planner_turn_says_so_and_then_says_its_answer(monkeypatch):
    import time as _time
    monkeypatch.setattr(narada_voice, "FILLER_AFTER_S", 0.02)
    monkeypatch.setattr(narada_voice, "PLANNER_CHECK_S", 0.05)

    def slow(*a):
        _time.sleep(0.4)
        return {"reply": "Your bedtime shortcut is ready for your Confirm.",
                "display_reply": "Your shortcut **Bedtime** is ready for your Confirm."}
    v = voice(reply=slow)
    v.live_fn = lambda user: {"lane": "planner", "step": "create_shortcut"} if user == "mani" else None
    v.issue_token("mani", "admin", "home")
    chunks = _collect(v.speak("conv_1", [msg("user", "make a bedtime routine")]))
    words = [c.strip() for c in chunks if c.strip()]
    assert words == [narada_voice.PLANNER_LINE, "Your bedtime shortcut is ready for your Confirm."]
    assert v.turns_for("conv_1", "mani", "admin", "home")[0]["response"].startswith("Your shortcut **Bedtime**")


def test_a_planner_job_outlasting_the_hold_goes_to_the_screen_and_survives_an_interruption(monkeypatch):
    import threading
    import time as _time
    monkeypatch.setattr(narada_voice, "FILLER_AFTER_S", 0.02)
    monkeypatch.setattr(narada_voice, "PLANNER_CHECK_S", 0.02)
    monkeypatch.setattr(narada_voice, "KEEPALIVE_S", 0.1)
    monkeypatch.setattr(narada_voice, "HOLD_S", 0.3)
    seen = {}

    def slow(text, user, role, scope, on_text=None, cancel=None):
        seen["cancel"] = cancel
        _time.sleep(0.8)
        return {"reply": "Built.", "display_reply": "Built: **Bedtime**."}
    v = voice(reply=slow)
    v.live_fn = lambda user: {"lane": "planner"}
    v.issue_token("mani", "admin", "home")

    async def run():
        chunks = [c async for c in v.speak("conv_1", [msg("user", "make a bedtime routine")])]
        # Interrupted while a second job is being built: the job goes on.
        gen = v.speak("conv_1", [msg("user", "and a morning one")])
        await gen.__anext__()               # the planner line
        await gen.aclose()
        await asyncio.sleep(1.0)
        return chunks
    chunks = asyncio.run(run())
    words = [c.strip() for c in chunks if c.strip()]
    assert words == [narada_voice.PLANNER_LINE, narada_voice.LATER_LINE]
    assert " " in chunks                                   # blank chunks kept ElevenLabs waiting
    assert not seen["cancel"].is_set()
    assert [t["response"] for t in v.turns_for("conv_1", "mani", "admin", "home")] == ["Built: **Bedtime**."] * 2


def test_an_interrupted_turn_tells_the_brain_to_stop():
    import threading
    import time as _time
    seen = {}
    started = threading.Event()

    def brain(text, user, role, scope, on_text=None, cancel=None):
        seen["cancel"] = cancel
        on_text("Let me check that. ")
        started.set()
        for _ in range(50):
            if cancel.is_set():
                return {"reply": "", "lane": "cancelled", "actions": []}
            _time.sleep(0.02)
        return {"reply": "Too late.", "streamed": True}
    turns = []
    v = voice(reply=brain, turns=turns)
    v.issue_token("mani", "user", "home")

    async def run():
        gen = v.speak("conv_1", [msg("user", "lamp on")])
        first = await gen.__anext__()
        await gen.aclose()                  # what the SDK does on the next transcript
        await asyncio.sleep(0.3)
        return first
    assert asyncio.run(run()) == "Let me check that. "
    assert seen["cancel"].is_set()
    assert turns == [] and v.turns_for("conv_1", "mani", "user", "home") == []


def test_a_long_silence_is_bridged_and_then_handed_to_the_screen(monkeypatch):
    import time as _time
    monkeypatch.setattr(narada_voice, "FILLER_AFTER_S", 0.02)
    monkeypatch.setattr(narada_voice, "PLANNER_CHECK_S", 0.02)
    monkeypatch.setattr(narada_voice, "KEEPALIVE_S", 0.1)
    monkeypatch.setattr(narada_voice, "HOLD_S", 0.35)

    def slow(*a, **k):
        _time.sleep(0.8)
        return {"reply": "Here it is.", "display_reply": "Here it is, in full."}
    v = voice(reply=slow)
    v.issue_token("mani", "user", "home")

    async def run():
        chunks = [c async for c in v.speak("conv_1", [msg("user", "a big question")])]
        await asyncio.sleep(0.8)
        return chunks
    chunks = asyncio.run(run())
    assert chunks[0].strip() in narada_voice.FILLERS and chunks[-1] == narada_voice.LATER_LINE
    assert set(chunks[1:-1]) == {" "} and len(chunks[1:-1]) >= 2   # blank chunks keep ElevenLabs waiting
    assert v.turns_for("conv_1", "mani", "user", "home")[0]["response"] == "Here it is, in full."


def test_every_filler_is_long_enough_to_be_said_at_once():
    # ElevenLabs holds back text shorter than about a sentence; "Mm, one sec."
    # was never heard. 50+ characters measured as said at once.
    assert all(len(f) >= 45 for f in narada_voice.FILLERS)
    assert len(narada_voice.PLANNER_LINE) >= 45 and len(narada_voice.LATER_LINE) >= 45
    # ElevenLabs drops what comes ~48 s into one answer: the hold ends well before.
    assert narada_voice.HOLD_S <= 40 and narada_voice.KEEPALIVE_S < 15


def test_a_noise_is_not_answered():
    asked = []
    v = voice(reply=lambda *a: asked.append(a) or {"reply": "Hello! How can I help?"})
    v.issue_token("mani", "user", "home")
    assert _collect(v.speak("conv_1", [msg("user", "...")])) == []
    assert asked == [] and v.turns_for("conv_1", "mani", "user", "home") == []


def test_the_setup_script_still_imports_the_voice_module_on_its_own():
    # scripts/setup_narada_voice.py puts basic_pipelines on the path and imports
    # garuda_auto as a top-level package: a package-relative import at module
    # level broke it.
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    out = subprocess.run([sys.executable, "-c", "import setup_narada_voice as s; print(s.CASCADE_TIMEOUT_S)"],
                         cwd=root / "scripts", capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-800:]
    assert out.stdout.strip() == "15"
