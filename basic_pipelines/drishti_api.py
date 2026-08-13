"""The Drishti API.

Kept out of Garuda_web.py, which is already ~3,800 lines. Everything the
router needs travels in a context object so tests can build one over a
temporary directory with no hardware present.
"""
import hashlib
import os
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .drishti_auth import (COOKIE_NAME, create_session, destroy_session,
                           require_drishti_admin, require_drishti_session)
from .garuda_auto import actuation_log
from .garuda_auto.actuators import RelayBank
from .garuda_auto.device_registry import DeviceRegistry
from .garuda_auto.device_types import TYPES, actions_for
from .garuda_auto.local_lane import answer as local_answer
from .garuda_auto.matcher import LocalMatcher
from .garuda_auto.nim_client import NimClient
from .garuda_auto.pending_store import PendingStore
from .garuda_auto.rule_schema import build_schema
from .garuda_auto.rule_store import RuleStore
from .garuda_auto.transports import DeviceRouter, MqttBank


@dataclass
class DrishtiContext:
    registry: DeviceRegistry
    schema: object
    store: RuleStore
    pending: PendingStore
    device_router: DeviceRouter
    matcher: LocalMatcher
    nim: NimClient
    log_path: str
    channel_to_pin: dict
    relay_bank: RelayBank
    mqtt_bank: MqttBank
    descriptor: dict = field(default_factory=dict)
    synthesis_cache: dict = field(default_factory=dict)
    suppression_log: list = field(default_factory=list)

    # Injected by whoever wires the router into an application. This module
    # must not import Garuda_web: that module is run as a script, so its live
    # globals sit under __main__, and `from .Garuda_web import USERS` would
    # import a *second* copy whose USERS is still empty — a login that can
    # never succeed. Injection also lets the tests supply fakes.
    # Called after the device registry changes, so anything holding derived
    # state (the scene builder's device slots) can rebuild. Without it a device
    # added through the API never appears in the descriptor until a restart.
    on_registry_change: object = None
    authenticate: object = None    # (username, password) -> role str or None
    system_state: object = None    # () -> dict merged into GET /state
    frame_source: object = None    # (request) -> async byte generator
    set_privacy: object = None     # (bool) -> None, turns the camera off

    def rebuild(self):
        """Re-derive everything that depends on the device registry."""
        self.schema = build_schema(self.registry)
        self.store.rebind(self.schema)
        self.relay_bank.close()
        self.relay_bank = RelayBank({
            d["id"]: self.channel_to_pin[d["transport"]["channel"]]
            for d in self.registry.actuators()
            if d["transport"]["kind"] == "relay"
        })
        self.mqtt_bank.bind(self.registry)
        self.device_router = DeviceRouter(self.registry, self.relay_bank, self.mqtt_bank)
        if self.on_registry_change is not None:
            self.on_registry_change()


def build_context(*, data_dir, relay_channels, channel_to_pin,
                  mqtt_host="localhost", nim_key="", nim_model="",
                  matcher_backend="fuzzy"):
    registry = DeviceRegistry(os.path.join(data_dir, "devices.json"), relay_channels)
    schema = build_schema(registry)
    store = RuleStore(os.path.join(data_dir, "rules.json"), schema)
    pending = PendingStore(os.path.join(data_dir, "pending.json"))
    relay_bank = RelayBank({
        d["id"]: channel_to_pin[d["transport"]["channel"]]
        for d in registry.actuators() if d["transport"]["kind"] == "relay"
    })
    mqtt_bank = MqttBank(mqtt_host)
    mqtt_bank.bind(registry)
    return DrishtiContext(
        registry=registry, schema=schema, store=store, pending=pending,
        device_router=DeviceRouter(registry, relay_bank, mqtt_bank),
        matcher=LocalMatcher(store, backend=matcher_backend),
        nim=NimClient(nim_key, nim_model),
        log_path=os.path.join(data_dir, "actuations.jsonl"),
        channel_to_pin=dict(channel_to_pin),
        relay_bank=relay_bank, mqtt_bank=mqtt_bank,
    )


