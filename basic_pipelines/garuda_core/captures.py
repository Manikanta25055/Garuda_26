"""Captures: stills, sound and video from the camera, kept on the Pi.

The live views (/stream, /api/snapshot, /api/audio/clip) show the room and
forget it. A capture is the same thing written down: a JPEG of the frame, a
WAV of the next N seconds of the microphone, a WebM of the next N seconds of
video (with the sound in it when ffmpeg is installed). A shortcut step makes
one ("when an alert starts, save a picture and the next 10 seconds of sound"),
and so can a person or Narada.

Captures made by one run of a shortcut share a `group` (the run's id), so the
picture and the sound of one alert are shown, and emailed, together.

    index.json      [{id, kind, status, file, mime, bytes, at, seconds, by,
                      label, group, alert, keep, error}]
    policy.json     {days}
    <id>.jpg|.wav|.webm
    <id>.thumb.jpg  made when it is first asked for

With ffmpeg a video is encoded while it is recorded (stream_video) and is
saved the moment it ends; without it the frames are kept and encoded after.

A recording is listed at once with status "recording" and becomes "saved" or
"failed". One still recording when the service stopped is marked failed on
start. The oldest captures go first once there are more than MAX_ITEMS of them
or they take more than MAX_BYTES; a capture marked keep is never removed that
way, and so do captures older than the retention period (`days`, 30 by
default, 0 for no limit). An admin changes it on the Captures page; it is kept
in policy.json beside the index.

The page shows captures as events: one shortcut run's group, or a capture on
its own, newest first (events()), so the newest event is shown in full and the
rest as a history, a page at a time. A small JPEG of a picture or a video
(<id>.thumb.jpg) is made the first time the history asks for it.

The disk is shared with everything else on the Pi, so a capture also keeps
MIN_FREE_BYTES of it free: the oldest go first when it runs short, and a new
one is refused (LowDisk) when clearing cannot make the room. A file in the
folder that the index does not list (one written as the service stopped) is
removed on start; an index that cannot be read is set aside, not overwritten.

Nothing leaves the Pi except by an email a person or a shortcut asked for.
"""
import json
import logging
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
import time

from .storage import _atomic_json_write

log = logging.getLogger(__name__)

KINDS = ("snapshot", "audio", "video")
EXT = {"snapshot": (".jpg", "image/jpeg"), "audio": (".wav", "audio/wav"),
       "video": (".webm", "video/webm")}
MAX_ITEMS = int(os.environ.get("GARUDA_CAPTURES_MAX", "500"))
MAX_BYTES = int(os.environ.get("GARUDA_CAPTURES_MAX_MB", "2048")) * 1024 * 1024
MIN_FREE_BYTES = int(os.environ.get("GARUDA_CAPTURES_MIN_FREE_MB", "500")) * 1024 * 1024
RETAIN_DAYS = int(os.environ.get("GARUDA_CAPTURES_DAYS", "30"))      # 0: no age limit
MAX_RETAIN_DAYS = 3650
EXPIRE_EVERY_S = 300            # how often reading the list also clears what has aged out
THUMB_WIDTH = 320
THUMB_SUFFIX = ".thumb.jpg"
MAX_AUDIO_S = 30
MAX_VIDEO_S = 60
VIDEO_FPS_CAP = 15.0
MAX_LABEL = 80
FINISH_WAIT_S = MAX_VIDEO_S + 30      # how long anything waits for a recording to end
_CAPTURE_EXTS = {ext for ext, _ in EXT.values()}


class LowDisk(OSError):
    """There is not room on the disk for another capture."""


def _free_bytes(directory):
    probe = directory
    while not os.path.exists(probe) and os.path.dirname(probe) != probe:
        probe = os.path.dirname(probe)
    return shutil.disk_usage(probe).free


