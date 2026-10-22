"""Captures: stills, sound and video saved from the camera, by hand, by Narada and
by a shortcut that an alert starts."""
import asyncio
import threading
import time

import cv2
import numpy as np
import pytest

import Garuda_web as gw
from basic_pipelines.garuda_core import captures as cap


class FakeFeed:
    """The microphone: `seconds` of a quiet tone, at once."""
    available = True

    def __init__(self):
        self.asked = []

    def level(self, seconds=1.0):
        return None

    def keep_alive(self, seconds):
        pass

    async def record(self, seconds):
        self.asked.append(seconds)
        await asyncio.sleep(0.05)
        return b"\x10\x00" * int(16000 * seconds)


@pytest.fixture(autouse=True)
def camera(app_client, monkeypatch, tmp_path):
    monkeypatch.setattr(gw, "CAPTURES", cap.CaptureStore(str(tmp_path / "captures")))
    feed = FakeFeed()
    monkeypatch.setattr(gw, "AUDIO_FEED", feed)
    frame = np.zeros((48, 64, 3), np.uint8)
    frame[:, :32] = (0, 0, 255)
    ok, jpeg = cv2.imencode(".jpg", frame)
    monkeypatch.setattr(gw.STATE.camera, "frame_raw", frame)
    monkeypatch.setattr(gw.STATE.camera, "frame_buffer", jpeg.tobytes())
    monkeypatch.setattr(gw.STATE.camera, "frame_ts", time.time())
    stop = threading.Event()

    def frames():               # the pipeline publishing new frames
        while not stop.is_set():
            gw.STATE.camera.frame_seq += 1
            gw.STATE.camera.frame_ts = time.time()
            time.sleep(0.05)

    threading.Thread(target=frames, daemon=True).start()
    yield feed
    stop.set()


def _poll(fn, seconds=8):
    deadline = time.time() + seconds
    while time.time() < deadline:
        got = fn()
        if got:
            return got
        time.sleep(0.05)
    return fn()


def test_captures_need_a_session(app_client):
    assert app_client.get("/api/captures").status_code == 401
    assert app_client.post("/api/captures/snapshot", json={}).status_code == 401


def test_a_snapshot_is_saved_listed_and_served(app_client, user_headers):
    made = app_client.post("/api/captures/snapshot", json={"label": "door"}, headers=user_headers)
    assert made.status_code == 200, made.text
    entry = made.json()["capture"]
    assert entry["status"] == "saved" and entry["kind"] == "snapshot" and entry["label"] == "door"
    listed = app_client.get("/api/captures", headers=user_headers).json()
    assert [c["id"] for c in listed["captures"]] == [entry["id"]]
    got = app_client.get(listed["captures"][0]["url"], headers=user_headers)
    assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg"
    assert got.content[:2] == b"\xff\xd8"


def test_sound_is_the_next_seconds_and_waits_unless_told_not_to(app_client, user_headers, camera):
    out = app_client.post("/api/captures/audio", json={"seconds": 10}, headers=user_headers).json()
    assert out["capture"]["status"] == "saved" and camera.asked == [10]
    path = gw.CAPTURES.path_of(out["capture"])
    assert open(path, "rb").read(4) == b"RIFF"
    later = app_client.post("/api/captures/audio", json={"seconds": 2, "wait": False},
                            headers=user_headers).json()
    assert later["capture"]["status"] == "recording" and "Recording" in later["result"]
    assert _poll(lambda: gw.CAPTURES.get(later["capture"]["id"])["status"] == "saved")
    assert app_client.post("/api/captures/audio", json={"seconds": 99},
                           headers=user_headers).status_code == 422


def test_video_is_saved_as_webm_with_its_sound(app_client, user_headers, monkeypatch, camera):
    monkeypatch.setattr(cap, "ffmpeg", lambda: None)      # no ffmpeg: the sound goes beside it
    out = app_client.post("/api/captures/video", json={"seconds": 1, "label": "hall"},
                          headers=user_headers)
    assert out.status_code == 200, out.text
    video = out.json()["capture"]
    assert video["status"] == "saved" and video["mime"] == "video/webm" and video["bytes"] > 0
    group = gw.CAPTURES.list(group=video["group"])
    assert sorted(c["kind"] for c in group) == ["audio", "video"] and video["group"]
    assert _poll(lambda: all(c["status"] == "saved" for c in gw.CAPTURES.list(group=video["group"])))
    served = app_client.get(f"/api/captures/{video['id']}/file", headers=user_headers)
    assert served.status_code == 200 and served.content[:4] == b"\x1a\x45\xdf\xa3"   # EBML


def test_only_its_maker_or_an_admin_keeps_or_deletes_one(app_client, user_headers, admin_headers):
    mine = app_client.post("/api/captures/snapshot", json={}, headers=admin_headers).json()["capture"]
    assert app_client.request("DELETE", f"/api/captures/{mine['id']}",
                              headers=user_headers).status_code == 403
    kept = app_client.post(f"/api/captures/{mine['id']}/keep", json={"keep": True},
                           headers=admin_headers).json()
    assert kept["capture"]["keep"] is True
    assert app_client.request("DELETE", f"/api/captures/{mine['id']}",
                              headers=admin_headers).status_code == 200
    assert gw.CAPTURES.list() == []


