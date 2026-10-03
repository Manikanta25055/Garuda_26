"""Drishti's own sessions.

Accounts are shared with Garuda -- the same users.json, the same people --
but sessions and cookies are not. Widening Garuda's cookie to
.veeramanikanta.in would hand every Drishti session to garuda. and api. as
well, so Drishti issues its own host-scoped cookie instead.
"""
import json
import logging
import os
import secrets
import threading
import time

from fastapi import HTTPException, Request

log = logging.getLogger(__name__)

COOKIE_NAME = "drishti_session"
DEFAULT_DURATION_S = 8 * 3600

_sessions = {}
_lock = threading.Lock()

# Set by configure(). Until then sessions live only in memory, which is what the
# tests and any embedding without a data directory get.
_store_path = None


def configure(path):
    """Back the session table with a file, and read whatever is already there.

    Sessions were a plain dict, so every restart of the service signed everyone
    out -- including the restarts that deploying a change requires. A house you
    are locked out of because its camera server was updated is a worse failure
    than the one this costs: the tokens are now at rest on disk, which is why
    the file is written 0600 and holds no password.
    """
    global _store_path
    _store_path = path
    _load()


def _load():
    if not _store_path or not os.path.exists(_store_path):
        return
    try:
        with open(_store_path) as handle:
            stored = json.load(handle)
    except Exception as exc:
        # A half-written file costs the sessions, never the service. Everyone
        # signs in again -- which is exactly where we were before this existed.
        log.warning("session store unreadable, starting empty: %s", exc)
        return
    now = time.time()
    with _lock:
        _sessions.clear()
        _sessions.update({
            token: session for token, session in stored.items()
            if isinstance(session, dict) and session.get("expires", 0) > now
        })


def _save_locked():
    """Write the table out. The caller already holds _lock."""
    if not _store_path:
        return
    temp = f"{_store_path}.tmp"
    try:
        # Written to a temporary file and renamed, so a crash mid-write leaves
        # the previous table rather than a truncated one.
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(_sessions, handle)
        os.replace(temp, _store_path)
    except Exception as exc:
        log.warning("could not persist sessions: %s", exc)


def create_session(username, role, duration=None):
    token = secrets.token_urlsafe(32)
    expires = time.time() + (DEFAULT_DURATION_S if duration is None else duration)
    with _lock:
        _sessions[token] = {"username": username, "role": role, "expires": expires}
        _save_locked()
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
            _save_locked()
            return None
        return dict(session)


def destroy_session(token):
    with _lock:
        existed = _sessions.pop(token, None) is not None
        if existed:
            _save_locked()
        return existed


def prune_expired():
    now = time.time()
    with _lock:
        stale = [t for t, s in _sessions.items() if s["expires"] < now]
        for token in stale:
            _sessions.pop(token, None)
        if stale:
            _save_locked()
    return len(stale)


def invalidate_user(username):
    """Drop every session belonging to one account, after a password change."""
    with _lock:
        stale = [t for t, s in _sessions.items() if s["username"] == username]
        for token in stale:
            _sessions.pop(token, None)
        if stale:
            _save_locked()
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