class CaptureStore:
    def __init__(self, directory, clock=time.time, max_items=MAX_ITEMS, max_bytes=MAX_BYTES,
                 min_free=MIN_FREE_BYTES, free_fn=_free_bytes, days=None):
        self.dir = str(directory)
        self._index_path = os.path.join(self.dir, "index.json")
        self._policy_path = os.path.join(self.dir, "policy.json")
        self._clock = clock
        self._lock = threading.RLock()
        self._done = threading.Condition(self._lock)
        self.max_items = max_items
        self.max_bytes = max_bytes
        self.min_free = min_free
        self._free_fn = free_fn
        self.days = _load_days(self._policy_path, RETAIN_DAYS if days is None else days)
        self._expired_at = 0.0
        self._no_thumb = set()          # ids a small picture could not be made of
        self.items, readable = _load_index(self._index_path)
        _clear_leftovers(self.dir, self.items if readable else None)
        stale = [c for c in self.items if c.get("status") == "recording"]
        for entry in stale:
            entry.update(status="failed", error="the service stopped while it was recording")
        if stale:
            self._save()
        with self._lock:
            self._prune()               # what aged out while the service was off

    # ── keeping the index ────────────────────────────────────────────────────
    def _save(self):
        _atomic_json_write(self._index_path, self.items)

    def path_of(self, entry):
        return os.path.join(self.dir, entry["file"]) if entry.get("file") else None

    def begin(self, kind, *, by, label="", group="", alert="", seconds=None):
        """List a capture that is about to be made; returns its entry."""
        if kind not in KINDS:
            raise ValueError(f"kind is one of {', '.join(KINDS)}")
        with self._lock:
            if self._free() < self.min_free:
                self._prune()
                if self._free() < self.min_free:
                    raise LowDisk("the disk is nearly full; clear some captures or other files")
        capture_id = secrets.token_hex(6)
        ext, mime = EXT[kind]
        entry = {"id": capture_id, "kind": kind, "status": "recording",
                 "file": capture_id + ext, "mime": mime, "bytes": 0, "at": self._clock(),
                 "seconds": seconds, "by": by or "", "label": str(label or "")[:MAX_LABEL],
                 "group": str(group or "")[:40], "alert": str(alert or "")[:200],
                 "keep": False, "error": ""}
        os.makedirs(self.dir, exist_ok=True)
        with self._lock:
            self.items.insert(0, entry)
            self._save()
        return dict(entry)

    def finish(self, capture_id, data=None, *, mime=None, file=None):
        """The capture is on disk: `data` is written for it, or it is already
        at its path. Returns the entry, or None if it was deleted meanwhile."""
        with self._lock:
            entry = self._find(capture_id)
            if entry is None:
                return None
            if file:
                entry["file"] = file
            if mime:
                entry["mime"] = mime
            path = self.path_of(entry)
        if data is not None:
            _write_atomic(path, data)
        size = os.path.getsize(path) if os.path.exists(path) else 0
        with self._lock:
            entry = self._find(capture_id)
            if entry is None:            # deleted while it was being written
                _unlink(path)
                return None
            entry.update(status="saved" if size else "failed", bytes=size,
                         error="" if size else "nothing was recorded")
            self._prune()
            self._save()
            self._done.notify_all()
            return dict(entry)

    def fail(self, capture_id, why):
        with self._lock:
            entry = self._find(capture_id)
            if entry is None:
                return None
            self._remove_files(entry)
            entry.update(status="failed", error=str(why)[:200], bytes=0)
            self._save()
            self._done.notify_all()
            return dict(entry)

    def _free(self):
        try:
            return self._free_fn(self.dir)
        except OSError:
            return self.min_free         # cannot tell: do not block captures on it

    def _find(self, capture_id):
        return next((c for c in self.items if c["id"] == capture_id), None)

    def thumb_path(self, entry):
        return os.path.join(self.dir, entry["id"] + THUMB_SUFFIX)

    def _remove_files(self, entry):
        _unlink(self.path_of(entry))
        _unlink(self.thumb_path(entry))

    def _prune(self):
        # Called with the lock held. Oldest first; kept and recording ones stay.
        # Past the retention period one goes whatever the room; otherwise only
        # while there are too many, they take too much, or the disk runs short.
        def removable():
            return [c for c in reversed(self.items)
                    if not c.get("keep") and c.get("status") != "recording"]
        total = sum(c.get("bytes") or 0 for c in self.items)
        short = self.min_free - self._free()
        cutoff = self._clock() - self.days * 86400 if self.days else None
        removed = False
        self._expired_at = self._clock()
        for entry in removable():
            aged = cutoff is not None and (entry.get("at") or 0) < cutoff
            if (not aged and len(self.items) <= self.max_items and total <= self.max_bytes
                    and short <= 0):
                break
            self._remove_files(entry)
            total -= entry.get("bytes") or 0
            short -= entry.get("bytes") or 0
            self.items.remove(entry)
            removed = True
        if removed:
            self._save()

    # ── reading ──────────────────────────────────────────────────────────────
    def get(self, capture_id):
        with self._lock:
            entry = self._find(capture_id)
            return dict(entry) if entry else None

    def list(self, *, kind=None, group=None, since=None, label=None, limit=50):
        with self._lock:
            out = [dict(c) for c in self.items
                   if (not kind or c["kind"] == kind) and (not group or c.get("group") == group)
                   and (since is None or c["at"] >= since)
                   and (not label or label.lower() in (c.get("label") or "").lower())]
        return out[:max(1, min(int(limit or 50), 500))]

    def events(self, *, kind=None, before=None, limit=10):
        """Captures as events, newest first: the captures of one shortcut run
        (its group) together, any other capture on its own. Each event is
        {key, at, last, by, label, alert, keep, kinds, items}; `at` is when it
        started, `last` when its newest capture was made, the order and the
        cursor: pass the last event's `last` as `before` for the page after.

        Returns {events, total, more, next_before}."""
        limit = max(1, min(int(limit or 10), 100))
        with self._lock:
            if self._clock() - self._expired_at > EXPIRE_EVERY_S:
                self._prune()
            items = [dict(c) for c in self.items]
        groups, order = {}, []
        for c in items:                             # newest first already
            key = c.get("group") or c["id"]
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(c)
        events = []
        for key in order:
            whole = sorted(groups[key], key=lambda c: c.get("at") or 0)
            # A filter picks the events with that kind and shows only those
            # captures; the event's label and alert are still its own.
            its = [c for c in whole if not kind or c["kind"] == kind]
            if not its:
                continue
            kinds = {}
            for c in its:
                kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
            events.append({
                "key": key, "group": its[0].get("group") or "",
                "at": its[0].get("at") or 0, "last": its[-1].get("at") or 0,
                "by": its[0].get("by") or "",
                "label": next((c["label"] for c in whole if c.get("label")), ""),
                "alert": next((c["alert"] for c in whole if c.get("alert")), ""),
                "keep": any(c.get("keep") for c in its),
                "recording": any(c.get("status") == "recording" for c in its),
                "kinds": kinds, "items": its})
        events.sort(key=lambda e: e["last"], reverse=True)
        total = len(events)
        if before is not None:
            events = [e for e in events if e["last"] < before]
        page = events[:limit]
        more = len(events) > limit
        return {"events": page, "total": total, "more": more,
                "next_before": page[-1]["last"] if more and page else None}

    def usage(self):
        with self._lock:
            return {"count": len(self.items),
                    "bytes": sum(c.get("bytes") or 0 for c in self.items),
                    "kept": sum(1 for c in self.items if c.get("keep")),
                    "max_items": self.max_items, "max_bytes": self.max_bytes,
                    "days": self.days}

    def wait_for(self, ids, timeout=FINISH_WAIT_S):
        """Block until none of `ids` is still recording, or the time is up.
        Returns their entries (those that still exist)."""
        deadline = time.monotonic() + timeout
        with self._lock:
            while True:
                entries = [c for c in (self._find(i) for i in ids) if c]
                left = deadline - time.monotonic()
                if all(c["status"] != "recording" for c in entries) or left <= 0:
                    return [dict(c) for c in entries]
                self._done.wait(min(left, 1.0))

    # ── changing ─────────────────────────────────────────────────────────────
    def set_keep(self, capture_id, keep):
        with self._lock:
            entry = self._find(capture_id)
            if entry is None:
                return None
            entry["keep"] = bool(keep)
            self._save()
            return dict(entry)

    def delete(self, capture_id):
        with self._lock:
            entry = self._find(capture_id)
            if entry is None:
                return False
            self.items.remove(entry)
            self._save()
            self._done.notify_all()
        self._remove_files(entry)
        return True

    def clear(self, before=None):
        """Delete every capture not marked keep and not recording, made before
        `before` (epoch seconds; all of them without it). Returns how many."""
        with self._lock:
            gone = [c for c in self.items
                    if not c.get("keep") and c.get("status") != "recording"
                    and (before is None or (c.get("at") or 0) < before)]
            if not gone:
                return 0
            ids = {c["id"] for c in gone}
            self.items = [c for c in self.items if c["id"] not in ids]
            self._save()
            self._done.notify_all()
        for entry in gone:
            self._remove_files(entry)
        return len(gone)

    def set_days(self, days):
        """Keep captures for `days` (0: no age limit); older ones go now."""
        days = int(days)
        if not 0 <= days <= MAX_RETAIN_DAYS:
            raise ValueError(f"days is 0 to {MAX_RETAIN_DAYS}")
        with self._lock:
            self.days = days
            os.makedirs(self.dir, exist_ok=True)
            _atomic_json_write(self._policy_path, {"days": days})
            self._prune()
        return days

    # ── small pictures for the history ───────────────────────────────────────
    def thumb(self, capture_id):
        """The path of a small JPEG of a saved picture or video, made the first
        time it is asked for; None for sound, or when one cannot be made."""
        entry = self.get(capture_id)
        if (entry is None or entry["status"] != "saved" or entry["kind"] == "audio"
                or capture_id in self._no_thumb):
            return None
        path = self.thumb_path(entry)
        if os.path.exists(path):
            return path
        data = make_thumb(self.path_of(entry), entry["kind"])
        if not data:
            self._no_thumb.add(capture_id)
            return None
        _write_atomic(path, data)
        with self._lock:
            if self._find(capture_id) is None:      # deleted meanwhile
                _unlink(path)
                return None
        return path