def test_a_group_is_emailed_with_its_files_attached(app_client, user_headers, monkeypatch):
    sent = []
    monkeypatch.setattr(gw, "_send_mail", lambda subject, body, to=None, attachments=():
                        sent.append((subject, body, attachments)))
    monkeypatch.setattr(gw.STATE.config, "email_sender", "garuda@example.com")
    monkeypatch.setattr(gw.STATE.config, "email_sender_pass", "x")
    monkeypatch.setattr(gw.STATE.config, "email_recipients", ["owner@example.com"])
    for kind, body in (("snapshot", {}), ("audio", {"seconds": 2, "wait": False})):
        app_client.post(f"/api/captures/{kind}", json={"group": "run1", "label": "Alert", **body},
                        headers=user_headers)
    out = app_client.post("/api/captures/email", json={"group": "run1"}, headers=user_headers)
    assert out.status_code == 200, out.text
    subject, body, files = sent[0]
    assert "Alert" in subject and [f[1] for f in files] == ["image/jpeg", "audio/wav"]
    monkeypatch.setattr(gw.STATE.modes, "email_off", True)
    assert app_client.post("/api/captures/email", json={"group": "run1"},
                           headers=user_headers).status_code == 409


def test_the_mail_carries_the_files():
    from basic_pipelines.garuda_core import mailer
    msg = mailer.build("s", "b", sender="a@x", to=["b@x"],
                       attachments=[("p.jpg", "image/jpeg", b"\xff\xd8x")])
    parts = list(msg.iter_attachments())
    assert parts[0].get_filename() == "p.jpg" and parts[0].get_content() == b"\xff\xd8x"


def test_an_alert_shortcut_saves_a_picture_and_the_next_ten_seconds_of_sound(
        app_client, monkeypatch, camera):
    """The request that started this: on an alert, a screenshot and the next 10 s of sound."""
    store = gw._shortcuts_mod.ShortcutStore(None)
    monkeypatch.setattr(gw.SHORTCUTS, "store", store)
    monkeypatch.setattr(gw.SHORTCUTS, "_seen_when", set())
    program = {"name": "Alert evidence", "trigger": {"type": "alert", "kind": "any"},
               "steps": [{"do": "capture_snapshot"},
                         {"do": "capture_audio", "args": {"seconds": 10}}]}
    clean = gw.SHORTCUTS.check(program, "admin")
    sid = store.add(clean, created_by="admin")["id"]
    gw.SHORTCUTS.tick()
    assert gw.CAPTURES.list() == []
    monkeypatch.setattr(gw.STATE.alerts, "active", True)
    monkeypatch.setattr(gw.STATE.alerts, "danger_trigger_info", "knife (0.91)")
    gw.SHORTCUTS.tick()
    run = _poll(lambda: (store.get(sid)["runs"] or [None])[0])
    assert run and run["outcome"] == "done", run
    saved = gw.CAPTURES.list()
    assert sorted(c["kind"] for c in saved) == ["audio", "snapshot"]
    assert {c["group"] for c in saved} == {run["id"]} and {c["label"] for c in saved} == {"Alert evidence"}
    assert all(c["alert"] == "knife (0.91)" and c["by"] == "admin" for c in saved)
    assert camera.asked == [10]


def test_narada_has_the_tools_to_build_it():
    from basic_pipelines.garuda_auto import capabilities as caps
    planner = {t["function"]["name"] for t in caps.tools("planner")}
    assert {"capture_snapshot", "capture_audio", "capture_video", "list_captures",
            "email_captures", "keep_capture", "delete_capture"} <= planner
    assert caps.BY_NAME["delete_capture"].tier == "confirm"


def test_the_store_clears_the_oldest_and_never_a_kept_one(tmp_path):
    clock = [1000.0]
    store = cap.CaptureStore(str(tmp_path), clock=lambda: clock[0], max_items=2)
    ids = []
    for i in range(3):
        clock[0] += 1
        entry = store.begin("snapshot", by="a")
        if i == 0:
            store.set_keep(entry["id"], True)
        store.finish(entry["id"], b"x" * 10)
        ids.append(entry["id"])
    assert {c["id"] for c in store.list()} == {ids[0], ids[2]}
    pending = store.begin("audio", by="a")
    again = cap.CaptureStore(str(tmp_path))
    assert again.get(pending["id"])["status"] == "failed"


@pytest.mark.skipif(not cap.ffmpeg(), reason="ffmpeg is not installed")
def test_with_ffmpeg_the_sound_goes_into_the_video(app_client, user_headers, camera):
    out = app_client.post("/api/captures/video", json={"seconds": 1}, headers=user_headers).json()
    assert out["capture"]["status"] == "saved", out
    assert [c["kind"] for c in gw.CAPTURES.list()] == ["video"]
    probe = cap.subprocess.run([cap.ffmpeg(), "-i", gw.CAPTURES.path_of(out["capture"])],
                               capture_output=True, text=True).stderr
    assert "Audio: opus" in probe and "Video: vp8" in probe
