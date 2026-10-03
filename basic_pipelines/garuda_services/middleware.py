"""Middleware: the global rate limit, the per-product scope and the security headers.

Moved out of Garuda_web.py (2026-10). Function bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`: the
live module, handed over once by bind(). Garuda_web imports these functions
back under the same names.
"""
import hmac
import os

from fastapi import Request

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


async def global_rate_limit(request: Request, call_next):
    path = request.url.path
    _eval_tok = os.environ.get("GARUDA_EVAL_TOKEN", "")
    _tok_hdr = request.headers.get("X-Eval-Token", "")
    _eval_bypass = bool(_eval_tok) and hmac.compare_digest(_tok_hdr.encode(), _eval_tok.encode())
    if not _eval_bypass and not any(path.startswith(p) for p in core._RATE_EXEMPT_PREFIXES):
        token = request.headers.get("X-Garuda-Token") or request.cookies.get("garuda_session")
        signed_in = bool(token) and core.get_session(token) is not None
        allowed = (core._check_rate_limit(request, "session", core._RATE_LIMIT_SESSION) if signed_in
                   else core._check_rate_limit(request))
        if not allowed:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Too many requests. Try again later."}, status_code=429)
    return await call_next(request)


async def product_scope(request: Request, call_next):
    """The security-only product has no home automation, on the server too."""
    if (request.url.path.startswith("/api/home")
            and core._product_for_host(request.headers.get("host")) == "security"):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return await call_next(request)


async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    if response.headers.get("Content-Security-Policy", "").endswith("sandbox allow-scripts"):
        # An artifact page (garuda_routes/artifacts.py) brings its own, stricter
        # policy and must be framed by the chat: the site's policy is not laid over it.
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        # blob:/data: scripts are the voice client's audio worklets; the
        # ElevenLabs hosts carry Narada's WebRTC voice session.
        "script-src 'self' 'unsafe-inline' blob: data:; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' blob: data:; "
        "media-src 'self' blob: mediastream:; "
        "font-src 'self'; "
        "connect-src 'self' wss: ws: https://*.elevenlabs.io; "
        "frame-ancestors 'none'"
    )
    # Answers from the API describe this moment and this user; nothing between
    # the Pi and the browser should keep a copy.
    if request.url.path.startswith("/api/") and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
    # The page's own script and style: kept, but asked after on every load (a
    # 304 when unchanged). With no header the browser guessed how long they
    # stay fresh and ran yesterday's script against today's server.
    elif request.url.path.startswith("/static/") and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-cache"
    return response
