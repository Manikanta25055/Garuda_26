"""What every request gets, whichever route answers it.

  request id   X-Request-ID on every response (taken from the caller when it
               sends a sane one), and on every log line written while the
               request runs. "It failed at 21:04" becomes one grep.
  /api/v1      every /api/... route also answers under /api/v<N>/..., so a
               client can pin the contract it was built against. The bare
               /api/ paths stay: the web app and the native app use them.
  one error    errors keep FastAPI's {"detail": ...} (clients read it) and
  shape        gain {"error": {code, status, message, request_id}}. An
               unexpected exception is a logged traceback and a clean JSON
               500 with the id to quote, never a stack trace to the browser.
  access log   one line per API call: method, path, status, time, client.
"""
import logging
import re
import time
import uuid

from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .logging_setup import request_id as _request_id

log = logging.getLogger("garuda.http")
access = logging.getLogger("garuda.access")

_SANE_ID = re.compile(r"^[A-Za-z0-9_.-]{8,64}$")
_CODES = {400: "bad_request", 401: "not_authenticated", 403: "forbidden", 404: "not_found",
          405: "method_not_allowed", 409: "conflict", 413: "too_large", 422: "validation_error",
          429: "rate_limited", 500: "internal_error", 501: "not_implemented",
          502: "upstream_error", 503: "unavailable", 504: "timeout"}


class ApiVersionAlias:
    """ASGI middleware: /api/v<N>/x is served by the route for /api/x."""

    def __init__(self, app, version="1"):
        self.app = app
        self._prefix = f"/api/v{version}"

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path == self._prefix or path.startswith(self._prefix + "/"):
                rewritten = "/api" + path[len(self._prefix):]
                scope = dict(scope, path=rewritten, raw_path=rewritten.encode())
        await self.app(scope, receive, send)


def _message(detail):
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list) and detail:
        first = detail[0]
        if isinstance(first, dict):
            where = ".".join(str(p) for p in first.get("loc", []) if p != "body")
            return f"{where}: {first.get('msg', 'invalid')}" if where else str(first.get("msg", "invalid"))
    return str(detail)


def error_body(status, detail):
    return {"detail": detail,
            "error": {"code": _CODES.get(status, "error"), "status": status,
                      "message": _message(detail), "request_id": _request_id.get()}}


def install(app, *, api_version="1", client_ip=None, quiet_paths=("/api/health", "/api/ready", "/api/heartbeat")):
    """Attach the middleware and error handlers to `app`. Call after the
    app's own middleware is registered, so this wraps all of it."""

    @app.middleware("http")
    async def request_context(request, call_next):
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if _SANE_ID.match(incoming) else uuid.uuid4().hex[:12]
        token = _request_id.set(rid)
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            response.headers["X-Garuda-API"] = api_version
            return response
        finally:
            path = request.url.path
            if path.startswith("/api/") and path not in quiet_paths:
                ms = (time.perf_counter() - started) * 1000
                ip = client_ip(request) if client_ip else (request.client.host if request.client else "-")
                level = logging.WARNING if status >= 500 else logging.INFO
                access.log(level, "%s %s %d %.0fms %s", request.method, path, status, ms, ip)
            _request_id.reset(token)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return JSONResponse(error_body(exc.status_code, exc.detail), status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(error_body(422, jsonable_encoder(exc.errors(), custom_encoder={Exception: str})),
                            status_code=422)

    @app.exception_handler(Exception)
    async def unhandled(request, exc):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            error_body(500, "Something went wrong on the server. Quote the request id if you report it."),
            status_code=500, headers={"X-Request-ID": _request_id.get()})

    app.add_middleware(ApiVersionAlias, version=api_version)


def tag_routes(app, groups):
    """Give untagged routes an OpenAPI tag from their path, so the generated
    reference is grouped by area instead of being one flat list of 80 routes.
    `groups` is [(path prefix, tag)], first match wins."""
    for route in app.routes:
        path = getattr(route, "path", "")
        if not hasattr(route, "tags") or route.tags:
            continue
        for prefix, tag in groups:
            if path.startswith(prefix):
                route.tags = [tag]
                break
    app.openapi_schema = None
