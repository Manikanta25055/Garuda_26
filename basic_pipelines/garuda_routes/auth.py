"""Signing in and out: password login, session restore, refresh, the admin one-time code, and password reset.

Moved out of Garuda_web.py (2026-10). Handler bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`.
"""
import asyncio
import datetime
import hmac
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

# Wrong admin codes allowed per hour, across every code sent. Each code has its
# own three tries, but asking for a new code used to reset them, so knowing the
# password was enough to walk the whole million-code space.
ADMIN_OTP_FAILURES_PER_HOUR = 10


def _admin_otp_blocked(core) -> bool:
    now = time.time()
    fails = core.STATE.auth.admin_otp_failures
    fails[:] = [t for t in fails if now - t < 3600]
    return len(fails) >= ADMIN_OTP_FAILURES_PER_HOUR


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
    # Required. When it could be left out, the code was looked up among every
    # pending one and a wrong guess counted against none of them: the three-try
    # limit never applied, and only the per-address lockout stood in the way.
    username: str = Field(min_length=1, max_length=64)
    otp: str = Field(max_length=16)
    new_password: str

class SendForgotOTPRequest(BaseModel):
    username: str


def build_auth_router(core):
    router = APIRouter()

    @router.get("/api/users-public")
    async def users_public():
        """Return non-sensitive user info for login screen profile cards."""
        result = []
        for uname, udata in core.STATE.auth.users.items():
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
        user = core.STATE.auth.users.get(u)
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
            # No exceptions: the evaluation harness's env-gated bypass is gone.
            raise HTTPException(403, "Admin accounts must sign in via the Admin Access flow.")
        # Auto-migrate plaintext passwords to hashed
        if not user["password"].startswith("pbkdf2:"):
            user["password"] = await asyncio.to_thread(core._hash_password, p)
        core._clear_login_failure(ip)
        access_token = core.create_session(u)
        refresh_token = core.create_refresh_token(u)
        core.STATE.auth.sessions[access_token]["refresh"] = refresh_token
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
        # The refresh token stays in its httpOnly cookie, never in the body:
        # a page that stored it was handing a week-long sign-in to its scripts.
        return body

    @router.get("/api/session")
    async def session_info(session=Depends(core.require_session)):
        """Return current session user info — used to restore session on page refresh."""
        u = session["username"]
        return {
            "role": session["role"],
            "username": u,
            "display_name": core.STATE.auth.users.get(u, {}).get("display_name", u),
            "box_color": core.STATE.auth.users.get(u, {}).get("box_color", "#1565c0"),
            "logs_unlocked": session.get("logs_unlocked", False),
        }

    @router.post("/api/logout")
    async def logout(request: Request, response: Response):
        token = request.headers.get("X-Garuda-Token") or request.cookies.get("garuda_session")
        sess = core.STATE.auth.sessions.pop(token, None) if token else None
        # Also revoke the refresh token so stolen refresh tokens can't mint new
        # sessions. Its cookie is scoped to /api/refresh and never reaches this
        # route, so the one this session was issued with is remembered on it.
        for refresh in (request.cookies.get("garuda_refresh"), request.headers.get("X-Garuda-Refresh"),
                        (sess or {}).get("refresh")):
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
        if u not in core.STATE.auth.users:
            core._revoke_refresh(refresh)
            raise HTTPException(401, "User no longer exists.")
        access_token = core.create_session(u)
        core.STATE.auth.sessions[access_token]["refresh"] = refresh
        core._set_session_cookies(request, response, access_token)
        return {
            "token": access_token,
            "role": core.STATE.auth.users[u]["role"],
            "username": u,
        }

    @router.post("/api/refresh/revoke")
    async def revoke_refresh(request: Request, response: Response):
        """Sign-out's second half: end the refresh token this browser holds.

        The refresh cookie is sent only under /api/refresh, so /api/logout
        never saw it and a signed-out browser's token stayed valid for its
        whole week. This route is under that path, so the cookie arrives.
        """
        refresh = request.cookies.get("garuda_refresh") or request.headers.get("X-Garuda-Refresh")
        revoked = core._revoke_refresh(refresh) if refresh else False
        response.delete_cookie("garuda_refresh", path="/api/refresh",
                               secure=core._cookie_secure(request), httponly=True,
                               samesite="none" if core._cookie_secure(request) and core._is_cross_site(request) else "lax")
        return {"ok": True, "revoked": bool(revoked)}

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
        user = core.STATE.auth.users.get(u)
        stored = user["password"] if user else core._DUMMY_PASSWORD_HASH
        password_ok = await asyncio.to_thread(core._verify_password, p, stored)
        if user is None or not password_ok or user.get("role") != "admin":
            core._record_login_failure(ip)
            raise HTTPException(401, "Invalid admin credentials.")
        if _admin_otp_blocked(core):
            raise HTTPException(429, "Too many wrong codes in the last hour. Try again later, "
                                     "or sign in with a master key.")
        # Auto-migrate plaintext passwords to hashed
        if not user["password"].startswith("pbkdf2:"):
            user["password"] = await asyncio.to_thread(core._hash_password, p)
            await asyncio.to_thread(core.save_users)
        core.STATE.auth.admin_otp = core.generate_otp_code(6)
        core.STATE.auth.admin_otp_user = u          # store server-side so step 2 cannot be hijacked
        core.STATE.auth.admin_otp_ts = time.time()  # for expiry check
        core.STATE.auth.admin_otp_attempts = 0      # a new code gets its own three tries
        dest = core.STATE.config.email_recipients[0] if core.STATE.config.email_recipients else core.STATE.config.email_sender
        # SMTP can take ten seconds; off the event loop so nothing else waits on it.
        ok, err = await asyncio.to_thread(core.send_otp_via_email, dest, core.STATE.auth.admin_otp)
        if not ok:
            return {"ok": False, "error": err}
        return {"ok": True}

    @router.post("/api/admin/verify-otp")
    async def admin_verify_otp(data: VerifyOTPRequest, request: Request, response: Response):
        """Admin login step 2: verify OTP, issue session."""
        if not core._check_rate_limit(request):
            raise HTTPException(429, "Too many requests. Try again later.")
        ip = core._get_client_ip(request)
        if core._is_login_locked(ip) or _admin_otp_blocked(core):
            raise HTTPException(429, "Too many failed attempts. Try again later.")
        if not core.STATE.auth.admin_otp or not core.STATE.auth.admin_otp_user:
            raise HTTPException(401, "No OTP pending. Please restart login.")
        if time.time() - core.STATE.auth.admin_otp_ts > 300:
            core.STATE.auth.admin_otp = None; core.STATE.auth.admin_otp_user = None; core.STATE.auth.admin_otp_attempts = 0
            raise HTTPException(401, "OTP expired. Please request a new one.")
        if core.STATE.auth.admin_otp_attempts >= 3:
            core.STATE.auth.admin_otp = None; core.STATE.auth.admin_otp_user = None; core.STATE.auth.admin_otp_attempts = 0
            raise HTTPException(401, "Too many incorrect attempts. Please restart login.")
        if not hmac.compare_digest(data.otp.strip().encode(), str(core.STATE.auth.admin_otp).encode()):
            core.STATE.auth.admin_otp_attempts += 1
            core.STATE.auth.admin_otp_failures.append(time.time())
            core._record_login_failure(ip)
            if _admin_otp_blocked(core):
                core.log_system_update("[SECURITY] Admin sign-in codes blocked for an hour after "
                                       f"{ADMIN_OTP_FAILURES_PER_HOUR} wrong codes (last from {ip}).")
            raise HTTPException(401, "Invalid OTP.")
        u = core.STATE.auth.admin_otp_user   # use server-stored username, not client-supplied
        core.STATE.auth.admin_otp = None; core.STATE.auth.admin_otp_user = None; core.STATE.auth.admin_otp_ts = 0; core.STATE.auth.admin_otp_attempts = 0
        core.STATE.auth.admin_otp_failures.clear()
        if u not in core.STATE.auth.users or core.STATE.auth.users[u]["role"] != "admin":
            raise HTTPException(401, "Account not authorised.")
        core._clear_login_failure(ip)
        access_token = core.create_session(u)
        refresh_token = core.create_refresh_token(u)
        core.STATE.auth.sessions[access_token]["refresh"] = refresh_token
        core._set_session_cookies(request, response, access_token, refresh_token)
        core.log_system_update(f"Admin login: {u}")
        body = {
            "role": "admin",
            "username": u,
            "display_name": core.STATE.auth.users[u].get("display_name", u),
            "token": access_token,   # for cross-origin clients
        }
        # The refresh token stays in its httpOnly cookie, never in the body:
        # a page that stored it was handing a week-long sign-in to its scripts.
        return body

    @router.post("/api/forgot/send-otp")
    async def forgot_send_otp(data: SendForgotOTPRequest, request: Request):
        if not core._check_rate_limit(request):
            raise HTTPException(429, "Too many requests. Try again later.")
        u = data.username.strip()
        # Always return the same response regardless of whether user exists (anti-enumeration)
        if u not in core.STATE.auth.users:
            return {"ok": True}
        otp = core.generate_otp_code(6)
        core.STATE.auth.forgot_otp_store[u] = {"otp": otp, "ts": time.time(), "attempts": 0}
        core.STATE.auth.user_forgot_otp = otp   # test-facing alias
        # Send to the user's own email if stored, else fall back to admin recipient
        dest = core.STATE.auth.users[u].get("email") or (core.STATE.config.email_recipients[0] if core.STATE.config.email_recipients else core.STATE.config.email_sender)
        ok, err = await asyncio.to_thread(core.send_otp_via_email, dest, otp)
        if not ok:
            core.STATE.auth.forgot_otp_store.pop(u, None)
            return {"ok": False, "error": err}
        return {"ok": True}

    @router.post("/api/forgot/reset")
    async def forgot_reset(data: ForgotPasswordRequest, request: Request):
        ip = core._get_client_ip(request)
        if core._is_login_locked(ip):
            raise HTTPException(429, "Too many failed attempts. Try again later.")
        if not core._check_rate_limit(request):
            raise HTTPException(429, "Too many requests. Try again later.")
        u = data.username.strip()
        state = core.STATE.auth.forgot_otp_store.get(u)
        if not state:
            core.STATE.auth.user_forgot_otp = None
            raise HTTPException(401, "No OTP pending.")
        if time.time() - state["ts"] > 300:
            core.STATE.auth.forgot_otp_store.pop(u, None)
            core.STATE.auth.user_forgot_otp = None
            raise HTTPException(401, "OTP expired. Please request a new one.")
        if state["attempts"] >= 3:
            core.STATE.auth.forgot_otp_store.pop(u, None)
            core.STATE.auth.user_forgot_otp = None
            raise HTTPException(401, "Too many incorrect attempts. Please request a new OTP.")
        if not hmac.compare_digest(data.otp.strip().encode(), str(state["otp"]).encode()):
            state["attempts"] += 1
            core._record_login_failure(ip)
            if state["attempts"] >= 3:
                core.STATE.auth.forgot_otp_store.pop(u, None)
                core.STATE.auth.user_forgot_otp = None
            raise HTTPException(401, "Invalid OTP.")
        err = core._validate_password_strength(data.new_password)
        if err:
            raise HTTPException(400, err)
        if u not in core.STATE.auth.users:
            raise HTTPException(404, "User not found.")
        core.STATE.auth.users[u]["password"] = await asyncio.to_thread(core._hash_password, data.new_password.strip())
        core._invalidate_user_sessions(u)
        await asyncio.to_thread(core.save_users)
        core.log_system_update(f"Password reset for {u}.")
        core.STATE.auth.forgot_otp_store.pop(u, None)
        core.STATE.auth.user_forgot_otp = None
        return {"ok": True}

    return router
