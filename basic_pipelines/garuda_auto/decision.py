"""A decision engine in the "System One" shape: state in, typed answers out.

Most of what a home assistant hears is not a conversation. "Lamp off",
"run study scene", "is the fan on" need a label, a device and an action, not
a paragraph -- and a label with a calibrated confidence is exactly what lets
the house act at once when it is sure and hand over to the language model
when it is not. That is the shape TypeSafe's Jev exposes (typed questions,
each answered with a value, a probability distribution and a confidence), so
the interface here is that shape, with two interchangeable backends:

  LocalBackend  on the Pi, instant, offline. Literal matching over the
                device and scene names; it knows what it does not know and
                says so with a low confidence.
  JevBackend    TypeSafe's hosted Jev (POST /v1/systemone). Used when a
                JEV_API_KEY is configured. Jev is cloud-only and in early
                access, so its wire format here follows the published
                example and every failure falls back to LocalBackend.

Egress: Jev receives the utterance and the device/scene names, never frames,
readings or history -- the same boundary the rule compiler keeps.
"""
import logging
import re
import time

import requests

log = logging.getLogger(__name__)

INTENTS = ("device_control", "all_off", "scene", "timer", "state_query",
           "automation_rule", "mode_change", "explain", "other")
MODES = ("dnd", "night", "idle", "emergency", "privacy", "email_off")

_CONDITIONAL = re.compile(r"\b(when|whenever|if|unless|every time|as soon as|each time)\b", re.I)
_TIMER = re.compile(r"\b(in|after)\s+(\d+|an?|half an?)\s*(sec|second|min|minute|hour|hr)s?\b"
                    r"|\bat\s+\d{1,2}(:\d{2})?\s*(am|pm)?\b|\b(tonight|tomorrow|every day|daily|weekdays)\b", re.I)
_QUESTION = re.compile(r"^\s*(is|are|was|what|how|who|which|did|does|do)\b|\?\s*$", re.I)
_WHY = re.compile(r"\bwhy\b", re.I)
_OFF = re.compile(r"\b(off|stop|kill|shut|disable)\b", re.I)
_ON = re.compile(r"\b(on|start|enable)\b", re.I)
_ALL = re.compile(r"\b(everything|all (the )?(devices|lights|things)|whole house)\b", re.I)
_MODE = re.compile(r"\b(dnd|do not disturb|night mode|idle mode|emergency|privacy|email alerts?)\b", re.I)
_JOIN = re.compile(r"\b(and|then|also|plus)\b|,")
_SCENE = re.compile(r"\b(scene|mode for|routine)\b", re.I)


class Answer(dict):
    """{"value", "probs", "confidence", "backend"}: a dict so it serialises."""

    @property
    def value(self):
        return self["value"]

    @property
    def confidence(self):
        return self["confidence"]


def _answer(value, confidence, options, backend):
    confidence = max(0.0, min(1.0, confidence))
    rest = [o for o in options if o != value]
    probs = {value: round(confidence, 3)}
    for o in rest:
        probs[o] = round((1 - confidence) / max(1, len(rest)), 3)
    return Answer(value=value, probs=probs, confidence=round(confidence, 3), backend=backend)


def routing_questions(devices, scenes):
    """The typed questions asked of every utterance."""
    return {
        "intent": {"type": "choice", "options": list(INTENTS),
                   "criteria": "What does the person want the house to do?"},
        "device": {"type": "choice", "options": [d["id"] for d in devices] + ["none"],
                   "criteria": "Which single device is being controlled, if any?"},
        "action": {"type": "choice", "options": ["on", "off", "none"],
                   "criteria": "Should the device be switched on or off?"},
        "scene": {"type": "choice", "options": [s["id"] for s in scenes] + ["none"],
                  "criteria": "Which saved scene is being run, if any?"},
    }


