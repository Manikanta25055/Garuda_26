"""The second look at a weapon detection: what is sent to Jev, how its answers read, what the email does."""
import base64

import cv2
import numpy as np
import pytest

from basic_pipelines.garuda_auto import vision_check as vc


class FakeJev:
    configured = True

    def __init__(self, answers=None, error=None):
        self.answers, self.error, self.sent = answers or {}, error, None

    def ask(self, state, questions, *, assets=None, model=None, timeout=None):
        self.sent = {"state": state, "questions": questions, "assets": assets, "model": model}
        if self.error:
            raise self.error
        return self.answers


def answers(p, what, screen=0.1, held=0.5):
    return {"weapon": {"type": "noul", "noul": p}, "screen": {"type": "noul", "noul": screen},
            "held": {"type": "noul", "noul": held}, "what": {"type": "choice", "choice": what}}


FRAME = np.zeros((640, 640, 3), np.uint8)


@pytest.mark.parametrize("got, verdict", [
    (answers(0.97, "knife"), "confirmed"),
    (answers(0.97, "knife", screen=0.8), "unclear"),          # a knife on a screen or poster
    (answers(0.08, "nothing_clear"), "harmless"),
    (answers(0.11, "knife"), "unclear"),                      # low, but Jev still names a weapon
    (answers(0.3, "harmless_object"), "unclear"),
    ({}, "unclear"),
])
def test_how_answers_read(got, verdict):
    assert vc.verdict(got) == verdict


def test_the_frame_and_a_close_up_are_sent_as_images_jev_accepts():
    jev = FakeJev(answers(0.97, "knife"))
    out = vc.VisionCheck(jev).check(FRAME, (0.45, 0.45, 0.5, 0.5), "Knife")
    assert out["verdict"] == "confirmed" and jev.sent["model"] == "jev-preview"
    assert set(jev.sent["assets"]) == {"img-frame", "img-close"}
    for ref in ("img-frame", "img-close"):
        img = cv2.imdecode(np.frombuffer(base64.b64decode(jev.sent["assets"][ref]["data_base64"]), np.uint8), 1)
        assert vc.MIN_SIDE <= min(img.shape[:2]) and max(img.shape[:2]) <= vc.MAX_SIDE
    assert jev.sent["state"]["frame"] == "img-frame" and "weapon" in jev.sent["questions"]


@pytest.mark.parametrize("vision", [vc.VisionCheck(None), vc.VisionCheck(FakeJev(error=TimeoutError("slow")))])
def test_no_jev_or_a_failure_is_unchecked(vision):
    assert vision.check(FRAME, None)["verdict"] == "unchecked"
    assert vc.VisionCheck(FakeJev()).check(None, None)["verdict"] == "unchecked"


# ── what the email does (through the real app) ───────────────────────────────

@pytest.fixture
def mail(monkeypatch):
    import Garuda_web as gw
    sent = []
    monkeypatch.setattr(gw, "_send_mail", lambda subject, body: sent.append(body))
    monkeypatch.setattr(gw.STATE.modes, "email_off", False)
    monkeypatch.setattr(gw.STATE.modes, "idle", False)
    monkeypatch.setattr(gw.STATE.alerts, "last_email_sent_time", 0)
    monkeypatch.setattr(gw.STATE.alerts, "email_held_at", 0.0)
    return gw, sent


def test_a_harmless_reading_holds_the_email_and_a_confirmed_one_says_so(mail, monkeypatch):
    gw, sent = mail
    monkeypatch.setattr(gw, "VISION", vc.VisionCheck(FakeJev(answers(0.05, "harmless_object"))))
    assert gw.email_after_second_look(FRAME, None, "Knife")["verdict"] == "harmless"
    assert sent == [] and gw.STATE.alerts.email_held_at > 0
    monkeypatch.setattr(gw, "VISION", vc.VisionCheck(FakeJev(answers(0.97, "knife"))))
    gw.email_after_second_look(FRAME, None, "Knife", again=True)
    assert len(sent) == 1 and "confirmed" in sent[0] and "earlier look said harmless" in sent[0]
    assert gw.STATE.alerts.email_held_at == 0.0


def test_a_failed_second_look_never_costs_the_email(mail, monkeypatch):
    gw, sent = mail
    monkeypatch.setattr(gw, "VISION", vc.VisionCheck(FakeJev(error=ConnectionError("down"))))
    assert gw.email_after_second_look(FRAME, None, "Knife")["verdict"] == "unchecked"
    assert len(sent) == 1 and "not done" in sent[0]


class _Box:
    def xmin(self): return 0.4
    def ymin(self): return 0.4
    def xmax(self): return 0.6
    def ymax(self): return 0.7


class _Det:
    def __init__(self, label, conf):
        self.label, self.conf = label, conf

    def get_label(self): return self.label
    def get_confidence(self): return self.conf
    def get_bbox(self): return _Box()


def test_the_detector_hands_the_triggering_frame_and_box_to_the_second_look(monkeypatch):
    import threading
    import types
    import Garuda_web as gw
    seen, done = [], threading.Event()

    def look(frame, box, label, again=False):
        seen.append((frame is not None and frame.shape, box, label))
        done.set()
        return {"verdict": "unchecked", "why": "test"}
    roi = types.SimpleNamespace(get_objects_typed=lambda kind: [_Det("Knife", 0.9)])
    monkeypatch.setattr(gw, "hailo", types.SimpleNamespace(HAILO_DETECTION=1, get_roi_from_buffer=lambda b: roi))
    monkeypatch.setattr(gw, "get_caps_from_pad", lambda pad: ("RGB", 640, 640))
    monkeypatch.setattr(gw, "get_numpy_from_buffer", lambda *a: FRAME.copy() + 90)
    monkeypatch.setattr(gw, "email_after_second_look", look)
    monkeypatch.setattr(gw, "trigger_software_alert", lambda: None)
    monkeypatch.setattr(gw.DRISHTI_RUNTIME, "observe", lambda *a: None)
    monkeypatch.setattr(gw.STATE.alerts, "danger_active", False)
    monkeypatch.setattr(gw.STATE.alerts, "active", False)
    monkeypatch.setattr(gw.STATE.config, "detection_threshold", 0.3)
    monkeypatch.setattr(gw.STATE.modes, "privacy", False)
    monkeypatch.setattr(gw.STATE.camera, "label_consec_frames", {})
    user = types.SimpleNamespace(use_frame=True, danger_labels=["Knife"], person_detected=False,
                                 increment=lambda: None, get_count=lambda: 1, set_frame=lambda f: None)
    info = types.SimpleNamespace(get_buffer=lambda: object())
    for _ in range(2):                                   # two frames in a row fire the alert
        gw.app_callback(None, info, user)
    assert done.wait(5)
    assert seen == [((640, 640, 3), (0.4, 0.4, 0.6, 0.7), "Knife")]


def test_the_scene_sentence_rides_along_and_never_decides():
    described = []
    vision = vc.VisionCheck(FakeJev(answers(0.05, "harmless_object")),
                            describe=lambda jpeg: described.append(len(jpeg)) or "A person holds a phone.")
    out = vision.check(FRAME, None, "Knife")
    assert out["verdict"] == "harmless" and out["scene"] == "A person holds a phone." and described
    assert "Scene (NVIDIA): A person holds a phone." in vc.say(out)
    slow = vc.VisionCheck(FakeJev(answers(0.97, "knife")), timeout=0.5,
                          describe=lambda jpeg: __import__("time").sleep(3) or "late")
    assert slow.check(FRAME, None)["scene"] == ""          # too slow: the email does not wait for it
