"""Narada over HTTP: typed chat (whole and streamed), the voice token and the models-and-usage panel.

Moved out of Garuda_web.py (2026-10). Handler bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`.
"""
import anyio.to_thread
import asyncio
import json
import re
import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(max_length=2000)


def build_narada_router(core):
    router = APIRouter()

    @router.post("/api/chat")
    async def chat(data: ChatRequest, request: Request, session=Depends(core.require_session)):
        msg = data.message.strip()
        if not msg:
            raise HTTPException(400, "Empty message")
        scope = core._product_for_host(request.headers.get("host"))
        result = await anyio.to_thread.run_sync(
            lambda: core._assistant_reply(msg, session["username"], session["role"], scope))
        return {"response": result["reply"], "lane": result.get("lane"),
                "actions": result.get("actions", []), "proposal": result.get("proposal"),
                "route": result.get("route"), "model": result.get("model")}


    @router.post("/api/chat/stream")
    async def chat_stream(data: ChatRequest, request: Request, session=Depends(core.require_session)):
        """SSE chat: the NIM agent's reply, replayed word by word."""
        msg = data.message.strip()
        if not msg:
            raise HTTPException(400, "Empty message")

        loop  = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()
        user, role = session["username"], session["role"]
        scope = core._product_for_host(request.headers.get("host"))

        def _agent_worker():
            # The agent answers in one piece after its tool calls, so the reply is
            # replayed word by word to keep the chat's typing feel.
            try:
                result = core._assistant_reply(msg, user, role, scope)
            except Exception as exc:
                result = {"reply": f"Something went wrong: {type(exc).__name__}", "actions": []}
            meta = {k: result.get(k) for k in ("lane", "actions", "proposal", "model")}
            loop.call_soon_threadsafe(queue.put_nowait, ("meta", meta))
            for word in re.findall(r"\S+\s*", result["reply"]):
                loop.call_soon_threadsafe(queue.put_nowait, ("token", word))
            loop.call_soon_threadsafe(queue.put_nowait, ("done", None))

        threading.Thread(target=_agent_worker, daemon=True).start()

        async def generate():
            yield f"data: {json.dumps({'type': 'start'})}\n\n"
            while True:
                try:
                    kind, payload_val = await asyncio.wait_for(queue.get(), timeout=90)
                except asyncio.TimeoutError:
                    break
                if kind == "done":
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    break
                if kind == "meta":
                    yield f"data: {json.dumps({'type': 'meta', **payload_val})}\n\n"
                    continue
                yield f"data: {json.dumps({'type': 'token', 'text': payload_val})}\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/api/narada/voice/token")
    async def narada_voice_token(request: Request, session=Depends(core.require_session)):
        """A one-conversation token for the browser; binds it to this user."""
        if not core.NARADA_VOICE.configured:
            raise HTTPException(503, "Voice is not set up yet: an admin needs to run "
                                     "scripts/setup_narada_voice.py.")
        scope = core._product_for_host(request.headers.get("host"))
        try:
            return await anyio.to_thread.run_sync(
                lambda: core.NARADA_VOICE.issue_token(session["username"], session["role"], scope))
        except Exception as exc:
            core.log_system_update(f"Narada voice token failed: {type(exc).__name__}")
            raise HTTPException(502, "Could not reach the voice service. Try again in a moment.")


    @router.get("/api/narada/info")
    async def narada_info(session=Depends(core.require_session)):
        """What the "i" panel on the Narada page shows: models and usage."""
        nim = core.NIM_CHAT.status()
        voice = await anyio.to_thread.run_sync(core.NARADA_VOICE.info)
        return {"nim": {k: nim.get(k) for k in ("configured", "models", "last_model",
                                                "last_latency_s", "calls", "tokens_used")},
                "voice": voice}

    return router
