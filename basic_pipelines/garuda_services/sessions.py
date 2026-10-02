"""Sessions: sign-in sessions, refresh tokens, cookies, rate limiting, login lockout and the route guards.

Moved out of Garuda_web.py (2026-10). Function bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`: the
live module, handed over once by bind(). Garuda_web imports these functions
back under the same names.
"""
import json
import os
import secrets
import time

from fastapi import HTTPException, Request
from typing import Optional

core = None


def bind(module):
    """Called by Garuda_web with itself, before anything here runs."""
    global core
    if core is not None and core is not module:
        # A second copy of Garuda_web (imported under another name) would
        # silently take over the state every function here reads.
        raise RuntimeError(f"{__name__} is already bound to {core.__name__}; "
                           f"refusing a second copy, {module.__name__}")
    core = module


def _invalidate_user_sessions(username: str, except_token: str | None = None,
                              except_refresh: str | None = None) -> int:
    """Remove all active sessions for a user. Returns count removed.

    Refresh tokens go too: a password change that left them alive let anyone
    holding an old refresh cookie keep minting sessions for another week.
    """
    to_delete = [
        t for t, s in list(core._sessions.items())
        if s.get("username") == username and t != except_token
    ]
    for t in to_delete:
        core._sessions.pop(t, None)
    keep_digest = core._rt_digest(except_refresh) if except_refresh else None
    for t in [t for t, s in list(core._refresh_tokens.items())
              if s.get("username") == username and t != except_refresh]:
        core._refresh_tokens.pop(t, None)
        core._refresh_dirty = True
    for d in [d for d, s in list(core._persisted_refresh.items())
              if s.get("username") == username and d != keep_digest]:
        core._persisted_refresh.pop(d, None)
        core._refresh_dirty = True
    return len(to_delete)


def _get_client_ip(request) -> str:
    """Return the real client IP, trusting proxy headers only from local proxies.

    Works for a Request or a WebSocket. Cloudflare sets CF-Connecting-IP itself
    and overwrites whatever the caller sent. X-Forwarded-For is appended to, so
    its first entry is whatever the caller typed: taking that one let anyone
    dodge the rate limit and the login lockout with a made-up header. The last
    entry is the one the proxy added.
    """
    client = request.client.host if request.client else "unknown"
    if client in ("127.0.0.1", "::1", "localhost"):
        cf = request.headers.get("CF-Connecting-IP", "").strip()
        if cf:
            return cf
        fwd = request.headers.get("X-Forwarded-For", "").split(",")[-1].strip()
        return fwd or client
    return client


def _check_rate_limit(request, bucket: str = "", limit: Optional[int] = None) -> bool:
    """Return True if request is within rate limit, False if exceeded."""
    limit = core._RATE_LIMIT if limit is None else limit
    ip = core._get_client_ip(request)
    now = time.time()
    stamps = core._rate_store[f"{bucket}:{ip}" if bucket else ip]
    stamps[:] = [t for t in stamps if now - t < core._RATE_WINDOW]
    if len(stamps) >= limit:
        return False
    stamps.append(now)
    return True


def _prune_rate_state():
    """Drop rate-limit and lockout records nobody is using any more.

    Both tables are keyed by client address and only ever grew: a scanner
    walking addresses left one entry behind for each, for the life of the
    process.
    """
    now = time.time()
    for key in list(core._rate_store.keys()):
        stamps = core._rate_store.get(key)
        if not stamps or now - max(stamps) > 3600:
            core._rate_store.pop(key, None)
    for ip in list(core._login_failures.keys()):
        entry = core._login_failures.get(ip) or {}
        until = entry.get("lockout_until", 0.0)
        if (until and now >= until) or (not until and now - entry.get("last", now) > 3600):
            core._login_failures.pop(ip, None)


def _is_login_locked(ip: str) -> bool:
    """Return True if the IP is currently locked out from login attempts."""
    entry = core._login_failures.get(ip)
    if not entry:
        return False
    if entry["lockout_until"] == 0.0:
        # Failures accumulating but threshold not yet reached
        return False
    if time.time() < entry["lockout_until"]:
        return True
    # Lockout expired — clear it
    core._login_failures.pop(ip, None)
    return False


