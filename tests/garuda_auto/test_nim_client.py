import json
import pytest
from basic_pipelines.garuda_auto.device_registry import DeviceRegistry
from basic_pipelines.garuda_auto.rule_schema import build_schema
from basic_pipelines.garuda_auto.nim_client import NimClient

FAN = {"id": "fan", "name": "Fan", "type": "fan", "room": "study",
       "transport": {"kind": "relay", "channel": 2}}
LAMP = {"id": "lamp", "name": "Lamp", "type": "light", "room": "study",
        "transport": {"kind": "relay", "channel": 1}}


@pytest.fixture
def schema(tmp_path):
    r = DeviceRegistry(str(tmp_path / "devices.json"), relay_channels=(1, 2, 3))
    for entry in (FAN, LAMP):
        ok, reason = r.add(entry)
        assert ok, reason
    return build_schema(r)


RULE_JSON = json.dumps({
    "source_utterance": "turn the fan off when the room is empty for five minutes",
    "when": {"all": [
        {"field": "occupancy", "op": "==", "value": "empty"},
        {"field": "occupancy_duration_s", "op": ">=", "value": 300},
    ]},
    "then": [{"device": "fan", "action": "off"}],
    "cooldown_s": 60,
})


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _completion(content, tokens=120):
    return {"choices": [{"message": {"content": content}}], "usage": {"total_tokens": tokens}}


# ── the egress boundary ─────────────────────────────────────────────────────

def test_request_carries_the_schema_and_nothing_else(schema):
    """The boundary is structural, so assert structure.

    A literal blocklist would both false-positive on legal enum values (the
    schema must name "occupied" -- it is a legal value of `occupancy`) and
    let a real leak through (a reading of 28.5 does not match a substring
    written for 3). Instead: pin the exact key set, pin the schema block to
    schema_for_prompt() verbatim, and require that no field name appears
    outside it. There is then nowhere in the body for a reading to live.
    """
    client = NimClient("key", "some-model")
    body = client.build_request("turn the fan off when empty", [], schema)
    sent = json.loads(body["messages"][1]["content"])

    assert set(sent) == {"schema", "already_known", "instruction"}
    assert sent["schema"] == schema.schema_for_prompt()
    assert sent["instruction"] == "turn the fan off when empty"
    assert sent["already_known"] == []

    # The utterance is allowed to say "temperature"; nothing else is.
    outside = json.dumps({k: v for k, v in sent.items()
                          if k not in ("schema", "instruction")})
    for field in schema.fields:
        assert field not in outside, f"{field} leaked outside the schema block"


def test_build_request_cannot_be_handed_a_descriptor(schema):
    """The real guarantee: live values are not in scope, so they cannot leak.

    This fails the moment someone threads a descriptor through, which is the
    regression the egress claim actually needs protection against. The schema
    parameter is safe by construction -- the following test pins that.
    """
    import inspect
    params = set(inspect.signature(NimClient.build_request).parameters)
    assert params == {"self", "utterance", "existing_rules", "schema"}


def test_the_schema_block_contains_only_names_and_bounds(schema):
    """The schema parameter must not become a smuggling route for readings."""
    payload = schema.schema_for_prompt()
    assert set(payload) == {"fields", "devices", "operators"}
    for name, field in payload["fields"].items():
        assert set(field) <= {"type", "values", "min", "max"}, name


def test_existing_rules_contribute_only_their_utterances(schema):
    client = NimClient("key", "m")
    stored = [{
        "id": "r_001",
        "source_utterance": "turn the lamp off after nine",
        "when": {"all": [{"field": "hour", "op": ">=", "value": 21}]},
        "then": [{"device": "lamp", "action": "off"}],
        "last_fired_at": 1760000000.0,
    }]
    sent = json.loads(client.build_request("x", stored, schema)["messages"][1]["content"])
    assert sent["already_known"] == ["turn the lamp off after nine"]


def test_a_user_added_device_reaches_the_prompt(tmp_path):
    r = DeviceRegistry(str(tmp_path / "devices.json"), relay_channels=(1, 2, 3))
    r.add({"id": "lamp_desk", "name": "Desk lamp", "type": "light", "room": "study",
           "transport": {"kind": "relay", "channel": 1}})
    body = NimClient("key", "m").build_request("turn the desk lamp on", [], build_schema(r))
    sent = json.loads(body["messages"][1]["content"])
    assert "lamp_desk" in sent["schema"]["devices"]
    assert "lamp_desk_state" in sent["schema"]["fields"]


# ── synthesis ───────────────────────────────────────────────────────────────

def test_successful_synthesis_returns_a_rule(monkeypatch, schema):
    import basic_pipelines.garuda_auto.nim_client as mod
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: FakeResponse(_completion(RULE_JSON)))
    client = NimClient("key", "some-model")
    rule, reason = client.synthesize(
        "turn the fan off when the room is empty for five minutes", [], schema)
    assert reason == ""
    assert rule["then"] == [{"device": "fan", "action": "off"}]
    assert client.tokens_used == 120


