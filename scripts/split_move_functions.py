#!/usr/bin/env python3
"""Move a group of plain functions out of Garuda_web.py into garuda_services/.

The companion of split_move_routes.py, for code that is not a route handler:
background loops, persistence, alerts. A development tool, not part of the
service.

The moved module keeps a reference to the live Garuda_web module:

    core = None
    def bind(module): ...      # Garuda_web calls this with itself, once

Every name that belongs to Garuda_web is rewritten to `core.<name>`, including
calls between the moved functions themselves, so a value or a function that a
test patches on Garuda_web is still the one that gets used. `global X`
assignments become `core.X = ...`. Garuda_web imports the functions back under
their old names, so nothing that calls them changes.

Refuses, rather than guesses, when a function:
  * uses globals(), locals(), vars(), __name__ or __file__ (they would point
    at the new module);
  * names a Garuda_web value in a default argument, decorator or annotation
    (those are evaluated at import, before bind() has run).

    python3 scripts/split_move_functions.py presence \\
        --functions _get_local_subnet,_presence_poller --doc '...' [--dry-run]
"""
import argparse
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from split_move_routes import (GW, MODULE_BOUND, OWN_IMPORTS, ROOT, free_names,  # noqa: E402
                               function_locals, module_names, unresolved)

SERVICES = ROOT / "basic_pipelines" / "garuda_services"
EXTRA_IMPORTS = {
    "subprocess": "import subprocess", "socket": "import socket", "ipaddress": "import ipaddress",
    "smtplib": "import smtplib", "traceback": "import traceback", "hashlib": "import hashlib",
    "tempfile": "import tempfile", "math": "import math", "signal": "import signal",
    "sys": "import sys", "np": "import numpy as np", "logging": "import logging",
    "Path": "from pathlib import Path", "defaultdict": "from collections import defaultdict",
}
OWN = {**OWN_IMPORTS, **EXTRA_IMPORTS}


