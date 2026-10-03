"""Narada asked about the past: it searches the logs on disk and reads what comes back.

Asked "what happened at 2026-10-02 14:33:05" (a time copied from the logs), the
quick model had no tool that reached the logs, and the planner's read_logs was
always refused; the answer was a guess. These hold the whole path: the rule in
the prompt, the tool on the quick model, the entries in the tool's answer.
"""
import json

import pytest

import Garuda_web as gw
from basic_pipelines.garuda_auto.agent import fit_result
from basic_pipelines.garuda_auto.llm import NimChat
from basic_pipelines.narada_brain import persona


class Resp:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        pass


def completion(content="", tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg, "finish_reason": "stop"}], "usage": {"total_tokens": 10}}


def call(name, args, cid="c1"):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class ScriptedChat(NimChat):
    def __init__(self, script):
        self.requests = []
        replies = iter(script)

        def post(url, headers=None, json=None, timeout=None):
            self.requests.append(json)
            return Resp(next(replies))
        super().__init__("key", ["m1"], post=post)


def _write(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(line + "\n" for line in lines))


@pytest.fixture
def scripted(app_client, monkeypatch):
    def install(script):
        chat = ScriptedChat(script)
        monkeypatch.setattr(gw.AGENT, "chat", chat)
        return chat
    return install


def test_a_pasted_time_is_searched_and_the_entries_reach_the_model(app_client, user_headers,
                                                                    scripted):
    _write(gw.PERM_SYSTEM_LOG + ".1", ["[2026-10-02 14:30:00] Pipeline started.",
                                       "[2026-10-02 14:33:05] Alert triggered.",
                                       "[2026-10-02 14:33:06] Email alert sent."])
    _write(gw.PERM_DETECTION_LOG, ["[2026-10-02 14:33:04] [DANGER] knife conf=0.87"])
    chat = scripted([
        completion(tool_calls=[call("search_history", {"at": "[2026-10-02 14:33:05]"})]),
        completion("At 14:33:04 the camera saw a knife; the alert went off a second later."),
    ])
    r = app_client.post("/api/chat", json={"message": "what happened at [2026-10-02 14:33:05]?"},
                        headers=user_headers)
    assert r.status_code == 200, r.text
    assert "knife" in r.json()["response"]
    first = chat.requests[0]
    # The quick model is told to look, and has the tool to look with.
    assert "search_history before you answer" in first["messages"][0]["content"]
    assert "search_history" in {t["function"]["name"] for t in first["tools"]}
    tool_msg = chat.requests[1]["messages"][-1]
    assert tool_msg["role"] == "tool"
    found = json.loads(tool_msg["content"])
    assert [e["text"] for e in found["entries"]] == [
        "Pipeline started.", "[DANGER] knife conf=0.87", "Alert triggered.", "Email alert sent."]


def test_the_rule_is_in_both_products_prompts():
    for scope in ("home", "security"):
        assert persona.HISTORY_RULES in persona.system_prompt("asha", "user", scope=scope)
    assert persona.HISTORY_RULES in persona.protected_text()


def test_a_long_answer_is_shortened_not_cut_into_broken_json():
    entries = [{"ts": f"2026-10-02 14:{m:02d}:00", "source": "system", "text": "x" * 120}
               for m in range(60)]
    out = {"at": "2026-10-02 14:30:00", "total": 60, "omitted_before": 0, "omitted_after": 0,
           "entries": entries}
    text = fit_result(out, 4000)
    assert len(text) <= 4000
    fitted = json.loads(text)                       # still valid
    kept = fitted["entries"]
    # The far ends went; the moment itself is still there, and the counts add up.
    assert "2026-10-02 14:30:00" in [e["ts"] for e in kept]
    assert fitted["omitted_before"] + len(kept) + fitted["omitted_after"] == 60
    assert fitted["not_shown"] == 60 - len(kept) and "narrower" in fitted["note"]
    # Small answers pass unchanged.
    assert fit_result({"ok": True}, 4000) == '{"ok": true}'


def test_a_log_list_loses_its_oldest_lines_first():
    out = {"system_log": [f"[2026-10-02 14:{m:02d}:00] line {m}" for m in range(60)]}
    fitted = json.loads(fit_result(out, 800))
    assert fitted["system_log"][-1].endswith("line 59")


def test_recent_activity_gives_the_date_and_the_second(app_client):
    from basic_pipelines.garuda_auto import actuation_log
    actuation_log.record(gw.DRISHTI_CTX.log_path, device="lamp", action="on", rule_id=None,
                         matched=None, ok=True, source="manual", actor="asha",
                         clock=lambda: 1790000000.0)
    out = gw.AGENT._run_tool("recent_activity", {"limit": 1}, "asha", "user")
    import time
    assert out["activity"][0]["when"] == time.strftime("%Y-%m-%d %H:%M:%S (%a)",
                                                       time.localtime(1790000000.0))
