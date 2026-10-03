"""Feedback: anyone can send it, an admin reads it.

Moved out of Garuda_web.py (2026-10). Handler bodies are unchanged except
that names belonging to Garuda_web are read from `core`. Where the entries
are stored (and the lock around them) stays with Garuda_web.
"""
import asyncio
import datetime
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel


class FeedbackRequest(BaseModel):
    message: str
    category: str = "general"   # bug | feature | general | other
    rating: int   = 0           # 1-5 stars, 0 = not rated
    name: str     = ""          # optional, anonymous if blank


def build_feedback_router(core):
    router = APIRouter()

    @router.post("/api/feedback")
    async def submit_feedback(data: FeedbackRequest, request: Request):
        """Public endpoint — no auth required. Rate-limited to 5 per hour per IP."""
        ip = core._get_client_ip(request)
        now = time.time()
        # Reuse _rate_store but with a separate key to avoid conflating with API limits
        fb_key = f"fb:{ip}"
        stamps = core.STATE.auth.rate_store[fb_key]
        stamps[:] = [t for t in stamps if now - t < 3600]
        if len(stamps) >= 5:
            raise HTTPException(429, "Too many feedback submissions. Try again later.")
        stamps.append(now)

        msg = data.message.strip()
        if not msg:
            raise HTTPException(400, "Message cannot be empty.")
        if len(msg) > 1000:
            raise HTTPException(400, "Message too long (max 1000 chars).")
        rating = max(0, min(5, int(data.rating)))
        category = data.category.strip().lower()[:16]
        if category not in ("bug", "feature", "general", "other"):
            category = "general"
        name = data.name.strip()[:64] if data.name else ""

        entry = {
            "id": int(time.time() * 1000),
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "category": category,
            "rating": rating,
            "name": name or "Anonymous",
            "message": msg,
            "ip": ip,
        }

        def _write_entry():
            with core._feedback_lock:
                entries = core._load_feedback()
                entries.append(entry)
                del entries[:-core._FEEDBACK_MAX]    # an open endpoint must not grow a file for ever
                core._save_feedback(entries)

        await asyncio.to_thread(_write_entry)
        core.log_system_update(f"Feedback received [{category}] from {name or 'Anonymous'}")
        return {"ok": True}

    @router.get("/api/feedback")
    async def get_feedback(session=Depends(core.require_admin)):
        """Admin-only — returns all stored feedback entries."""
        def _read():
            with core._feedback_lock:
                return core._load_feedback()
        entries = await asyncio.to_thread(_read)
        return {"feedback": entries, "count": len(entries)}

    return router
