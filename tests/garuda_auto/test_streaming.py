"""A spoken answer streams: the model's text is said a sentence at a time."""
import asyncio
import json
import time

import requests

from basic_pipelines.garuda_auto import narada_voice
from basic_pipelines.garuda_auto.agent import _Speech
from basic_pipelines.garuda_auto.llm import NimChat, _read_stream
from basic_pipelines.narada_brain import guards
from basic_pipelines.garuda_auto.narada_voice import NaradaVoice
from test_narada_voice import FakeSDK


class FakeStream:
    status_code = 200

    def __init__(self, chunks, broken_after=None):
        self.lines = ["data: " + json.dumps(c) for c in chunks] + ["data: [DONE]"]
        self.broken_after, self.closed = broken_after, False

    def iter_lines(self, decode_unicode=False):
        for i, line in enumerate(self.lines):
            if self.broken_after is not None and i == self.broken_after:
                raise requests.exceptions.ChunkedEncodingError("cut")
            yield line if decode_unicode else line.encode("utf-8")
            yield "" if decode_unicode else b""

    def close(self):
        self.closed = True


def delta(**d):
    return {"choices": [{"delta": d}]}


def test_a_streamed_answer_is_handed_over_as_it_arrives_and_read_whole():
    pieces = []
    resp = FakeStream([delta(content="All quiet. "), delta(content="Lamp is on."),
                       {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"total_tokens": 9}}])
    payload = _read_stream(resp, pieces.append)
    assert pieces == ["All quiet. ", "Lamp is on."] and resp.closed
    assert payload["choices"][0]["message"]["content"] == "All quiet. Lamp is on."
    assert payload["choices"][0]["finish_reason"] == "stop" and payload["usage"]["total_tokens"] == 9


def test_a_real_event_stream_is_read_as_utf8_not_latin1():
    # NIM's text/event-stream names no charset; requests then guesses Latin-1
    # and every curly quote or dash came out as boxes on the phone.
    import io
    from requests.models import Response
    body = ("data: " + json.dumps({"choices": [{"delta": {"content": "Here’s 04:11 – 04:43 ✓"}}]},
                                  ensure_ascii=False) + "\n\ndata: [DONE]\n\n").encode("utf-8")
    resp = Response()
    resp.status_code, resp.raw = 200, io.BytesIO(body)
    resp.headers["Content-Type"] = "text/event-stream"
    resp.encoding = requests.utils.get_encoding_from_headers(resp.headers)   # as the adapter does
    pieces = []
    payload = _read_stream(resp, pieces.append)
    assert pieces == ["Here’s 04:11 – 04:43 ✓"]
    assert payload["choices"][0]["message"]["content"] == "Here’s 04:11 – 04:43 ✓"


def test_tool_calls_are_put_back_together_from_their_pieces():
    resp = FakeStream([
        delta(tool_calls=[{"index": 0, "id": "call_1", "function": {"name": "search_", "arguments": '{"date"'}}]),
        delta(tool_calls=[{"index": 0, "function": {"name": "history", "arguments": ': "today"}'}}]),
    ])
    message = _read_stream(resp, lambda p: None)["choices"][0]["message"]
    assert message["tool_calls"] == [{"id": "call_1", "type": "function",
                                      "function": {"name": "search_history", "arguments": '{"date": "today"}'}}]


def test_a_stream_broken_after_speaking_keeps_what_was_said():
    resp = FakeStream([delta(content="First part. "), delta(content="Second.")], broken_after=1)
    payload = _read_stream(resp, lambda p: None)
    assert payload["choices"][0]["message"]["content"] == "First part. "
    assert payload["choices"][0]["finish_reason"] == "error"


def test_chat_streams_when_asked():
    sent = []

    def post(url, headers, json, timeout, **kw):
        sent.append((json.get("stream"), kw.get("stream")))
        return FakeStream([delta(content="Hi there.")])
    nim = NimChat(api_key="k", models=["m"], post=post)
    pieces = []
    msg = nim.chat([{"role": "user", "content": "hi"}], on_text=pieces.append)
    assert msg["content"] == "Hi there." and pieces == ["Hi there."] and sent == [(True, True)]


def test_the_sentence_buffer_says_whole_clean_sentences(monkeypatch):
    monkeypatch.setattr(guards, "SPOKEN_SENTENCES", 10)
    said = []
    s = _Speech(said.append, lambda t: t.replace("**", "").strip())
    for piece in ["In the last 48 hours:\n\n**Tamper** al", "erts at 04.11. Then 3", ". was odd! And ", "the end"]:
        s.feed(piece)
    s.flush(final=True)
    assert said == ["In the last 48 hours: ", "Tamper alerts at 04.11. ", "Then 3. was odd! ",
                    "And the end ", None]
    assert s.spoken


