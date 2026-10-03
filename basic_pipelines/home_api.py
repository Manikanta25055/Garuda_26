"""The home-automation API behind Garuda's own sign-in: /api/home/*.

This is Drishti's feature set moved into the Garuda web app. Drishti had its
own sign-in and cookie; here every route takes Garuda's session dependencies,
injected by Garuda_web (this module must not import it -- see the note in
drishti_api.DrishtiContext).

Roles, as agreed for the merged app:
  user   switch devices, run scenes, set one-off timers, propose automations,
         read everything on the Home / Automations / Insights pages.
  admin  also confirms automations, manages devices, scenes, recurring
         schedules, home settings and the AI keys.
"""
import time

import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .garuda_auto import actuation_log
from .garuda_auto.device_types import TYPES, actions_for
from .garuda_auto.rule_schema import render_rule


class ActionRequest(BaseModel):
    action: str = Field(max_length=16)


class AllOffRequest(BaseModel):
    room: str | None = Field(default=None, max_length=64)


class InstructRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class DeviceRequest(BaseModel):
    id: str = Field(max_length=32)
    name: str = Field(max_length=64)
    type: str = Field(max_length=32)
    room: str = Field(max_length=64)
    transport: dict
    watts: float | None = Field(default=None, ge=0, le=5000)


class DeviceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    room: str | None = Field(default=None, max_length=64)
    watts: float | None = Field(default=None, ge=0, le=5000)
    enabled: bool | None = None


class SceneStep(BaseModel):
    device: str = Field(max_length=32)
    action: str = Field(max_length=16)


class SceneRequest(BaseModel):
    name: str = Field(min_length=1, max_length=48)
    actions: list[SceneStep] = Field(min_length=1, max_length=16)


class ScheduleRequest(BaseModel):
    device: str | None = Field(default=None, max_length=32)
    action: str | None = Field(default=None, max_length=16)
    scene: str | None = Field(default=None, max_length=32)
    time: str | None = Field(default=None, max_length=5)
    days: list[int] | None = Field(default=None, max_length=7)
    in_minutes: float | None = Field(default=None, gt=0, le=7 * 24 * 60)
    label: str = Field(default="", max_length=64)


class SettingsRequest(BaseModel):
    settings: dict


# Printable, no whitespace: these end up in .env and in HTTP headers.
_TOKEN = r"^[A-Za-z0-9._~+/=:-]*$"
_MODELS = r"^[A-Za-z0-9._/:, -]*$"


class AIConfigRequest(BaseModel):
    nim_api_key: str | None = Field(default=None, max_length=200, pattern=_TOKEN)
    nim_model: str | None = Field(default=None, max_length=120, pattern=_TOKEN)
    nim_fallback_models: str | None = Field(default=None, max_length=400, pattern=_MODELS)
    jev_api_key: str | None = Field(default=None, max_length=200, pattern=r"^\s*[A-Za-z0-9._~+/=:-]*\s*$")
    jev_base_url: str | None = Field(default=None, max_length=200, pattern=r"^https://[A-Za-z0-9.-]+(:\d+)?(/[A-Za-z0-9._/-]*)?$")
    decision_threshold: float | None = Field(default=None, ge=0.5, le=0.99)