class LoginRequest(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class InstructRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class PrivacyRequest(BaseModel):
    on: bool


class DeviceRequest(BaseModel):
    id: str = Field(max_length=32)
    name: str = Field(max_length=64)
    type: str = Field(max_length=32)
    room: str = Field(max_length=64)
    transport: dict


def _render(rule):
    """Plain-language rendering of a rule, for the card."""
    combinator, conditions = next(iter(rule["when"].items()))
    joiner = " and " if combinator == "all" else " or "
    when = joiner.join(f"{c['field']} {c['op']} {c['value']}" for c in conditions)
    then = ", ".join(f"{a['device']} → {a['action']}" for a in rule["then"])
    return {"when": when, "then": then}


def build_router(ctx):
    router = APIRouter(prefix="/api/drishti")

    @router.post("/login")
    async def login(data: LoginRequest, response: Response):
        role = None
        if ctx.authenticate is not None:
            role = ctx.authenticate(data.username, data.password)
        if not role:
            raise HTTPException(status_code=401, detail="invalid credentials")
        token = create_session(data.username, role)
        response.set_cookie(COOKIE_NAME, token, httponly=True,
                            samesite="lax", secure=True, path="/")
        return {"ok": True, "username": data.username, "role": role}

    @router.get("/state")
    async def state(session=Depends(require_drishti_session)):
        descriptor = dict(ctx.descriptor)
        body = {
            "occupancy": descriptor.get("occupancy", "empty"),
            "person_count": descriptor.get("person_count", 0),
            "temperature_c": descriptor.get("temperature_c"),
            "humidity_pct": descriptor.get("humidity_pct"),
            "modes": {},
            "uptime_s": 0,
            "pipeline": "unknown",
            "online": bool(ctx.nim.api_key),
        }
        if ctx.system_state is not None:
            body.update(ctx.system_state())
        return body

    @router.get("/stream")
    async def stream(request: Request, session=Depends(require_drishti_session)):
        # Authenticated first, so an anonymous caller gets 401 rather than 503
        # and cannot use this to probe whether a camera exists.
        if ctx.frame_source is None:
            raise HTTPException(status_code=503, detail="no camera on this host")
        return StreamingResponse(
            ctx.frame_source(request),
            media_type="multipart/x-mixed-replace; boundary=frame")

    @router.post("/privacy")
    async def privacy(body: PrivacyRequest, session=Depends(require_drishti_session)):
        """Turn the camera off, or back on.

        MODE_PRIVACY was reachable only through the voice assistant, so the app
        could read the flag and never change it -- a camera in a house with no
        off switch you can press.
        """
        # Authenticated first, so an anonymous caller gets 401 rather than 503
        # and cannot use this to probe whether a camera exists.
        if ctx.set_privacy is None:
            raise HTTPException(status_code=503, detail="no camera on this host")
        ctx.set_privacy(body.on)
        return {"privacy": body.on}

    @router.post("/logout")
    async def logout(request: Request, response: Response,
                     session=Depends(require_drishti_session)):
        destroy_session(request.cookies.get(COOKIE_NAME))
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"ok": True}

    @router.get("/device-types")
    async def device_types(session=Depends(require_drishti_session)):
        # The channel list travels with the catalogue so the client never has
        # to know one. A client that hardcoded 1-7 would offer a channel this
        # deployment does not have, and the whole reason a user picks a channel
        # instead of a BCM pin is that the server owns that mapping.
        return {"types": {name: {"actions": sorted(actions_for(name)),
                                 "state": spec["state"]}
                          for name, spec in TYPES.items()},
                "channels": sorted(ctx.channel_to_pin)}

    @router.get("/devices")
    async def list_devices(session=Depends(require_drishti_session)):
        return {"devices": [
            {**d,
             "state": ctx.device_router.state(d["id"]),
             "available": ctx.device_router.available(d["id"])}
            for d in ctx.registry.devices
        ]}

    @router.post("/devices")
    async def add_device(data: DeviceRequest, session=Depends(require_drishti_admin)):
        ok, reason = ctx.registry.add(data.model_dump())
        if not ok:
            raise HTTPException(status_code=400, detail=reason)
        ctx.rebuild()
        return {"ok": True, "id": data.id}

    @router.delete("/devices/{device_id}")
    async def delete_device(device_id: str, session=Depends(require_drishti_admin)):
        if not ctx.registry.delete(device_id):
            raise HTTPException(status_code=404, detail=f"unknown device: {device_id}")
        before = len(ctx.store.orphaned)
        ctx.rebuild()
        return {"ok": True, "orphaned": len(ctx.store.orphaned) - before}

    @router.post("/instruct")
    async def instruct(data: InstructRequest, session=Depends(require_drishti_session)):
        text = data.text.strip()

        local = local_answer(text, registry=ctx.registry, descriptor=ctx.descriptor,
                             router=ctx.device_router, log_path=ctx.log_path,
                             store=ctx.store)
        if local is not None:
            return {"lane": "local", "ok": True, **local}

        hit = ctx.matcher.match(text)
        if hit is not None:
            # Every suppression event is recorded here or the headline metric
            # does not exist.
            ctx.suppression_log.append({
                "utterance": text,
                "rule_id": hit.get("id", ""),
                "score": ctx.matcher._score(text, hit.get("source_utterance", "")),
                "backend": ctx.matcher.backend_name,
            })
            return {"lane": "known", "ok": True, "resolved": "already-known",
                    "rule": hit, "rendered": _render(hit)}

        key = hashlib.sha256(text.lower().encode()).hexdigest()
        cached = ctx.synthesis_cache.get(key)
        if cached is not None:
            rule, reason = cached, ""
        else:
            rule, reason = await ctx.nim.synthesize_async(
                text, ctx.store.rules, ctx.schema)
            if rule is not None:
                ctx.synthesis_cache[key] = rule

        if rule is None:
            return {"lane": "compile", "ok": False, "resolved": "compiled",
                    "reason": reason, "still_working": True,
                    "vocabulary": sorted(ctx.schema.fields)}

        conflict = ctx.store.find_conflict(rule)
        proposal_id = ctx.pending.add(rule, conflict=conflict)
        return {"lane": "compile", "ok": True, "resolved": "compiled",
                "proposal_id": proposal_id, "rule": rule,
                "rendered": _render(rule), "conflict": conflict}

    @router.get("/proposals")
    async def list_proposals(session=Depends(require_drishti_session)):
        return {"proposals": [{**p, "rendered": _render(p["rule"])}
                              for p in ctx.pending.all()]}

    @router.post("/proposals/{proposal_id}/confirm")
    async def confirm(proposal_id: str, session=Depends(require_drishti_session)):
        item = ctx.pending.get(proposal_id)
        if item is None:
            raise HTTPException(status_code=404, detail="no such proposal")
        ok, reason = ctx.store.add(item["rule"])
        if not ok:
            raise HTTPException(status_code=400, detail=reason)
        ctx.pending.pop(proposal_id)
        return {"ok": True}

    @router.delete("/proposals/{proposal_id}")
    async def discard(proposal_id: str, session=Depends(require_drishti_session)):
        ctx.pending.pop(proposal_id)
        return {"ok": True}

    @router.get("/rules")
    async def list_rules(session=Depends(require_drishti_session)):
        return {
            "rules": [{**r, "rendered": _render(r)} for r in ctx.store.rules],
            "orphaned": ctx.store.orphaned,
        }

    @router.delete("/rules/{rule_id}")
    async def delete_rule(rule_id: str, session=Depends(require_drishti_session)):
        if not ctx.store.delete(rule_id):
            raise HTTPException(status_code=404, detail="no such rule")
        return {"ok": True}

    @router.post("/rules/{rule_id}/toggle")
    async def toggle_rule(rule_id: str, session=Depends(require_drishti_session)):
        for rule in ctx.store.rules:
            if rule.get("id") == rule_id:
                rule["enabled"] = not rule.get("enabled", True)
                ctx.store.save()
                return {"ok": True, "enabled": rule["enabled"]}
        raise HTTPException(status_code=404, detail="no such rule")

    @router.get("/activity")
    async def activity(limit: int = 200, session=Depends(require_drishti_session)):
        return {"entries": actuation_log.recent(ctx.log_path, limit=min(limit, 500))}

    return router
