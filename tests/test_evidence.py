"""Evidence: the seconds before an alert, and one ZIP to hand over."""
import hashlib
import io
import json
import types
import zipfile

import cv2
import numpy as np
import pytest

import conftest  # noqa: F401
import Garuda_web as gw
from basic_pipelines.garuda_core import captures as cap
from basic_pipelines.garuda_routes import evidence


@pytest.fixture
def store(monkeypatch, tmp_path):
    s = cap.CaptureStore(str(tmp_path / "captures"))
    monkeypatch.setattr(gw, "CAPTURES", s)
    return s


def _jpeg(shade):
    ok, buf = cv2.imencode(".jpg", np.full((48, 64, 3), shade, np.uint8))
    return buf.tobytes()


# ── the ring ─────────────────────────────────────────────────────────────────

def test_the_ring_keeps_only_new_frames_at_its_rate_and_age():
    ring = evidence.FrameRing(seconds=2, fps=10)
    t = 100.0
    assert ring.offer(1, b"a", t)
    assert not ring.offer(1, b"a", t + 0.5)          # the same frame again
    assert not ring.offer(2, b"b", t + 0.01)         # too soon after the last
    for i in range(3, 60):                            # six seconds at 10 a second
        ring.offer(i, b"x%d" % i, t + i * 0.1)
    kept = ring.recent(t + 59 * 0.1)
    assert 19 <= len(kept) <= 22 and kept[-1] == b"x59"


# ── the alert clip ───────────────────────────────────────────────────────────

def _fake_core(alerts):
    camera = types.SimpleNamespace(frame_lock=__import__("threading").Lock(), frame_seq=0, frame_buffer=b"")
    return types.SimpleNamespace(STATE=types.SimpleNamespace(alerts=alerts, camera=camera))


class _Stop(Exception):
    pass


def test_a_clip_is_saved_once_when_an_alert_starts(monkeypatch):
    monkeypatch.setenv("GARUDA_ALERT_CLIP", "1")
    alerts = types.SimpleNamespace(active=False, night_presence_active=False, danger_trigger_info="")
    core = _fake_core(alerts)
    spawned, ticks = [], iter(range(10))
    script = {2: True, 3: True, 5: False, 6: True}     # starts, holds, ends, starts again inside the cooldown

    def sleep(_):
        i = next(ticks, None)
        if i is None:
            raise _Stop
        if i in script:
            alerts.active = script[i]
            alerts.danger_trigger_info = "knife (0.91)"
    with pytest.raises(_Stop):
        evidence.alert_clip_loop(core, evidence.FrameRing(), sleep=sleep, clock=lambda: 1000.0,
                                 spawn=lambda fn, *a: spawned.append(a))
    assert len(spawned) == 1 and spawned[0][2] == "knife (0.91)"


def test_the_clip_holds_the_seconds_before_and_after(monkeypatch, store):
    ring = evidence.FrameRing(seconds=5)
    for i in range(30):
        ring.offer(i, _jpeg(i), 50.0 + i * 0.1)
    seen = {}

    def encode(captures, jpegs, fps, out):
        seen["n"] = len(jpegs)
        open(out, "wb").write(b"webm")
        return True
    monkeypatch.setattr(evidence, "encode", encode)
    monkeypatch.setattr(gw.STATE.camera, "frame_buffer", _jpeg(200))
    entry = evidence.save_alert_clip(gw, ring, "knife", 10, now=lambda: 53.0,
                                     record=lambda fn, s, fps_cap: ([_jpeg(1)] * 4, fps_cap))
    assert entry["status"] == "saved" and entry["kind"] == "video" and entry["alert"] == "knife"
    assert entry["label"].startswith("Alert clip: 5 s before")
    assert seen["n"] == 30 + 4


def test_a_clip_with_no_frames_is_marked_failed(store):
    entry = evidence.save_alert_clip(gw, evidence.FrameRing(), "knife", 1,
                                     record=lambda fn, s, fps_cap: ([], 0))
    assert entry is None
    assert store.list()[0]["status"] == "failed"


def test_frames_become_a_playable_video_without_ffmpeg(monkeypatch, tmp_path):
    monkeypatch.setattr(cap, "ffmpeg", lambda: None)
    out = str(tmp_path / "clip.webm")
    if not evidence.encode(cap, [_jpeg(i * 10) for i in range(10)], 10, out):
        pytest.skip("this OpenCV has no VP8 encoder")
    assert cv2.VideoCapture(out).read()[0]


def test_the_clip_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("GARUDA_ALERT_CLIP", "0")
    assert evidence.alert_clip_loop(None, None) is None


# ── the export ───────────────────────────────────────────────────────────────

def _saved(store, kind, data, group="g1", alert="knife"):
    e = store.begin(kind, by="admin", label="Kitchen", group=group, alert=alert)
    return store.finish(e["id"], data)


def test_an_event_exports_as_one_zip_with_hashes(app_client, admin_headers, store):
    pic = _saved(store, "snapshot", _jpeg(10))
    snd = _saved(store, "audio", b"RIFF....WAVEdata")
    _saved(store, "snapshot", _jpeg(99), group="other")
    r = app_client.get("/api/evidence/export?group=g1", headers=admin_headers)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    manifest = json.loads(z.read("manifest.json"))
    assert len(manifest["files"]) == 2 and "README.txt" in z.namelist()
    for f in manifest["files"]:
        assert hashlib.sha256(z.read(f["file"])).hexdigest() == f["sha256"]
        assert f["during_alert"] == "knife"
    assert {f["kind"] for f in manifest["files"]} == {pic["kind"], snd["kind"]}


def test_a_single_capture_exports_by_id_and_a_link_token_works(app_client, admin_headers, store):
    pic = _saved(store, "snapshot", _jpeg(10), group="")
    token = admin_headers["X-Garuda-Token"]
    r = app_client.get(f"/api/evidence/export?id={pic['id']}&token={token}")
    assert r.status_code == 200
    assert len(json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read("manifest.json"))["files"]) == 1


def test_export_needs_a_sign_in_and_a_real_event(app_client, store):
    assert app_client.get("/api/evidence/export?group=g1").status_code == 401


def test_export_of_nothing_is_refused(app_client, admin_headers, store):
    assert app_client.get("/api/evidence/export", headers=admin_headers).status_code == 400
    assert app_client.get("/api/evidence/export?group=nope", headers=admin_headers).status_code == 404
