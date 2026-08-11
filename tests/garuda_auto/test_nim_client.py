import json
from basic_pipelines.garuda_auto.nim_client import NimClient

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


def test_request_carries_the_schema_and_nothing_else():
    """The boundary is structural, so assert structure.

    A literal blocklist would both false-positive on legal enum values (the
    schema must name "occupied" -- it is a legal value of `occupancy`) and
    let a real leak through (a reading of 28.5 does not match a substring
    written for 3). Instead: pin the exact key set, pin the schema block to
    schema_for_prompt() verbatim, and require that no field name appears
    outside it. There is then nowhere in the body for a reading to live.
    """
    from basic_pipelines.garuda_auto.rule_schema import FIELDS, schema_for_prompt

    client = NimClient("key", "some-model")
    body = client.build_request("turn the fan off when empty", [])
    sent = json.loads(body["messages"][1]["content"])

    assert set(sent) == {"schema", "already_known", "instruction"}
    assert sent["schema"] == schema_for_prompt()
    assert sent["instruction"] == "turn the fan off when empty"
    assert sent["already_known"] == []

    # The utterance is allowed to say "temperature"; nothing else is.
    outside = json.dumps({k: v for k, v in sent.items()
                          if k not in ("schema", "instruction")})
    for field in FIELDS:
        assert field not in outside, f"{field} leaked outside the schema block"


def test_build_request_cannot_be_handed_a_descriptor():
    """The real guarantee: live values are not in scope, so they cannot leak.

    This fails the moment someone threads a descriptor through, which is the
    regression the egress claim actually needs protection against.
    """
    import inspect
    params = set(inspect.signature(NimClient.build_request).parameters)
    assert params == {"self", "utterance", "existing_rules"}


def test_existing_rules_contribute_only_their_utterances():
    client = NimClient("key", "m")
    stored = [{
        "id": "r_001",
        "source_utterance": "turn the lamp off after nine",
        "when": {"all": [{"field": "hour", "op": ">=", "value": 21}]},
        "then": [{"device": "lamp", "action": "off"}],
        "last_fired_at": 1760000000.0,
    }]
    sent = json.loads(client.build_request("x", stored)["messages"][1]["content"])
    assert sent["already_known"] == ["turn the lamp off after nine"]


def test_successful_synthesis_returns_a_rule(monkeypatch):
    import basic_pipelines.garuda_auto.nim_client as mod
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: FakeResponse(_completion(RULE_JSON)))
    client = NimClient("key", "some-model")
    rule, reason = client.synthesize("turn the fan off when the room is empty for five minutes", [])
    assert reason == ""
    assert rule["then"] == [{"device": "fan", "action": "off"}]
    assert client.tokens_used == 120


def test_fenced_json_is_unwrapped(monkeypatch):
    import basic_pipelines.garuda_auto.nim_client as mod
    fenced = f"```json\n{RULE_JSON}\n```"
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: FakeResponse(_completion(fenced)))
    rule, reason = NimClient("key", "m").synthesize("x", [])
    assert reason == ""
    assert rule is not None


def test_unparseable_response_is_reported_not_raised(monkeypatch):
    import basic_pipelines.garuda_auto.nim_client as mod
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: FakeResponse(_completion("I cannot help with that")))
    rule, reason = NimClient("key", "m").synthesize("x", [])
    assert rule is None
    assert "parse" in reason.lower()


def test_invalid_rule_is_rejected_by_the_validator(monkeypatch):
    import basic_pipelines.garuda_auto.nim_client as mod
    bad = json.dumps({
        "source_utterance": "unlock the door when i get home",
        "when": {"all": [{"field": "occupancy", "op": "==", "value": "occupied"}]},
        "then": [{"device": "front_door_lock", "action": "unlock"}],
    })
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: FakeResponse(_completion(bad)))
    rule, reason = NimClient("key", "m").synthesize("unlock the door when i get home", [])
    assert rule is None
    assert "front_door_lock" in reason


def test_network_failure_is_reported_not_raised(monkeypatch):
    import basic_pipelines.garuda_auto.nim_client as mod

    def boom(*a, **k):
        raise mod.requests.exceptions.ConnectionError("no route to host")

    monkeypatch.setattr(mod.requests, "post", boom)
    rule, reason = NimClient("key", "m").synthesize("x", [])
    assert rule is None
    assert reason


def test_missing_api_key_short_circuits():
    rule, reason = NimClient("", "m").synthesize("x", [])
    assert rule is None
    assert "key" in reason.lower()


def test_source_utterance_is_forced_to_the_user_text(monkeypatch):
    import basic_pipelines.garuda_auto.nim_client as mod
    drifted = json.loads(RULE_JSON)
    drifted["source_utterance"] = "something the model made up"
    monkeypatch.setattr(mod.requests, "post",
                        lambda *a, **k: FakeResponse(_completion(json.dumps(drifted))))
    rule, _ = NimClient("key", "m").synthesize("the exact words i said", [])
    assert rule["source_utterance"] == "the exact words i said"
