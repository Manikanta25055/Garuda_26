"""Fixes from the 2026-10-24 bloat and bug audit: one test (or a few) per fix."""
import types
from unittest.mock import MagicMock

import numpy as np

import conftest  # noqa: F401
import Garuda_web as gw
from basic_pipelines.garuda_services import detection, pipeline, presence


# ── the alert email names the object that was seen ──────────────────────────

def _mail_ready(monkeypatch):
    for mode in ("email_off", "idle", "emergency", "night"):
        monkeypatch.setattr(gw.STATE.modes, mode, False)
    monkeypatch.setattr(gw.STATE.alerts, "last_email_sent_time", 0.0)
    monkeypatch.setattr(gw.STATE.config, "danger_labels", ["Knife", "Scissors", "Hammer"])
    monkeypatch.setattr(gw._cloud, "publish_alert_async", MagicMock())
    send = MagicMock()
    monkeypatch.setattr(gw, "_send_mail", send)
    return send


def test_alert_email_names_the_detected_object(monkeypatch):
    send = _mail_ready(monkeypatch)
    gw.send_email_alert(label="Knife")
    subject, body = send.call_args.args[:2]
    assert subject.endswith("— Knife") and "Scissors" not in subject
    assert "Object(s): Knife\n" in body


def test_second_look_passes_the_label_to_the_email(monkeypatch):
    send = _mail_ready(monkeypatch)
    monkeypatch.setattr(gw, "VISION", None)
    gw.email_after_second_look(None, None, "Hammer")
    assert send.call_args.args[0].endswith("— Hammer")


def test_an_email_without_a_label_still_lists_the_watched_objects(monkeypatch):
    send = _mail_ready(monkeypatch)
    gw.send_email_alert()
    assert "Knife, Scissors, Hammer" in send.call_args.args[0]


# ── the ARP sweep only covers a home-sized LAN ───────────────────────────────

def _routes(monkeypatch, text):
    monkeypatch.setattr(presence.subprocess, "check_output", lambda *a, **k: text)


def test_sweep_skips_container_bridges_and_wide_routes(monkeypatch):
    _routes(monkeypatch,
            "default via 192.168.1.1 dev wlan0 proto dhcp\n"
            "172.17.0.0/16 dev docker0 proto kernel scope link src 172.17.0.1\n"
            "10.0.0.0/8 dev eth1 proto kernel scope link\n"
            "192.168.1.0/24 dev wlan0 proto kernel scope link src 192.168.1.20\n")
    assert gw._get_local_subnet() == "192.168.1.0/24"


def test_sweep_has_no_subnet_when_none_is_home_sized(monkeypatch):
    _routes(monkeypatch, "172.18.0.0/16 dev br-1a2b proto kernel scope link\n"
                         "10.20.0.0/16 dev eth0 proto kernel scope link\n")
    assert gw._get_local_subnet() == ""


def test_sweep_keeps_a_wired_slash_23(monkeypatch):
    _routes(monkeypatch, "10.1.0.0/23 dev eth0 proto kernel scope link src 10.1.0.5\n")
    assert gw._get_local_subnet() == "10.1.0.0/23"


# ── a camera pipeline that stops delivering frames is restarted ─────────────

def test_a_pipeline_without_frames_counts_as_stalled(monkeypatch):
    app = types.SimpleNamespace(_started_at=1000.0)
    monkeypatch.setattr(gw.STATE.camera, "frame_ts", 0.0)
    stalled = lambda now: pipeline.GStreamerDetectionApp._stalled(app, now)
    assert not stalled(1000.0 + pipeline.STALL_RESTART_S - 1)      # still starting up
    assert stalled(1000.0 + pipeline.STALL_RESTART_S + 1)          # never delivered a frame
    monkeypatch.setattr(gw.STATE.camera, "frame_ts", 2000.0)
    assert not stalled(2000.0 + 5)                                  # frames are flowing
    assert stalled(2000.0 + pipeline.STALL_RESTART_S + 1)          # and then they stopped


