"""Captures over HTTP: save a still, the next seconds of sound or of video, and
look at, keep, email or delete what was saved (garuda_core/captures.py).

Each kind is one POST, so a button, a script, Narada and a shortcut step all
make a capture the same way. With wait (the default) the request answers
once the capture is on disk; without it, at once, with the capture listed as
"recording": a shortcut can then save a picture, ten seconds of sound and ten
seconds of video of the same moment, side by side.

Anyone signed in may make and look at captures: they show what the live
camera already shows them. A capture is deleted, or marked keep, by the
person it was made for (a shortcut's maker) or an admin.
"""
import asyncio
import concurrent.futures
import datetime
import os
import tempfile
import time
from typing import Optional

import cv2
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

MAX_RECORDING = 4               # recordings of sound or video at the same time
MAX_EMAIL_BYTES = 20 * 1024 * 1024


class SnapshotRequest(BaseModel):
    label: str = Field("", max_length=80)
    group: str = Field("", max_length=40)


class AudioRequest(SnapshotRequest):
    seconds: float = Field(10, ge=1, le=30)
    wait: bool = True


class VideoRequest(SnapshotRequest):
    seconds: float = Field(10, ge=1, le=60)
    sound: bool = True
    wait: bool = True


class EmailRequest(BaseModel):
    group: str = Field("", max_length=40)
    ids: list[str] = Field(default_factory=list, max_length=20)
    note: str = Field("", max_length=500)


class KeepRequest(BaseModel):
    keep: bool = True