def def_time_names(fn):
    """Names evaluated when the `def` statement runs: defaults, decorators, annotations."""
    out = set()
    parts = list(fn.decorator_list) + list(fn.args.defaults) + [d for d in fn.args.kw_defaults if d]
    for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs:
        if a.annotation is not None:
            parts.append(a.annotation)
    if fn.returns is not None:
        parts.append(fn.returns)
    for part in parts:
        for node in ast.walk(part):
            if isinstance(node, ast.Name):
                out.add((node.id, node.lineno, node.col_offset))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("area")
    ap.add_argument("--functions", required=True, help="comma-separated top-level function names")
    ap.add_argument("--doc", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    source = GW.read_text()
    lines = source.split("\n")
    tree = ast.parse(source)
    wanted = [n.strip() for n in args.functions.split(",") if n.strip()]
    gw_names = module_names(tree) - set(OWN)

    nodes = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted:
            if node.name in nodes:
                raise SystemExit(f"{node.name} is defined twice at module level")
            nodes[node.name] = node
    missing = [n for n in wanted if n not in nodes]
    if missing:
        raise SystemExit(f"not top-level functions of Garuda_web.py: {missing}")

    used_own, pieces, ranges = set(), [], []
    for name in wanted:
        fn = nodes[name]
        start = min([fn.lineno] + [d.lineno for d in fn.decorator_list]) - 1
        end = fn.end_lineno
        local = function_locals(fn)
        frozen = def_time_names(fn)
        frozen_pos = {(l, c) for _, l, c in frozen}
        for ident, lineno, _ in frozen:
            if ident in gw_names:
                raise SystemExit(f"{name}() names {ident} in a default, decorator or annotation "
                                 f"(line {lineno}); that is evaluated before bind(). Change it first.")
        seg = lines[start:end]
        edits, drop = [], set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Name) and node.id in MODULE_BOUND and node.id not in local:
                raise SystemExit(f"{name}() uses {node.id} (line {node.lineno}), which is tied to "
                                 f"Garuda_web. Wrap it in a helper that stays there.")
            if isinstance(node, ast.Global):
                text = lines[node.lineno - 1].strip()
                if ";" in text or not text.startswith("global ") or node.end_lineno != node.lineno:
                    raise SystemExit(f"line {node.lineno} mixes `global` with other code: {text!r}")
                drop.add(node.lineno - 1 - start)
            if not isinstance(node, ast.Name):
                continue
            if (node.lineno, node.col_offset) in frozen_pos:
                if node.id in OWN:
                    used_own.add(node.id)
                continue
            if node.id in OWN and node.id not in local:
                used_own.add(node.id)
                continue
            if node.id in local or node.id not in gw_names:
                continue
            edits.append((node.lineno - 1 - start, node.col_offset, node.id))
        for line_no, col, ident in sorted(set(edits), key=lambda e: (e[0], -e[1])):
            raw = seg[line_no].encode("utf-8")
            old = ident.encode()
            if raw[col:col + len(old)] != old:
                raise SystemExit(f"{name}(): cannot place `core.` before {ident} on line "
                                 f"{start + line_no + 1} (inside an f-string?). Assign it to a "
                                 f"local first.\n    {seg[line_no]}")
            seg[line_no] = (raw[:col] + b"core." + raw[col:]).decode("utf-8")
        pieces.append("\n".join(l for i, l in enumerate(seg) if i not in drop))
        ranges.append((start, end))

    plain = sorted({OWN[k] for k in used_own if OWN[k].startswith("import ")})
    grouped = {}
    for k in used_own:
        if OWN[k].startswith("from "):
            grouped.setdefault(OWN[k].split(" import ")[0], []).append(k)
    froms = [f"{mod} import {', '.join(sorted(v))}" for mod, v in sorted(grouped.items())]
    module = (f'"""{args.doc}\n\nMoved out of Garuda_web.py (2026-10). Function bodies are unchanged except\n'
              f'that names belonging to Garuda_web are read, and assigned, through `core`: the\n'
              f'live module, handed over once by bind(). Garuda_web imports these functions\n'
              f'back under the same names.\n"""\n'
              + "\n".join(plain) + ("\n" if plain else "")
              + ("\n" + "\n".join(froms) + "\n" if froms else "")
              + "\ncore = None\n\n\ndef bind(module):\n"
                '    """Called by Garuda_web with itself, before anything here runs."""\n'
                "    global core\n"
                "    if core is not None and core is not module:\n"
                "        # A second copy of Garuda_web (imported under another name) would\n"
                "        # silently take over the state every function here reads.\n"
                '        raise RuntimeError(f"{__name__} is already bound to {core.__name__}; "\n'
                '                           f"refusing a second copy, {module.__name__}")\n'
                "    core = module\n\n\n"
              + "\n\n\n".join(pieces) + "\n")

    left = [n for n in unresolved(module) if n not in ("core", "bind")]
    if left:
        raise SystemExit(f"unresolved names in the new module: {left}")
    compile(module, f"{args.area}.py", "exec")

    for start, end in sorted(ranges, reverse=True):
        del lines[start:end]
        while start < len(lines) and start > 0 and lines[start] == "" and lines[start - 1] == "":
            del lines[start]
    new_source = "\n".join(lines)
    names = ", ".join(wanted)
    for prefix in (".", "basic_pipelines."):
        anchor = f"    from {prefix}garuda_core import API_VERSION, BUILD\n"
        if new_source.count(anchor) != 1:
            raise SystemExit("import anchor not found in Garuda_web.py")
        add = (f"    from {prefix}garuda_services import {args.area} as _svc_{args.area}\n"
               f"    from {prefix}garuda_services.{args.area} import (  # noqa: F401\n        {names})\n")
        new_source = new_source.replace(anchor, add + anchor, 1)
    bind_anchor = "# garuda_services modules read this module's state through `core`.\n"
    if new_source.count(bind_anchor) != 1:
        raise SystemExit("bind anchor not found in Garuda_web.py (add it once, after the imports)")
    new_source = new_source.replace(bind_anchor, bind_anchor + f"_svc_{args.area}.bind(sys.modules[__name__])\n", 1)
    compile(new_source, "Garuda_web.py", "exec")
    # Nothing left in Garuda_web may *define* a moved name again (it would
    # shadow the import), and the import must come before every use, which it
    # does: the import block is at the top.
    redefined = [n.name for n in ast.parse(new_source).body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in wanted]
    if redefined:
        raise SystemExit(f"still defined in Garuda_web.py: {redefined}")
    _ = free_names

    print(f"functions moved : {names}")
    print(f"core.* rewrites : {module.count('core.')}")
    if args.dry_run:
        print(module)
        return
    SERVICES.mkdir(exist_ok=True)
    init = SERVICES / "__init__.py"
    if not init.exists():
        init.write_text('"""What the Garuda service does in the background and to its own state:\n'
                        'presence, monitors, alerts, persistence. One module per concern.\n\n'
                        'Each module is bound to the live Garuda_web module (`bind(core)`) and\n'
                        'reads its state through it at call time; none of them imports Garuda_web.\n"""\n')
    (SERVICES / f"{args.area}.py").write_text(module)
    GW.write_text(new_source)
    print(f"wrote garuda_services/{args.area}.py; Garuda_web.py is now {new_source.count(chr(10)) + 1} lines")


if __name__ == "__main__":
    main()