def test_a_stalled_pipeline_quits_its_loop_and_says_why(monkeypatch):
    loop = MagicMock()
    app = types.SimpleNamespace(_started_at=0.0, loop=loop)
    app._stalled = lambda: True
    logged = []
    monkeypatch.setattr(gw, "log_system_update", logged.append)
    assert pipeline.GStreamerDetectionApp._check_stall(app) is False
    loop.quit.assert_called_once()
    assert logged and "restarting the camera pipeline" in logged[0]


def test_a_healthy_pipeline_keeps_its_timer():
    app = types.SimpleNamespace(loop=MagicMock(), _stalled=lambda: False)
    assert pipeline.GStreamerDetectionApp._check_stall(app) is True
    app.loop.quit.assert_not_called()


# ── the frame callback: copies only what it uses; the covered lens in seconds ─

class _Det:
    def __init__(self, label, conf=0.9):
        self.label, self.conf = label, conf

    def get_label(self): return self.label
    def get_confidence(self): return self.conf
    def get_bbox(self):
        return types.SimpleNamespace(xmin=lambda: 0.1, ymin=lambda: 0.1, xmax=lambda: 0.3, ymax=lambda: 0.3)


def _callback_env(monkeypatch, dets, frame, publish):
    reads = []

    def read(*a):
        reads.append(1)
        return frame.copy()
    roi = types.SimpleNamespace(get_objects_typed=lambda kind: dets)
    monkeypatch.setattr(gw, "hailo", types.SimpleNamespace(HAILO_DETECTION=1, get_roi_from_buffer=lambda b: roi))
    monkeypatch.setattr(gw, "get_caps_from_pad", lambda pad: ("RGB", 64, 64))
    monkeypatch.setattr(gw, "get_numpy_from_buffer", read)
    monkeypatch.setattr(gw, "_frame_publisher", types.SimpleNamespace(due=lambda: publish))
    monkeypatch.setattr(gw.DRISHTI_RUNTIME, "observe", lambda *a: None)
    # Not time for the rules' light reading in this frame.
    monkeypatch.setattr(gw.STATE.camera, "drishti_last_observe", 1e12)
    monkeypatch.setattr(gw.STATE.config, "detection_threshold", 0.3)
    monkeypatch.setattr(gw.STATE.modes, "privacy", False)
    monkeypatch.setattr(gw.STATE.camera, "label_consec_frames", {})
    monkeypatch.setattr(gw.STATE.alerts, "blind_since", 0.0)
    monkeypatch.setattr(gw.STATE.alerts, "blind_alert_sent", False)
    user = types.SimpleNamespace(use_frame=True, danger_labels=["Knife"], person_detected=False,
                                 increment=lambda: None, get_count=lambda: 1, set_frame=lambda f: None)
    info = types.SimpleNamespace(get_buffer=lambda: object())
    return reads, lambda: gw.app_callback(None, info, user)


def test_an_unpublished_empty_frame_is_not_copied(monkeypatch):
    reads, run = _callback_env(monkeypatch, [], np.full((64, 64, 3), 120, np.uint8), publish=False)
    run()
    assert reads == []


def test_a_published_frame_is_copied_once(monkeypatch):
    reads, run = _callback_env(monkeypatch, [], np.random.randint(0, 255, (64, 64, 3), np.uint8), publish=True)
    run()
    assert reads == [1]


def test_a_covered_lens_alarms_after_ten_seconds_not_after_a_frame_count(monkeypatch):
    clock = [5000.0]
    monkeypatch.setattr(detection.time, "time", lambda: clock[0])
    sent = MagicMock()
    monkeypatch.setattr(gw, "_send_tamper_email", sent)
    monkeypatch.setattr(gw, "_append_detection_perm", lambda *a, **k: None)
    reads, run = _callback_env(monkeypatch, [], np.zeros((64, 64, 3), np.uint8), publish=True)
    for _ in range(600):                      # ten seconds of frames at 60 fps, in an instant
        run()
    assert gw.STATE.alerts.blind_alert_sent is False
    clock[0] += detection.BLIND_ALERT_S + 0.1
    run()
    assert gw.STATE.alerts.blind_alert_sent is True
