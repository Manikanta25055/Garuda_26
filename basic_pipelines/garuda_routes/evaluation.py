"""The evaluation harness: inject a detection, tag the log, read the frame counter. Closed unless GARUDA_EVAL_TOKEN is set.

Moved out of Garuda_web.py (2026-10). Handler bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`.
"""
import asyncio
import time

from fastapi import APIRouter, Request
from pydantic import BaseModel


class EvalInjectRequest(BaseModel):
    label: str = "Knife"
    confidence: float = 0.92
    email: bool = False

class EvalTagRequest(BaseModel):
    tag: str
    note: str = ""


def build_evaluation_router(core):
    router = APIRouter()

    @router.post("/api/eval/inject_danger")
    async def eval_inject_danger(data: EvalInjectRequest, request: Request):
        core._require_eval_token(request)
        t_req = time.time()
        core.log_scissors_detection(data.label)
        core.log_system_update(f"[EVAL_INJECT] {data.label} conf={data.confidence:.2f}")
        core.trigger_software_alert()
        if data.email:
            try:
                await asyncio.to_thread(core.send_email_alert)
            except Exception as e:
                core.log_system_update(f"[EVAL_INJECT] email failed: {e}")
        return {"ok": True, "t_request": t_req, "t_alert": time.time(),
                "latency_ms": round((time.time() - t_req) * 1000, 2),
                "label": data.label, "confidence": data.confidence}

    @router.post("/api/eval/tag")
    async def eval_tag(data: EvalTagRequest, request: Request):
        core._require_eval_token(request)
        msg = f"[EVAL_TAG] {data.tag}"
        if data.note:
            msg += f" — {data.note}"
        core.log_system_update(msg)
        return {"ok": True, "t": time.time(), "tag": data.tag, "note": data.note}

    @router.get("/api/eval/fps_probe")
    async def eval_fps_probe(request: Request):
        core._require_eval_token(request)
        with core.STATE.modes.lock:
            modes = {
                "dnd": core.STATE.modes.dnd, "email_off": core.STATE.modes.email_off,
                "idle": core.STATE.modes.idle, "night": core.STATE.modes.night,
                "emergency": core.STATE.modes.emergency, "privacy": core.STATE.modes.privacy,
            }
        cm = core._cascade_metrics.snapshot() if core._cascade_metrics else {}
        return {
            "t": time.time(),
            "uptime": time.time() - core._app_start_time,
            "total_frames": core.STATE.camera.total_frames,
            "modes": modes,
            "cascade": cm,
            "alert_active": core.STATE.alerts.active,
        }

    return router
