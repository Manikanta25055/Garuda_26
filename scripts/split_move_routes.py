#!/usr/bin/env python3
"""Move one block of route handlers out of Garuda_web.py into garuda_routes/.

A development tool for the staged split of Garuda_web.py, not part of the
service. It does by program what is error-prone by hand:

  * cuts the handlers between two markers out of Garuda_web.py
  * rewrites every name that belongs to Garuda_web as `core.<name>`, so the
    handler reads (and assigns) the live module's state at call time; `global`
    statements become plain attribute assignments on `core`
  * leaves locals, builtins and ordinary imports alone
  * writes garuda_routes/<area>.py with a build_<area>_router(core) function
  * puts `fastapi_app.include_router(...)` where the handlers were, and adds
    the import to both import branches of Garuda_web.py

Then it re-parses the new module and refuses to finish if any name in it is
left unresolved.

    python3 scripts/split_move_routes.py master_keys \\
        --start '@fastapi_app.post("/api/master_key/login")' \\
        --end '@fastapi_app.get("/api/heartbeat")' \\
        --doc 'Master keys: ...'

Run the test suite afterwards; tests/test_split_contract.py checks that no
route, method or sign-in guard changed.
"""
import argparse
import ast
import builtins
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GW = ROOT / "basic_pipelines" / "Garuda_web.py"
ROUTES = ROOT / "basic_pipelines" / "garuda_routes"

# Names a route module imports for itself instead of reaching through `core`:
# the standard library and the web framework. Everything else that Garuda_web
# defines or imports is state or behaviour the tests may patch there.
OWN_IMPORTS = {
    "asyncio": "import asyncio", "datetime": "import datetime", "hmac": "import hmac",
    "json": "import json", "os": "import os", "re": "import re", "time": "import time",
    "threading": "import threading", "secrets": "import secrets", "sqlite3": "import sqlite3",
    "Optional": "from typing import Optional", "List": "from typing import List",
    "APIRouter": "from fastapi import APIRouter", "Depends": "from fastapi import Depends",
    "HTTPException": "from fastapi import HTTPException", "Request": "from fastapi import Request",
    "Response": "from fastapi import Response", "WebSocket": "from fastapi import WebSocket",
    "WebSocketDisconnect": "from fastapi import WebSocketDisconnect",
    "StreamingResponse": "from fastapi.responses import StreamingResponse",
    "HTMLResponse": "from fastapi.responses import HTMLResponse",
    "FileResponse": "from fastapi.responses import FileResponse",
    "JSONResponse": "from fastapi.responses import JSONResponse",
    "BaseModel": "from pydantic import BaseModel", "Field": "from pydantic import Field",
}


def module_names(tree):
    """Every name bound at the top level of a module (defs, assignments, imports)."""
    names = set()
    for node in ast.walk(tree):
        if node is tree:
            continue
    for node in tree.body:
        for sub in ast.walk(node) if isinstance(node, (ast.Try, ast.If, ast.With)) else [node]:
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(sub.name)
            elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                for alias in sub.names:
                    names.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(sub, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
                for target in targets:
                    for leaf in ast.walk(target):
                        if isinstance(leaf, ast.Name):
                            names.add(leaf.id)
    # `global X` inside a function also makes X a module name.
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            names.update(node.names)
    return names


def function_locals(fn):
    """Names that are local somewhere inside `fn` (its own or a nested scope's)."""
    declared_global = {n for node in ast.walk(fn) if isinstance(node, ast.Global) for n in node.names}
    local = set()
    for node in ast.walk(fn):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = node.args
            for a in args.posonlyargs + args.args + args.kwonlyargs:
                local.add(a.arg)
            if args.vararg:
                local.add(args.vararg.arg)
            if args.kwarg:
                local.add(args.kwarg.arg)
            if node is not fn and not isinstance(node, ast.Lambda):
                local.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            local.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            local.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                local.add((alias.asname or alias.name).split(".")[0])
    return local - declared_global


def rewrite(block, gw_names):
    """Return (new_source, own_imports_needed, moved_function_names)."""
    tree = ast.parse(block)
    lines = block.split("\n")
    edits = []          # (line index, col, old text, new text)
    drop_lines = set()
    used_own = set()
    functions = []
    for top in tree.body:
        if not isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)):
            raise SystemExit(f"only functions can be moved; found {type(top).__name__} "
                             f"at line {top.lineno} of the block")
        functions.append(top.name)
        local = function_locals(top)
        for node in ast.walk(top):
            if isinstance(node, ast.Global):
                drop_lines.add(node.lineno - 1)
            if not isinstance(node, ast.Name):
                continue
            name = node.id
            if name in OWN_IMPORTS and name not in local:
                used_own.add(name)
                continue
            if name in local or name not in gw_names:
                continue
            edits.append((node.lineno - 1, node.col_offset, name))
        # Decorators: @fastapi_app.get(...) hangs off the router instead.
        for deco in top.decorator_list:
            for node in ast.walk(deco):
                if isinstance(node, ast.Name) and node.id == "fastapi_app":
                    edits = [e for e in edits if not (e[0] == node.lineno - 1 and e[1] == node.col_offset)]
                    edits.append((node.lineno - 1, node.col_offset, "fastapi_app->router"))
    # Columns from ast are UTF-8 byte offsets; apply right-to-left per line.
    for line_no, col, name in sorted(set(edits), key=lambda e: (e[0], -e[1])):
        raw = lines[line_no].encode("utf-8")
        if name == "fastapi_app->router":
            old, new = b"fastapi_app", b"router"
        else:
            old, new = name.encode(), b"core." + name.encode()
        assert raw[col:col + len(old)] == old, (line_no, col, name, lines[line_no])
        lines[line_no] = (raw[:col] + new + raw[col + len(old):]).decode("utf-8")
    out = [line for i, line in enumerate(lines) if i not in drop_lines]
    return "\n".join(out), used_own, functions


