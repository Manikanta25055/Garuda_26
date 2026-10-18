"""A second look at a weapon detection, before the alert email goes out.

The camera's own model (YOLO on the Hailo, 30-50 frames a second) decides
that something is a knife, scissors or a hammer; it is fast and on the Pi, and
it also fires on a laptop hinge in the dark or a blurred close-up (the three
alert clips kept on 2026-10-18 were all of that kind). On the rising edge of
an alert this module sends that one frame, and a close-up of the box, to
TypeSafe's Jev with a few typed questions: is there really one, is a person
holding it, is it on a screen or in a photo, what is it.

What it may change is only the email (Garuda_web.send_email_alert): the alarm
on the screen and the log are as before.
  confirmed   the email goes out and says the second look agreed
  harmless    the email is held; the event is logged with Jev's reading
  unclear     the email goes out as before, saying it was not confirmed
  unchecked   Jev was not configured, too slow or failed: as before

So a cloud failure never silences an alert (owner's rule, 2026-10-18), and
nothing leaves the Pi except the frame of a detection (owner's decision).

At the same moment an NVIDIA vision model (nemotron-3.5-super-vl, about 1 s)
writes one sentence about the scene for the email. It only describes; Jev's
typed answers decide. (cosmos-reason2-8b is listed for the account but not
callable, 2026-10-18.)
"""
import base64
import concurrent.futures
import logging
import time

import requests

import cv2
import numpy as np

log = logging.getLogger(__name__)

MIN_SIDE, MAX_SIDE = 280, 1568          # what Jev accepts (docs.typesafe.ai/images)
HARMLESS_BELOW = 0.15                   # P(weapon) under this, and Jev names no weapon: hold the email
CONFIRMED_AT = 0.6
WEAPONS = ("knife", "scissors", "hammer", "other_blade_or_tool")

QUESTIONS = {
    "weapon": {"type": "noul",
               "instructions": "Is there a real knife, blade, pair of scissors or hammer in `frame`? "
                               "`close_up` is the part of `frame` the camera's detector pointed at; "
                               "look at both.",
               "criteria": {"true": "One of those is clearly physically there, whoever holds it.",
                            "false": "None is there: what the detector saw is something else (a "
                                     "laptop edge, a phone, a pen, a shadow, furniture) or the "
                                     "picture is too dark or blurred to show one."}},
    "held": {"type": "noul",
             "instructions": "Is a person holding a knife, blade, scissors or hammer in `frame`?"},
    "screen": {"type": "noul",
               "instructions": "Is the object in `close_up` shown on a screen, poster or printed "
                               "photo, rather than being physically in the room?"},
    "what": {"type": "choice",
             "instructions": "What is the object the detector found, in `close_up`?",
             "criteria": {"knife": "A knife or other bladed weapon.",
                          "scissors": "A pair of scissors.",
                          "hammer": "A hammer.",
                          "other_blade_or_tool": "Another sharp or heavy tool that could hurt someone.",
                          "harmless_object": "Something harmless: a phone, pen, remote, cup, laptop, "
                                             "cable, furniture or part of the room.",
                          "nothing_clear": "Too dark, blurred or covered to tell."}},
}


