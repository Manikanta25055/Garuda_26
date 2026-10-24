"""Evidence: the seconds before an alert, and one file to hand over.

Owners say the clip they get starts after the thing they needed to see. The
camera's frames are already JPEGs (the stream encodes them), so a small ring
keeps the last few seconds of them. When an alert starts, the ring and the
next seconds are saved together as one video capture, labelled as an alert
clip, beside the other captures on Events.

An export is one ZIP for the police or an insurer: every capture of one
event, with a manifest that lists when each was made, why, and its SHA-256,
so a copy can be shown to be unaltered.

    GARUDA_ALERT_CLIP=0       turns the alert clip off
    GARUDA_ALERT_CLIP_BEFORE  seconds kept before an alert (default 5)
    GARUDA_ALERT_CLIP_AFTER   seconds recorded after it (default 10)
"""
import collections
import datetime
import hashlib
import io
import json
import os
import subprocess
import tempfile
import threading
import time
import zipfile
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

RING_FPS = 10.0
COOLDOWN_S = 60.0
MAX_EXPORT_BYTES = 200 * 1024 * 1024


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


class FrameRing:
    """The last `seconds` of camera JPEGs, at most `fps` of them a second."""

    def __init__(self, seconds=5.0, fps=RING_FPS):
        self.seconds, self.step = seconds, 1.0 / fps
        self.frames = collections.deque(maxlen=max(1, int(seconds * fps) + 1))
        self.lock = threading.Lock()
        self._last_seq, self._last_t = None, 0.0

    def offer(self, seq, jpeg, now):
        """Keep this frame if it is new and its turn has come."""
        if not jpeg or seq == self._last_seq or now - self._last_t < self.step * 0.9:
            return False
        with self.lock:
            self.frames.append((now, jpeg))
        self._last_seq, self._last_t = seq, now
        return True

    def recent(self, now):
        with self.lock:
            return [j for t, j in self.frames if now - t <= self.seconds + self.step]


def _newest(core):
    with core.STATE.camera.frame_lock:
        return core.STATE.camera.frame_seq, core.STATE.camera.frame_buffer


def encode(captures, jpegs, fps, out_path):
    """The JPEGs as one WebM: through ffmpeg when the Pi has it, else OpenCV."""
    tool = captures.ffmpeg()
    if tool:
        import cv2
        import numpy as np
        first = cv2.imdecode(np.frombuffer(jpegs[0], np.uint8), cv2.IMREAD_COLOR)
        if first is not None:
            try:
                subprocess.run(captures.stream_command(tool, first.shape[1::-1], out_path, fps),
                               input=b"".join(jpegs), stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=120, check=True)
                if os.path.exists(out_path) and os.path.getsize(out_path):
                    return True
            except (OSError, subprocess.SubprocessError):
                pass
    return captures.encode_video(jpegs, fps, out_path)


def save_alert_clip(core, ring, why, after, *, record=None, now=time.time):
    """The ring's frames and the next `after` seconds, saved as one capture."""
    captures = core._captures_mod
    record = record or captures.record_frames
    before = ring.recent(now())
    try:
        entry = core.CAPTURES.begin("video", by="alert", alert=why,
                                    label=f"Alert clip: {int(ring.seconds)} s before, {int(after)} s after",
                                    seconds=round(ring.seconds + after))
    except captures.LowDisk as exc:
        core.log_system_update(f"[EVIDENCE] alert clip not saved: {exc}")
        return None
    try:
        later, _ = record(lambda: _newest(core), after, fps_cap=RING_FPS)
        jpegs = before + later
        if not jpegs:
            core.CAPTURES.fail(entry["id"], "the camera sent no frames")
            return None
        work = tempfile.mkdtemp(prefix="alert-clip-", dir=core.CAPTURES.dir)
        out = os.path.join(work, "clip.webm")
        try:
            if not encode(captures, jpegs, RING_FPS, out):
                core.CAPTURES.fail(entry["id"], "the video could not be encoded")
                return None
            current = core.CAPTURES.get(entry["id"])
            if current is None:
                return None
            os.replace(out, core.CAPTURES.path_of(current))
        finally:
            for name in os.listdir(work):
                try:
                    os.unlink(os.path.join(work, name))
                except OSError:
                    pass
            os.rmdir(work)
        return core.CAPTURES.finish(entry["id"])
    except Exception as exc:
        core.CAPTURES.fail(entry["id"], f"{type(exc).__name__}: {exc}")
        return None


