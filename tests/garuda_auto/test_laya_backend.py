"""The Laya backend: the Jev wire format, Laya's question shape, and falling back to local."""
import requests

from basic_pipelines.garuda_auto.decision import (DecisionEngine, LayaBackend, LocalBackend,
                                                  routing_questions)

DEVICES = [{"id": "lamp", "name": "Lamp", "room": "study"}, {"id": "fan", "name": "Fan", "room": ""}]
SCENES = [{"id": "movie", "name": "Movie night"}]


class Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def answers(intent="device_control", device="lamp", action="on", scene="none", conf=0.91):
    return {"answers": {
        "intent": {"choice": intent, "confidence": conf, "probabilities": {intent: conf}},
        "device": {"choice": device, "confidence": 0.97},
        "action": {"choice": action, "probabilities": {action: 0.88, "none": 0.12}},
        "scene": {"choice": scene, "confidence": 0.9}}}


def test_off_unless_a_url_is_given():
    assert LayaBackend("").configured is False and LayaBackend("http://127.0.0.1:8000").configured is True


def test_questions_are_sent_the_way_laya_reads_them_and_no_key_is_sent():
    sent = {}

    def post(url, headers=None, json=None, timeout=None):
        sent.update(url=url, headers=headers, json=json)
        return Resp(answers())
    backend = LayaBackend("http://127.0.0.1:8000/", post=post)
    state = {"utterance": "it is dark in the study", "devices": DEVICES, "scenes": SCENES}
    out = backend.decide(state, routing_questions(DEVICES, SCENES))
    assert sent["url"] == "http://127.0.0.1:8000/v1/systemone" and "Authorization" not in sent["headers"]
    assert sent["json"]["state"] == {"body": "it is dark in the study"}
    q = sent["json"]["questions"]
    assert q["device"]["criteria"] == {"lamp": "Lamp in the study", "fan": "Fan",
                                       "none": "no single device is named"}
    assert q["scene"]["criteria"]["movie"] == "the scene called Movie night"
    assert q["intent"]["type"] == "choice" and "switch one named device" in q["intent"]["criteria"]["device_control"]
    assert set(q["action"]["criteria"]) == {"on", "off", "none"} and q["action"]["instructions"]
    assert out["intent"]["value"] == "device_control" and out["intent"]["backend"] == "laya"
    assert out["action"]["confidence"] == 0.88            # taken from the probabilities when no confidence is given


def test_an_answer_outside_the_options_or_a_dead_server_falls_back_to_local():
    def wrong(url, headers=None, json=None, timeout=None):
        return Resp(answers(device="toaster"))

    def dead(url, headers=None, json=None, timeout=None):
        raise requests.ConnectionError("refused")
    for post in (wrong, dead):
        engine = DecisionEngine(LocalBackend(lambda: DEVICES, lambda: SCENES),
                                LayaBackend("http://127.0.0.1:8000", post=post))
        route = engine.route("lamp on", DEVICES, SCENES)
        assert route["intent"]["backend"] == "local" and route["device"]["value"] == "lamp"
        assert engine.status()["jev_errors"] == 1