def _load_index(path):
    """(entries, readable). An index that is there but cannot be read is moved
    aside to index.json.bad-<time>, so the next save does not overwrite it."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return [], False
    except (OSError, ValueError) as exc:
        log.error("captures: the index could not be read (%s); it is kept aside", exc)
        try:
            os.replace(path, f"{path}.bad-{int(time.time())}")
        except OSError:
            pass
        return [], False
    if not isinstance(data, list):
        return [], False
    return [c for c in data if isinstance(c, dict) and c.get("id")], True


def _load_days(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            days = int(json.load(fh).get("days"))
    except (OSError, ValueError, TypeError, AttributeError):
        return max(0, int(default))
    return days if 0 <= days <= MAX_RETAIN_DAYS else max(0, int(default))


def make_thumb(path, kind, width=THUMB_WIDTH):
    """A small JPEG of a picture, or of a video's first frame; None when it
    cannot be read. Decoding a video uses OpenCV, else the ffmpeg command."""
    import cv2
    import numpy as np
    frame = None
    try:
        if kind == "snapshot":
            frame = cv2.imread(path, cv2.IMREAD_COLOR)
        elif kind == "video":
            cap = cv2.VideoCapture(path)
            try:
                ok, frame = cap.read()
            finally:
                cap.release()
            if not ok:
                frame = None
            tool = ffmpeg()
            if frame is None and tool:
                out = subprocess.run(
                    [tool, "-nostdin", "-loglevel", "error", "-i", path, "-frames:v", "1",
                     "-f", "image2pipe", "-c:v", "mjpeg", "-"],
                    capture_output=True, timeout=20, check=False).stdout
                if out:
                    frame = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)
    except (cv2.error, OSError, subprocess.SubprocessError) as exc:
        log.warning("captures: no small picture of %s: %s", os.path.basename(path), exc)
        return None
    if frame is None:
        return None
    h, w = frame.shape[:2]
    if w > width:
        frame = cv2.resize(frame, (width, max(1, round(h * width / w))),
                           interpolation=cv2.INTER_AREA)
    ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 72])
    return jpeg.tobytes() if ok else None


def _write_atomic(path, data):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        _unlink(tmp)
        raise


def _unlink(path):
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


def _clear_leftovers(directory, listed=None):
    """What a recording cut short by a stop leaves: its work folder and any
    half-written file. Nothing is recording yet when the store starts. With
    `listed` (a readable index), a capture file it does not name goes too."""
    try:
        names = os.listdir(directory)
    except OSError:
        return
    known = {c.get("file") for c in listed or ()}
    known |= {c.get("id", "") + THUMB_SUFFIX for c in listed or ()}
    for name in names:
        path = os.path.join(directory, name)
        if name.startswith("capture-") and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif name.endswith(".part"):
            _unlink(path)
        elif (listed is not None and os.path.splitext(name)[1] in _CAPTURE_EXTS
              and name not in known):
            _unlink(path)


# ── video ─────────────────────────────────────────────────────────────────────

def record_frames(frame_fn, seconds, *, clock=time.monotonic, sleep=time.sleep,
                  fps_cap=VIDEO_FPS_CAP, cancel=None):
    """The camera's frames over the next `seconds`, as JPEGs, and the real
    frame rate. frame_fn() -> (seq, jpeg bytes) of the newest frame, or None.

    The pipeline has already encoded every published frame for the stream, so
    keeping its JPEG costs nothing here; the frames are turned into video once
    the recording is over, off the camera's path."""
    frames, last_seq = [], None
    start = clock()
    end = start + seconds
    step = 1.0 / fps_cap
    next_at = start
    while clock() < end:
        if cancel is not None and cancel.is_set():
            break
        now = clock()
        if now >= next_at:
            got = frame_fn()
            if got is not None and got[0] != last_seq and got[1]:
                last_seq = got[0]
                frames.append(got[1])
                next_at = now + step * 0.9
        sleep(0.01)
    took = max(clock() - start, 0.001)
    return frames, (len(frames) / took if frames else 0.0)


