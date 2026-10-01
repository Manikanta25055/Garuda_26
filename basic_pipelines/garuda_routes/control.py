"""Running the system: the state snapshot, mode switches, the heartbeat for an outside monitor, and the emergency stop.

Moved out of Garuda_web.py (2026-10). Handler bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`.
"""
import asyncio
import hmac
import os
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional


class ModeRequest(BaseModel):
    mode: str   # "dnd","email_off","idle","night","emergency","privacy"
    value: bool


def build_control_router(core):
    router = APIRouter()

    @router.get("/api/state")
    async def get_state(session=Depends(core.require_session)):
        payload = await asyncio.to_thread(core.get_state_dict)
        return core._state_for_role(payload, session["role"])

    @router.get("/api/cascade_metrics")
    async def get_cascade_metrics(session=Depends(core.require_session)):
        return core._cascade_metrics.snapshot()

    # The native (Swift) app posts to /api/set-mode, which never existed here: its
    # mode switches failed with 404. Same handler, both names.
    @router.post("/api/modes")
    @router.post("/api/set-mode", include_in_schema=False)
    async def set_mode(data: ModeRequest, session=Depends(core.require_session)):
        mode_map = {
            "dnd": "MODE_DND", "email_off": "MODE_EMAIL_OFF",
            "idle": "MODE_IDLE", "night": "MODE_NIGHT",
            "emergency": "MODE_EMERGENCY", "privacy": "MODE_PRIVACY",
        }
        if data.mode not in mode_map:
            raise HTTPException(400, f"Unknown mode: {data.mode}")
        if data.mode in core.ADMIN_ONLY_MODES and data.value and session["role"] != "admin":
            # Idle and Email Off stop the system telling anyone about a threat.
            # Any signed-in profile could switch them on; switching them back off
            # (the safe direction) stays open to everyone.
            raise HTTPException(403, "Only an admin can turn this mode on: it silences alerts.")
        with core._mode_lock:
            core._set_mode_flag(mode_map[data.mode], data.value)
            if data.mode == "emergency" and data.value:
                core.MODE_DND = False
        await core._async_save_config()
        core.log_system_update(f"Mode {data.mode} set to {data.value} by {session['username']}")
        core.push_urgent_ws()
        return {"ok": True, "modes": core.get_state_dict()["modes"]}

    @router.get("/api/heartbeat")
    async def heartbeat(request: Request, key: Optional[str] = None):
        """Health check for external monitors (UptimeRobot etc.).
        Accepts an optional ?key= query param or X-Heartbeat-Key header to guard
        the dead-man reset. Without a key the endpoint still returns health data
        but does NOT reset the deadman timer (prevents unauthenticated suppression).
        """
        _HEARTBEAT_KEY = os.environ.get("HEARTBEAT_KEY", "")
        provided = key or request.headers.get("X-Heartbeat-Key", "")
        # Only reset dead-man's switch if key matches (or no key configured)
        if not _HEARTBEAT_KEY or hmac.compare_digest(str(provided).encode(), _HEARTBEAT_KEY.encode()):
            core._last_heartbeat = time.time()
            core._deadman_alert_sent = False
            core._heartbeat_ever = True
        return {"ok": True, "uptime": int(time.time() - core._app_start_time)}

    @router.post("/api/emergency-stop")
    async def emergency_stop(session=Depends(core.require_admin)):
        core.log_system_update(f"Emergency stop by {session['username']}.")
        threading.Thread(target=core.stop_app, daemon=True).start()
        return {"ok": True}

    return router