def _fit(img):
    """Within Jev's 280-1568 px per side, upright RGB -> JPEG bytes (baseline)."""
    h, w = img.shape[:2]
    scale = 1.0
    if max(h, w) > MAX_SIDE:
        scale = MAX_SIDE / max(h, w)
    if min(h, w) * scale < MIN_SIDE:
        scale = MIN_SIDE / min(h, w)
    if scale != 1.0:
        img = cv2.resize(img, (max(MIN_SIDE, round(w * scale)), max(MIN_SIDE, round(h * scale))),
                         interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
    h, w = img.shape[:2]
    if max(h, w) > MAX_SIDE:                    # a very thin crop, scaled up past the cap
        img = img[:MAX_SIDE, :MAX_SIDE]
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise ValueError("could not encode the frame")
    return buf.tobytes()


def close_up(frame, bbox, pad=0.5):
    """The detector's box (fractions xmin, ymin, xmax, ymax) with `pad` of its size around it."""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = bbox
    bw, bh = (x2 - x1) * pad, (y2 - y1) * pad
    x1, x2 = max(0, int((x1 - bw) * w)), min(w, int((x2 + bw) * w))
    y1, y2 = max(0, int((y1 - bh) * h)), min(h, int((y2 + bh) * h))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return frame
    return frame[y1:y2, x1:x2]


def verdict(answers):
    """confirmed | harmless | unclear, from Jev's answers."""
    p = float((answers.get("weapon") or {}).get("noul", 0.5))
    screen = float((answers.get("screen") or {}).get("noul", 0.0))
    what = (answers.get("what") or {}).get("choice")
    if p >= CONFIRMED_AT and screen < 0.5 and what in WEAPONS:
        return "confirmed"
    if p < HARMLESS_BELOW and what not in WEAPONS:
        return "harmless"
    return "unclear"


DESCRIBE_PROMPT = ("This is one frame from a home security camera that flagged a possible weapon. "
                   "In one plain sentence, say what is happening and what the person is holding, if "
                   "anyone. If nothing dangerous is visible, say so.")


def nim_describer(api_key, models=("nvidia/nemotron-3.5-super-vl-120b-a12b",
                                   "meta/llama-3.2-11b-vision-instruct"),
                  base_url="https://integrate.api.nvidia.com/v1", timeout=4.0, post=None):
    """jpeg bytes -> one sentence about the scene, from the first NIM vision model that answers."""
    post = post or requests.post

    def describe(jpeg):
        url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
        for model in models:
            try:
                r = post(f"{base_url}/chat/completions", timeout=timeout,
                         headers={"Authorization": f"Bearer {api_key}"},
                         json={"model": model, "max_tokens": 120, "temperature": 0,
                               "chat_template_kwargs": {"enable_thinking": False},
                               "messages": [{"role": "user", "content": [
                                   {"type": "text", "text": DESCRIBE_PROMPT},
                                   {"type": "image_url", "image_url": {"url": url}}]}]})
                r.raise_for_status()
                text = " ".join((r.json()["choices"][0]["message"].get("content") or "").split())
                if text:
                    return text[:300]
            except Exception as exc:
                log.warning("scene description by %s failed: %s", model, exc)
        return ""
    return describe if api_key else None


class VisionCheck:
    def __init__(self, jev, *, model="jev-preview", timeout=4.0, clock=time.monotonic, describe=None):
        self.jev = jev
        self.model = model
        self.timeout = timeout
        self._clock = clock
        self.describe = describe        # jpeg -> sentence (NVIDIA), for the email only
        self._pool = concurrent.futures.ThreadPoolExecutor(2, thread_name_prefix="vision")
        self.stats = {"checked": 0, "confirmed": 0, "harmless": 0, "unclear": 0, "unchecked": 0,
                      "last_ms": None, "last_error": ""}

    @property
    def configured(self):
        return self.jev is not None and self.jev.configured

    def check(self, frame, bbox=None, label=""):
        """frame: RGB array. Returns {"verdict", "p_weapon", "held", "screen", "what", "ms"}."""
        if not self.configured or frame is None:
            self.stats["unchecked"] += 1
            return {"verdict": "unchecked", "why": "not configured" if frame is not None else "no frame"}
        started = self._clock()
        try:
            frame = np.ascontiguousarray(frame[..., :3])
            crop = close_up(frame, bbox) if bbox else frame
            whole = _fit(frame)
            scene = self._pool.submit(self.describe, whole) if self.describe else None
            assets = {ref: {"type": "image", "format": "jpeg",
                            "data_base64": base64.b64encode(jpeg).decode("ascii")}
                      for ref, jpeg in (("img-frame", whole), ("img-close", _fit(crop)))}
            answers = self.jev.ask({"frame": "img-frame", "close_up": "img-close",
                                    "detector_said": label or "a weapon"},
                                   QUESTIONS, assets=assets, model=self.model, timeout=self.timeout)
        except Exception as exc:
            self.stats["unchecked"] += 1
            self.stats["last_error"] = f"{type(exc).__name__}: {exc}"[:160]
            log.warning("vision check failed: %s", exc)
            return {"verdict": "unchecked", "why": f"{type(exc).__name__}"}
        ms = round((self._clock() - started) * 1000)
        out = {"verdict": verdict(answers),
               "p_weapon": round(float((answers.get("weapon") or {}).get("noul", 0)), 3),
               "held": round(float((answers.get("held") or {}).get("noul", 0)), 3),
               "screen": round(float((answers.get("screen") or {}).get("noul", 0)), 3),
               "what": (answers.get("what") or {}).get("choice"), "ms": ms}
        if scene is not None:
            try:
                out["scene"] = scene.result(timeout=max(0.5, self.timeout - ms / 1000)) or ""
            except Exception:
                out["scene"] = ""
        self.stats["checked"] += 1
        self.stats[out["verdict"]] += 1
        self.stats["last_ms"] = ms
        return out


def say(result):
    """One line for the email and the log."""
    result = result or {}
    v = result.get("verdict", "unchecked")
    if v == "unchecked":
        return "Second look: not done (" + result.get("why", "unavailable") + ")."
    tail = (f"{result.get('what')}, P(weapon) {result.get('p_weapon')}, person holding it "
            f"{result.get('held')}, on a screen {result.get('screen')}")
    line = {"confirmed": "Second look (Jev): confirmed: ", "harmless": "Second look (Jev): looks harmless: ",
            "unclear": "Second look (Jev): not confirmed: "}[v] + tail + "."
    if result.get("scene"):
        line += f" Scene (NVIDIA): {result['scene']}"
    return line