def test_fenced_json_is_unwrapped(monkeypatch, schema):
    import basic_pipelines.garuda_auto.nim_client as mod
    fenced = f"```json\n{RULE_JSON}\n```"
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: FakeResponse(_completion(fenced)))
    rule, reason = NimClient("key", "m").synthesize("x", [], schema)
    assert reason == ""
    assert rule is not None


def test_unparseable_response_is_reported_not_raised(monkeypatch, schema):
    import basic_pipelines.garuda_auto.nim_client as mod
    monkeypatch.setattr(mod.requests, "post",
                        lambda *a, **k: FakeResponse(_completion("I cannot help with that")))
    rule, reason = NimClient("key", "m").synthesize("x", [], schema)
    assert rule is None
    assert "parse" in reason.lower()


def test_the_budget_leaves_room_for_a_model_that_reasons_first(schema):
    """A reasoning model spends the completion budget before it emits the rule.

    At 512 the deliberation for an ordinary two-condition rule ran the JSON off
    the end mid-string, so the caller saw a parse failure for a rule the model
    had in fact worked out correctly. Pinned as a floor rather than an equality
    so the budget can grow without editing the test.
    """
    body = NimClient("key", "m").build_request("turn the lamp on at dusk", [], schema)
    assert body["max_tokens"] >= 2048


def test_a_truncated_completion_is_not_reported_as_a_parse_failure(monkeypatch, schema):
    """Being cut off and talking nonsense are different failures.

    Only one of them is worth retrying unchanged, so the user is told which.
    """
    import basic_pipelines.garuda_auto.nim_client as mod
    cut_off = '{"source_utterance": "x", "when": {"all": [{"field": "occupancy", "op": "=='
    payload = _completion(cut_off)
    payload["choices"][0]["finish_reason"] = "length"
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: FakeResponse(payload))
    rule, reason = NimClient("key", "m").synthesize("x", [], schema)
    assert rule is None
    assert "parse" not in reason.lower()
    assert "room" in reason.lower()


def test_invalid_rule_is_rejected_by_the_validator(monkeypatch, schema):
    import basic_pipelines.garuda_auto.nim_client as mod
    bad = json.dumps({
        "source_utterance": "unlock the door when i get home",
        "when": {"all": [{"field": "occupancy", "op": "==", "value": "occupied"}]},
        "then": [{"device": "front_door_lock", "action": "unlock"}],
    })
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: FakeResponse(_completion(bad)))
    rule, reason = NimClient("key", "m").synthesize(
        "unlock the door when i get home", [], schema)
    assert rule is None
    assert "front_door_lock" in reason


def test_network_failure_is_reported_not_raised(monkeypatch, schema):
    import basic_pipelines.garuda_auto.nim_client as mod

    def boom(*a, **k):
        raise mod.requests.exceptions.ConnectionError("no route to host")

    monkeypatch.setattr(mod.requests, "post", boom)
    rule, reason = NimClient("key", "m").synthesize("x", [], schema)
    assert rule is None
    assert reason


def test_missing_api_key_short_circuits(schema):
    rule, reason = NimClient("", "m").synthesize("x", [], schema)
    assert rule is None
    assert "key" in reason.lower()


def test_source_utterance_is_forced_to_the_user_text(monkeypatch, schema):
    import basic_pipelines.garuda_auto.nim_client as mod
    drifted = json.loads(RULE_JSON)
    drifted["source_utterance"] = "something the model made up"
    monkeypatch.setattr(mod.requests, "post",
                        lambda *a, **k: FakeResponse(_completion(json.dumps(drifted))))
    rule, _ = NimClient("key", "m").synthesize("the exact words i said", [], schema)
    assert rule["source_utterance"] == "the exact words i said"


# ── the event loop ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_synthesize_async_returns_the_same_shape(schema):
    rule, reason = await NimClient("", "model").synthesize_async("anything", [], schema)
    assert rule is None
    assert reason == "no NIM API key configured"


@pytest.mark.asyncio
async def test_synthesize_async_does_not_block_the_loop(monkeypatch, schema):
    """A 20s timeout on the event loop would stall the MJPEG stream and the
    websocket broadcaster. Prove the call is handed to a worker thread."""
    import asyncio
    import threading
    import basic_pipelines.garuda_auto.nim_client as mod

    calling_thread = {}

    def slow_post(*a, **k):
        calling_thread["name"] = threading.current_thread().name
        return FakeResponse(_completion(RULE_JSON))

    monkeypatch.setattr(mod.requests, "post", slow_post)
    client = NimClient("key", "m")

    ticks = 0

    async def heartbeat():
        nonlocal ticks
        for _ in range(3):
            await asyncio.sleep(0)
            ticks += 1

    rule, _ = (await asyncio.gather(
        client.synthesize_async("x", [], schema), heartbeat()))[0]

    assert rule is not None
    assert calling_thread["name"] != threading.main_thread().name
    assert ticks == 3
