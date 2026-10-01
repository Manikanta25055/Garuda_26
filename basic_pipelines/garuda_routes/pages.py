"""The page itself and the three files a browser asks for by fixed name: favicon, web-app manifest, service worker.

Moved out of Garuda_web.py (2026-10). Handler bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`.
"""
from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse


def build_pages_router(core):
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        host = (request.headers.get("host") or "").split(":")[0].lower()
        if core.DRISHTI_APP_ENABLED and host == core.DRISHTI_HOST:
            drishti_index = core.DRISHTI_DIST / "index.html"
            if drishti_index.is_file():
                return HTMLResponse(drishti_index.read_text())
        html_path = core._static_dir / "index.html"
        if html_path.exists():
            product = core._product_for_host(host)
            html = html_path.read_text().replace(
                '<html lang="en" data-theme="light">',
                f'<html lang="en" data-theme="light" data-product="{product}">', 1)
            if product == "home":
                html = html.replace("<title>Garuda</title>", "<title>Drishti</title>", 1)
            # Always revalidated: the page names the versioned scripts, so a stale
            # copy of it pins a browser to an old build.
            return HTMLResponse(html, headers={"Cache-Control": "no-cache"})
        return HTMLResponse("<h1>Garuda Web</h1><p>garuda_web/index.html not found.</p>")

    @router.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        p = core._static_dir / "favicon.ico"
        return FileResponse(str(p), media_type="image/x-icon") if p.exists() else Response(status_code=204)

    @router.get("/manifest.json")
    async def pwa_manifest():
        p = core._static_dir / "manifest.json"
        return FileResponse(str(p), media_type="application/manifest+json") if p.exists() else JSONResponse({})

    @router.get("/sw.js")
    async def service_worker():
        p = core._static_dir / "sw.js"
        return FileResponse(str(p), media_type="application/javascript",
                            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"}) if p.exists() else Response("", media_type="application/javascript")

    return router
