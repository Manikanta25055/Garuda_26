"""The routing model's backend: what the model is shown, how its answer is read, and falling back."""
import json
from pathlib import Path

import pytest

from basic_pipelines.garuda_auto import router
from basic_pipelines.garuda_auto.decision import DecisionEngine, JevBackend, LocalBackend, routing_questions
from basic_pipelines.garuda_auto.router import RouterBackend

DEVICES = [{"id": "lamp", "name": "Lamp", "room": "study"}, {"id": "fan", "name": "Fan", "room": ""}]
SCENES = [{"id": "movie", "name": "Movie night"}]
EVAL = Path(__file__).resolve().parent.parent / "eval"


class Words:
    """A tokenizer of whole words: a word's id is its length, so a test can read the ids."""

    class Encoding:
        def __init__(self, ids):
            self.ids = ids

    def encode(self, text, add_special_tokens=False):
        return self.Encoding([len(w) for w in text.split()])


class Fake:
    """A model that answers what it is told to."""

    def __init__(self, **answers):
        self.answers = {"intent": ("device_control", {"device_control": 0.93, "other": 0.07}),
                        "device": ("lamp", {"lamp": 0.9, "fan": 0.04, "none": 0.06}),
                        "action": ("on", {"on": 0.97, "off": 0.02, "none": 0.01}),
                        "scene": ("none", {"none": 0.99, "movie": 0.01}), **answers}
        self.asked = None

    def answer(self, say, devices, scenes):
        self.asked = (say, devices, scenes)
        return self.answers


def test_the_sentence_comes_first_and_every_candidate_is_opened_by_a_marker():
    ids, device_at, scene_at = router.encode(Words(), 101, 102, "too dark in here", DEVICES, SCENES)
    assert ids[0] == 101 and ids[1:5] == [3, 4, 2, 4]                 # [CLS], then the sentence
    assert device_at == [5, 9] and scene_at == [12]
    assert [ids[i] for i in device_at + scene_at] == [102, 102, 102] and ids[-1] == 102
    assert ids[6:9] == [7, 5, 5]                                      # "device: Lamp, study"
    assert ids[10:12] == [7, 3]                                       # "device: Fan": no room, no comma


def test_a_long_sentence_and_a_big_house_are_cut_to_what_the_model_is_shown():
    many = [{"id": f"d{i}", "name": f"Relay {i}", "room": ""} for i in range(40)]
    ids, device_at, scene_at = router.encode(Words(), 1, 2, "word " * 200, many, [])
    assert len(device_at) == router.MAX_DEVICES and scene_at == []
    assert device_at[0] == 1 + router.MAX_SAY_TOKENS


def test_answers_carry_a_probability_for_every_option_and_the_backends_name():
    fake = Fake()
    backend = RouterBackend(model=fake)
    state = {"utterance": "it is dark in the study", "devices": DEVICES, "scenes": SCENES}
    out = backend.decide(state, routing_questions(DEVICES, SCENES))
    assert fake.asked == ("it is dark in the study", DEVICES, SCENES)
    assert out["device"]["value"] == "lamp" and out["device"]["confidence"] == 0.9
    assert out["device"]["probs"] == {"lamp": 0.9, "fan": 0.04, "none": 0.06}
    assert out["intent"]["backend"] == "router" and out["intent"]["probs"]["scene"] == 0.0


def test_absent_files_leave_it_unconfigured_and_say_why(tmp_path):
    backend = RouterBackend(str(tmp_path))
    assert backend.configured is False and "model.onnx" in backend.error


def test_an_unfetched_lfs_pointer_is_not_mistaken_for_a_model(tmp_path):
    for name in router.FILES:
        (tmp_path / name).write_text("version https://git-lfs.github.com/spec/v1\n")
    backend = RouterBackend(str(tmp_path))
    assert backend.configured is False and "git lfs pull" in backend.error


def engine(router_backend, jev=None):
    return DecisionEngine(LocalBackend(lambda: DEVICES, lambda: SCENES), jev, router=router_backend)


def test_the_engine_asks_the_router_before_the_matcher_and_counts_it():
    eng = engine(RouterBackend(model=Fake()))
    route = eng.route("it is dark in the study", DEVICES, SCENES)
    assert route["intent"]["backend"] == "router" and route["device"]["value"] == "lamp"
    status = eng.status()
    assert status["router"] == 1 and status["router_configured"] is True and status["last_backend"] == "router"


def test_an_answer_outside_the_options_or_a_broken_model_falls_back_to_the_matcher():
    class Broken:
        def answer(self, *a):
            raise RuntimeError("bad file")
    for model in (Fake(device=("toaster", {"toaster": 1.0})), Broken()):
        eng = engine(RouterBackend(model=model))
        route = eng.route("lamp on", DEVICES, SCENES)
        assert route["intent"]["backend"] == "local" and route["device"]["value"] == "lamp"
        assert eng.status()["router_errors"] == 1


def test_without_a_model_the_matcher_answers_and_nothing_is_counted_as_an_error(tmp_path):
    eng = engine(RouterBackend(str(tmp_path)))
    assert eng.route("lamp on", DEVICES, SCENES)["intent"]["backend"] == "local"
    assert eng.status()["router_errors"] == 0 and eng.status()["router_configured"] is False


def test_a_configured_jev_is_asked_first():
    def post(url, headers=None, json=None, timeout=None):
        class R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"answers": {"intent": {"choice": "other", "confidence": 0.8},
                                    "device": {"choice": "none", "confidence": 0.8},
                                    "action": {"choice": "none", "confidence": 0.8},
                                    "scene": {"choice": "none", "confidence": 0.8}}}
        return R()
    eng = engine(RouterBackend(model=Fake()), JevBackend("key", post=post))
    assert eng.route("lamp on", DEVICES, SCENES)["intent"]["backend"] == "jev"


# ── the model that ships ─────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def shipped():
    pytest.importorskip("onnxruntime")
    backend = RouterBackend()
    if not backend.configured:
        pytest.skip(f"the routing model is not here: {backend.error}")
    return backend


def whole_decisions_right(backend, cases):
    right = 0
    for case in cases:
        state = {"utterance": case["say"], "devices": case["devices"], "scenes": case["scenes"]}
        out = backend.decide(state, routing_questions(case["devices"], case["scenes"]))
        right += all(case[f] is None or out[f]["value"] == case[f] for f in ("intent", "device", "action", "scene"))
    return right


@pytest.mark.slow
def test_the_shipped_model_keeps_its_score_on_the_hand_written_set(shipped):
    data = json.loads((EVAL / "routing_cases.json").read_text())
    cases = [{"devices": data["devices"], "scenes": data["scenes"], **c} for c in data["cases"]]
    floor = shipped.model.meta["floor"]["routing_cases"]
    assert whole_decisions_right(shipped, cases) >= floor


@pytest.mark.slow
def test_the_shipped_model_reads_a_house_it_was_not_trained_on(shipped):
    house = [{"id": "x1", "name": "Aquarium light", "room": "lobby"},
             {"id": "x2", "name": "Workshop blower", "room": "shed"}]
    state = {"utterance": "turn off the workshop blower", "devices": house, "scenes": []}
    out = shipped.decide(state, routing_questions(house, []))
    assert (out["intent"]["value"], out["device"]["value"], out["action"]["value"]) == ("device_control", "x2", "off")