def encode_video(jpegs, fps, out_path):
    """JPEG frames to a WebM (VP8) at out_path; True when it worked. Plays in
    every browser, unlike OpenCV's MPEG-4 Part 2. Only used without ffmpeg
    (stream_video): it takes about 1.5 s of a core per second of video."""
    import cv2
    import numpy as np
    first = None
    for data in jpegs:
        first = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if first is not None:
            break
    if first is None:
        return False
    h, w = first.shape[:2]
    fps = max(1.0, min(float(fps or VIDEO_FPS_CAP), 30.0))
    writer = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"VP80"), fps, (w, h))
    if not writer.isOpened():
        writer.release()
        return False
    try:
        for data in jpegs:
            frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                continue
            if frame.shape[:2] != (h, w):
                frame = cv2.resize(frame, (w, h))
            writer.write(frame)
    finally:
        writer.release()
    return os.path.exists(out_path) and os.path.getsize(out_path) > 0


def ffmpeg():
    return shutil.which("ffmpeg")


def stream_command(tool, size, out_path, fps=VIDEO_FPS_CAP):
    """ffmpeg reading JPEGs on stdin at a steady `fps` and writing a WebM (VP8).

    VP8's realtime mode at its fastest setting costs about a quarter of one
    core at 720p and 15 fps, a seventh of what OpenCV's VP8 needs, so a
    recording keeps up with the camera and is saved the moment it ends. It runs
    niced, below the detection pipeline."""
    w, h = (max(2, v - v % 2) for v in size)
    cmd = [tool, "-nostdin", "-loglevel", "error", "-y",
           "-f", "image2pipe", "-framerate", f"{fps:g}", "-c:v", "mjpeg", "-i", "-",
           "-vf", f"scale={w}:{h},format=yuv420p", "-an",
           "-c:v", "libvpx", "-deadline", "realtime", "-cpu-used", "16", "-b:v", "1500k",
           "-lag-in-frames", "0", "-threads", "1", "-f", "webm", out_path]
    nice = shutil.which("nice")
    return [nice, "-n", "10", *cmd] if nice else cmd


