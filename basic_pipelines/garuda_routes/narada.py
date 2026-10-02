"""Narada over HTTP: typed chat (whole and streamed), the voice token and the models-and-usage panel.

Moved out of Garuda_web.py (2026-10). Handler bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`.
"""
import anyio.to_thread
import asyncio
import json
import re
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(max_length=2000)


class MemoryFactRequest(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    category: str | None = Field(default=None, max_length=20)


class MuteRequest(BaseModel):
    key: str = Field(min_length=3, max_length=80, pattern=r"^[a-z-]+(:[A-Za-z0-9_-]+){0,2}$")


class ActionConfirmRequest(BaseModel):
    # What the person typed on the card (a password for a new account).
    fields: dict[str, str] = Field(default_factory=dict, max_length=4)


class MemoryEditRequest(BaseModel):
    text: str | None = Field(default=None, max_length=300)
    category: str | None = Field(default=None, max_length=20)


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
                "memory": result.get("memory", []), "offer": result.get("offer"),
                "observation": result.get("observation"),
                "confirm": result.get("confirm", []),
                "artifacts": result.get("artifacts", []), "steps": result.get("steps", []),
                "planner": bool(result.get("planner")),
                "route": result.get("route"), "model": result.get("model")}


    @router.post("/api/chat/stream")
    async def chat_stream(data: ChatRequest, request: Request, session=Depends(core.require_session)):
        """SSE chat: what Narada is doing while it works (which model took the
        request, each step as it starts and ends), then the reply, word by word."""
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
                result = core._assistant_reply(
                    msg, user, role, scope,
                    progress=lambda event: loop.call_soon_threadsafe(
                        queue.put_nowait, ("progress", event)))
            except Exception as exc:
                result = {"reply": f"Something went wrong: {type(exc).__name__}", "actions": []}
            meta = {k: result.get(k) for k in ("lane", "actions", "proposal", "memory", "offer",
                                               "observation", "confirm", "artifacts", "steps",
                                               "planner", "model")}
            loop.call_soon_threadsafe(queue.put_nowait, ("meta", meta))
            for word in re.findall(r"\S+\s*", result["reply"]):
                loop.call_soon_threadsafe(queue.put_nowait, ("token", word))
            loop.call_soon_threadsafe(queue.put_nowait, ("done", None))

        threading.Thread(target=_agent_worker, daemon=True).start()

        async def generate():
            yield f"data: {json.dumps({'type': 'start'})}\n\n"
            waited = 0
            while True:
                try:
                    kind, payload_val = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    # The planner can think for a minute or two between steps. A
                    # comment line keeps the tunnel from closing a quiet response.
                    waited += 15
                    if waited >= 900:
                        break
                    yield ": waiting\n\n"
                    continue
                waited = 0
                if kind == "progress":
                    yield f"data: {json.dumps({'type': 'progress', 'event': payload_val})}\n\n"
                    continue
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

    @router.get("/api/narada/progress")
    async def narada_progress(session=Depends(core.require_session)):
        """What Narada is doing for this person right now, for a spoken turn:
        the browser cannot be sent steps through the voice service."""
        return {"progress": core.AGENT.live_for(session["username"])}

    # ── What Narada knows ─────────────────────────────────────────────────────
    # One memory for the household: anyone signed in may read and correct it.
    # Every change is written to the system log with who made it.

    def _memory_view():
        memory = core.BRAIN.memory
        newest = lambda facts: sorted(facts, key=lambda f: f.get("updated", 0), reverse=True)  # noqa: E731
        return {"facts": newest(memory.facts()), "pending": newest(memory.pending()),
                "archived": sorted(memory.archived(), key=lambda f: f["archived"]["at"], reverse=True),
                "categories": list(core.BRAIN.memory_categories)}

    @router.get("/api/narada/memory")
    async def narada_memory(since: float | None = None, session=Depends(core.require_session)):
        """Everything Narada knows; with ?since=<time> only what memory did after it
        (how a spoken conversation gets its "Saved to memory" chips)."""
        if since is not None:
            return {"recent": core.BRAIN.events_since(since), "now": time.time()}
        return {**_memory_view(), "now": time.time()}

    @router.post("/api/narada/memory")
    async def narada_memory_add(data: MemoryFactRequest, session=Depends(core.require_session)):
        outcome = core.BRAIN.memory.remember(data.text, category=data.category,
                                             origin="manual", by=session["username"])
        if outcome["status"] == "rejected":
            raise HTTPException(400, outcome["reason"])
        core.log_system_update(f"Narada memory: {session['username']} added a fact ({outcome['status']})")
        return {"status": outcome["status"], "fact": outcome["fact"]}

    @router.patch("/api/narada/memory/{fact_id}")
    async def narada_memory_edit(fact_id: str, data: MemoryEditRequest,
                                 session=Depends(core.require_session)):
        if data.text is None and data.category is None:
            raise HTTPException(400, "Nothing to change")
        fact, reason = core.BRAIN.memory.edit(fact_id, text=data.text, category=data.category)
        if fact is None:
            raise HTTPException(404 if reason == "no such fact" else 400, reason)
        core.log_system_update(f"Narada memory: {session['username']} edited a fact")
        return {"fact": fact}

    @router.delete("/api/narada/memory/{fact_id}")
    async def narada_memory_remove(fact_id: str, forever: bool = False,
                                   session=Depends(core.require_session)):
        """Archive a fact (it can be restored), or with ?forever=1 remove it for good."""
        memory = core.BRAIN.memory
        existing = memory.get(fact_id)
        if existing is None:
            raise HTTPException(404, "No such fact")
        if forever:
            memory.purge(fact_id)
        elif not existing.get("archived"):
            memory.forget(fact_id)
        core.log_system_update(f"Narada memory: {session['username']} "
                               f"{'deleted' if forever else 'removed'} a fact")
        return {"ok": True}

    @router.post("/api/narada/memory/{fact_id}/restore")
    async def narada_memory_restore(fact_id: str, session=Depends(core.require_session)):
        fact = core.BRAIN.memory.restore(fact_id)
        if fact is None:
            raise HTTPException(404, "No such removed fact")
        core.log_system_update(f"Narada memory: {session['username']} restored a fact")
        return {"fact": fact}

    @router.post("/api/narada/memory/{fact_id}/confirm")
    async def narada_memory_confirm(fact_id: str, session=Depends(core.require_session)):
        """Keep a fact Narada was holding because the person had not said it in so many words."""
        fact = core.BRAIN.memory.confirm(fact_id)
        if fact is None:
            raise HTTPException(404, "Nothing is waiting under that id")
        core.log_system_update(f"Narada memory: {session['username']} confirmed a fact")
        return {"fact": fact}

    @router.post("/api/narada/observations/mute")
    async def narada_mute_observation(data: MuteRequest, session=Depends(core.require_session)):
        """ "Don't tell me this": Narada stops making one kind of remark. It is kept
        in memory as the household's choice, and removing it there undoes it."""
        names = {d["id"]: d["name"] for d in core.DRISHTI_CTX.registry.devices}
        outcome = core.BRAIN.mute_notice(data.key, names, by=session["username"])
        if outcome is None:
            raise HTTPException(400, "Narada makes no such remark")
        if outcome["status"] == "rejected":
            raise HTTPException(400, outcome["reason"])
        core.log_system_update(f"Narada: {session['username']} muted the remark {data.key}")
        return {"ok": True, "fact": outcome["fact"]}

    # ── Cards Narada asks a person to confirm ─────────────────────────────────
    # The model proposes these; nothing happens until the person who asked taps
    # Confirm. The action then runs as them, through the site's own endpoint,
    # so the endpoint's guard and checks decide here too.

    @router.post("/api/narada/actions/{action_id}/confirm")
    async def narada_action_confirm(action_id: str, data: ActionConfirmRequest, request: Request,
                                    session=Depends(core.require_session)):
        agent = core.AGENT
        entry = agent.confirmations.take(action_id, session["username"])
        if entry is None:
            raise HTTPException(404, "That card has expired. Ask Narada again.")
        capability = core.AGENT_CAPABILITIES[entry["capability"]]
        args = dict(entry["args"])
        for name in capability.typed:
            value = (data.fields.get(name) or "").strip()
            if value:
                args[name] = value
            elif name in capability.required:
                agent.confirmations.restore(entry)
                raise HTTPException(400, f"{name.replace('_', ' ').capitalize()} is needed.")
        method, path = capability.call.split(" ", 1)
        out = await agent.site.acall(method, path, args, session, request)
        words = capability.name.replace("_", " ")
        if isinstance(out, dict) and "error" in out:
            if out.get("status") == 400:
                agent.confirmations.restore(entry)   # fixable on the card: try again
            core.log_system_update(f"Narada: {session['username']} confirmed {words}; refused "
                                   f"({out['error']})")
            raise HTTPException(out.get("status") or 400, out["error"])
        core.log_system_update(f"Narada: {session['username']} confirmed {words}")
        # Narada should know its proposal was carried out when the person next speaks.
        shown = ", ".join(f"{k}={v}" for k, v in entry["args"].items())
        core.BRAIN.record(entry["key"] or session["username"], [{
            "role": "assistant",
            "content": f"(The person tapped Confirm on your card: {words} {shown}. It is done.)"}])
        return {"ok": True, "result": out}

    @router.post("/api/narada/actions/{action_id}/cancel")
    async def narada_action_cancel(action_id: str, session=Depends(core.require_session)):
        entry = core.AGENT.confirmations.take(action_id, session["username"])
        if entry is not None:
            core.BRAIN.record(entry["key"] or session["username"], [{
                "role": "assistant",
                "content": "(The person tapped Cancel on your card: "
                           f"{entry['capability'].replace('_', ' ')}. It was not done.)"}])
        return {"ok": True}

    return router
