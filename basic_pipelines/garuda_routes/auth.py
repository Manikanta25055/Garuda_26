"""Signing in and out: password login, session restore, refresh, the admin one-time code, and password reset.

Moved out of Garuda_web.py (2026-10). Handler bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`.
"""
import asyncio
import datetime
import hmac
import os
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from typing import Optional


class LoginRequest(BaseModel):
    username: str
    password: str
    remember_me: bool = False

class OTPRequest(BaseModel):
    username: str
    password: str

class VerifyOTPRequest(BaseModel):
    username: str
    otp: str

class ForgotPasswordRequest(BaseModel):
    username: Optional[str] = None   # omittable — endpoint resolves from OTP store
    otp: str
    new_password: str

class SendForgotOTPRequest(BaseModel):
    username: str


def build_auth_router(core):
    router = APIRouter()

    @router.get("/api/users-public")
    async def users_public():
        """Return non-sensitive user info for login screen profile cards."""
        result = []
        for uname, udata in core.USERS.items():
            if udata.get("role") == "user":
                result.append({
                    "username": uname,
                    "display_name": udata.get("display_name", uname),
                    "box_color": udata.get("box_color", "#1565c0"),
                })
        return result

    @router.post("/api/login")
    async def login(data: LoginRequest, request: Request, response: Response):
        ip = core._get_client_ip(request)
        if core._is_login_locked(ip):
            raise HTTPException(429, "Too many failed attempts. Try again later.")
        if not core._check_rate_limit(request):
            raise HTTPException(429, "Too many requests. Try again later.")
        u = data.username.strip()[:64]
        p = data.password.strip()[:256]
        user = core.USERS.get(u)
        # PBKDF2 at 600k rounds takes a noticeable fraction of a second on the Pi;
        # on the event loop it froze the state socket and every camera stream for
        # that long. The same work is done for an unknown name, so the answer
        # takes as long either way.
        stored = user["password"] if user else core._DUMMY_PASSWORD_HASH
        password_ok = await asyncio.to_thread(core._verify_password, p, stored)
        if user is None or not password_ok:
            core._record_login_failure(ip)
            raise HTTPException(401, "Invalid username or password.")
        if user.get("role") == "admin":
            # Only said once the password has been proved: before, any name could
            # be tested for "is this an admin?" without knowing anything.
            # Test-only bypass for the P1-4 evaluation harness. Set GARUDA_EVAL_OTP_BYPASS=1
            # in the environment before starting the server to allow a named service admin
            # (GARUDA_EVAL_SERVICE_ADMIN) to sign in via /api/login without the email OTP.
            _bypass = os.environ.get("GARUDA_EVAL_OTP_BYPASS", "") == "1"
            _allowed = os.environ.get("GARUDA_EVAL_SERVICE_ADMIN", "")
            if not (_bypass and _allowed and u == _allowed):
                raise HTTPException(403, "Admin accounts must sign in via the Admin Access flow.")
        # Auto-migrate plaintext passwords to hashed
        if not user["password"].startswith("pbkdf2:"):
            user["password"] = await asyncio.to_thread(core._hash_password, p)
        core._clear_login_failure(ip)
        access_token = core.create_session(u)
        refresh_token = core.create_refresh_token(u)
        core._set_session_cookies(request, response, access_token, refresh_token,
                             persistent=bool(data.remember_me))
        core.log_system_update(f"Login: {u}")
        core._remember_user_activity(u, "logins", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        await asyncio.to_thread(core.save_users)
        body = {
            "role": user["role"],
            "username": u,
            "display_name": user.get("display_name", u),
            "box_color": user.get("box_color", "#1565c0"),
            "token": access_token,   # for cross-origin clients that can't use cookies
        }
        if core._is_cross_site(request):
            body["refresh_token"] = refresh_token
        return body

    @router.get("/api/session")
    async def session_info(session=Depends(core.require_session)):
        """Return current session user info — used to restore session on page refresh."""
        u = session["username"]
        return {
            "role": session["role"],
            "username": u,
            "display_name": core.USERS.get(u, {}).get("display_name", u),
            "box_color": core.USERS.get(u, {}).get("box_color", "#1565c0"),
            "logs_unlocked": session.get("logs_unlocked", False),
        }

    @router.post("/api/logout")
    async def logout(request: Request, response: Response):
        token = request.headers.get("X-Garuda-Token") or request.cookies.get("garuda_session")
        if token:
            core._sessions.pop(token, None)
        # Also revoke the refresh token so stolen refresh tokens can't mint new sessions
        for refresh in (request.cookies.get("garuda_refresh"), request.headers.get("X-Garuda-Refresh")):
            if refresh:
                core._revoke_refresh(refresh)
        response.delete_cookie("garuda_session")
        response.delete_cookie("garuda_refresh", path="/api/refresh")
        return {"ok": True}

    @router.post("/api/refresh")
    async def refresh_session(request: Request, response: Response):
        """Exchange a valid refresh token for a new 15-minute access token."""
        # The header is for a front end hosted on another site (Vercel): its
        # browser never sends our SameSite cookie back, so it was signed out every
        # 15 minutes when the access token ran out.
        refresh = request.cookies.get("garuda_refresh") or request.headers.get("X-Garuda-Refresh")
        if not refresh:
            raise HTTPException(401, "No refresh token.")
        rs = core.get_refresh_token(refresh)
        if not rs:
            raise HTTPException(401, "Refresh token expired or invalid. Please log in again.")
        u = rs["username"]
        if u not in core.USERS:
            core._revoke_refresh(refresh)
            raise HTTPException(401, "User no longer exists.")
        access_token = core.create_session(u)
        core._set_session_cookies(request, response, access_token)
        return {
            "token": access_token,
            "role": core.USERS[u]["role"],
            "username": u,
        }

    @router.post("/api/admin/send-otp")
    async def admin_send_otp(data: OTPRequest, request: Request, response: Response):
        """Admin login step 1: verify credentials, send OTP."""
        ip = core._get_client_ip(request)
        if core._is_login_locked(ip):
            raise HTTPException(429, "Too many failed attempts. Try again later.")
        if not core._check_rate_limit(request):
            raise HTTPException(429, "Too many requests. Try again later.")
        u = data.username.strip()[:64]
        p = data.password.strip()[:256]
        user = core.USERS.get(u)
        stored = user["password"] if user else core._DUMMY_PASSWORD_HASH
        password_ok = await asyncio.to_thread(core._verify_password, p, stored)
        if user is None or not password_ok or user.get("role") != "admin":
            core._record_login_failure(ip)
            raise HTTPException(401, "Invalid admin credentials.")
        # Auto-migrate plaintext passwords to hashed
        if not user["password"].startswith("pbkdf2:"):
            user["password"] = await asyncio.to_thread(core._hash_password, p)
            await asyncio.to_thread(core.save_users)
        core.ADMIN_OTP = core.generate_otp_code(6)
        core._admin_otp_user = u          # store server-side so step 2 cannot be hijacked
        core._admin_otp_ts = time.time()  # for expiry check
        core._admin_otp_attempts = 0      # a new code gets its own three tries
        dest = core.STATE.config.email_recipients[0] if core.STATE.config.email_recipients else core.STATE.config.email_sender
        # SMTP can take ten seconds; off the event loop so nothing else waits on it.
        ok, err = await asyncio.to_thread(core.send_otp_via_email, dest, core.ADMIN_OTP)
        if not ok:
            return {"ok": False, "error": err}
        return {"ok": True}

    @router.post("/api/admin/verify-otp")
    async def admin_verify_otp(data: VerifyOTPRequest, request: Request, response: Response):
        """Admin login step 2: verify OTP, issue session."""
        if not core._check_rate_limit(request):
            raise HTTPException(429, "Too many requests. Try again later.")
        if not core.ADMIN_OTP or not core._admin_otp_user:
            raise HTTPException(401, "No OTP pending. Please restart login.")
        if time.time() - core._admin_otp_ts > 300:
            core.ADMIN_OTP = None; core._admin_otp_user = None; core._admin_otp_attempts = 0
            raise HTTPException(401, "OTP expired. Please request a new one.")
        if core._admin_otp_attempts >= 3:
            core.ADMIN_OTP = None; core._admin_otp_user = None; core._admin_otp_attempts = 0
            raise HTTPException(401, "Too many incorrect attempts. Please restart login.")
        if not hmac.compare_digest(data.otp.strip().encode(), str(core.ADMIN_OTP).encode()):
            core._admin_otp_attempts += 1
            raise HTTPException(401, "Invalid OTP.")
        u = core._admin_otp_user   # use server-stored username, not client-supplied
        core.ADMIN_OTP = None; core._admin_otp_user = None; core._admin_otp_ts = 0; core._admin_otp_attempts = 0
        if u not in core.USERS or core.USERS[u]["role"] != "admin":
            raise HTTPException(401, "Account not authorised.")
        ip = core._get_client_ip(request)
        core._clear_login_failure(ip)
        access_token = core.create_session(u)
        refresh_token = core.create_refresh_token(u)
        core._set_session_cookies(request, response, access_token, refresh_token)
        core.log_system_update(f"Admin login: {u}")
        body = {
            "role": "admin",
            "username": u,
            "display_name": core.USERS[u].get("display_name", u),
            "token": access_token,   # for cross-origin clients
        }
        if core._is_cross_site(request):
            body["refresh_token"] = refresh_token
        return body

    @router.post("/api/forgot/send-otp")
    async def forgot_send_otp(data: SendForgotOTPRequest, request: Request):
        if not core._check_rate_limit(request):
            raise HTTPException(429, "Too many requests. Try again later.")
        u = data.username.strip()
        # Always return the same response regardless of whether user exists (anti-enumeration)
        if u not in core.USERS:
            return {"ok": True}
        otp = core.generate_otp_code(6)
        core._forgot_otp_store[u] = {"otp": otp, "ts": time.time(), "attempts": 0}
        core.USER_FORGOT_OTP = otp   # test-facing alias
        # Send to the user's own email if stored, else fall back to admin recipient
        dest = core.USERS[u].get("email") or (core.STATE.config.email_recipients[0] if core.STATE.config.email_recipients else core.STATE.config.email_sender)
        ok, err = await asyncio.to_thread(core.send_otp_via_email, dest, otp)
        if not ok:
            core._forgot_otp_store.pop(u, None)
            return {"ok": False, "error": err}
        return {"ok": True}

    @router.post("/api/forgot/reset")
    async def forgot_reset(data: ForgotPasswordRequest, request: Request):
        ip = core._get_client_ip(request)
        if core._is_login_locked(ip):
            raise HTTPException(429, "Too many failed attempts. Try again later.")
        if not core._check_rate_limit(request):
            raise HTTPException(429, "Too many requests. Try again later.")
        # username optional: if omitted, find user by matching OTP across store
        if data.username:
            u = data.username.strip()
        else:
            guess = data.otp.strip()
            u = next((k for k, v in list(core._forgot_otp_store.items())
                      if hmac.compare_digest(str(v.get("otp", "")).encode(), guess.encode())), None)
            if not u:
                # A guess with no username used to cost nothing: no attempt was
                # counted against anyone. It now counts against the caller.
                if core._forgot_otp_store:
                    core._record_login_failure(ip)
                raise HTTPException(401, "No OTP pending.")
        state = core._forgot_otp_store.get(u)
        if not state:
            core.USER_FORGOT_OTP = None
            raise HTTPException(401, "No OTP pending.")
        if time.time() - state["ts"] > 300:
            core._forgot_otp_store.pop(u, None)
            core.USER_FORGOT_OTP = None
            raise HTTPException(401, "OTP expired. Please request a new one.")
        if state["attempts"] >= 3:
            core._forgot_otp_store.pop(u, None)
            core.USER_FORGOT_OTP = None
            raise HTTPException(401, "Too many incorrect attempts. Please request a new OTP.")
        if not hmac.compare_digest(data.otp.strip().encode(), str(state["otp"]).encode()):
            state["attempts"] += 1
            core._record_login_failure(ip)
            if state["attempts"] >= 3:
                core._forgot_otp_store.pop(u, None)
                core.USER_FORGOT_OTP = None
            raise HTTPException(401, "Invalid OTP.")
        err = core._validate_password_strength(data.new_password)
        if err:
            raise HTTPException(400, err)
        if u not in core.USERS:
            raise HTTPException(404, "User not found.")
        core.USERS[u]["password"] = await asyncio.to_thread(core._hash_password, data.new_password.strip())
        core._invalidate_user_sessions(u)
        await asyncio.to_thread(core.save_users)
        core.log_system_update(f"Password reset for {u}.")
        core._forgot_otp_store.pop(u, None)
        core.USER_FORGOT_OTP = None
        return {"ok": True}

    return router
