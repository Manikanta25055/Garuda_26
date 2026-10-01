"""The safety net for splitting Garuda_web.py into modules.

Moving code between files must change nothing a client or a test can see.
These tests freeze what "nothing" means:

  * every route: its methods, its path and which sign-in it demands
    (none / session / admin / logs), compared against tests/contract/routes.json
  * every name the rest of the test suite reaches for on the Garuda_web module

A refactor that drops a route, loosens its guard, or stops exporting a name
fails here, before anything else gets a chance to pass by accident.

When a change to the API is intended, regenerate the snapshot and review the
diff like any other change:

    GARUDA_UPDATE_CONTRACT=1 python -m pytest tests/test_split_contract.py
"""
import json
import os
import re
from pathlib import Path

import conftest  # noqa: F401
import Garuda_web as gw

HERE = Path(__file__).resolve().parent
SNAPSHOT = HERE / "contract" / "routes.json"
GUARDS = ("require_logs", "require_admin", "require_session",
          "require_drishti_admin", "require_drishti_session")


def _guard(dependant):
    """The strictest sign-in dependency a route declares, by name."""
    names = set()
    stack = list(getattr(dependant, "dependencies", []) or [])
    while stack:
        dep = stack.pop()
        names.add(getattr(dep.call, "__name__", ""))
        stack.extend(getattr(dep, "dependencies", []) or [])
    for guard in GUARDS:
        if guard in names:
            return guard
    return "none"


def current_contract():
    routes = []
    for route in gw.fastapi_app.routes:
        kind = type(route).__name__
        if kind == "APIRoute":
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                routes.append({"kind": "http", "method": method, "path": route.path,
                               "guard": _guard(route.dependant)})
        elif kind == "APIWebSocketRoute":
            routes.append({"kind": "websocket", "method": "WS", "path": route.path,
                           "guard": _guard(route.dependant)})
        elif kind == "Mount":
            routes.append({"kind": "mount", "method": "-", "path": route.path, "guard": "none"})
    return sorted(routes, key=lambda r: (r["path"], r["method"], r["kind"]))


def test_routes_methods_and_guards_are_unchanged():
    now = current_contract()
    if os.environ.get("GARUDA_UPDATE_CONTRACT") == "1":
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(json.dumps(now, indent=1) + "\n")
    frozen = json.loads(SNAPSHOT.read_text())
    key = lambda r: (r["kind"], r["method"], r["path"])           # noqa: E731
    before, after = {key(r): r["guard"] for r in frozen}, {key(r): r["guard"] for r in now}
    missing = sorted(set(before) - set(after))
    added = sorted(set(after) - set(before))
    changed = sorted((k, before[k], after[k]) for k in before.keys() & after.keys()
                     if before[k] != after[k])
    assert not missing, f"routes that disappeared: {missing}"
    assert not changed, f"routes whose sign-in requirement changed: {changed}"
    assert not added, f"new routes (regenerate the snapshot if intended): {added}"


def test_no_route_is_registered_twice():
    seen, twice = set(), []
    for r in current_contract():
        k = (r["kind"], r["method"], r["path"])
        if k in seen:
            twice.append(k)
        seen.add(k)
    assert not twice, f"registered more than once: {twice}"


def test_every_name_the_suite_uses_is_still_on_the_module():
    """Tests patch and call `gw.<name>`. A name that moved to another module
    must still be importable from Garuda_web, or those tests silently patch
    nothing."""
    used = set()
    for path in list(HERE.glob("*.py")) + list((HERE / "garuda_auto").glob("*.py")):
        if path.name == Path(__file__).name:
            continue
        used |= set(re.findall(r"\bgw\.([A-Za-z_][A-Za-z0-9_]*)", path.read_text()))
    missing = sorted(name for name in used if not hasattr(gw, name))
    assert not missing, f"no longer on Garuda_web: {missing}"


def test_middleware_stack_is_unchanged():
    names = [m.cls.__name__ for m in gw.fastapi_app.user_middleware]
    assert names == ["ApiVersionAlias", "BaseHTTPMiddleware", "BaseHTTPMiddleware",
                     "BaseHTTPMiddleware", "BaseHTTPMiddleware", "CORSMiddleware"], names