def _record_login_failure(ip: str):
    """Increment failure count; trigger lockout after _LOGIN_MAX_ATTEMPTS."""
    entry = core._login_failures.setdefault(ip, {"count": 0, "lockout_until": 0.0})
    entry["count"] += 1
    entry["last"] = time.time()
    if entry["count"] >= core._LOGIN_MAX_ATTEMPTS:
        entry["lockout_until"] = time.time() + core._LOGIN_LOCKOUT_SECONDS
        core.log_system_update(
            f"[SECURITY] Login lockout: {ip} after {entry['count']} failed attempts "
            f"({core._LOGIN_LOCKOUT_SECONDS}s cooldown)."
        )


def _clear_login_failure(ip: str):
    """Clear failure record after a successful login."""
    core._login_failures.pop(ip, None)


def _load_refresh_tokens():
    data = {}
    try:
        if os.path.exists(core.REFRESH_TOKENS_FILE):
            with open(core.REFRESH_TOKENS_FILE, encoding="utf-8") as f:
                data = json.load(f)
    except Exception:
        data = {}
    now = time.time()
    core._persisted_refresh = {
        d: rec for d, rec in (data.items() if isinstance(data, dict) else [])
        if isinstance(rec, dict) and rec.get("expires", 0) > now and rec.get("username") in core.USERS
    }


def _save_refresh_tokens():
    """Write the digests of every live refresh token. Blocking (fsync)."""
    core._refresh_dirty = False
    now = time.time()
    snapshot = {d: rec for d, rec in list(core._persisted_refresh.items()) if rec.get("expires", 0) > now}
    for token, rec in list(core._refresh_tokens.items()):
        if rec.get("expires", 0) > now:
            snapshot[core._rt_digest(token)] = rec
    try:
        core._atomic_json_write(core.REFRESH_TOKENS_FILE, snapshot)
        os.chmod(core.REFRESH_TOKENS_FILE, 0o600)
    except Exception as exc:
        core._syslog.warning("could not persist refresh tokens: %s", exc)


def _revoke_refresh(token) -> bool:
    if not token:
        return False
    hit = core._refresh_tokens.pop(token, None) is not None
    hit = (core._persisted_refresh.pop(core._rt_digest(token), None) is not None) or hit
    if hit:
        core._refresh_dirty = True
    return hit


def create_refresh_token(username: str) -> str:
    token = secrets.token_hex(64)
    now = time.time()
    core._refresh_tokens[token] = {
        "username": username,
        "role": core.USERS[username]["role"],
        "expires": now + core._REFRESH_DURATION,
        "created_at": now,
    }
    core._refresh_dirty = True
    return token


def _prune_expired_refresh_tokens():
    now = time.time()
    for t in [t for t, s in list(core._refresh_tokens.items()) if s.get("expires", 0) <= now]:
        core._refresh_tokens.pop(t, None)
        core._refresh_dirty = True
    for d in [d for d, s in list(core._persisted_refresh.items()) if s.get("expires", 0) <= now]:
        core._persisted_refresh.pop(d, None)
        core._refresh_dirty = True


def _user_signed_in(username: str) -> bool:
    """True while `username` still holds a live session or refresh token.

    Long-lived connections (the state socket, the camera streams) are checked
    against this, not against the token they opened with: access tokens rotate
    every 15 minutes, but sign-out, a password change and deleting the account
    all leave the user with nothing, and the connection must end with it.
    """
    now = time.time()
    if any(s.get("username") == username and s.get("expires", 0) > now
           for s in list(core._sessions.values())):
        return True
    return any(s.get("username") == username and s.get("expires", 0) > now
               for s in list(core._refresh_tokens.values()) + list(core._persisted_refresh.values()))


def _is_cross_site(request) -> bool:
    """True when the page calling the API lives on another site (the Vercel copy).

    Such a page never gets our SameSite cookies back, so it is handed the
    refresh token in the body and returns it in X-Garuda-Refresh.
    """
    origin = request.headers.get("origin") or ""
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if not origin or not host:
        return False
    origin_host = origin.split("://", 1)[-1].split("/", 1)[0].split(":")[0].lower()
    return origin_host != host


