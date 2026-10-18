"""A decision engine in the "System One" shape: state in, typed answers out.

Most of what a home assistant hears is not a conversation. "Lamp off",
"run study scene", "is the fan on" need a label, a device and an action, not
a paragraph -- and a label with a calibrated confidence is exactly what lets
the house act at once when it is sure and hand over to the language model
when it is not. That is the shape TypeSafe's Jev exposes (typed questions,
each answered with a value, a probability distribution and a confidence), so
the interface here is that shape, with three interchangeable backends:

  LocalBackend   on the Pi, instant, offline. Literal matching over the
                 device and scene names; it knows what it does not know and
                 says so with a low confidence.
  RouterBackend  on the Pi, offline (router.py). A small encoder trained for
                 exactly these questions, so it also reads a sentence that
                 names nothing ("it's too dark in the study") and Telugu or
                 Hindi. Used when its model file and onnxruntime are present.
  JevBackend     TypeSafe's hosted Jev (POST /v1/systemone). Used when a
                 JEV_API_KEY is configured. Jev is cloud-only and in early
                 access, so its wire format here follows the published
                 example.

The router answers (the matcher when the router is missing or fails); Jev,
when configured, answers the same questions in parallel as a second opinion
(Route.jev), and agent.py acts at once only when the two agree.

Egress: Jev receives the utterance and the device/scene names, never frames,
readings or history -- the same boundary the rule compiler keeps.
"""
import logging
import re
import time

import requests

log = logging.getLogger(__name__)

# "build" is work for the planner (agent.py): something to be made, steps that
# depend on each other, or the upkeep of the house. Only the routing model
# gives that answer; the literal matcher has no way to tell.
INTENTS = ("device_control", "all_off", "scene", "timer", "state_query",
           "automation_rule", "mode_change", "explain", "other", "build")
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


# What each routing answer means, in words Jev reads (decision questions are
# graded on these descriptions, not on the option names).
INTENT_MEANING = {
    "device_control": "Switch exactly one device on or off, now. A device meant but not named "
                      "(\"it's too dark in here\") counts. Not when the person says not to touch it.",
    "all_off": "Switch everything off now: the whole house, or everything in one room.",
    "scene": "Run one of the saved scenes now.",
    "timer": "Switch a device or run a scene later: at a clock time or after a delay.",
    "state_query": "A question about how the house is right now (is something on, what is running).",
    "automation_rule": "A standing rule: when, whenever or if something happens, do something.",
    "mode_change": "Turn a security mode on or off: do not disturb, night, idle, emergency, "
                   "privacy, email alerts.",
    "explain": "Asks why something happened, or for the reason behind the house's behaviour.",
    "build": "Asks to make something: a shortcut, routine, button, dashboard, page or a program "
             "of several steps.",
    "other": "Anything else: conversation, a general question, a request that says what NOT to do "
             "or to leave something as it is, or more than one action in one sentence.",
}


class JevBackend:
    """TypeSafe Jev over HTTP (docs.typesafe.ai/api). Cloud-only; see the module docstring.

    Our questions are {"type": "choice", "options": [...], "criteria": "the
    question"}; Jev's are {"type": "choice", "instructions": ..., "criteria":
    {option: what it means}}. This class translates, and `extra` adds Jev's own
    questions (noul, score) to the same call, so one round trip answers both.
    """
    name = "jev"

    def __init__(self, api_key="", base_url="https://api.typesafe.ai", timeout=2.5, post=None,
                 model="jev-latest"):
        self.api_key = api_key or ""
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.model = model or "jev-latest"
        self._post = post or requests.post

    @property
    def configured(self):
        return bool(self.api_key)

    @staticmethod
    def _meaning(name, option, state):
        if name == "intent":
            return INTENT_MEANING.get(option)
        if option == "none":
            return {"device": "No device, or more than one.", "scene": "No saved scene.",
                    "action": "Neither on nor off."}.get(name)
        if name == "device":
            d = next((d for d in state.get("devices", ()) if d.get("id") == option), None)
            if d:
                return d.get("name", option) + (f", in the {d['room']}" if d.get("room") else "")
        if name == "scene":
            sc = next((x for x in state.get("scenes", ()) if x.get("id") == option), None)
            if sc:
                return f"The scene called {sc.get('name', option)}"
        return None

    def _question(self, name, q, state):
        if q.get("type") != "choice":
            return q                                    # already in Jev's shape
        return {"type": "choice",
                "instructions": f"{q.get('criteria', '')} The person said `utterance`.".strip(),
                "criteria": {o: self._meaning(name, o, state) for o in q.get("options", [])}}

    def ask(self, state, questions, *, assets=None, model=None, timeout=None):
        """One call: Jev's raw answers keyed like `questions` (already in Jev's shape).
        `assets` ({ref: {"type": "image", "format": "jpeg", "data_base64": ...}})
        turns on images for the call (docs.typesafe.ai/images)."""
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        body = {"model": model or self.model, "state": state, "questions": questions}
        if assets:
            headers["TypeSafe-Danger-Zone-Images"] = "true"
            body["assets"] = assets
        resp = self._post(f"{self.base_url}/v1/systemone", headers=headers, json=body,
                          timeout=timeout or self.timeout)
        resp.raise_for_status()
        payload = resp.json()
        return payload.get("answers", payload)

    def lasting(self, facts):
        """{fact: P(it stays true about the person or household after today)}, one call."""
        if not facts:
            return {}
        questions = {f"f{i}": {
            "type": "noul",
            "instructions": f"Does `facts.f{i}` state something about a person or the household "
                            "that stays true after today, so it is worth remembering for later "
                            "conversations?",
            "criteria": {"true": "A lasting fact: a routine, habit, preference, relationship, "
                                 "allergy, schedule that repeats, or what something in the house is for.",
                         "false": "Only about this moment or today: what they are doing now, a "
                                  "one-off plan, a feeling right now, or a request."}}
            for i, _ in enumerate(facts)}
        answers = self.ask({"facts": {f"f{i}": fact for i, fact in enumerate(facts)}}, questions)
        return {fact: float((answers.get(f"f{i}") or {}).get("noul"))
                for i, fact in enumerate(facts)
                if isinstance((answers.get(f"f{i}") or {}).get("noul"), (int, float))}

    def decide(self, state, questions, extra=None):
        st = state if isinstance(state, dict) else {"utterance": str(state)}
        asked = {n: self._question(n, q, st) for n, q in questions.items()}
        asked.update(extra or {})
        answers = self.ask({"utterance": st.get("utterance", "")}, asked)
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
        for name in extra or {}:
            if isinstance(answers.get(name), dict):
                out[name] = answers[name]
        return out