def unresolved(module_source):
    tree = ast.parse(module_source)
    known = set(dir(builtins)) | module_names(tree) | {"core", "router"}
    missing = set()
    for top in ast.walk(tree):
        if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)):
            local = function_locals(top)
            for node in ast.walk(top):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    if node.id not in local and node.id not in known:
                        missing.add(node.id)
    # A name local to an outer function is visible in an inner one.
    outer_locals = set()
    for top in tree.body:
        if isinstance(top, ast.FunctionDef):
            outer_locals |= function_locals(top)
    return sorted(missing - outer_locals)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("area", help="module name under garuda_routes/, e.g. master_keys")
    ap.add_argument("--start", required=True, help="text that begins the block (first decorator)")
    ap.add_argument("--end", required=True, help="text that begins what follows the block")
    ap.add_argument("--doc", required=True, help="module docstring, first paragraph")
    ap.add_argument("--models", default="", help="comma-separated pydantic classes to move too")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    source = GW.read_text()
    if source.count(args.start) != 1 or source.count(args.end) != 1:
        raise SystemExit("--start and --end must each match exactly once")
    a, b = source.index(args.start), source.index(args.end)
    if a >= b:
        raise SystemExit("--start must come before --end")
    block = source[a:b].rstrip() + "\n"
    gw_tree = ast.parse(source)
    gw_names = module_names(gw_tree) - set(OWN_IMPORTS)

    body, used_own, functions = rewrite(block, gw_names)

    models_src, model_names = [], [m.strip() for m in args.models.split(",") if m.strip()]
    model_ranges = []
    for name in model_names:
        node = next((n for n in gw_tree.body if isinstance(n, ast.ClassDef) and n.name == name), None)
        if node is None:
            raise SystemExit(f"model not found at module level: {name}")
        seg = "\n".join(source.split("\n")[node.lineno - 1:node.end_lineno])
        models_src.append(seg)
        model_ranges.append((node.lineno - 1, node.end_lineno))
        for leaf in ast.walk(node):
            if isinstance(leaf, ast.Name) and leaf.id in OWN_IMPORTS:
                used_own.add(leaf.id)

    used_own |= {"APIRouter"}
    plain = sorted(v for k, v in OWN_IMPORTS.items() if k in used_own and v.startswith("import "))
    grouped = {}
    for k in used_own:
        stmt = OWN_IMPORTS[k]
        if stmt.startswith("from "):
            mod = stmt.split(" import ")[0]
            grouped.setdefault(mod, []).append(k)
    froms = [f"{mod} import {', '.join(sorted(names))}" for mod, names in sorted(grouped.items())]
    indented = "\n".join(("    " + line if line else line) for line in body.rstrip().split("\n"))
    module = (f'"""{args.doc}\n\nMoved out of Garuda_web.py (2026-10). Handler bodies are unchanged except\n'
              f'that names belonging to Garuda_web are read, and assigned, through `core`.\n"""\n'
              + "\n".join(plain) + ("\n\n" if plain else "") + "\n".join(froms) + "\n\n\n"
              + ("\n\n".join(models_src) + "\n\n\n" if models_src else "")
              + f"def build_{args.area}_router(core):\n    router = APIRouter()\n\n"
              + indented + "\n\n    return router\n")

    left = unresolved(module)
    if left:
        raise SystemExit(f"unresolved names in the new module: {left}")
    compile(module, f"{args.area}.py", "exec")

    exported = [f"build_{args.area}_router"] + model_names
    new_source = (source[:a] + f"fastapi_app.include_router(build_{args.area}_router(sys.modules[__name__]))\n\n"
                  + source[b:])
    if model_ranges:
        lines = new_source.split("\n")
        for start, end in sorted(model_ranges, reverse=True):
            # the block was cut after the models or before them; find by text instead
            pass
        for seg in models_src:
            if new_source.count(seg + "\n") != 1:
                raise SystemExit("could not remove a model unambiguously")
            new_source = new_source.replace(seg + "\n\n", "", 1) if (seg + "\n\n") in new_source \
                else new_source.replace(seg + "\n", "", 1)
    for prefix in (".", "basic_pipelines."):
        anchor = f"    from {prefix}garuda_routes.feedback import"
        if new_source.count(anchor) != 1:
            raise SystemExit("import anchor not found in Garuda_web.py")
        line = (f"    from {prefix}garuda_routes.{args.area} import {', '.join(exported)}"
                + ("  # noqa: F401" if model_names else "") + "\n")
        new_source = new_source.replace(anchor, line + anchor, 1)
    compile(new_source, "Garuda_web.py", "exec")

    print(f"functions moved : {', '.join(functions)}")
    print(f"models moved    : {', '.join(model_names) or '-'}")
    print(f"core.* rewrites : {body.count('core.')}")
    if args.dry_run:
        print(module)
        return
    (ROUTES / f"{args.area}.py").write_text(module)
    GW.write_text(new_source)
    print(f"wrote garuda_routes/{args.area}.py; Garuda_web.py is now {new_source.count(chr(10)) + 1} lines")


if __name__ == "__main__":
    main()
