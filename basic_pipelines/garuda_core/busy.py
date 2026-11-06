"""What the server is busy with: refused requests, a stalled event loop, and
which thread is using the processor.

"Too many requests" kept appearing with nothing on the page saying why, and a
slow site could be a busy automation or a request holding the event loop.
Three records answer that after the fact, in the journal
(`journalctl -u garuda-web | grep busy:`) and in /api/system/info:

  refusals   each 429 from the global limit: whose budget, which path, and the
             paths that used that budget up in the last minute
  stalls     each time the event loop did not turn for over STALL_S, with the
             requests that were in flight at that moment
  threads    every SAMPLE_S, the threads that used the most processor time
             (a shortcut's thread is named shortcut-<id>), logged when one
             holds most of a core for the whole stretch

Nothing here sends anything anywhere; it keeps a few hundred small records in
memory and writes a line to the log when something is wrong.
"""
import asyncio
import collections
import itertools
import logging
import os
import threading
import time

log = logging.getLogger("garuda.busy")

STALL_S = 1.0               # the event loop is "stalled" past this
SAMPLE_S = 30.0             # how often thread CPU is sampled
HOG_SHARE = 0.8             # one thread on this share of a core for a whole sample is logged
LOG_EVERY_S = 30.0          # at most one log line per budget per this many seconds


class Busy:
    def __init__(self, clock=time.time, window_s=60.0):
        self._clock = clock
        self.window_s = window_s
        self._lock = threading.Lock()
        self._recent = collections.deque(maxlen=5000)       # (t, budget, path)
        self.refusals = collections.deque(maxlen=100)
        self.stalls = collections.deque(maxlen=50)
        self.threads = []                                   # last sample, busiest first
        self.hogs = collections.deque(maxlen=50)
        self._inflight = {}                                 # n -> (t0, method, path)
        self._ids = itertools.count()
        self._last_logged = {}

    # ── requests ──────────────────────────────────────────────────────────────

    def counted(self, budget, path):
        with self._lock:
            self._recent.append((self._clock(), budget, path))

    def top_paths(self, budget, n=6):
        since = self._clock() - self.window_s
        with self._lock:
            paths = collections.Counter(_shape(p) for t, b, p in self._recent if b == budget and t >= since)
        return paths.most_common(n)

    def refused(self, budget, path, limit):
        now = self._clock()
        top = self.top_paths(budget)
        entry = {"at": now, "budget": budget, "path": path, "limit": limit,
                 "used_by": [{"path": p, "count": c} for p, c in top]}
        with self._lock:
            self.refusals.append(entry)
            quiet = now - self._last_logged.get(budget, 0) < LOG_EVERY_S
            if not quiet:
                self._last_logged[budget] = now
        if not quiet:
            log.warning("busy: 429 for %s on %s (limit %s a minute); used by %s",
                        budget, path, limit, ", ".join(f"{p} x{c}" for p, c in top) or "nothing recorded")
        return entry

    def enter(self, method, path):
        n = next(self._ids)
        with self._lock:
            self._inflight[n] = (self._clock(), method, path)
        return n

    def leave(self, n):
        with self._lock:
            self._inflight.pop(n, None)

    def in_flight(self):
        now = self._clock()
        with self._lock:
            items = sorted(self._inflight.values())
        return [{"method": m, "path": p, "for_s": round(now - t0, 2)} for t0, m, p in items]

    # ── the event loop ────────────────────────────────────────────────────────

    def stalled(self, seconds):
        entry = {"at": self._clock(), "seconds": round(seconds, 2), "in_flight": self.in_flight()[:8]}
        with self._lock:
            self.stalls.append(entry)
        log.warning("busy: event loop stalled %.1f s; in flight: %s", seconds,
                    ", ".join(f"{r['method']} {r['path']} ({r['for_s']} s)" for r in entry["in_flight"])
                    or "no request (a background task)")
        return entry

    async def watch_loop(self, period=0.25):
        loop = asyncio.get_running_loop()
        while True:
            start = loop.time()
            await asyncio.sleep(period)
            late = loop.time() - start - period
            if late > STALL_S:
                self.stalled(late)

    # ── threads ───────────────────────────────────────────────────────────────

    def sample_threads(self, before, after, elapsed):
        """Turn two {tid: cpu seconds} readings into the busiest threads."""
        names = {t.native_id: t.name for t in threading.enumerate() if t.native_id}
        rows = []
        for tid, cpu in after.items():
            used = cpu - before.get(tid, cpu)
            if used <= 0:
                continue
            rows.append({"thread": names.get(tid) or _comm(tid), "tid": tid,
                         "core_share": round(used / elapsed, 2)})
        rows.sort(key=lambda r: -r["core_share"])
        self.threads = rows[:10]
        for r in self.threads:
            if r["core_share"] >= HOG_SHARE:
                entry = {"at": self._clock(), **r, "over_s": round(elapsed)}
                with self._lock:
                    self.hogs.append(entry)
                log.warning("busy: thread %s used %.0f%% of a core for %.0f s",
                            r["thread"], 100 * r["core_share"], elapsed)
        return self.threads

    def watch_threads(self, stop, period=SAMPLE_S):
        """Run in its own thread until `stop` is set."""
        before, t0 = _thread_cpu(), time.monotonic()
        while not stop.wait(period):
            after, t1 = _thread_cpu(), time.monotonic()
            try:
                self.sample_threads(before, after, max(t1 - t0, 1e-6))
            except Exception:
                log.exception("busy: thread sample")
            before, t0 = after, t1

    def report(self):
        with self._lock:
            refusals, stalls, hogs = list(self.refusals), list(self.stalls), list(self.hogs)
        return {"refused_requests": refusals[-30:], "event_loop_stalls": stalls[-20:],
                "busy_threads_now": self.threads, "thread_hogs": hogs[-20:],
                "in_flight": self.in_flight()}


def _shape(path):
    """One line per kind of request: ids in a path are folded."""
    parts = path.split("/")
    return "/".join("<id>" if len(p) >= 8 and any(c.isdigit() for c in p) else p for p in parts)


def _thread_cpu():
    """{native thread id: user+system seconds} for this process (Linux)."""
    out = {}
    tick = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    try:
        tids = os.listdir("/proc/self/task")
    except OSError:
        return out
    for tid in tids:
        try:
            with open(f"/proc/self/task/{tid}/stat") as f:
                fields = f.read().rsplit(")", 1)[1].split()
            out[int(tid)] = (int(fields[11]) + int(fields[12])) / tick
        except (OSError, IndexError, ValueError):
            continue
    return out


def _comm(tid):
    try:
        with open(f"/proc/self/task/{tid}/comm") as f:
            return f.read().strip() or str(tid)
    except OSError:
        return str(tid)


BUSY = Busy()
