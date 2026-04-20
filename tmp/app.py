"""Minimal Vercel Python entrypoint for the Garuda web frontend.

This serves the static SPA from basic_pipelines/garuda_web without importing
the hardware-bound FastAPI backend, which is not deployable on Vercel.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Iterable


WEB_ROOT = Path(__file__).resolve().parent / "basic_pipelines" / "garuda_web"
INDEX_HTML = WEB_ROOT / "index.html"


def _read_file(path: Path) -> bytes:
    with path.open("rb") as f:
        return f.read()


def _response(start_response, status: str, body: bytes, content_type: str) -> Iterable[bytes]:
    start_response(
        status,
        [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "public, max-age=300"),
        ],
    )
    return [body]


def app(environ, start_response):
    path = (environ.get("PATH_INFO") or "/").strip()
    rel_path = path.lstrip("/")

    if rel_path.startswith("static/"):
        target = WEB_ROOT / rel_path[len("static/") :]
        if target.is_file():
            content_type, _ = mimetypes.guess_type(str(target))
            return _response(
                start_response,
                "200 OK",
                _read_file(target),
                content_type or "application/octet-stream",
            )

    if rel_path in ("", "/"):
        return _response(start_response, "200 OK", _read_file(INDEX_HTML), "text/html; charset=utf-8")

    if rel_path.startswith("api/"):
        body = b'{"error":"Garuda backend is not deployed on Vercel."}'
        return _response(start_response, "404 Not Found", body, "application/json; charset=utf-8")

    return _response(start_response, "200 OK", _read_file(INDEX_HTML), "text/html; charset=utf-8")
