"""Configuration: read and change settings, and the custom Narada phrases (admin only).

Moved out of Garuda_web.py (2026-10). Handler bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`.
"""
import asyncio
import os
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional


class ConfigUpdateRequest(BaseModel):
    detection_threshold: Optional[float] = None
    email_sender: Optional[str] = None
    email_sender_pass: Optional[str] = None
    email_recipients: Optional[List[str]] = None
    email_cooldown: Optional[int] = None
    danger_label: Optional[str] = None    # legacy single-label (maps to danger_labels)
    danger_labels: Optional[List[str]] = None
    privacy: Optional[bool] = None
    watch_labels: Optional[List[str]] = None
    mode_schedule: Optional[dict] = None
    night_presence_start: Optional[str] = None
    night_presence_end: Optional[str] = None
    night_presence_enabled: Optional[bool] = None

class CustomCommandRequest(BaseModel):
    phrase: str
    response: str

class DeleteCommandRequest(BaseModel):
    phrase: str


def build_config_router(core):
    router = APIRouter()

    @router.get("/api/config")
    async def get_config(session=Depends(core.require_admin)):
        return {
            "detection_threshold": core.STATE.config.detection_threshold,
            "danger_labels": core.STATE.config.danger_labels,
            "email_sender": core.STATE.config.email_sender,
            "email_recipients": core.STATE.config.email_recipients,
            "email_cooldown": core.STATE.config.email_cooldown,
            "privacy": core.STATE.modes.privacy,
            "custom_voice_commands": core.STATE.config.custom_voice_commands,
            "custom_modes": core.STATE.modes.custom,
            "watch_labels": core.STATE.config.watch_labels,
            "mode_schedule": core.STATE.modes.schedule,
            "night_presence_window": core.STATE.config.night_presence_window,
        }

    @router.post("/api/config")
    async def update_config(data: ConfigUpdateRequest, session=Depends(core.require_admin)):
        if data.detection_threshold is not None:
            core.STATE.config.detection_threshold = max(0.05, min(0.95, data.detection_threshold))
        if data.email_sender is not None:
            sender = data.email_sender.strip()
            if sender and not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', sender):
                raise HTTPException(400, f"Invalid sender address: {sender}")
            core.STATE.config.email_sender = sender
        if data.email_sender_pass is not None:
            new_pass = data.email_sender_pass.strip()
            if len(new_pass) > 128 or any(c in new_pass for c in "\r\n\x00"):
                raise HTTPException(400, "Invalid app password.")
            core.STATE.config.email_sender_pass = new_pass
            # Kept out of config.json on purpose, which meant a password typed
            # into the Email page worked until the next restart and then silently
            # reverted. It goes to .env (0600), next to the other secrets.
            try:
                await asyncio.to_thread(core._set_env_vars, core.HOME_ENV_PATH, {"EMAIL_SENDER_PASS": new_pass})
                os.environ["EMAIL_SENDER_PASS"] = new_pass
            except (OSError, ValueError) as exc:
                core.log_system_update(f"Email password applied but not saved: {exc}")
        if data.email_recipients is not None:
            if len(data.email_recipients) > 10:
                raise HTTPException(400, "Maximum 10 email recipients allowed.")
            for addr in data.email_recipients:
                if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', addr):
                    raise HTTPException(400, f"Invalid email address: {addr}")
            core.STATE.config.email_recipients = data.email_recipients
        if data.email_cooldown is not None:
            if not (5 <= data.email_cooldown <= 3600):
                raise HTTPException(400, "Email cooldown must be between 5 and 3600 seconds.")
            core.STATE.config.email_cooldown = data.email_cooldown
        if data.privacy is not None:
            with core.STATE.modes.lock:
                core.STATE.modes.privacy = data.privacy
        # Accept danger_labels (list) or legacy danger_label (single)
        if data.danger_labels is not None:
            cleaned = core._clean_labels(data.danger_labels)
            if not cleaned:
                # An empty list would switch every alert off without saying so.
                raise HTTPException(400, "At least one danger label is required.")
            core.STATE.config.danger_labels = cleaned
            if core.app_gst and hasattr(core.app_gst, 'user_data'):
                core.app_gst.user_data.danger_labels = list(core.STATE.config.danger_labels)
        elif data.danger_label is not None:
            new_lbl = data.danger_label.strip()[:64]
            if new_lbl:
                core.STATE.config.danger_labels = [new_lbl]
                if core.app_gst and hasattr(core.app_gst, 'user_data'):
                    core.app_gst.user_data.danger_labels = list(core.STATE.config.danger_labels)
        if data.watch_labels is not None:
            core.STATE.config.watch_labels = core._clean_labels(data.watch_labels)
        if data.mode_schedule is not None:
            # Validate structure: {mode: {start: HH:MM, end: HH:MM}}
            valid_modes = {"dnd", "email_off", "idle", "night"}
            clean = {}
            for k, v in data.mode_schedule.items():
                if k in valid_modes and isinstance(v, dict):
                    s = v.get("start", "")
                    e = v.get("end", "")
                    if isinstance(s, str) and isinstance(e, str) and core._HHMM_RE.match(s) and core._HHMM_RE.match(e):
                        clean[k] = {"start": s, "end": e}
            core.STATE.modes.schedule = clean
        # Night presence window
        if data.night_presence_start is not None or data.night_presence_end is not None or data.night_presence_enabled is not None:
            with core._np_lock:
                if data.night_presence_start is not None:
                    if core._HHMM_RE.match(data.night_presence_start):
                        core.STATE.config.night_presence_window["start"] = data.night_presence_start
                if data.night_presence_end is not None:
                    if core._HHMM_RE.match(data.night_presence_end):
                        core.STATE.config.night_presence_window["end"] = data.night_presence_end
                if data.night_presence_enabled is not None:
                    core.STATE.config.night_presence_window["enabled"] = data.night_presence_enabled
        await core._async_save_config()
        core.log_system_update("Config updated.")
        return {"ok": True}

    @router.post("/api/config/command/add")
    async def add_command(data: CustomCommandRequest, session=Depends(core.require_admin)):
        phrase = (data.phrase or "").strip()
        if not phrase:
            raise HTTPException(400, "Command phrase cannot be empty.")
        if len(phrase) > 200:
            raise HTTPException(400, "Command phrase must be 200 characters or fewer.")
        if len(data.response or "") > 500:
            raise HTTPException(400, "Command response must be 500 characters or fewer.")
        if len(core.STATE.config.custom_voice_commands) >= 100 and phrase.lower() not in core.STATE.config.custom_voice_commands:
            raise HTTPException(400, "Maximum 100 custom commands reached.")
        core.STATE.config.custom_voice_commands[phrase.lower()] = data.response
        await core._async_save_config()
        return {"ok": True}

    @router.post("/api/config/command/delete")
    async def delete_command(data: DeleteCommandRequest, session=Depends(core.require_admin)):
        core.STATE.config.custom_voice_commands.pop(data.phrase.lower(), None)
        await core._async_save_config()
        return {"ok": True}

    return router