def _alert_reason(alerts):
    if alerts.active:
        return (alerts.danger_trigger_info or "alert").strip().replace("\n", ", ")
    if alerts.night_presence_active:
        return "person seen while armed"
    return ""


def alert_clip_loop(core, ring, every=0.05, sleep=time.sleep, clock=time.time, spawn=None):
    """Feed the ring, and save a clip on the moment an alert starts."""
    if os.environ.get("GARUDA_ALERT_CLIP", "1") == "0":
        return
    after = _env_float("GARUDA_ALERT_CLIP_AFTER", 10.0)
    spawn = spawn or (lambda fn, *a: threading.Thread(target=fn, args=a, daemon=True).start())
    was, last_clip = False, 0.0
    while True:
        now = clock()
        seq, jpeg = _newest(core)
        ring.offer(seq, jpeg, now)
        why = _alert_reason(core.STATE.alerts)
        if why and not was and now - last_clip >= COOLDOWN_S:
            last_clip = now
            spawn(save_alert_clip, core, ring, why, after)
        was = bool(why)
        sleep(every)


# ── export ────────────────────────────────────────────────────────────────────

def _stamp(t):
    return datetime.datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


def export_zip(core, entries):
    """(ZIP bytes, filename) of `entries` with a manifest."""
    buf = io.BytesIO()
    manifest = {"made": _stamp(time.time()), "system": "Garuda", "files": []}
    total = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:     # JPEG and WebM do not shrink
        for e in sorted(entries, key=lambda e: e["at"]):
            path = core.CAPTURES.path_of(e)
            if e.get("status") != "saved" or not path or not os.path.isfile(path):
                continue
            total += os.path.getsize(path)
            if total > MAX_EXPORT_BYTES:
                raise HTTPException(413, "This event is too large to export in one file.")
            digest = hashlib.sha256()
            with open(path, "rb") as fh:
                for block in iter(lambda: fh.read(1 << 20), b""):
                    digest.update(block)
            name = f"{datetime.datetime.fromtimestamp(e['at']).strftime('%Y%m%d_%H%M%S')}_{e['kind']}_{e['id']}{os.path.splitext(path)[1]}"
            z.write(path, name)
            manifest["files"].append({"file": name, "kind": e["kind"], "made": _stamp(e["at"]),
                                      "seconds": e.get("seconds"), "by": e.get("by", ""),
                                      "label": e.get("label", ""), "during_alert": e.get("alert", ""),
                                      "bytes": os.path.getsize(path), "sha256": digest.hexdigest()})
        if not manifest["files"]:
            raise HTTPException(404, "Nothing saved to export for that event.")
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        lines = [f"Garuda evidence export, made {manifest['made']}", ""]
        for f in manifest["files"]:
            lines.append(f"{f['made']}  {f['kind']:<8} {f['file']}")
            lines.append(f"    {f['label'] or 'saved by hand'}"
                         + (f"; during an alert: {f['during_alert']}" if f["during_alert"] else ""))
            lines.append(f"    SHA-256 {f['sha256']}")
        lines += ["", "Check a file with: sha256sum <file> (Linux, macOS: shasum -a 256)."]
        z.writestr("README.txt", "\n".join(lines) + "\n")
    first = manifest["files"][0]["file"][:15]
    return buf.getvalue(), f"garuda_evidence_{first}.zip"


def build_evidence_router(core):
    router = APIRouter()

    @router.get("/api/evidence/export")
    async def export(request: Request, group: str = "", id: str = "", token: Optional[str] = None):
        """Takes ?token= too, so a plain download link works."""
        session = core.get_session(request.cookies.get("garuda_session")
                                   or request.headers.get("X-Garuda-Token") or token)
        if not session:
            raise HTTPException(401, "Not authenticated")
        if group:
            entries = core.CAPTURES.list(group=group, limit=500)
        elif id:
            entries = [e for e in [core.CAPTURES.get(id)] if e]
        else:
            raise HTTPException(400, "Name the event: group or id.")
        if not entries:
            raise HTTPException(404, "No such event.")
        import anyio
        data, name = await anyio.to_thread.run_sync(export_zip, core, entries)
        core.log_system_update(f"[EVIDENCE] {len(entries)} capture(s) exported by {session['username']}")
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"',
                                 "Cache-Control": "no-store"})

    return router
