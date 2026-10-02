"""Logs: the in-memory logs and the full download. Both need an admin session with the master key entered.

Moved out of Garuda_web.py (2026-10). Handler bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`.
"""
import asyncio
import datetime

from fastapi import APIRouter, Depends, Response


def build_logs_router(core):
    router = APIRouter()

    @router.get("/api/logs")
    async def get_logs(session=Depends(core.require_logs)):
        return {
            "system_log": core.system_updates_log,
            "voice_log": core.voice_assistant_log,
            "voice_responses": core.voice_responses,
            "presence_log": core.STATE.presence.log[-200:],
            "detection_log": core._detection_log[-200:],
        }

    @router.get("/api/logs/download")
    async def download_logs(session=Depends(core.require_logs)):
        """Return all permanent logs as a single combined text file for download."""
        # Up to 30 MB of files are read here: on a worker thread, not the loop.
        content = await asyncio.to_thread(core._combined_log_text)
        fname = f"garuda-full-log-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
        return Response(
            content=content,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    return router
