"""The page itself and the files a browser asks for by fixed name: favicon, apple-touch-icon, web-app manifest, service worker.

Moved out of Garuda_web.py (2026-10). Handler bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`.
"""
from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse


# Each product has its own icon: Garuda's blue "G", Drishti's ink "D"
# (scripts/draw_app_icons.py). The files a browser asks for by fixed name
# are chosen by the address, like the page's title.
BRAND_DIR = {"security": "", "home": "drishti/"}


def build_pages_router(core):
    router = APIRouter()

    def brand_file(request, name):
        product = core._product_for_host(request.headers.get("host") or "")
        p = core._static_dir / (BRAND_DIR[product] + name)
        return p if p.exists() else core._static_dir / name

    @router.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        host = (request.headers.get("host") or "").split(":")[0].lower()
        html_path = core._static_dir / "index.html"
        if html_path.exists():
            product = core._product_for_host(host)
            html = html_path.read_text().replace(
                '<html lang="en" data-theme="light">',
                f'<html lang="en" data-theme="light" data-product="{product}">', 1)
            if product == "home":
                html = html.replace("<title>Garuda</title>", "<title>Drishti</title>", 1)
                for icon in ("favicon.png", "apple-touch-icon.png"):
                    html = html.replace(f'"/static/{icon}?v=', f'"/static/drishti/{icon}?v=', 1)
            # Always revalidated: the page names the versioned scripts, so a stale
            # copy of it pins a browser to an old build.
            return HTMLResponse(html, headers={"Cache-Control": "no-cache"})
        return HTMLResponse("<h1>Garuda Web</h1><p>garuda_web/index.html not found.</p>")

    @router.get("/favicon.ico", include_in_schema=False)
    async def favicon(request: Request):
        p = brand_file(request, "favicon.ico")
        return FileResponse(str(p), media_type="image/x-icon") if p.exists() else Response(status_code=204)

    # Safari asks for these at the site root for Favorites, the Start Page and
    # Top Sites, whatever the page links; a 404 there shows a letter instead.
    @router.get("/apple-touch-icon.png", include_in_schema=False)
    @router.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
    async def apple_touch_icon(request: Request):
        p = brand_file(request, "apple-touch-icon.png")
        return FileResponse(str(p), media_type="image/png") if p.exists() else Response(status_code=404)

    @router.get("/manifest.json")
    async def pwa_manifest(request: Request):
        p = brand_file(request, "manifest.json")
        return FileResponse(str(p), media_type="application/manifest+json") if p.exists() else JSONResponse({})

    @router.get("/sw.js")
    async def service_worker():
        p = core._static_dir / "sw.js"
        return FileResponse(str(p), media_type="application/javascript",
                            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"}) if p.exists() else Response("", media_type="application/javascript")

    return router
