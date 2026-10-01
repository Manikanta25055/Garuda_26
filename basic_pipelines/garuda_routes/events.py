"""The event queue over HTTP: what a client missed, what is waiting, how much.

Moved out of Garuda_web.py (2026-10). Handler bodies are unchanged except
that names belonging to Garuda_web are read from `core`.
"""
from fastapi import APIRouter, Depends


def build_events_router(core):
    router = APIRouter()

    @router.get("/api/events/since")
    async def events_since(since: str = "", limit: int = 500, session=Depends(core.require_session)):
        """Return events after the given ISO timestamp, oldest-first."""
        limit = max(1, min(limit, 500))   # SQLite reads LIMIT -1 as "no limit"
        events = core.get_events_since(since[:40], limit)
        return {"events": events, "count": len(events)}

    @router.get("/api/events/pending")
    async def events_pending(session=Depends(core.require_session)):
        """Return all unsynced events and mark them as synced."""
        unsynced = core.get_unsynced_events(1000)
        if unsynced:
            max_id = max(e["id"] for e in unsynced)
            core.mark_events_synced(max_id)
        return {"events": unsynced, "count": len(unsynced)}

    @router.get("/api/events/stats")
    async def events_stats(session=Depends(core.require_session)):
        """Return queue statistics."""
        pending = core.get_pending_count()
        total = core._events.total(core.EVENTS_DB)
        return {"pending": pending, "total": total, "online": core._net_online}

    return router