class LocalBackend:
    name = "local"

    def __init__(self, devices_fn, scenes_fn):
        self._devices = devices_fn
        self._scenes = scenes_fn

    def _find_device(self, lowered):
        best = None
        for d in self._devices():
            for cand in (d["name"].lower(), d["id"].replace("_", " ")):
                if re.search(rf"\b{re.escape(cand)}s?\b", lowered) and (best is None or len(cand) > best[1]):
                    best = (d["id"], len(cand))
        return best[0] if best else None

    def _find_scene(self, lowered):
        best = None
        for s in self._scenes():
            cand = s["name"].lower()
            if cand in lowered and (best is None or len(cand) > best[1]):
                best = (s["id"], len(cand))
        return best[0] if best else None

    def decide(self, state, questions):
        text = str(state.get("utterance", "") if isinstance(state, dict) else state)
        lowered = text.lower()
        device = self._find_device(lowered)
        scene = self._find_scene(lowered)
        wants_off, wants_on = bool(_OFF.search(lowered)), bool(_ON.search(lowered))
        action = "off" if wants_off else "on" if wants_on else "none"
        words = len(lowered.split())

        # Two requests in one sentence ("arm night mode and switch everything
        # off") are for the model: acting on the half a matcher recognised
        # would silently drop the other half.
        cues = sum(bool(x) for x in (device, scene, _ALL.search(lowered), _MODE.search(lowered)))
        compound = cues > 1 or (bool(_JOIN.search(lowered)) and words > 5)

        # Ordered from most to least specific. Each branch states how sure a
        # literal matcher can honestly be.
        if compound and not _CONDITIONAL.search(lowered):
            intent, conf = "other", 0.5
        elif _CONDITIONAL.search(lowered):
            intent, conf = "automation_rule", 0.9
        elif _WHY.search(lowered):
            intent, conf = "explain", 0.85
        elif _TIMER.search(lowered) and (device or scene):
            intent, conf = "timer", 0.8
        elif _QUESTION.search(text):
            intent, conf = "state_query", 0.75 if device or "anyone" in lowered else 0.5
        elif _ALL.search(lowered) and wants_off:
            intent, conf = "all_off", 0.93
        elif scene and (not device or _SCENE.search(lowered)):
            intent, conf = "scene", 0.92 if words <= 6 else 0.8
        elif device and action != "none" and not (wants_on and wants_off):
            # Short imperatives are what a literal matcher is good at. A long
            # sentence that happens to contain "lamp" and "on" is not.
            intent, conf = "device_control", 0.96 if words <= 6 else 0.82 if words <= 10 else 0.6
        elif _MODE.search(lowered) and action != "none":
            intent, conf = "mode_change", 0.8
        else:
            intent, conf = "other", 0.4

        options = {name: q.get("options", []) for name, q in questions.items()}
        out = {}
        if "intent" in questions:
            out["intent"] = _answer(intent, conf, options["intent"], self.name)
        if "device" in questions:
            out["device"] = _answer(device or "none", 0.95 if device else 0.6, options["device"], self.name)
        if "action" in questions:
            out["action"] = _answer(action, 0.95 if action != "none" and not (wants_on and wants_off) else 0.5,
                                    options["action"], self.name)
        if "scene" in questions:
            out["scene"] = _answer(scene or "none", 0.95 if scene else 0.6, options["scene"], self.name)
        return out


class JevBackend:
    """TypeSafe Jev over HTTP. Cloud-only; see the module docstring."""
    name = "jev"

    def __init__(self, api_key="", base_url="https://api.typesafe.ai", timeout=3.0, post=None):
        self.api_key = api_key or ""
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._post = post or requests.post

    @property
    def configured(self):
        return bool(self.api_key)

    def decide(self, state, questions):
        body = {"state": state if isinstance(state, str) else _state_text(state),
                "questions": questions}
        resp = self._post(f"{self.base_url}/v1/systemone",
                          headers={"Authorization": f"Bearer {self.api_key}",
                                   "Content-Type": "application/json"},
                          json=body, timeout=self.timeout)
        resp.raise_for_status()
        payload = resp.json()
        answers = payload.get("answers", payload)
        out = {}
        for name, q in questions.items():
            raw = answers.get(name)
            if not isinstance(raw, dict):
                raise ValueError(f"Jev returned no answer for {name}")
            value = raw.get("choice", raw.get("value"))
            if q.get("type") == "choice" and value not in q.get("options", []):
                raise ValueError(f"Jev answered {name} outside its options")
            probs = raw.get("probabilities") or raw.get("probs") or {}
            if isinstance(probs, list):
                probs = dict(zip(q.get("options", []), probs))
            out[name] = Answer(value=value, probs=probs,
                               confidence=float(raw.get("confidence", 0.0)), backend=self.name)
        return out


# What each intent means, for a backend that reads descriptions (Laya's
# "criteria" are label -> description; Jev's wire format took a bare list).
INTENT_CRITERIA = {
    "device_control": "switch one named device on or off right now",
    "all_off": "switch everything, or every device in a room, off",
    "scene": "run a saved scene by its name",
    "timer": "do something after a delay or at a clock time",
    "state_query": "a question about what is on, off, or who is home",
    "automation_rule": "a standing rule: when, if or whenever something happens, do something",
    "mode_change": "change a security mode such as do not disturb, night, idle or privacy",
    "explain": "asks why something happened",
    "other": "anything else: conversation, general questions, several requests at once",
}


