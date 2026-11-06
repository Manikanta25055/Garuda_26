"""Captures: stills, sound and video saved from the camera, by hand, by Narada and
by a shortcut that an alert starts."""
import asyncio
import os
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
    again = cap.CaptureStore(str(tmp_path), clock=lambda: clock[0])
    assert again.get(pending["id"])["status"] == "failed"


@pytest.mark.skipif(not cap.ffmpeg(), reason="ffmpeg is not installed")
def test_with_ffmpeg_the_sound_goes_into_the_video(app_client, user_headers, camera):
    out = app_client.post("/api/captures/video", json={"seconds": 1}, headers=user_headers).json()
    assert out["capture"]["status"] == "saved", out
    assert [c["kind"] for c in gw.CAPTURES.list()] == ["video"]
    probe = cap.subprocess.run([cap.ffmpeg(), "-i", gw.CAPTURES.path_of(out["capture"])],
                               capture_output=True, text=True).stderr
    assert "Audio: opus" in probe and "Video: vp8" in probe


@pytest.mark.skipif(not cap.ffmpeg(), reason="ffmpeg is not installed")
def test_a_video_is_encoded_while_it_records_and_lasts_as_long(tmp_path):
    frame = np.zeros((48, 64, 3), np.uint8)
    jpeg = cv2.imencode(".jpg", frame)[1].tobytes()
    seq = iter(range(1000))
    out = str(tmp_path / "v.webm")
    started = time.monotonic()
    # A slow camera (a new frame every few calls): the newest one fills the gaps.
    got = cap.stream_video(lambda: (next(seq) // 5, jpeg), 2, out)
    took = time.monotonic() - started
    assert got > 0 and took < 2 + 2.5
    probe = cap.subprocess.run([cap.ffmpeg(), "-i", out], capture_output=True, text=True).stderr
    assert "Video: vp8" in probe and "Duration: 00:00:02.0" in probe, probe


def test_no_frames_no_video(tmp_path):
    out = str(tmp_path / "v.webm")
    assert cap.stream_video(lambda: None, 1, out, tool=cap.ffmpeg() or "ffmpeg") == 0
    assert not (tmp_path / "v.webm").exists()


def test_what_a_stopped_recording_left_is_cleared_on_start(tmp_path):
    tmp_path = tmp_path / "left"
    tmp_path.mkdir()
    (tmp_path / "capture-abc").mkdir()
    (tmp_path / "capture-abc" / "video.webm").write_bytes(b"x")
    (tmp_path / "123.jpg.part").write_bytes(b"x")
    (tmp_path / "kept.jpg").write_bytes(b"x")
    cap.CaptureStore(str(tmp_path))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["kept.jpg"]


def test_a_short_disk_clears_the_oldest_then_refuses(tmp_path):
    free = [10_000]
    store = cap.CaptureStore(str(tmp_path), min_free=1000, free_fn=lambda d: free[0])
    old = store.begin("snapshot", by="a")
    store.finish(old["id"], b"x" * 600)
    kept = store.begin("snapshot", by="a")
    store.set_keep(kept["id"], True)
    store.finish(kept["id"], b"x" * 600)
    free[0] = 500                  # something else filled the disk
    store._free_fn = lambda d: free[0] + (600 if store.get(old["id"]) is None else 0)
    assert store.begin("snapshot", by="a")["status"] == "recording"
    assert store.get(old["id"]) is None and store.get(kept["id"])
    free[0] = 100
    with pytest.raises(cap.LowDisk):
        store.begin("snapshot", by="a")
    assert store.get(kept["id"])


def test_a_full_disk_answers_507(app_client, user_headers, monkeypatch, camera):
    monkeypatch.setattr(gw.CAPTURES, "_free_fn", lambda d: 0)
    out = app_client.post("/api/captures/snapshot", json={}, headers=user_headers)
    assert out.status_code == 507 and "disk" in out.json()["detail"]


def test_an_unlisted_file_goes_on_start_and_a_broken_index_is_kept(tmp_path):
    tmp_path = tmp_path / "caps"
    store = cap.CaptureStore(str(tmp_path))
    entry = store.begin("snapshot", by="a")
    store.finish(entry["id"], b"x")
    (tmp_path / "orphan.jpg").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"x")
    cap.CaptureStore(str(tmp_path))
    assert {p.name for p in tmp_path.iterdir()} == {"index.json", "notes.txt", entry["file"]}
    (tmp_path / "index.json").write_text("{broken")
    again = cap.CaptureStore(str(tmp_path))
    assert again.list() == [] and (tmp_path / entry["file"]).exists()
    assert any(p.name.startswith("index.json.bad-") for p in tmp_path.iterdir())


# ── the page: the latest event, a history by day, and how long they stay ──────

def _saved(store, kind="snapshot", data=b"x", **kw):
    entry = store.begin(kind, by=kw.pop("by", "admin"), **kw)
    return store.finish(entry["id"], data)


def test_events_group_a_run_and_come_a_page_at_a_time(tmp_path):
    clock = [1000.0]
    store = cap.CaptureStore(str(tmp_path), clock=lambda: clock[0])
    for i in range(5):
        clock[0] += 10
        _saved(store, label=f"one {i}")
    clock[0] += 10
    _saved(store, group="run", label="alert")
    clock[0] += 1
    _saved(store, "audio", group="run")
    first = store.events(limit=2)
    assert first["total"] == 6 and first["more"] is True
    newest = first["events"][0]
    assert newest["key"] == "run" and newest["label"] == "alert"
    assert [c["kind"] for c in newest["items"]] == ["snapshot", "audio"]   # in the order made
    assert newest["kinds"] == {"snapshot": 1, "audio": 1}
    seen = [e["key"] for e in first["events"]]
    before = first["next_before"]
    while before is not None:
        page = store.events(limit=2, before=before)
        seen += [e["key"] for e in page["events"]]
        before = page["next_before"]
    assert len(seen) == len(set(seen)) == 6                               # each once, none missed
    assert [e["label"] for e in store.events(kind="audio")["events"]] == ["alert"]


def test_captures_older_than_the_retention_go_unless_kept(tmp_path):
    day = 86400
    clock = [100 * day]
    store = cap.CaptureStore(str(tmp_path), clock=lambda: clock[0], days=7)
    old = _saved(store)
    kept = _saved(store)
    store.set_keep(kept["id"], True)
    clock[0] += 3 * day
    recent = _saved(store)
    clock[0] += 5 * day                 # old and kept are 8 days old, recent 5
    store.events()                      # reading the list clears what aged out
    ids = {c["id"] for c in store.list()}
    assert ids == {kept["id"], recent["id"]}
    assert not os.path.exists(os.path.join(str(tmp_path), old["file"]))
    assert store.set_days(1) == 1       # shorter: recent goes at once
    assert {c["id"] for c in store.list()} == {kept["id"]}
    again = cap.CaptureStore(str(tmp_path), clock=lambda: clock[0])
    assert again.days == 1              # the admin's choice outlives a restart
    assert store.set_days(0) == 0       # no age limit
    with pytest.raises(ValueError):
        store.set_days(-1)


def test_clear_removes_the_history_but_not_the_kept_or_the_latest(tmp_path):
    clock = [1000.0]
    store = cap.CaptureStore(str(tmp_path), clock=lambda: clock[0])
    a = _saved(store)
    clock[0] += 1
    b = _saved(store)
    store.set_keep(b["id"], True)
    clock[0] += 1
    latest = _saved(store)
    assert store.clear(before=latest["at"]) == 1
    assert {c["id"] for c in store.list()} == {b["id"], latest["id"]}
    assert store.get(a["id"]) is None


def test_a_small_picture_is_made_once_and_goes_with_its_capture(tmp_path):
    store = cap.CaptureStore(str(tmp_path))
    frame = np.full((480, 640, 3), 90, np.uint8)
    pic = _saved(store, data=cv2.imencode(".jpg", frame)[1].tobytes())
    path = store.thumb(pic["id"])
    small = cv2.imread(path)
    assert small.shape[1] == cap.THUMB_WIDTH and small.shape[0] == 240
    assert store.thumb(pic["id"]) == path
    sound = _saved(store, "audio")
    assert store.thumb(sound["id"]) is None
    cap.CaptureStore(str(tmp_path))     # a thumb is not an unlisted file
    assert os.path.exists(path)
    store.delete(pic["id"])
    assert not os.path.exists(path)


def test_a_video_has_a_small_picture_of_its_first_frame(tmp_path):
    store = cap.CaptureStore(str(tmp_path))
    jpeg = cv2.imencode(".jpg", np.full((120, 160, 3), 200, np.uint8))[1].tobytes()
    entry = store.begin("video", by="admin")
    assert cap.encode_video([jpeg] * 10, 10, store.path_of(entry))
    store.finish(entry["id"])
    path = store.thumb(entry["id"])
    assert path and cv2.imread(path).shape[1] == 160


def test_the_events_page_and_its_admin_controls(app_client, user_headers, admin_headers):
    for label in ("first", "second", "third"):
        assert app_client.post("/api/captures/snapshot", json={"label": label},
                               headers=user_headers).status_code == 200
    got = app_client.get("/api/captures/events?limit=2", headers=user_headers).json()
    assert got["total"] == 3 and got["more"] is True
    assert [e["label"] for e in got["events"]] == ["third", "second"]
    item = got["events"][0]["items"][0]
    thumb = app_client.get(item["thumb"], headers=user_headers)
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    nxt = app_client.get(f"/api/captures/events?before={got['next_before']}",
                         headers=user_headers).json()
    assert [e["label"] for e in nxt["events"]] == ["first"]
    assert got["usage"]["days"] == gw.CAPTURES.days
    # Only an admin sets how long they stay or clears the history.
    assert app_client.post("/api/captures/policy", json={"days": 7},
                          headers=user_headers).status_code == 403
    assert app_client.post("/api/captures/clear", json={}, headers=user_headers).status_code == 403
    set_ = app_client.post("/api/captures/policy", json={"days": 7}, headers=admin_headers)
    assert set_.status_code == 200 and set_.json()["days"] == 7
    assert app_client.post("/api/captures/policy", json={"days": -1},
                          headers=admin_headers).status_code == 422
    latest = got["events"][0]["items"][0]["at"]
    cleared = app_client.post("/api/captures/clear", json={"before": latest}, headers=admin_headers)
    assert cleared.json()["removed"] == 2
    left = app_client.get("/api/captures/events", headers=user_headers).json()
    assert [e["label"] for e in left["events"]] == ["third"]
