"""Narada's routing model: a small encoder, trained for this one job, on the Pi.

The literal matcher in decision.py knows a sentence only by the words in it:
"lamp on" it gets, "it's too dark in the study" it cannot. This is the same
decision made by a model that has read the house. It is given the sentence
and, after it, every device and scene the house has:

    [CLS] it's too dark in the study [SEP] device: Lamp, study [SEP] device: Fan, bedroom
          [SEP] scene: Movie night [SEP]

and answers all four routing questions in one pass. What is wanted and
whether it is on or off are read from the first position. Which device and
which scene are chosen by pointing: every [SEP] that opens a candidate gets a
score, the first position stands for "none of them", and the highest wins.
Pointing is what lets one model serve any house: it never learns a device, it
learns to pick one out of whatever list it is shown.

The model is a file (resources/models/narada_router/) made by
scripts/router_train.py and run with onnxruntime, with no torch and nothing
sent off the Pi. With the files or onnxruntime missing, `configured` is False
and the decision engine carries on with the literal matcher.
"""
import json
import logging
import os

import numpy as np

log = logging.getLogger(__name__)

# How much of each thing the model is shown. A sentence longer than this is
# cut, and a house with more than this many devices has the rest left out
# (they can then only be reached through the language model).
MAX_SAY_TOKENS = 48
MAX_NAME_TOKENS = 12
MAX_DEVICES = 24
MAX_SCENES = 12

FILES = ("model.onnx", "tokenizer.json", "meta.json")


def describe_device(device):
    room = (device.get("room") or "").strip()
    return f"device: {device['name']}" + (f", {room}" if room else "")


def describe_scene(scene):
    return f"scene: {scene['name']}"


def encode(tokenizer, cls_id, sep_id, say, devices, scenes, cache=None):
    """Token ids for one sentence in one house, and where each candidate's marker sits.

    Returns (ids, device_positions, scene_positions). The trainer and the Pi
    both come through here, so they cannot disagree about what the model sees.
    """
    def pieces(text, limit):
        if cache is not None and text in cache:
            return cache[text]
        ids = tokenizer.encode(text, add_special_tokens=False).ids[:limit]
        if cache is not None:
            cache[text] = ids
        return ids

    ids = [cls_id] + tokenizer.encode(say, add_special_tokens=False).ids[:MAX_SAY_TOKENS]
    device_positions, scene_positions = [], []
    for device in devices[:MAX_DEVICES]:
        device_positions.append(len(ids))
        ids.append(sep_id)
        ids.extend(pieces(describe_device(device), MAX_NAME_TOKENS))
    for scene in scenes[:MAX_SCENES]:
        scene_positions.append(len(ids))
        ids.append(sep_id)
        ids.extend(pieces(describe_scene(scene), MAX_NAME_TOKENS))
    ids.append(sep_id)
    return ids, device_positions, scene_positions


def _softmax(scores, temperature):
    scaled = np.asarray(scores, dtype=np.float64) / max(temperature, 1e-3)
    scaled -= scaled.max()
    exp = np.exp(scaled)
    return exp / exp.sum()


class RouterModel:
    """The files loaded: `answer(say, devices, scenes)` is one pass of the model."""

    def __init__(self, directory, threads=2):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        with open(os.path.join(directory, "meta.json")) as f:
            self.meta = json.load(f)
        self.tokenizer = Tokenizer.from_file(os.path.join(directory, "tokenizer.json"))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        options = ort.SessionOptions()
        # Two of the Pi's four cores: the camera pipeline keeps the others.
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(os.path.join(directory, "model.onnx"), options,
                                            providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self.session.get_inputs()}
        self._names = {}

    def answer(self, say, devices, scenes):
        """{"intent": (value, {option: probability}), "device": ..., "action": ..., "scene": ...}"""
        meta = self.meta
        ids, device_at, scene_at = encode(self.tokenizer, meta["cls_id"], meta["sep_id"], say, devices, scenes,
                                          cache=self._names)
        if len(self._names) > 512:                 # names change rarely; do not let renames pile up
            self._names.clear()
        feed = {"input_ids": np.asarray([ids], dtype=np.int64),
                "attention_mask": np.ones((1, len(ids)), dtype=np.int64)}
        intent, action, device, scene = self.session.run(
            None, {k: v for k, v in feed.items() if k in self._inputs})
        heat = meta.get("temperature", {})
        out = {}
        for name, scores, labels in (("intent", intent[0], meta["intents"]),
                                     ("action", action[0], meta["actions"])):
            probs = _softmax(scores, heat.get(name, 1.0))
            out[name] = (labels[int(probs.argmax())], dict(zip(labels, probs.tolist())))
        for name, scores, at, things in (("device", device[0], device_at, devices),
                                         ("scene", scene[0], scene_at, scenes)):
            # Position 0 is "none"; then one marker for each candidate shown.
            probs = _softmax(scores[[0] + at], heat.get(name, 1.0))
            labels = ["none"] + [t["id"] for t in things[:len(at)]]
            out[name] = (labels[int(probs.argmax())], dict(zip(labels, probs.tolist())))
        return out


def default_directory():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "..", "..", "resources", "models", "narada_router")


class RouterBackend:
    """The routing model as a decision backend (see decision.DecisionEngine)."""
    name = "router"

    def __init__(self, directory=None, model=None):
        self.directory = os.path.normpath(directory or default_directory())
        self.model = model
        self.error = ""
        if model is None:
            self._load()

    def _load(self):
        missing = [f for f in FILES if not os.path.isfile(os.path.join(self.directory, f))]
        if missing:
            self.error = f"missing {', '.join(missing)} in {self.directory}"
            return
        try:
            # A git-lfs pointer left unfetched is a small text file, not a model.
            if os.path.getsize(os.path.join(self.directory, "model.onnx")) < 100_000:
                raise ValueError("model.onnx is a git-lfs pointer: run `git lfs pull`")
            self.model = RouterModel(self.directory)
            self.model.answer("hello", [], [])     # the first pass is the slow one: pay for it at start-up
        except Exception as exc:                   # no onnxruntime, a damaged file: the matcher carries on
            self.model = None
            self.error = f"{type(exc).__name__}: {exc}"[:200]
            log.warning("routing model not loaded, using the literal matcher: %s", self.error)

    @property
    def configured(self):
        return self.model is not None

    def decide(self, state, questions):
        from .decision import Answer

        if not isinstance(state, dict):
            raise ValueError("the routing model needs the house, not only the sentence")
        answers = self.model.answer(str(state.get("utterance", "")), state.get("devices") or [],
                                    state.get("scenes") or [])
        out = {}
        for name, question in questions.items():
            value, probs = answers[name]
            options = question.get("options", [])
            if value not in options:
                raise ValueError(f"the routing model answered {name} outside its options")
            out[name] = Answer(value=value, probs={o: round(probs.get(o, 0.0), 3) for o in options},
                               confidence=round(probs[value], 3), backend=self.name)
        return out
