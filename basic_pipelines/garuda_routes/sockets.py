"""The two sockets a signed-in browser opens: the state push (/ws) and the camera frames (/ws/stream).

Moved out of Garuda_web.py (2026-10). Handler bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`.
"""
import asyncio
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Optional


def build_sockets_router(core):
    router = APIRouter()

    @router.websocket("/ws/stream")
    async def ws_stream(websocket: WebSocket, token: Optional[str] = None):
        """Streams JPEG frames as binary WebSocket messages (~same as MJPEG but WS).
        Works through Cloudflare Tunnel (unlike raw UDP WebRTC)."""
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
        last_seq = -1
        next_check = time.time() + core._STREAM_RECHECK_S
        try:
            while True:
                if time.time() >= next_check:
                    if not core._user_signed_in(username):
                        await websocket.close(code=4001)
                        return
                    next_check = time.time() + core._STREAM_RECHECK_S
                with core._frame_lock:
                    seq   = core._frame_seq
                    frame = core._frame_buffer if seq != last_seq else None
                if frame is not None:
                    last_seq = seq
                    await websocket.send_bytes(frame)
                else:
                    await asyncio.sleep(0.02)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            core.log_system_update(f"[STREAM] WS stream error: {type(e).__name__}")

    @router.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket, token: Optional[str] = None):
        # Rate-limit WebSocket connections per IP
        if not core._ws_connect_allowed(websocket):
            await websocket.close(code=4029)
            return
        # Accept token from cookie (same-origin) or query param (cross-origin)
        token = websocket.cookies.get("garuda_session") or token
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
