"""Garuda's HTTP routes, one module per area.

Each module has a `build_*_router(core)` function. `core` is the live
Garuda_web module, handed over by Garuda_web itself: a handler reads
`core.USERS`, `core._rate_store` and so on at the moment it runs, so it always
sees the current state (and whatever a test has patched in).

These modules never `import Garuda_web`. The service starts that file as a
script, so its live globals belong to `__main__`; importing it by name would
build a second, empty copy.
"""
