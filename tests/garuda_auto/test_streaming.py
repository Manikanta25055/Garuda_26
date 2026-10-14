"""A spoken answer streams: the model's text is said a sentence at a time."""
import asyncio
import json
import time

import requests

from basic_pipelines.garuda_auto import narada_voice
from basic_pipelines.garuda_auto.agent import _Speech
from basic_pipelines.garuda_auto.llm import NimChat, _read_stream
from basic_pipelines.garuda_auto.narada_voice import NaradaVoice
from test_narada_voice import FakeSDK


class FakeStream:
    status_code = 200

    def __init__(self, chunks, broken_after=None):
        self.lines = ["data: " + json.dumps(c) for c in chunks] + ["data: [DONE]"]
        self.broken_after, self.closed = broken_after, False

    def iter_lines(self, decode_unicode=True):
        for i, line in enumerate(self.lines):
            if self.broken_after is not None and i == self.broken_after:
                raise requests.exceptions.ChunkedEncodingError("cut")
            yield line
            yield ""

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


def test_the_sentence_buffer_says_whole_clean_sentences():
    said = []
    s = _Speech(said.append, lambda t: t.replace("**", "").strip())
    for piece in ["In the last 48 hours:\n\n1. **Tamper** al", "erts at 04.11. Then 3", ". was odd! And ", "the end"]:
        s.feed(piece)
    s.flush()
    assert said == ["In the last 48 hours: ", "1. Tamper alerts at 04.11. ", "Then 3. was odd! ", "And the end "]
    assert s.round_spoken


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
