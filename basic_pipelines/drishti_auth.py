"""Drishti's own sessions.

Accounts are shared with Garuda -- the same users.json, the same people --
but sessions and cookies are not. Widening Garuda's cookie to
.veeramanikanta.in would hand every Drishti session to garuda. and api. as
well, so Drishti issues its own host-scoped cookie instead.
"""
import secrets
import threading
import time

from fastapi import HTTPException, Request

COOKIE_NAME = "drishti_session"
DEFAULT_DURATION_S = 8 * 3600

_sessions = {}
_lock = threading.Lock()


def create_session(username, role, duration=None):
    token = secrets.token_urlsafe(32)
    expires = time.time() + (DEFAULT_DURATION_S if duration is None else duration)
    with _lock:
        _sessions[token] = {"username": username, "role": role, "expires": expires}
    return token


def get_session(token):
    if not token:
        return None
    with _lock:
        session = _sessions.get(token)
        if session is None:
            return None
        if session["expires"] < time.time():
            _sessions.pop(token, None)
            return None
        return dict(session)


def destroy_session(token):
    with _lock:
        return _sessions.pop(token, None) is not None


def prune_expired():
    now = time.time()
    with _lock:
        stale = [t for t, s in _sessions.items() if s["expires"] < now]
        for token in stale:
            _sessions.pop(token, None)
    return len(stale)


def invalidate_user(username):
    """Drop every session belonging to one account, after a password change."""
    with _lock:
        stale = [t for t, s in _sessions.items() if s["username"] == username]
        for token in stale:
            _sessions.pop(token, None)
    return len(stale)


def require_drishti_session(request: Request):
    session = get_session(request.cookies.get(COOKIE_NAME))
    if session is None:
        raise HTTPException(status_code=401, detail="not signed in")
    return session


def require_drishti_admin(request: Request):
    session = require_drishti_session(request)
    if session.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    return session