def stream_video(frame_fn, seconds, out_path, *, tool=None, fps=VIDEO_FPS_CAP,
                 clock=time.monotonic, sleep=time.sleep, cancel=None):
    """Record the camera's next `seconds` straight into a WebM at out_path,
    encoding as it goes. Returns how many distinct frames went in (0: none came,
    or ffmpeg failed). frame_fn() -> (seq, jpeg bytes) of the newest frame, or None.

    Frames go in at a steady `fps`, the newest one repeated when the camera has
    not sent a new one, so the video lasts exactly as long as the recording."""
    import cv2
    import numpy as np
    tool = tool or ffmpeg()
    if not tool:
        return 0
    total = max(1, int(round(seconds * fps)))
    proc, written, distinct, last, last_seq = None, 0, 0, None, None
    start = clock()
    deadline = start + seconds + 5          # the camera stopped: stop waiting for it
    try:
        while written < total and clock() < deadline:
            if cancel is not None and cancel.is_set():
                break
            got = frame_fn()
            if got is not None and got[1] and got[0] != last_seq:
                last_seq, last = got[0], got[1]
                distinct += 1
            if last is not None and proc is None:
                first = cv2.imdecode(np.frombuffer(last, np.uint8), cv2.IMREAD_COLOR)
                if first is None:
                    last, distinct = None, distinct - 1
                else:
                    proc = subprocess.Popen(stream_command(tool, first.shape[1::-1], out_path, fps),
                                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL)
            due = min(total, int((clock() - start) * fps) + 1)
            while proc is not None and written < due:
                proc.stdin.write(last)
                written += 1
            sleep(max(0.0, min(start + written / fps - clock(), 1.0 / fps)))
        if proc is None:
            return 0
        proc.stdin.close()
        proc.wait(timeout=30)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        log.warning("captures: the video encoder stopped: %s", exc)
        return 0
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
    if proc.returncode != 0 or not os.path.exists(out_path) or not os.path.getsize(out_path):
        log.warning("captures: the video encoder failed (exit %s)", proc.returncode)
        return 0
    return distinct


def mux_sound(video_path, wav_path, out_path, timeout=120):
    """The video with the sound in it (Opus in the same WebM); True when it worked."""
    tool = ffmpeg()
    if not tool:
        return False
    cmd = [tool, "-nostdin", "-loglevel", "error", "-y", "-i", video_path, "-i", wav_path,
           "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "libopus", "-b:a", "48k",
           "-shortest", out_path]
    try:
        subprocess.run(cmd, check=True, timeout=timeout, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE)
    except Exception as exc:
        log.warning("captures: could not add the sound to a video: %s", exc)
        _unlink(out_path)
        return False
    return os.path.exists(out_path) and os.path.getsize(out_path) > 0
