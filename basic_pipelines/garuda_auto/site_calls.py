"""Doing what a button does: Narada runs the site's own endpoint as the person.

Most routes hold their rules in the handler itself (who may change a schedule,
what a valid phrase is, how a clip is started). Copying those into agent tools
would give the house two versions of each rule. So a capability that names a
route is carried out by calling that route's handler here, with the session of
the person who asked: the same guard decides, the same request model checks the
arguments, and an HTTP error comes back as the reason it was not done.

Only handlers that answer with data are called this way. One that needs the
browser's request (a stream, a file download) is not, and says so.
"""
import asyncio
import inspect
import threading
import typing

from fastapi import HTTPException, Request
from fastapi.params import Depends
from fastapi.routing import APIRoute
from pydantic import BaseModel, ValidationError

MAX_ITEMS = 40          # of any list handed back to the model
CALL_TIMEOUT_S = 25

# What each sign-in guard asks of a session.
_GUARDS = {
    "require_session": lambda s: None,
    "require_admin": lambda s: None if s.get("role") == "admin" else "only an admin can do that",
    "require_logs": lambda s: (
        "only an admin can do that" if s.get("role") != "admin"
        else None if s.get("logs_unlocked") else "the master key must be entered on the Logs page first"),
}


def trim(value, limit=MAX_ITEMS, key=""):
    """Long lists cut to `limit` items: the newest end of a log, the start of anything else."""
    if isinstance(value, dict):
        return {k: trim(v, limit, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        kept = value[-limit:] if key.endswith("log") else value[:limit]
        return [trim(v, limit) for v in kept]
    return value


def _coerce(value, kind):
    if kind is bool:
        return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes")
    if kind in (int, float):
        return kind(value)
    if kind is str:
        return str(value)
    return value


class SiteCaller:
    def __init__(self, app, loop_fn=None, timeout=CALL_TIMEOUT_S):
        self.app = app
        self.loop_fn = loop_fn or (lambda: None)
        self.timeout = timeout

    def route(self, method, path):
        for route in self.app.routes:
            if isinstance(route, APIRoute) and route.path == path and method in route.methods:
                return route
        return None

    def call(self, method, path, args, session):
        """Run `METHOD path` for `session` from a worker thread.
        Returns the handler's data, or {"error": why}."""
        coro, refusal = self._prepare(method, path, args, session, None)
        if refusal:
            return refusal
        try:
            return self._finish(self._run(coro))
        except HTTPException as exc:
            return {"error": str(exc.detail), "status": exc.status_code}
        except TimeoutError:
            return {"error": "that took too long and was abandoned"}

    async def acall(self, method, path, args, session, request=None):
        """The same from inside a request handler, which can lend its own request
        to an endpoint that reads cookies or headers."""
        coro, refusal = self._prepare(method, path, args, session, request)
        if refusal:
            return refusal
        try:
            return self._finish(await coro if inspect.isawaitable(coro) else coro)
        except HTTPException as exc:
            return {"error": str(exc.detail), "status": exc.status_code}

    def _prepare(self, method, path, args, session, request):
        route = self.route(method, path)
        if route is None:
            return None, {"error": f"the site has no {method} {path}"}
        try:
            kwargs = self._arguments(route, dict(args or {}), dict(session or {}), request)
        except _Refused as refusal:
            return None, {"error": str(refusal)}
        return route.endpoint(**kwargs), None

    @staticmethod
    def _finish(result):
        if not isinstance(result, (dict, list)):
            return {"error": "that answers with a file, not data"}
        return trim(result)

    def _arguments(self, route, args, session, request=None):
        endpoint = route.endpoint
        try:
            hints = typing.get_type_hints(endpoint)
        except Exception:
            hints = {}
        in_path = set(route.param_convertors)
        kwargs, body_name, body_model = {}, None, None
        for name, param in inspect.signature(endpoint).parameters.items():
            kind = hints.get(name, param.annotation)
            if isinstance(param.default, Depends):
                check = _GUARDS.get(getattr(param.default.dependency, "__name__", ""))
                if check is None:
                    raise _Refused("that needs a person's own sign-in")
                problem = check(session)
                if problem:
                    raise _Refused(problem)
                kwargs[name] = session
            elif inspect.isclass(kind) and issubclass(kind, BaseModel):
                body_name, body_model = name, kind
            elif kind is Request and request is not None:
                kwargs[name] = request
            elif name in in_path:
                if args.get(name) in (None, ""):
                    raise _Refused(f"{name} is needed")
                kwargs[name] = str(args.pop(name))
            elif name in args and args[name] is not None:
                try:
                    kwargs[name] = _coerce(args.pop(name), _plain(kind))
                except (TypeError, ValueError):
                    raise _Refused(f"{name} has the wrong kind of value")
            elif param.default is inspect.Parameter.empty:
                raise _Refused("that needs the browser's own request")
        if body_model is not None:
            try:
                kwargs[body_name] = body_model(**args)
            except ValidationError as exc:
                first = exc.errors()[0]
                raise _Refused(f"{'.'.join(map(str, first['loc']))}: {first['msg']}")
        return kwargs

    def _run(self, coro):
        if not inspect.isawaitable(coro):
            return coro
        loop = self.loop_fn()
        if loop is not None and loop.is_running():
            if getattr(loop, "_thread_id", None) == threading.get_ident():
                coro.close()
                raise RuntimeError("a site call must come from a worker thread")
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            try:
                return future.result(self.timeout)
            except TimeoutError:
                future.cancel()
                raise
        # No server loop (a script, a test that calls the agent directly).
        return asyncio.run(coro)


class _Refused(Exception):
    pass


def _plain(kind):
    """int for `int`, `int | None` and `Optional[int]`; None when it is not that simple."""
    if kind in (int, float, bool, str):
        return kind
    for inner in typing.get_args(kind):
        if inner in (int, float, bool, str):
            return inner
    return None
