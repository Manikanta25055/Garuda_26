"""Gzip for the page and its text files.

The page, its style sheet and its scripts are about 650 KB as they are on
disk (style.css alone is 230 KB) and were sent that way. Over the Cloudflare
tunnel the edge compressed them, but a phone or laptop on the home network or
on Tailscale downloaded every byte. Gzipped they are about a quarter of that.

Only these responses are compressed: whole files from /static/ whose type is
text, and the page itself. Camera streams, server-sent events and WebSockets
never pass through here, so nothing that must arrive frame by frame is held
back to be compressed.
"""
import gzip
import os
import threading

from starlette.datastructures import Headers
from starlette.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles

_TEXT_SUFFIXES = (".css", ".js", ".mjs", ".svg", ".json", ".html", ".txt", ".webmanifest")
_MIN_BYTES = 1024          # below this the header costs more than it saves
_LEVEL = 6                 # zlib's default: most of the gain for little CPU on a Pi
_CACHE_MAX_BYTES = 8 << 20

_cache = {}                # (path, mtime, size) -> gzipped bytes
_cache_bytes = 0
_lock = threading.Lock()


def accepts_gzip(headers) -> bool:
    for part in (headers.get("accept-encoding") or "").split(","):
        name, _, q = part.strip().partition(";")
        if name.strip().lower() in ("gzip", "*"):
            q = q.strip()
            return not (q.startswith("q=") and q[2:].strip() in ("0", "0.0", "0.00", "0.000"))
    return False


def _cached(key, read):
    global _cache_bytes
    with _lock:
        hit = _cache.get(key)
    if hit is not None:
        return hit
    data = gzip.compress(read(), compresslevel=_LEVEL, mtime=0)
    with _lock:
        if _cache_bytes + len(data) > _CACHE_MAX_BYTES:
            _cache.clear()
            _cache_bytes = 0
        if key not in _cache:
            _cache[key] = data
            _cache_bytes += len(data)
    return data


def _vary(headers):
    v = headers.get("vary")
    if not v:
        return "Accept-Encoding"
    return v if "accept-encoding" in v.lower() else v + ", Accept-Encoding"


def gzip_body(request_headers, body: bytes, media_type: str, headers=None) -> Response:
    """A response for `body`, gzipped when the browser accepts it."""
    headers = dict(headers or {})
    headers["Vary"] = _vary(headers)
    if len(body) < _MIN_BYTES or not accepts_gzip(request_headers):
        return Response(body, media_type=media_type, headers=headers)
    data = _cached(("body", hash(body), len(body)), lambda: body)
    headers["Content-Encoding"] = "gzip"
    return Response(data, media_type=media_type, headers=headers)


class CompressedStaticFiles(StaticFiles):
    """StaticFiles that sends text files gzipped (cached by path and mtime).

    Validation is unchanged: the ETag and Last-Modified are the file's, so a
    browser's revalidation still gets a 304 with no body.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if not isinstance(response, FileResponse) or response.status_code != 200:
            return response
        req = Headers(scope=scope)
        file_path = str(response.path)
        if (not file_path.endswith(_TEXT_SUFFIXES) or "range" in req
                or scope.get("method") == "HEAD"):
            return response
        stat = getattr(response, "stat_result", None)
        if stat is None:
            try:
                stat = os.stat(file_path)
            except OSError:
                return response
        headers = {k: v for k, v in response.headers.items()
                   if k.lower() not in ("content-length", "content-type", "accept-ranges")}
        headers["vary"] = _vary(response.headers)
        if stat.st_size < _MIN_BYTES or not accepts_gzip(req):
            response.headers["vary"] = headers["vary"]
            return response

        def read():
            with open(file_path, "rb") as f:
                return f.read()

        data = _cached((file_path, stat.st_mtime_ns, stat.st_size), read)
        headers["content-encoding"] = "gzip"
        return Response(data, status_code=200, headers=headers, media_type=response.media_type)
