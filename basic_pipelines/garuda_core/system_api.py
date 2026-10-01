"""Health, readiness, version and diagnostics.

  GET /api/health            is the process answering?  (public, cheap)
  GET /api/ready             is it doing its job?        (public; 503 when not)
  GET /api/meta              what this server is and can do, for clients
  GET /api/system/info       everything an admin needs to see what is wrong
  GET/POST /api/system/backups   list state backups / take one now
  GET /api/openapi.json      the API reference (admin; /docs is not public)

Liveness and readiness are different questions. A monitor that only asks
"does port 8080 answer" stays green while the camera has been dead for an
hour; /api/ready is the one to watch.
"""
import platform
import sys
import time

import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from . import API_VERSION, BUILD


def build_system_router(*, settings, supervisor, backups, admin_dep, probes, meta_fn,
                        openapi_fn, started_at, extra_info=None):
    """`probes` is {name: (callable -> (ok, detail), critical)}."""
    router = APIRouter(tags=["System"])

    def _run_probes():
        out = {}
        for name, (fn, critical) in probes.items():
            try:
                ok, detail = fn()
            except Exception as exc:              # a broken probe is a failed check
                ok, detail = False, f"{type(exc).__name__}: {exc}"
            out[name] = {"ok": bool(ok), "detail": detail, "critical": critical}
        return out

    @router.get("/api/health")
    async def health():
        return {"status": "ok", "uptime_s": int(time.time() - started_at()),
                "api_version": API_VERSION, "commit": BUILD["commit"]}

    @router.get("/api/ready")
    async def ready():
        checks = await anyio.to_thread.run_sync(_run_probes)
        is_ready = all(c["ok"] for c in checks.values() if c["critical"])
        # Public: which part is unwell, not the detail of why.
        body = {"ready": is_ready, "checks": {n: c["ok"] for n, c in checks.items()}}
        return JSONResponse(body, status_code=200 if is_ready else 503)

    @router.get("/api/meta")
    async def meta(request: Request):
        return {"api_version": API_VERSION, "commit": BUILD["commit"], **meta_fn(request)}

    @router.get("/api/system/info")
    async def system_info(session=Depends(admin_dep)):
        checks = await anyio.to_thread.run_sync(_run_probes)
        info = {
            "build": BUILD,
            "api_version": API_VERSION,
            "uptime_s": int(time.time() - started_at()),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "ready": all(c["ok"] for c in checks.values() if c["critical"]),
            "checks": checks,
            "workers": supervisor.status(),
            "settings": settings.public(),
            "problems": [{"severity": s, "message": m} for s, m in settings.problems()],
            "backups": {"count": len(backups.list()), "latest": (backups.list()[:1] or [None])[0],
                        "keep": backups.keep, "last_error": backups.last_error},
        }
        if extra_info is not None:
            info.update(extra_info())
        return info

    @router.get("/api/system/backups")
    async def list_backups(session=Depends(admin_dep)):
        return {"backups": backups.list(), "keep": backups.keep}

    @router.post("/api/system/backups")
    async def create_backup(session=Depends(admin_dep)):
        try:
            made = await anyio.to_thread.run_sync(backups.create)
        except OSError as exc:
            raise HTTPException(500, f"Backup failed: {type(exc).__name__}")
        if made is None:
            raise HTTPException(404, "There is no state to back up yet.")
        return {"ok": True, "backup": made}

    @router.get("/api/openapi.json", include_in_schema=False)
    async def openapi(session=Depends(admin_dep)):
        return JSONResponse(openapi_fn())

    return router
