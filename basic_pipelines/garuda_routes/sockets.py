"""The sockets a signed-in browser opens: the state push (/ws), the camera frames (/ws/stream)
and the microphone (/ws/audio).

Moved out of Garuda_web.py (2026-10). Handler bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`.
"""
import asyncio
import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Optional


def build_sockets_router(core):
    router = APIRouter()

    @router.websocket("/ws/stream")
    async def ws_stream(websocket: WebSocket, token: Optional[str] = None, ack: int = 0):
        """Streams JPEG frames as binary WebSocket messages (~same as MJPEG but WS).
        Works through Cloudflare Tunnel (unlike raw UDP WebRTC).

        With ?ack=1 the browser answers each frame once it is on screen, and no
        more than _STREAM_FRAMES_IN_FLIGHT go unanswered. On a slow link frames
        are then skipped instead of queuing in the network, so what is shown
        stays live rather than drifting seconds behind."""
        # Rate-limit WebSocket connections per IP (re-use the global _rate_store)
        if not core._ws_connect_allowed(websocket):
            await websocket.close(code=4029)
            return
        token = websocket.cookies.get("garuda_session") or token
        session = core.get_session(token)
        if not session:
            await websocket.close(code=4001)
            return
        username = session["username"]
        await websocket.accept()
        in_flight = 0
        acked = asyncio.Event()

        async def _read_acks():
            nonlocal in_flight
            while True:
                await websocket.receive_text()
                in_flight = max(0, in_flight - 1)
                acked.set()

        ack_task = asyncio.create_task(_read_acks()) if ack else None
        last_seq = -1
        next_check = time.time() + core._STREAM_RECHECK_S
        try:
            while True:
                if time.time() >= next_check:
                    if not core._user_signed_in(username):
                        await websocket.close(code=4001)
                        return
                    next_check = time.time() + core._STREAM_RECHECK_S
                if ack_task is not None:
                    if ack_task.done():
                        ack_task.result()          # the browser went away
                        return
                    if in_flight >= core._STREAM_FRAMES_IN_FLIGHT:
                        acked.clear()
                        try:
                            await asyncio.wait_for(acked.wait(), 1.0)
                        except asyncio.TimeoutError:
                            in_flight = 0          # an answer was lost; don't stall for good
                        continue
                with core.STATE.camera.frame_lock:
                    seq   = core.STATE.camera.frame_seq
                    frame = core.STATE.camera.frame_buffer if seq != last_seq else None
                if frame is not None:
                    last_seq = seq
                    in_flight += 1
                    await websocket.send_bytes(frame)
                else:
                    await asyncio.sleep(0.01)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            core.log_system_update(f"[STREAM] WS stream error: {type(e).__name__}")
        finally:
            if ack_task is not None:
                ack_task.cancel()

    @router.websocket("/ws/audio")
    async def ws_audio(websocket: WebSocket, token: Optional[str] = None):
        """The microphone, live: one text message describing the format, then
        20 ms chunks of raw PCM. Separate from /ws/stream so a large frame
        never holds the sound up behind it."""
        if not core._ws_connect_allowed(websocket):
            await websocket.close(code=4029)
            return
        token = websocket.cookies.get("garuda_session") or token
        session = core.get_session(token)
        if not session:
            await websocket.close(code=4001)
            return
        feed = core.AUDIO_FEED
        if not feed.available:
            await websocket.close(code=4503)
            return
        username = session["username"]
        await websocket.accept()
        await websocket.send_text(json.dumps(feed.describe()))
        entry = feed.subscribe()
        q = entry[1]
        closed = asyncio.create_task(_until_closed(websocket))
        next_check = time.time() + core._STREAM_RECHECK_S
        try:
            while not closed.done():
                if time.time() >= next_check:
                    if not core._user_signed_in(username):
                        await websocket.close(code=4001)
                        return
                    next_check = time.time() + core._STREAM_RECHECK_S
                try:
                    chunk = await asyncio.wait_for(q.get(), 1.0)
                except asyncio.TimeoutError:
                    continue                       # microphone silent or restarting
                await websocket.send_bytes(chunk)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            core.log_system_update(f"[STREAM] audio socket error: {type(e).__name__}")
        finally:
            closed.cancel()
            feed.unsubscribe(entry)

    @router.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket, token: Optional[str] = None):
        # Rate-limit WebSocket connections per IP
        if not core._ws_connect_allowed(websocket):
            await websocket.close(code=4029)
            return
        # Accept token from cookie (same-origin), header (the macOS app, which
        # keeps it out of the address) or query param (cross-origin browsers)
        token = (websocket.cookies.get("garuda_session")
                 or websocket.headers.get("X-Garuda-Token") or token)
        session = core.get_session(token)
        if not session:
            await websocket.close(code=4001)
            return
        await websocket.accept()
        core._ws_clients[websocket] = {"username": session["username"], "role": session["role"]}
        try:
            # Keep the connection alive; broadcaster pushes state.
            # Drain any client messages; the frontend does not send data, so we
            # just wait indefinitely — WebSocketDisconnect fires on close/error.
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            core._ws_clients.pop(websocket, None)

    return router


async def _until_closed(websocket):
    """Returns when the browser closes the socket (it sends nothing else)."""
    try:
        while True:
            await websocket.receive_text()
    except Exception:
        return
