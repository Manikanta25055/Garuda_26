"""User accounts: list, add, delete and update (admin only).

Moved out of Garuda_web.py (2026-10). Handler bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`.
"""
import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional


class AddUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"
    display_name: str = ""
    box_color: str = "#1565c0"

class DeleteUserRequest(BaseModel):
    username: str

class UpdateUserRequest(BaseModel):
    username: str
    new_password: Optional[str] = None
    display_name: Optional[str] = None
    box_color: Optional[str] = None


def build_users_router(core):
    router = APIRouter()

    @router.get("/api/users")
    async def list_users(session=Depends(core.require_admin)):
        result = {}
        for uname, udata in core.STATE.auth.users.items():
            result[uname] = {
                "role": udata.get("role"),
                "display_name": udata.get("display_name", uname),
                "box_color": udata.get("box_color", "#1565c0"),
            }
        return result

    @router.post("/api/users/add")
    async def add_user(data: AddUserRequest, session=Depends(core.require_admin)):
        un = (data.username or "").strip()
        if not un:
            raise HTTPException(400, "Username required.")
        if un in core.STATE.auth.users:
            raise HTTPException(400, "Username already exists.")
        if not re.match(r'^[a-zA-Z0-9_-]{3,32}$', un):
            raise HTTPException(400, "Username must be 3-32 chars, alphanumeric, underscore or hyphen only.")
        if data.role not in ("user",):
            raise HTTPException(400, "Role must be 'user'. Admins cannot be created via this endpoint.")
        err = core._validate_password_strength(data.password)
        if err:
            raise HTTPException(400, err)
        if not core._COLOR_RE.match(data.box_color or ""):
            raise HTTPException(400, "Colour must look like #1565c0.")
        core.STATE.auth.users[un] = {
            "password": await asyncio.to_thread(core._hash_password, data.password.strip()),
            "role": "user",
            "display_name": (data.display_name or un.capitalize()).strip()[:64],
            "box_color": data.box_color,
            "history": {"logins": [], "narada_activity": []},
        }
        await asyncio.to_thread(core.save_users)
        core.log_system_update(f"User added: {un}")
        return {"ok": True}

    @router.post("/api/users/delete")
    async def delete_user(data: DeleteUserRequest, session=Depends(core.require_admin)):
        if data.username == "admin":
            raise HTTPException(400, "Cannot delete the admin account.")
        if data.username not in core.STATE.auth.users:
            raise HTTPException(404, "User not found.")
        if core.STATE.auth.users[data.username].get("role") == "admin":
            # Includes the caller: an admin deleting the last admin (or themselves)
            # would leave the master key as the only way back in.
            raise HTTPException(400, "Admin accounts cannot be deleted here.")
        del core.STATE.auth.users[data.username]
        # The account is gone; so are its sessions, refresh tokens and any reset
        # code in flight. They used to stay valid until they expired by themselves.
        core._invalidate_user_sessions(data.username)
        core.STATE.auth.forgot_otp_store.pop(data.username, None)
        await asyncio.to_thread(core.save_users)
        core.log_system_update(f"User deleted: {data.username}")
        return {"ok": True}

    @router.post("/api/users/update")
    async def update_user(data: UpdateUserRequest, request: Request, session=Depends(core.require_admin)):
        if data.username not in core.STATE.auth.users:
            raise HTTPException(404, "User not found.")
        if data.box_color is not None and not core._COLOR_RE.match(data.box_color):
            raise HTTPException(400, "Colour must look like #1565c0.")
        if data.new_password:
            err = core._validate_password_strength(data.new_password)
            if err:
                raise HTTPException(400, err)
            core.STATE.auth.users[data.username]["password"] = await asyncio.to_thread(core._hash_password, data.new_password.strip())
            current_token = session.get("token")
            # The admin doing the change keeps their own way back in; every other
            # session and refresh token of that account is revoked.
            own_refresh = request.cookies.get("garuda_refresh") or request.headers.get("X-Garuda-Refresh")
            core._invalidate_user_sessions(data.username, except_token=current_token,
                                      except_refresh=own_refresh)
        if data.display_name is not None:
            core.STATE.auth.users[data.username]["display_name"] = data.display_name.strip()[:64]
        if data.box_color is not None:
            core.STATE.auth.users[data.username]["box_color"] = data.box_color
        await asyncio.to_thread(core.save_users)
        core.log_system_update(f"User updated: {data.username}")
        return {"ok": True}

    return router
