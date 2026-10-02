"""Shortcuts over HTTP: the programs Narada writes from the things it can do.

Anyone signed in may keep and run manual shortcuts; one that runs by itself
(on the clock, or when something becomes true) is an admin's to make, like a
recurring schedule. A shortcut is changed or removed by its maker or an admin.
"""
import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field


class ShortcutRequest(BaseModel):
    program: dict = Field(max_length=8)


def build_shortcuts_router(core):
    router = APIRouter(prefix="/api/home/shortcuts")
    shortcuts = core._shortcuts_mod

    def _view(entry, names=None):
        return {**entry, "rendered": shortcuts.describe(entry, names or core.SHORTCUTS.names())}

    def _own_or_admin(shortcut_id, session):
        entry = core.SHORTCUTS.store.get(shortcut_id)
        if entry is None:
            raise HTTPException(404, "no such shortcut")
        if session["role"] != "admin" and entry.get("created_by") != session["username"]:
            raise HTTPException(403, "Only an admin or its maker can change this.")
        return entry

    def _checked(program, session, self_id=None):
        try:
            return core.SHORTCUTS.check(program, session["role"], self_id=self_id)
        except shortcuts.Invalid as problem:
            raise HTTPException(400, str(problem))

    @router.get("")
    async def list_shortcuts(session=Depends(core.require_session)):
        names = core.SHORTCUTS.names()
        return {"shortcuts": [_view(s, names) for s in core.SHORTCUTS.store.all()],
                "running": core.SHORTCUTS.running()}

    @router.get("/vocabulary")
    async def shortcut_vocabulary(session=Depends(core.require_session)):
        """What a shortcut can be made of: the facts a condition may test (with
        their values now) and the capabilities a step may use."""
        facts = core.SHORTCUTS.facts()
        admin = session["role"] == "admin"
        return {"facts": facts,
                "steps": [{"name": c.name, "does": c.summary, "args": sorted(c.params),
                           "required": [r for r in c.required if r in c.params]}
                          for c in core.AGENT_CAPABILITIES.values()
                          if c.tier != "confirm" and c.name not in shortcuts.NOT_STEPS
                          and (admin or c.role != "admin")]}

    @router.post("/check")
    async def check_shortcut(data: ShortcutRequest, session=Depends(core.require_session)):
        """Check a shortcut without saving it, and say it back in plain lines."""
        clean = _checked(data.program, session)
        return {"ok": True, "program": clean,
                "rendered": shortcuts.describe(clean, core.SHORTCUTS.names())}

    @router.post("")
    async def create_shortcut(data: ShortcutRequest, session=Depends(core.require_session)):
        clean = _checked(data.program, session)
        try:
            entry = core.SHORTCUTS.store.add(clean, created_by=session["username"])
        except shortcuts.Invalid as problem:
            raise HTTPException(400, str(problem))
        core.log_system_update(f"Shortcut created: {entry['name']} by {session['username']}")
        core.push_urgent_ws()
        return {"ok": True, "shortcut": _view(entry)}

    @router.patch("/{shortcut_id}")
    async def update_shortcut(shortcut_id: str, data: ShortcutRequest,
                              session=Depends(core.require_session)):
        _own_or_admin(shortcut_id, session)
        clean = _checked(data.program, session, self_id=shortcut_id)
        entry = core.SHORTCUTS.store.replace(shortcut_id, clean)
        core.log_system_update(f"Shortcut changed: {entry['name']} by {session['username']}")
        return {"ok": True, "shortcut": _view(entry)}

    @router.post("/{shortcut_id}/run")
    async def run_shortcut(shortcut_id: str, session=Depends(core.require_session)):
        entry = core.SHORTCUTS.store.get(shortcut_id) or core.SHORTCUTS.store.find(shortcut_id)
        if entry is None:
            raise HTTPException(404, "no such shortcut")
        ok, reason, run_id = core.SHORTCUTS.run(entry["id"], by=session["username"],
                                                role=session["role"])
        if not ok:
            raise HTTPException(409, reason)
        return {"ok": True, "run": run_id, "name": entry["name"],
                "result": f"Started shortcut {entry['name']}"}

    @router.post("/{shortcut_id}/cancel")
    async def cancel_shortcut(shortcut_id: str, session=Depends(core.require_session)):
        return {"ok": True, "was_running": core.SHORTCUTS.cancel(shortcut_id)}

    @router.post("/{shortcut_id}/toggle")
    async def toggle_shortcut(shortcut_id: str, session=Depends(core.require_session)):
        _own_or_admin(shortcut_id, session)
        return {"ok": True, "enabled": core.SHORTCUTS.store.toggle(shortcut_id)}

    @router.delete("/{shortcut_id}")
    async def delete_shortcut(shortcut_id: str, session=Depends(core.require_session)):
        entry = _own_or_admin(shortcut_id, session)
        core.SHORTCUTS.cancel(shortcut_id)
        core.SHORTCUTS.store.delete(shortcut_id)
        core.log_system_update(f"Shortcut deleted: {entry['name']} by {session['username']}")
        return {"ok": True}

    return router
