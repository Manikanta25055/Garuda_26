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


def test_a_failing_brain_says_nothing_changed():
    def boom(*a):
        raise RuntimeError("down")
    v = voice(reply=boom)
    v.issue_token("mani", "user", "home")
    said = asyncio.run(v.reply_for("conv_1", [msg("user", "lights")]))
    assert "Nothing was changed" in said


def test_spoken_replies_are_trimmed_at_a_sentence():
    long = "The hall light is on. " * 40
    out = _spoken(long)
    assert len(out) <= narada_voice.MAX_SPOKEN_CHARS and out.endswith(".")
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


def test_a_turn_that_goes_to_the_planner_says_so_once(monkeypatch):
    import time as _time
    monkeypatch.setattr(narada_voice, "FILLER_AFTER_S", 0.02)
    monkeypatch.setattr(narada_voice, "PLANNER_CHECK_S", 0.05)

    def slow(*a):
        _time.sleep(0.4)
        return {"reply": "Your shortcut is waiting on the screen."}
    v = voice(reply=slow)
    v.live_fn = lambda user: {"lane": "planner", "step": "create_shortcut"} if user == "mani" else None
    v.issue_token("mani", "admin", "home")
    chunks = _collect(v.speak("conv_1", [msg("user", "make a bedtime routine")]))
    assert [c.strip() for c in chunks[1:]] == [narada_voice.PLANNER_LINE,
                                               "Your shortcut is waiting on the screen."]