def build_captures_router(core):
    router = APIRouter(prefix="/api/captures")
    captures = core._captures_mod
    _tasks = set()              # recordings running in the background (held so they finish)
    # A video holds a thread for as long as it records and encodes. Its own
    # threads, so recordings never use up the ones every other route's
    # asyncio.to_thread shares; two spare for the mux after a recording.
    _pool = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_RECORDING + 2,
                                                  thread_name_prefix="capture")

    async def _in_pool(fn, *args):
        return await asyncio.get_running_loop().run_in_executor(_pool, fn, *args)

    def store():
        return core.CAPTURES

    # ── what the camera and the alert are now ─────────────────────────────────
    def _camera_live():
        if time.time() - core.STATE.camera.frame_ts > 5.0:
            raise HTTPException(503, "The camera is not delivering frames right now")

    def _alert_now():
        alerts = core.STATE.alerts
        if alerts.active:
            return (alerts.danger_trigger_info or "alert").strip().replace("\n", ", ")
        if alerts.night_presence_active:
            return "night presence"
        if alerts.blind_alert_sent:
            return "camera covered"
        return ""

    def _newest_jpeg():
        with core.STATE.camera.frame_lock:
            return core.STATE.camera.frame_seq, core.STATE.camera.frame_buffer

    def _recording_count():
        return sum(1 for c in store().list(limit=50) if c["status"] == "recording")

    def _begin(kind, data, session, seconds=None):
        try:
            return store().begin(kind, by=session["username"], label=data.label.strip(),
                                 group=data.group.strip(), alert=_alert_now(), seconds=seconds)
        except captures.LowDisk as exc:
            raise HTTPException(507, f"Not saved: {exc}")

    def _owner_or_admin(capture_id, session):
        entry = store().get(capture_id)
        if entry is None:
            raise HTTPException(404, "no such capture")
        if session["role"] != "admin" and entry.get("by") != session["username"]:
            raise HTTPException(403, "Only an admin or the person it was saved for can change this.")
        return entry

    def _background(coro):
        task = asyncio.ensure_future(coro)
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
        return task

    async def _answer(entry, task, wait):
        if wait:
            try:
                await asyncio.wait_for(asyncio.shield(task), captures.FINISH_WAIT_S)
            except asyncio.TimeoutError:
                pass
            entry = store().get(entry["id"]) or entry
            if entry["status"] == "failed":
                raise HTTPException(503, entry.get("error") or "the capture failed")
        what = {"snapshot": "picture", "audio": "sound", "video": "video"}[entry["kind"]]
        return {"ok": True, "capture": entry,
                "result": (f"Saved the {what}" if entry["status"] == "saved"
                           else f"Recording the {what}; it is saved when it ends")}

    async def _wait_saved(ids):
        """Their entries once none is still recording, or the time is up.
        Polls, so a long video holds no thread while an email waits for it."""
        deadline = time.monotonic() + captures.FINISH_WAIT_S
        while True:
            entries = [e for e in (store().get(i) for i in ids) if e]
            if all(e["status"] != "recording" for e in entries) or time.monotonic() >= deadline:
                return entries
            await asyncio.sleep(0.25)

    # ── making them ───────────────────────────────────────────────────────────
    @router.post("/snapshot")
    async def capture_snapshot(data: SnapshotRequest, session=Depends(core.require_session)):
        """Save what the camera sees now as a JPEG."""
        _camera_live()
        with core.STATE.camera.frame_lock:
            raw = core.STATE.camera.frame_raw
        if raw is None:
            raise HTTPException(503, "No frame available yet")
        ok, jpeg = await asyncio.to_thread(cv2.imencode, ".jpg", raw, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise HTTPException(500, "Could not encode the frame")
        entry = _begin("snapshot", data, session)
        entry = await asyncio.to_thread(store().finish, entry["id"], jpeg.tobytes())
        if entry is None:
            raise HTTPException(500, "The picture was removed before it was saved")
        return {"ok": True, "capture": entry, "result": "Saved the picture"}

    async def _record_audio(capture_id, seconds):
        try:
            pcm = await core.AUDIO_FEED.record(seconds)
            if not pcm:
                store().fail(capture_id, "the microphone is not delivering sound right now")
                return None
            await asyncio.to_thread(store().finish, capture_id, core.audio_wav_bytes(pcm))
            return pcm
        except Exception as exc:
            store().fail(capture_id, f"{type(exc).__name__}: {exc}")
            return None

    @router.post("/audio")
    async def capture_audio(data: AudioRequest, session=Depends(core.require_session)):
        """Save the next `seconds` (1-30) of the room's sound as a WAV."""
        if not core.AUDIO_FEED.available:
            raise HTTPException(503, "There is no microphone capture on this system")
        if _recording_count() >= MAX_RECORDING:
            raise HTTPException(429, "Too many recordings are running; try again when one ends")
        entry = _begin("audio", data, session, data.seconds)
        task = _background(_record_audio(entry["id"], data.seconds))
        return await _answer(entry, task, data.wait)

    async def _record_video(capture_id, seconds, sound):
        work = tempfile.mkdtemp(prefix="capture-", dir=store().dir)
        try:
            pcm_task = (_background(core.AUDIO_FEED.record(seconds))
                        if sound and core.AUDIO_FEED.available and captures.ffmpeg() else None)
            silent = os.path.join(work, "video.webm")
            if captures.ffmpeg():
                if not await _in_pool(captures.stream_video, _newest_jpeg, seconds, silent):
                    store().fail(capture_id, "the camera sent no frames, or the video could not be encoded")
                    return
            else:
                jpegs, fps = await _in_pool(captures.record_frames, _newest_jpeg, seconds)
                if not jpegs:
                    store().fail(capture_id, "the camera sent no frames")
                    return
                if not await _in_pool(captures.encode_video, jpegs, fps, silent):
                    store().fail(capture_id, "the video could not be encoded")
                    return
                del jpegs
            final = silent
            pcm = await pcm_task if pcm_task is not None else None
            if pcm:
                wav = os.path.join(work, "sound.wav")
                with open(wav, "wb") as fh:
                    fh.write(core.audio_wav_bytes(pcm))
                mixed = os.path.join(work, "mixed.webm")
                if await _in_pool(captures.mux_sound, silent, wav, mixed):
                    final = mixed
            entry = store().get(capture_id)
            if entry is None:
                return
            os.replace(final, store().path_of(entry))
            store().finish(capture_id)
        except Exception as exc:
            store().fail(capture_id, f"{type(exc).__name__}: {exc}")
        finally:
            for name in os.listdir(work) if os.path.isdir(work) else ():
                try:
                    os.unlink(os.path.join(work, name))
                except OSError:
                    pass
            try:
                os.rmdir(work)
            except OSError:
                pass

    @router.post("/video")
    async def capture_video(data: VideoRequest, session=Depends(core.require_session)):
        """Save the next `seconds` (1-60) of video as a WebM, with the room's
        sound in it when the Pi has ffmpeg."""
        _camera_live()
        if _recording_count() >= MAX_RECORDING:
            raise HTTPException(429, "Too many recordings are running; try again when one ends")
        sound = data.sound and core.AUDIO_FEED.available
        if sound and not captures.ffmpeg():
            # Without ffmpeg the sound cannot go into the video: it is saved
            # beside it instead, in the same group, over the same seconds.
            if not data.group.strip():
                data.group = "v" + os.urandom(4).hex()
            beside = _begin("audio", data, session, data.seconds)
            _background(_record_audio(beside["id"], data.seconds))
            sound = False
        entry = _begin("video", data, session, data.seconds)
        task = _background(_record_video(entry["id"], data.seconds, sound))
        return await _answer(entry, task, data.wait)

    # ── looking at them ───────────────────────────────────────────────────────
    @router.get("")
    async def list_captures(kind: Optional[str] = None, group: Optional[str] = None,
                            label: Optional[str] = None, since: Optional[str] = None,
                            limit: int = 50, session=Depends(core.require_session)):
        """Saved captures, newest first. since is an ISO time or epoch seconds."""
        if kind and kind not in captures.KINDS:
            raise HTTPException(400, f"kind is one of {', '.join(captures.KINDS)}")
        after = None
        if since:
            try:
                after = float(since)
            except ValueError:
                try:
                    after = datetime.datetime.fromisoformat(since).timestamp()
                except ValueError:
                    raise HTTPException(400, "since is an ISO time or epoch seconds")
        items = store().list(kind=kind, group=group, label=label, since=after, limit=limit)
        return {"captures": [{**c, "url": f"/api/captures/{c['id']}/file"} for c in items],
                "usage": store().usage(), "sound_in_video": bool(captures.ffmpeg())}

    @router.get("/{capture_id}/file")
    async def capture_file(capture_id: str, request: Request, token: Optional[str] = None,
                           download: bool = False):
        """The capture itself. Takes ?token= too, for an <img>, <audio> or <video>."""
        session_token = (request.cookies.get("garuda_session")
                         or request.headers.get("X-Garuda-Token") or token)
        if not core.get_session(session_token):
            raise HTTPException(401, "Not authenticated")
        entry = store().get(capture_id)
        path = store().path_of(entry) if entry else None
        if entry is None or entry["status"] != "saved" or not path or not os.path.exists(path):
            raise HTTPException(404, "no such capture")
        stamp = datetime.datetime.fromtimestamp(entry["at"]).strftime("%Y%m%d_%H%M%S")
        name = f"garuda_{entry['kind']}_{stamp}{os.path.splitext(path)[1]}"
        return FileResponse(path, media_type=entry["mime"], filename=name,
                            content_disposition_type="attachment" if download else "inline",
                            headers={"Cache-Control": "private, max-age=3600"})

    # ── keeping, sending and removing them ────────────────────────────────────
    @router.post("/{capture_id}/keep")
    async def keep_capture(capture_id: str, data: KeepRequest,
                           session=Depends(core.require_session)):
        """Keep a capture from being cleared out to make room, or let it go again."""
        _owner_or_admin(capture_id, session)
        return {"ok": True, "capture": store().set_keep(capture_id, data.keep)}

    @router.delete("/{capture_id}")
    async def delete_capture(capture_id: str, session=Depends(core.require_session)):
        entry = _owner_or_admin(capture_id, session)
        store().delete(capture_id)
        core.log_system_update(f"Capture deleted: {entry['kind']} {capture_id} by {session['username']}")
        return {"ok": True}

    @router.post("/email")
    async def email_captures(data: EmailRequest, session=Depends(core.require_session)):
        """Email captures to the alert recipients: those named by ids, or every
        one in group. Waits for any of them still recording."""
        cfg = core.STATE.config
        if core.STATE.modes.email_off or core.STATE.modes.idle:
            raise HTTPException(409, "Alert emails are off (email_off or idle mode)")
        if not (cfg.email_sender and cfg.email_sender_pass and cfg.email_recipients):
            raise HTTPException(409, "No alert email is set up (Settings → Email)")
        ids = list(data.ids) or [c["id"] for c in store().list(group=data.group, limit=20)
                                 if data.group]
        if not ids:
            raise HTTPException(400, "Name the captures (ids) or their group")
        entries = await _wait_saved(ids)
        attachments, skipped, total = [], [], 0
        for entry in sorted((e for e in entries if e["status"] == "saved"), key=lambda e: e["at"]):
            path = store().path_of(entry)
            if total + entry["bytes"] > MAX_EMAIL_BYTES:
                skipped.append(entry)
                continue
            with open(path, "rb") as fh:
                attachments.append((os.path.basename(path), entry["mime"], fh.read()))
            total += entry["bytes"]
        if not attachments:
            raise HTTPException(404, "None of those captures is saved")
        first = min(e["at"] for e in entries)
        when = datetime.datetime.fromtimestamp(first).strftime("%Y-%m-%d %H:%M:%S")
        label = next((e["label"] for e in entries if e.get("label")), "")
        alert = next((e["alert"] for e in entries if e.get("alert")), "")
        subject = f"Garuda: {label or 'captures'} at {when}"[:120]
        lines = [f"Captured at {when}" + (f" during an alert: {alert}" if alert else "") + "."]
        if data.note.strip():
            lines += ["", data.note.strip()]
        lines += ["", *[f"- {e['kind']}" + (f", {e['seconds']:g} s" if e.get("seconds") else "")
                        for e in entries if e["status"] == "saved" and e not in skipped]]
        if skipped:
            lines += ["", f"{len(skipped)} more too large to attach: open the Captures list."]
        try:
            await asyncio.to_thread(core._send_mail, subject, "\n".join(lines),
                                    attachments=attachments)
        except Exception as exc:
            raise HTTPException(502, f"The email could not be sent: {type(exc).__name__}")
        core.log_system_update(f"Captures emailed ({len(attachments)}) for {session['username']}")
        return {"ok": True, "sent": len(attachments), "skipped": len(skipped),
                "result": f"Emailed {len(attachments)} capture{'s' if len(attachments) != 1 else ''}"}

    return router