def test_only_the_part_before_the_marker_is_said_even_split_across_pieces():
    said = []
    s = _Speech(said.append, lambda t: t.strip())
    for piece in ["Three things stood out, mostly tamper alerts early on 13 Oct. [", "[mo",
                  "re]]\n## Details\n1. Tamper at 04:11.\n2. Knife at 20:07.\n"]:
        s.feed(piece)
    s.flush(final=True)
    assert said == ["Three things stood out, mostly tamper alerts early on 13 Oct. ", None]


def test_without_a_marker_a_long_answer_is_cut_to_a_few_sentences_and_points_at_the_screen():
    said = []
    s = _Speech(said.append, lambda t: t.strip())
    s.feed("One is here. Two is here. Three is here. Four is here. Five is here. ")
    s.flush(final=True)
    assert said == ["One is here. ", "Two is here. ", "Three is here. ", guards.ON_SCREEN + " ", None]


def test_a_replaced_turn_stops_the_stream_mid_answer():
    import threading
    from basic_pipelines.garuda_auto.agent import _Cancelled
    stop = threading.Event()
    s = _Speech(lambda t: None, lambda t: t, cancel=stop)
    s.feed("Fine so far. ")
    stop.set()
    try:
        s.feed("More words. ")
    except _Cancelled:
        pass
    else:
        raise AssertionError("a cancelled turn kept streaming")


def test_recitation_spread_over_sentences_is_caught():
    from basic_pipelines.narada_brain import persona
    said = []
    s = _Speech(said.append, lambda t: t.strip())
    long_ones = [x for x in guards._sentences(persona.protected_text())][:3]
    for sentence in long_ones:
        s.feed(sentence.capitalize() + ". ")
    s.flush(final=True)
    assert guards.LEAK_REPLY + " " in said and said[-1] is None
    assert sum(1 for x in said if x and x.strip().lower().rstrip(".") in long_ones) < 2


def test_the_voice_speaks_each_sentence_as_it_is_written_and_never_twice():
    def brain(text, user, role, scope, on_text=None):
        on_text("First thing. ")
        time.sleep(0.4)
        on_text("Second thing. ")
        return {"reply": "First thing. Second thing.", "streamed": True}
    v = NaradaVoice("sk", "seng", reply_fn=brain, clock=lambda: 1000.0, client=FakeSDK())
    v.issue_token("mani", "user", "home")

    async def run():
        out, t0 = [], time.monotonic()
        async for chunk in v.speak("conv_1", [type("M", (), {"role": "user", "content": "news?"})()]):
            out.append((round(time.monotonic() - t0, 1), chunk))
        return out
    out = asyncio.run(run())
    assert [c for _, c in out] == ["First thing. ", "Second thing. "]
    assert out[0][0] < 0.3                               # before the brain had finished


def test_a_brain_that_cannot_stream_is_said_whole(monkeypatch):
    monkeypatch.setattr(narada_voice, "FILLER_AFTER_S", 5)
    v = NaradaVoice("sk", "seng", reply_fn=lambda text, user, role, scope: {"reply": "One. Two."},
                    clock=lambda: 1000.0, client=FakeSDK())
    v.issue_token("mani", "user", "home")

    async def run():
        return [c async for c in v.speak("conv_1", [type("M", (), {"role": "user", "content": "x"})()])]
    assert asyncio.run(run()) == ["One. ", "Two."]


def test_a_marker_before_anything_said_still_leaves_the_opening_spoken():
    # Measured on nemotron: it sometimes writes [[more]] first. Nothing would
    # have been said at all.
    said = []
    s = _Speech(said.append, lambda t: t.strip())
    for piece in ["[[more]]\n", "A good routine starts early. ", "Dim the lamp at ten. ",
                  "Switch the fan to low. ", "Then lights off at eleven. ", "Sleep well. "]:
        s.feed(piece)
    s.flush(final=True)
    assert said == ["A good routine starts early. ", "Dim the lamp at ten. ", "Switch the fan to low. ",
                    guards.ON_SCREEN + " ", None]


def test_the_voice_stops_before_reading_a_table_aloud():
    said = []
    s = _Speech(said.append, lambda t: t.strip())
    for piece in ["Here's a quick comparison of the lamp and fan:\n\n", "| Device | Type |\n",
                  "|---|---|\n| Lamp | Light |\n"]:
        s.feed(piece)
    s.flush(final=True)
    assert said == ["Here's a quick comparison of the lamp and fan: ", guards.ON_SCREEN + " ", None]


def test_a_short_closing_line_past_the_cap_is_said_not_pointed_at():
    said = []
    s = _Speech(said.append, lambda t: t.strip())
    s.feed("Dim the lamp. Turn off the fan. Switch on privacy mode. Shall I set that up? ")
    s.flush(final=True)
    assert said == ["Dim the lamp. ", "Turn off the fan. ", "Switch on privacy mode. ",
                    "Shall I set that up? ", None]
