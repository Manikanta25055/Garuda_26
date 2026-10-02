"""Master keys: sign in with one, unlock the logs, list, add (after an emailed code) and delete.

Moved out of Garuda_web.py (2026-10). Handler bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`.
"""
import asyncio
import hmac
import os
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response


def build_master_keys_router(core):
    router = APIRouter()

    @router.post("/api/master_key/login")
    async def master_key_login(data: dict, request: Request, response: Response):
        """Log in with only a master key — issues an admin session with logs unlocked."""
        ip = core._get_client_ip(request)
        if core._is_login_locked(ip):
            raise HTTPException(429, "Too many failed attempts. Try again later.")
        if not core._check_rate_limit(request):
            raise HTTPException(429, "Too many requests. Try again later.")
        key = str(data.get("key") or "").strip()
        # Also accept the bootstrap env var key in case keys file hasn't been written yet
        _env_key = os.environ.get("MASTER_KEY", "").strip()
        valid_keys = list(core.MASTER_KEYS) + ([_env_key] if _env_key else [])
        if not await asyncio.to_thread(core._master_key_matches, key, valid_keys):
            # Same five-strikes lockout as a password: this key is a full admin
            # sign-in, and before it could be guessed at 30 tries a minute for ever.
            core._record_login_failure(ip)
            raise HTTPException(401, "Invalid master key.")
        core._clear_login_failure(ip)
        # Persist env key to file so future restarts find it
        if not await asyncio.to_thread(core._master_key_matches, key, core.MASTER_KEYS):
            core.MASTER_KEYS.append(core._mk_hash(key))
            core.save_master_keys()
        token = core.create_master_session()
        response.set_cookie("garuda_session", token, httponly=True, samesite="lax",
                            secure=core._cookie_secure(request), max_age=3600)
        core.log_system_update("Master key login.")
        return {
            "role": "admin",
            "username": "admin",
            "display_name": core.USERS.get("admin", {}).get("display_name", "Admin"),
            "token": token,
            "logs_unlocked": True,
        }

    @router.post("/api/master_key/verify")
    async def master_key_verify(data: dict, request: Request, session=Depends(core.require_admin)):
        """Unlock logs on an existing admin session by verifying a master key."""
        ip = core._get_client_ip(request)
        if core._is_login_locked(ip):
            raise HTTPException(429, "Too many failed attempts. Try again later.")
        key = str(data.get("key") or "").strip()
        if not await asyncio.to_thread(core._master_key_matches, key, core.MASTER_KEYS):
            core._record_login_failure(ip)
            raise HTTPException(401, "Invalid master key.")
        # The session the request was authenticated with (header first, as in
        # require_session); the cookie-first lookup here could unlock a different one.
        token = session.get("token")
        if token and token in core._sessions:
            core._sessions[token]["logs_unlocked"] = True
        return {"ok": True, "logs_unlocked": True}

    @router.get("/api/master_keys")
    async def list_master_keys(session=Depends(core.require_admin)):
        """Return master keys with all but last 4 chars masked."""
        masked = [core._mk_mask(k) for k in core.MASTER_KEYS]
        return {"keys": masked, "count": len(core.MASTER_KEYS)}

    @router.post("/api/master_key/request_otp")
    async def master_key_request_otp(data: dict, session=Depends(core.require_admin)):
        """Step 1 of adding a master key: verify an existing key, then email OTP."""
        current = str(data.get("current_key") or "").strip()
        if not await asyncio.to_thread(core._master_key_matches, current, core.MASTER_KEYS):
            raise HTTPException(401, "Current master key is incorrect.")
        core.MASTER_KEY_OTP = core.generate_otp_code(6)
        core._master_otp_ts = time.time()
        core._master_otp_attempts = 0
        dest = core.STATE.config.email_recipients[0] if core.STATE.config.email_recipients else core.STATE.config.email_sender
        ok, err = await asyncio.to_thread(core.send_otp_via_email, dest, core.MASTER_KEY_OTP)
        if not ok:
            return {"ok": False, "error": err}
        return {"ok": True}

    @router.post("/api/master_key/add")
    async def master_key_add(data: dict, session=Depends(core.require_admin)):
        """Step 2: verify OTP and persist new master key."""
        otp = str(data.get("otp") or "").strip()
        new_key = str(data.get("new_key") or "").strip()
        if not core.MASTER_KEY_OTP:
            raise HTTPException(401, "Invalid OTP.")
        # The code had no expiry and no limit on guesses: six digits, a million
        # tries, as long as the server stayed up. Now five minutes and three tries.
        if core._master_otp_ts and time.time() - core._master_otp_ts > core._MASTER_OTP_TTL:
            core.MASTER_KEY_OTP = None
            raise HTTPException(401, "OTP expired. Request a new one.")
        if not otp or not hmac.compare_digest(otp.encode(), str(core.MASTER_KEY_OTP).encode()):
            core._master_otp_attempts += 1
            if core._master_otp_attempts >= 3:
                core.MASTER_KEY_OTP = None
                core._master_otp_attempts = 0
            raise HTTPException(401, "Invalid OTP.")
        if len(new_key) > 128:
            raise HTTPException(400, "Key must be at most 128 characters.")
        if not new_key or len(new_key) < 12:
            raise HTTPException(400, "Key must be at least 12 characters.")
        if not re.search(r'[A-Z]', new_key):
            raise HTTPException(400, "Key must contain at least one uppercase letter.")
        if not re.search(r'[a-z]', new_key):
            raise HTTPException(400, "Key must contain at least one lowercase letter.")
        if not re.search(r'[0-9]', new_key):
            raise HTTPException(400, "Key must contain at least one number.")
        if not re.search(r'[^A-Za-z0-9]', new_key):
            raise HTTPException(400, "Key must contain at least one symbol (!@#$ etc.).")
        _MK_COMMON = ['password','master','admin','garuda','security','qwerty','asdfgh',
                       'zxcvbn','123456','letmein','welcome','login','access']
        if any(w in new_key.lower() for w in _MK_COMMON):
            raise HTTPException(400, "Key contains a common word or sequence — choose something more random.")
        if await asyncio.to_thread(core._master_key_matches, new_key, core.MASTER_KEYS):
            raise HTTPException(400, "Key already exists.")
        # Reject keys too similar to existing ones (shared 6-char substring). Only
        # possible against a key still held as typed; a hashed key cannot be
        # compared this way, which is the point of hashing it.
        for existing in core.MASTER_KEYS:
            if core._mk_is_hashed(existing):
                continue
            for i in range(len(existing) - 5):
                if existing[i:i+6] in new_key:
                    raise HTTPException(400, "Key is too similar to an existing master key.")
        core.MASTER_KEYS.append(core._mk_hash(new_key))
        await asyncio.to_thread(core.save_master_keys)
        core.MASTER_KEY_OTP = None
        core.log_system_update("New master key added.")
        return {"ok": True}

    @router.post("/api/master_key/delete")
    async def master_key_delete(data: dict, session=Depends(core.require_admin)):
        """Delete a master key by index — cannot delete the last key."""
        idx = data.get("index")
        if idx is None or isinstance(idx, bool) or not isinstance(idx, int):
            raise HTTPException(400, "index required.")
        if len(core.MASTER_KEYS) <= 1:
            raise HTTPException(400, "Cannot delete the last master key.")
        if idx < 0 or idx >= len(core.MASTER_KEYS):
            raise HTTPException(400, "Index out of range.")
        core.MASTER_KEYS.pop(idx)
        core.save_master_keys()
        core.log_system_update("Master key deleted.")
        return {"ok": True}

    return router