def build_home_router(ctx, home, agent, digest, *, session_dep, admin_dep,
                      ai_configure=None, ai_test=None, suggestion_decided=None):
    # suggestion_decided(suggestion, accepted, user) is told when a routine the
    # house offered is accepted or dismissed, so the answer can be remembered.
    router = APIRouter(prefix="/api/home")

    def _user(session):
        return session.get("username", ""), session.get("role", "user")

    # ── overview ──────────────────────────────────────────────────────────────

    @router.get("/overview")
    async def overview(session=Depends(session_dep)):
        user, role = _user(session)
        return {
            "role": role,
            "devices": home.devices(),
            "scenes": home.scenes.scenes,
            "rooms": sorted({d.get("room", "") for d in ctx.registry.devices if d.get("room")}),
            "context": home.context(),
            "occupancy": ctx.descriptor.get("occupancy"),
            "person_count": ctx.descriptor.get("person_count"),
            "summary": home.summary(),
            "notices": home.notices,
            "ai": agent.status(),
        }

    # One request per page: the app allows 30 requests a minute per IP.

    @router.get("/automations")
    async def automations(session=Depends(session_dep)):
        return {"rules": await list_rules(session), "schedules": home.schedule_view(),
                "suggestions": home.suggestions(), "settings": _public_settings(),
                "devices": home.devices(), "scenes": home.scenes.scenes}

    @router.get("/insights")
    async def insights(days: int = 7, limit: int = 60, session=Depends(session_dep)):
        return {"usage": home.usage(days=max(1, min(days, 31))),
                "activity": (await activity(limit, session))["entries"],
                "settings": _public_settings()}

    def _public_settings():
        return {k: v for k, v in home.settings.items() if k != "dismissed_suggestions"}

    # ── devices ───────────────────────────────────────────────────────────────

    @router.post("/devices/{device_id}/set")
    async def set_device(device_id: str, data: ActionRequest, session=Depends(session_dep)):
        if ctx.registry.get(device_id) is None:
            raise HTTPException(404, f"unknown device: {device_id}")
        ok, reason = home.set(device_id, data.action, source="manual", actor=_user(session)[0])
        if not ok:
            raise HTTPException(400, reason)
        return {"ok": True, "state": ctx.device_router.state(device_id)}

    @router.post("/all-off")
    async def all_off(data: AllOffRequest, session=Depends(session_dep)):
        done, failed = home.all_off(actor=_user(session)[0], room=data.room)
        return {"ok": not failed, "turned_off": done, "failed": failed}

    @router.get("/device-types")
    async def device_types(session=Depends(session_dep)):
        return {"types": {name: {"actions": sorted(actions_for(name)), "state": spec["state"]}
                          for name, spec in TYPES.items()},
                "channels": sorted(ctx.channel_to_pin),
                "used_channels": sorted(d["transport"].get("channel") for d in ctx.registry.devices
                                        if d["transport"]["kind"] == "relay")}

    @router.post("/devices")
    async def add_device(data: DeviceRequest, session=Depends(admin_dep)):
        entry = data.model_dump()
        if entry.get("watts") is None:
            entry.pop("watts")
        ok, reason = ctx.registry.add(entry)
        if not ok:
            raise HTTPException(400, reason)
        ctx.rebuild()
        return {"ok": True, "id": data.id}

    @router.patch("/devices/{device_id}")
    async def update_device(device_id: str, data: DeviceUpdateRequest,
                            session=Depends(admin_dep)):
        fields = data.model_dump(exclude_unset=True)
        ok, reason = ctx.registry.update(device_id, fields)
        if not ok:
            raise HTTPException(404 if reason.startswith("unknown") else 400, reason)
        if "enabled" in fields:
            ctx.rebuild()
        return {"ok": True}

    @router.delete("/devices/{device_id}")
    async def delete_device(device_id: str, session=Depends(admin_dep)):
        if not ctx.registry.delete(device_id):
            raise HTTPException(404, f"unknown device: {device_id}")
        before = len(ctx.store.orphaned)
        ctx.rebuild()
        return {"ok": True, "orphaned": len(ctx.store.orphaned) - before}

    # ── assistant ─────────────────────────────────────────────────────────────

    @router.post("/instruct")
    async def instruct(data: InstructRequest, session=Depends(session_dep)):
        user, role = _user(session)
        return await anyio.to_thread.run_sync(
            lambda: agent.handle(data.text, user=user, role=role))

    # ── scenes ────────────────────────────────────────────────────────────────

    @router.post("/scenes/{scene_id}/run")
    async def run_scene(scene_id: str, session=Depends(session_dep)):
        ok, reason, results = home.run_scene(scene_id, actor=_user(session)[0])
        if not results and not ok:
            raise HTTPException(404, reason)
        return {"ok": ok, "reason": reason, "results": results}

    @router.post("/scenes")
    async def add_scene(data: SceneRequest, session=Depends(admin_dep)):
        ok, reason, scene = home.scenes.add(data.name, [s.model_dump() for s in data.actions],
                                            created_by=_user(session)[0])
        if not ok:
            raise HTTPException(400, reason)
        return {"ok": True, "scene": scene}

    @router.delete("/scenes/{scene_id}")
    async def delete_scene(scene_id: str, session=Depends(admin_dep)):
        if not home.scenes.delete(scene_id):
            raise HTTPException(404, "no such scene")
        return {"ok": True}

    # ── schedules ─────────────────────────────────────────────────────────────

    @router.get("/schedules")
    async def list_schedules(session=Depends(session_dep)):
        return {"schedules": home.schedule_view()}

    @router.post("/schedules")
    async def add_schedule(data: ScheduleRequest, session=Depends(session_dep)):
        user, role = _user(session)
        target = ({"scene": data.scene} if data.scene
                  else {"device": data.device, "action": data.action})
        if data.in_minutes is not None:
            ok, reason, entry = home.add_schedule(target, at=time.time() + data.in_minutes * 60,
                                                  label=data.label, created_by=user)
        else:
            if role != "admin":
                raise HTTPException(403, "Only an admin can create recurring schedules.")
            ok, reason, entry = home.add_schedule(target, time_hhmm=data.time, days=data.days,
                                                  label=data.label, created_by=user)
        if not ok:
            raise HTTPException(400, reason)
        return {"ok": True, "schedule": entry}

    def _own_or_admin(schedule_id, session):
        entry = home.schedules.get(schedule_id)
        if entry is None:
            raise HTTPException(404, "no such schedule")
        user, role = _user(session)
        if role != "admin" and entry.get("created_by") != user:
            raise HTTPException(403, "Only an admin or its creator can change this.")

    @router.post("/schedules/{schedule_id}/toggle")
    async def toggle_schedule(schedule_id: str, session=Depends(session_dep)):
        _own_or_admin(schedule_id, session)
        return {"ok": True, "enabled": home.schedules.toggle(schedule_id)}

    @router.delete("/schedules/{schedule_id}")
    async def delete_schedule(schedule_id: str, session=Depends(session_dep)):
        _own_or_admin(schedule_id, session)
        home.schedules.delete(schedule_id)
        return {"ok": True}

    # ── automations (rules) ───────────────────────────────────────────────────

    @router.get("/rules")
    async def list_rules(session=Depends(session_dep)):
        return {"rules": [{**r, "rendered": render_rule(r)} for r in ctx.store.rules],
                "orphaned": ctx.store.orphaned,
                "proposals": [{**p, "rendered": render_rule(p["rule"])} for p in ctx.pending.all()],
                "vocabulary": sorted(ctx.schema.fields)}

    @router.post("/proposals/{proposal_id}/confirm")
    async def confirm(proposal_id: str, session=Depends(admin_dep)):
        item = ctx.pending.get(proposal_id)
        if item is None:
            raise HTTPException(404, "no such proposal")
        ok, reason = ctx.store.add(item["rule"])
        if not ok:
            raise HTTPException(400, reason)
        ctx.pending.pop(proposal_id)
        return {"ok": True}

    @router.delete("/proposals/{proposal_id}")
    async def discard(proposal_id: str, session=Depends(session_dep)):
        ctx.pending.pop(proposal_id)
        return {"ok": True}

    @router.post("/rules/{rule_id}/toggle")
    async def toggle_rule(rule_id: str, session=Depends(admin_dep)):
        for rule in ctx.store.rules:
            if rule.get("id") == rule_id:
                rule["enabled"] = not rule.get("enabled", True)
                ctx.store.save()
                return {"ok": True, "enabled": rule["enabled"]}
        raise HTTPException(404, "no such rule")

    @router.delete("/rules/{rule_id}")
    async def delete_rule(rule_id: str, session=Depends(admin_dep)):
        if not ctx.store.delete(rule_id):
            raise HTTPException(404, "no such rule")
        return {"ok": True}

    # ── suggestions ───────────────────────────────────────────────────────────

    @router.get("/suggestions")
    async def suggestions(session=Depends(session_dep)):
        return {"suggestions": home.suggestions()}

    @router.post("/suggestions/{suggestion_id}/accept")
    async def accept_suggestion(suggestion_id: str, session=Depends(admin_dep)):
        match = next((s for s in home.suggestions() if s["id"] == suggestion_id), None)
        if match is None:
            raise HTTPException(404, "that suggestion is no longer offered")
        ok, reason, entry = home.add_schedule(
            {"device": match["device"], "action": match["action"]},
            time_hhmm=match["time"], days=match["days"], label="From your routine",
            created_by=_user(session)[0])
        if not ok:
            raise HTTPException(400, reason)
        home.dismiss_suggestion(suggestion_id)
        if suggestion_decided:
            suggestion_decided(match, True, _user(session)[0])
        return {"ok": True, "schedule": entry}

    @router.post("/suggestions/{suggestion_id}/dismiss")
    async def dismiss_suggestion(suggestion_id: str, session=Depends(session_dep)):
        match = next((s for s in home.suggestions() if s["id"] == suggestion_id), None)
        home.dismiss_suggestion(suggestion_id)
        if match is not None and suggestion_decided:
            suggestion_decided(match, False, _user(session)[0])
        return {"ok": True}

    # ── insight ───────────────────────────────────────────────────────────────

    @router.get("/activity")
    async def activity(limit: int = 100, session=Depends(session_dep)):
        names = {d["id"]: d["name"] for d in ctx.registry.devices}
        rules = {r.get("id"): r.get("source_utterance", "") for r in ctx.store.rules}
        entries = []
        for e in actuation_log.recent(ctx.log_path, limit=max(1, min(limit, 500))):
            entries.append({**e, "device_name": names.get(e["device"], e["device"]),
                            "rule": rules.get(e.get("rule_id"), "") if e.get("rule_id") else ""})
        return {"entries": entries}

    @router.get("/usage")
    async def usage(days: int = 7, session=Depends(session_dep)):
        return home.usage(days=max(1, min(days, 31)))

    @router.get("/digest")
    async def get_digest(refresh: bool = False, session=Depends(session_dep)):
        return await anyio.to_thread.run_sync(lambda: digest.build(refresh=refresh))

    # ── notices ───────────────────────────────────────────────────────────────

    @router.post("/notices/{notice_id}/dismiss")
    async def dismiss_notice(notice_id: str, session=Depends(session_dep)):
        home.dismiss_notice(notice_id)
        return {"ok": True}

    # ── settings (admin) ──────────────────────────────────────────────────────

    @router.get("/settings")
    async def get_settings(session=Depends(session_dep)):
        return _public_settings()

    @router.post("/settings")
    async def set_settings(data: SettingsRequest, session=Depends(admin_dep)):
        ok, reason = home.update_settings(data.settings)
        if not ok:
            raise HTTPException(400, reason)
        return {"ok": True}

    @router.get("/ai")
    async def ai_status(session=Depends(admin_dep)):
        return agent.status()

    @router.post("/ai")
    async def ai_config(data: AIConfigRequest, session=Depends(admin_dep)):
        if ai_configure is None:
            raise HTTPException(503, "AI configuration is not available")
        ai_configure(data.model_dump(exclude_unset=True), session.get("username", ""))
        return {"ok": True, "status": agent.status()}

    @router.post("/ai/test")
    async def ai_test_route(session=Depends(admin_dep)):
        if ai_test is None:
            raise HTTPException(503, "AI test is not available")
        return await anyio.to_thread.run_sync(ai_test)

    return router