class LayaBackend(JevBackend):
    """Laya (github.com/NandhaKishorM/laya) served on this machine or the LAN.

    `laya-serve` speaks the same POST /v1/systemone as Jev, so this is the Jev
    client with two differences: no key (it is yours, on your network), and
    the questions are sent the way Laya reads them, with a description for
    every option. An experiment, off unless LAYA_URL is set: see
    scripts/laya_experiment.py for what it costs on a Pi and how it scores.
    """
    name = "laya"

    def __init__(self, base_url="", timeout=8.0, post=None):
        super().__init__(api_key="", base_url=base_url or "", timeout=timeout, post=post)

    @property
    def configured(self):
        return bool(self.base_url)

    @staticmethod
    def questions_for_laya(questions, state):
        devices = {d["id"]: f"{d['name']}" + (f" in the {d['room']}" if d.get("room") else "")
                   for d in (state.get("devices") or [])} if isinstance(state, dict) else {}
        scenes = {s["id"]: f"the scene called {s['name']}" for s in (state.get("scenes") or [])} \
            if isinstance(state, dict) else {}
        describe = {
            "intent": lambda o: INTENT_CRITERIA.get(o, o),
            "device": lambda o: devices.get(o, "no single device is named" if o == "none" else o),
            "action": lambda o: {"on": "switch it on, start it", "off": "switch it off, stop it",
                                 "none": "neither on nor off is asked for"}.get(o, o),
            "scene": lambda o: scenes.get(o, "no saved scene is named" if o == "none" else o),
        }
        out = {}
        for name, q in questions.items():
            label = describe.get(name, lambda o: o)
            out[name] = {"type": "choice", "instructions": q.get("criteria", name),
                         "criteria": {o: label(o) for o in q.get("options", [])}}
        return out

    def decide(self, state, questions):
        text = state.get("utterance", "") if isinstance(state, dict) else str(state)
        resp = self._post(f"{self.base_url}/v1/systemone",
                          headers={"Content-Type": "application/json"},
                          json={"state": {"body": text},
                                "questions": self.questions_for_laya(questions, state)},
                          timeout=self.timeout)
        resp.raise_for_status()
        payload = resp.json()
        answers = payload.get("answers", payload)
        out = {}
        for name, q in questions.items():
            raw = answers.get(name)
            if not isinstance(raw, dict):
                raise ValueError(f"Laya returned no answer for {name}")
            value = raw.get("choice", raw.get("value"))
            if value not in q.get("options", []):
                raise ValueError(f"Laya answered {name} outside its options")
            probs = raw.get("probabilities") or raw.get("probs") or {}
            if isinstance(probs, list):
                probs = dict(zip(q.get("options", []), probs))
            confidence = raw.get("confidence")
            if confidence is None and isinstance(probs, dict):
                confidence = probs.get(value, 0.0)
            out[name] = Answer(value=value, probs=probs, confidence=float(confidence or 0.0),
                               backend=self.name)
        return out


def _state_text(state):
    parts = [f"Utterance: {state.get('utterance', '')}"]
    if state.get("devices"):
        parts.append("Devices: " + ", ".join(f"{d['id']} ({d['name']}, {d.get('room', '')})"
                                             for d in state["devices"]))
    if state.get("scenes"):
        parts.append("Scenes: " + ", ".join(f"{s['id']} ({s['name']})" for s in state["scenes"]))
    return "\n".join(parts)


class DecisionEngine:
    def __init__(self, local, jev=None, threshold=0.85, clock=time.monotonic):
        self.local = local
        self.jev = jev
        self.threshold = threshold
        self._clock = clock
        self.stats = {"local": 0, "jev": 0, "jev_errors": 0, "last_backend": "",
                      "last_latency_ms": None, "last_error": ""}

    def decide(self, state, questions):
        started = self._clock()
        if self.jev is not None and self.jev.configured:
            try:
                out = self.jev.decide(state, questions)
                self._note("jev", started)
                return out
            except Exception as exc:
                self.stats["jev_errors"] += 1
                self.stats["last_error"] = f"{type(exc).__name__}: {exc}"[:160]
                log.warning("Jev failed, using local backend: %s", exc)
        out = self.local.decide(state, questions)
        self._note("local", started)
        return out

    def _note(self, backend, started):
        self.stats[backend] += 1
        self.stats["last_backend"] = backend
        self.stats["last_latency_ms"] = round((self._clock() - started) * 1000, 1)

    def route(self, utterance, devices, scenes):
        state = {"utterance": utterance, "devices": devices, "scenes": scenes}
        return self.decide(state, routing_questions(devices, scenes))

    def status(self):
        return {"threshold": self.threshold,
                "jev_configured": bool(self.jev and self.jev.configured),
                **self.stats}
