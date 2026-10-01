"""The camera over HTTP: the MJPEG stream, a snapshot, clip recording and the WebRTC offer.

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
from pydantic import BaseModel
from typing import Optional


class WebRTCOfferRequest(BaseModel):
    sdp: str
    type: str


def build_camera_router(core):
    router = APIRouter()

    @router.get("/stream")
    async def mjpeg_stream(request: Request, token: Optional[str] = None):
        # Authenticate via cookie or ?token= query param
        session_token = request.cookies.get("garuda_session") or token
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
        session_token = request.cookies.get("garuda_session") or token
        if not core.get_session(session_token):
            raise HTTPException(401, "Not authenticated")
        with core._frame_lock:
            raw = core._frame_raw
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

    @router.post("/api/clip/start")
    async def clip_start(session=Depends(core.require_session)):
        # Fast check — avoid I/O if already recording
        with core._clip_lock:
            if core._clip_writer is not None:
                return {"ok": True, "already_recording": True, "path": core._clip_path}
        # Read frame dims and create VideoWriter OUTSIDE the lock (file I/O must not block event loop)
        with core._frame_lock:
            raw = core._frame_raw
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
        with core._clip_lock:
            if core._clip_writer is not None:
                writer.release()
                return {"ok": True, "already_recording": True, "path": core._clip_path}
            core._clip_writer = writer
            core._clip_path = new_path
            core._clip_start_time = time.time()
        core.log_system_update(f"Clip recording started by {session['username']}.")
        return {"ok": True, "path": core._clip_path}

    @router.post("/api/clip/stop")
    async def clip_stop(session=Depends(core.require_session)):
        with core._clip_lock:
            if core._clip_writer is None:
                return {"ok": True, "was_recording": False}
            core._clip_writer.release()
            core._clip_writer = None
            path = core._clip_path
        core.log_system_update(f"Clip saved: {path}")
        threading.Thread(target=core.exfiltrate_clip, args=(path,), daemon=True).start()
        return {"ok": True, "path": path}

    # ── WebRTC offer/answer ───────────────────────────────────────────────────────
    @router.post("/webrtc/offer")
    async def webrtc_offer(data: WebRTCOfferRequest, session=Depends(core.require_session)):
        if not core._WEBRTC_AVAILABLE:
            raise HTTPException(501, "aiortc not installed")
        if data.type != "offer" or len(data.sdp) > 20000:
            raise HTTPException(400, "Invalid offer")
        # Each connection runs its own H.264 encoder; without a ceiling a signed-in
        # client could open them until the Pi had nothing left for detection.
        if len(core._pc_set) >= core._MAX_PEER_CONNECTIONS:
            raise HTTPException(503, "Too many live video connections. Close one and try again.")
        pc = core.RTCPeerConnection()
        core._pc_set.add(pc)

        @pc.on("connectionstatechange")
        async def _on_state():
            if pc.connectionState in ("failed", "closed", "disconnected"):
                await pc.close()
                core._pc_set.discard(pc)

        try:
            pc.addTrack(core.GarudaVideoTrack())
            offer = core.RTCSessionDescription(sdp=data.sdp, type=data.type)
            await pc.setRemoteDescription(offer)
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)

            # Wait for ICE gathering to complete, but not for ever: with no route
            # out this loop never ended and the request (and the connection) hung.
            deadline = time.time() + 10
            while pc.iceGatheringState != "complete":
                if time.time() > deadline:
                    raise HTTPException(504, "WebRTC negotiation timed out")
                await asyncio.sleep(0.1)
        except Exception as exc:
            # A bad offer used to leave the half-built connection in _pc_set for good.
            core._pc_set.discard(pc)
            try:
                await pc.close()
            except Exception:
                pass
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(400, f"Could not negotiate video: {type(exc).__name__}")

        return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}

    return router