def _state_text(state):
    parts = [f"Utterance: {state.get('utterance', '')}"]
    if state.get("devices"):
        parts.append("Devices: " + ", ".join(f"{d['id']} ({d['name']}, {d.get('room', '')})"
                                             for d in state["devices"]))
    if state.get("scenes"):
        parts.append("Scenes: " + ", ".join(f"{s['id']} ({s['name']})" for s in state["scenes"]))
    return "\n".join(parts)


class Route(dict):
    """The routing answers, field -> Answer, from the router or the matcher.
    `jev` holds Jev's answers to the same questions (and to any extra ones)
    when Jev answered in time; {} otherwise."""
    jev = {}


class DecisionEngine:
    """The router (or the matcher) answers; Jev, when configured, answers the
    same questions at the same moment and its answers ride along as a second
    opinion (Route.jev). Code decides what agreement means (agent.py's fast
    lane): Jev routes worse than the router on its own (2026-10-18: 369 vs
    636 of 717 written sentences) but when both are sure and agree, they were
    wrong 0 times in 767."""

    def __init__(self, local, jev=None, threshold=0.85, clock=time.monotonic, router=None):
        self.local = local
        self.jev = jev
        self.router = router
        self.threshold = threshold
        self._clock = clock
        self.stats = {"local": 0, "router": 0, "jev": 0, "jev_errors": 0, "router_errors": 0,
                      "last_backend": "", "last_latency_ms": None, "last_error": "",
                      "jev_latency_ms": None}
        self._pool = None

    def decide(self, state, questions):
        """The router's answers, or the matcher's when the router is missing or fails."""
        started = self._clock()
        if self.router is not None and self.router.configured:
            try:
                out = self.router.decide(state, questions)
                self._note("router", started)
                return out
            except Exception as exc:
                self.stats["router_errors"] += 1
                self.stats["last_error"] = f"{type(exc).__name__}: {exc}"[:160]
                log.warning("router failed, trying the matcher: %s", exc)
        out = self.local.decide(state, questions)
        self._note("local", started)
        return out

    def _ask_jev(self, state, questions, extra):
        started = self._clock()
        try:
            out = self.jev.decide(state, questions, extra=extra)
        except Exception as exc:
            self.stats["jev_errors"] += 1
            self.stats["last_error"] = f"jev: {type(exc).__name__}: {exc}"[:160]
            log.warning("jev failed: %s", exc)
            return {}
        self.stats["jev"] += 1
        self.stats["jev_latency_ms"] = round((self._clock() - started) * 1000, 1)
        return out

    def _note(self, backend, started):
        self.stats[backend] += 1
        self.stats["last_backend"] = backend
        self.stats["last_latency_ms"] = round((self._clock() - started) * 1000, 1)

    def route(self, utterance, devices, scenes, extra=None):
        """`extra`: more questions for Jev, in its own shape, asked in the same call."""
        state = {"utterance": utterance, "devices": devices, "scenes": scenes}
        questions = routing_questions(devices, scenes)
        pending = None
        if self.jev is not None and self.jev.configured:
            if self._pool is None:
                import concurrent.futures
                self._pool = concurrent.futures.ThreadPoolExecutor(2, thread_name_prefix="jev")
            pending = self._pool.submit(self._ask_jev, state, questions, extra)
        route = Route(self.decide(state, questions))
        if pending is not None:
            try:
                route.jev = pending.result(timeout=getattr(self.jev, "timeout", 2.5) + 0.5)
            except Exception:
                route.jev = {}
        return route

    def status(self):
        return {"threshold": self.threshold,
                "jev_configured": bool(self.jev and self.jev.configured),
                "jev_model": getattr(self.jev, "model", ""),
                "router_configured": bool(self.router and self.router.configured),
                "router_error": getattr(self.router, "error", ""),
                **self.stats}
