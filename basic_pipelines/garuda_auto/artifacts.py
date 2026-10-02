"""Artifacts: small pages Narada writes to answer with something you can see.

When a table, a chart or a little tool says it better than a paragraph, the
planner writes one complete HTML document and the chat shows it in a frame.
Nothing here is a template: every artifact is written for the request.

The page is the model's own writing, so it is treated as untrusted. It is
served from its own address with a policy that gives it no network and no
origin (it cannot read the site's cookies or call its API), inside a sandboxed
frame. What it may do, it asks the chat page for, by message; the chat page
decides (garuda_routes/artifacts.py).
"""
import os
import re
import secrets
import threading
import time

from . import jsonfile

MAX_HTML = 200_000
MAX_KEPT = 80
_ID = re.compile(r"^[0-9a-f]{12}$")
# A script, stylesheet, image, frame or font fetched from another place.
_OUTSIDE = re.compile(
    r"""<(?:script|link|img|iframe|source|video|audio|embed|object)\b[^>]*?\b(?:src|href|data)\s*=\s*["']?\s*(?:https?:)?//[^\s"'>]+"""
    r"""|@import\s+(?:url\()?\s*["']?(?:https?:)?//[^\s"')]+"""
    r"""|url\(\s*["']?(?:https?:)?//[^\s"')]+""", re.I)


class ArtifactStore:
    def __init__(self, directory=None, clock=time.time):
        self._dir = directory
        self._clock = clock
        self._lock = threading.Lock()
        self._memory = {}                       # used when there is no directory
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.index = jsonfile.load(self._index_path, []) if directory else []

    @property
    def _index_path(self):
        return os.path.join(self._dir, "index.json")

    def _html_path(self, artifact_id):
        return os.path.join(self._dir, f"{artifact_id}.html")

    def add(self, title, html, by):
        """Keep one page. Returns its entry, or (None, reason)."""
        title = str(title or "").strip()[:80] or "Untitled"
        if not isinstance(html, str) or not html.strip():
            return None, "html is needed: one complete HTML document"
        if len(html) > MAX_HTML:
            return None, f"the page is too large (over {MAX_HTML // 1000} KB)"
        outside = _OUTSIDE.search(html)
        if outside:
            # It would be blocked in the frame anyway, leaving a broken page;
            # said here, the model writes it again without.
            return None, ("the page loads something from outside "
                          f"({outside.group(0)[:60]!r}), which is not allowed: use inline "
                          "<script> and <style> only, and draw charts yourself")
        entry = {"id": secrets.token_hex(6), "key": secrets.token_urlsafe(24), "title": title,
                 "by": by, "created": self._clock(), "size": len(html), "pinned": False}
        with self._lock:
            self._write(entry["id"], html)
            self.index.insert(0, entry)
            # The oldest unpinned pages make room.
            while len(self.index) > MAX_KEPT:
                victim = next((e for e in reversed(self.index) if not e.get("pinned")), None)
                if victim is None:
                    break
                self._drop(victim)
            self._save()
        return dict(entry), ""

    def get(self, artifact_id):
        with self._lock:
            return next((dict(e) for e in self.index if e["id"] == artifact_id), None)

    def html(self, artifact_id):
        if not _ID.match(artifact_id or ""):
            return None
        if not self._dir:
            return self._memory.get(artifact_id)
        try:
            with open(self._html_path(artifact_id), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None

    def all(self):
        with self._lock:
            return [dict(e) for e in self.index]

    def pin(self, artifact_id, pinned):
        with self._lock:
            for entry in self.index:
                if entry["id"] == artifact_id:
                    entry["pinned"] = bool(pinned)
                    self._save()
                    return dict(entry)
        return None

    def delete(self, artifact_id):
        with self._lock:
            entry = next((e for e in self.index if e["id"] == artifact_id), None)
            if entry is None:
                return False
            self._drop(entry)
            self._save()
            return True

    def _write(self, artifact_id, html):
        if not self._dir:
            self._memory[artifact_id] = html
            return
        with open(self._html_path(artifact_id), "w", encoding="utf-8") as f:
            f.write(html)

    def _drop(self, entry):
        self.index.remove(entry)
        self._memory.pop(entry["id"], None)
        if self._dir:
            try:
                os.remove(self._html_path(entry["id"]))
            except OSError:
                pass

    def _save(self):
        if self._dir:
            jsonfile.save(self._index_path, self.index)