def _cookie_secure(request) -> bool:
    """Secure cookies whenever the visitor reached us over HTTPS.

    SECURE_COOKIES in .env forces it; otherwise the proxy says which scheme the
    browser used, so a forgotten setting cannot send session cookies in clear.
    """
    if core._COOKIE_SECURE:
        return True
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return proto == "https" or '"scheme":"https"' in request.headers.get("cf-visitor", "").replace(" ", "")


def _set_session_cookies(request, response, access_token, refresh_token=None, persistent=True):
    """Set the session cookie and, when given, the refresh cookie.

    `persistent=False` is "Remember me" left unticked: the refresh cookie is
    then a browser-session cookie and goes when the browser closes.
    """
    secure = core._cookie_secure(request)
    response.set_cookie("garuda_session", access_token, httponly=True, samesite="lax",
                        secure=secure, max_age=core._ACCESS_DURATION)
    if refresh_token:
        response.set_cookie("garuda_refresh", refresh_token, httponly=True, samesite="lax",
                            secure=secure, path="/api/refresh",
                            max_age=core._REFRESH_DURATION if persistent else None)


def get_refresh_token(token: str) -> dict | None:
    s = core._refresh_tokens.get(token)
    if not s and token:
        # Issued before the last restart: known only by its digest.
        s = core._persisted_refresh.pop(core._rt_digest(token), None)
        if s:
            core._refresh_tokens[token] = s
    if not s:
        return None
    if s["expires"] <= time.time():
        core._revoke_refresh(token)
        return None
    return s


def create_session(username, duration=None):
    if duration is None:
        duration = core._ACCESS_DURATION
    token = secrets.token_hex(64)
    now = time.time()
    core._sessions[token] = {
        "username": username,
        "role": core.USERS[username]["role"],
        "expires": now + duration,
        "created_at": now,
        "max_lifetime": now + 86400,  # absolute 24-hour hard limit
        "logs_unlocked": False,
    }
    return token


def create_master_session(duration=3600):
    """Create an admin session via master key — logs unlocked immediately."""
    token = secrets.token_hex(64)
    now = time.time()
    core._sessions[token] = {
        "username": "admin",
        "role": "admin",
        "expires": now + duration,
        "created_at": now,
        "max_lifetime": now + 28800,  # absolute 8-hour hard limit for master sessions
        "logs_unlocked": True,
    }
    return token


def get_session(token):
    if not token:
        return None
    s = core._sessions.get(token)
    if not s:
        return None
    now = time.time()
    if s["expires"] <= now or now >= s.get("max_lifetime", now + 1):
        core._sessions.pop(token, None)   # pop: another thread may have pruned it already
        return None
    return s


def _prune_expired_sessions():
    """Remove sessions that have expired or exceeded their absolute lifetime."""
    now = time.time()
    dead = [t for t, s in list(core._sessions.items())
            if s["expires"] <= now or now >= s.get("max_lifetime", now + 1)]
    for t in dead:
        core._sessions.pop(t, None)
    core._prune_expired_refresh_tokens()


def require_session(request: Request):
    # X-Garuda-Token header takes priority (cross-origin API); cookie is browser fallback
    token = request.headers.get("X-Garuda-Token") or request.cookies.get("garuda_session")
    session = core.get_session(token)
    if not session:
        raise HTTPException(401, "Not authenticated")
    # No sliding window — access tokens are short-lived (15 min); use /api/refresh to renew.
    # Inject token so endpoints can exclude the current session during invalidation.
    session["token"] = token
    return session


def require_admin(request: Request):
    session = core.require_session(request)
    if session["role"] != "admin":
        raise HTTPException(403, "Admin access required")
    return session


def require_logs(request: Request):
    """Admin session AND master key must have been entered this session."""
    session = core.require_admin(request)
    if not session.get("logs_unlocked", False):
        raise HTTPException(403, "Master key required to view logs.")
    return session
