"""Background loops that are watched.

The service runs half a dozen daemon threads (presence, schedules, log
flushing, connectivity, the camera pipeline). A plain threading.Thread that
raises simply ends: nothing restarts it and nothing says so, and the feature
it carried is gone until someone notices and restarts the service. A
supervised worker is restarted with a growing pause, and its state is visible
on the readiness and diagnostics endpoints.
"""
import logging
import threading
import time

log = logging.getLogger(__name__)


class Supervisor:
    def __init__(self, clock=time.time, sleep=time.sleep):
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._workers = {}

    def spawn(self, name, target, *, args=(), restart=True, critical=False,
              max_backoff=60.0):
        """Run `target(*args)` on a daemon thread.

        A target that returns is finished (some loops are disabled by
        configuration and return at once). A target that raises is restarted
        after 1 s, 2 s, 4 s ... up to `max_backoff`, unless restart=False.
        `critical` marks a worker the service is not ready without.
        """
        with self._lock:
            existing = self._workers.get(name)
            if existing and existing["thread"].is_alive():
                return existing["thread"]
            info = {"name": name, "state": "starting", "restarts": 0, "last_error": "",
                    "started_at": self._clock(), "critical": critical, "thread": None}
            self._workers[name] = info

        def run():
            backoff = 1.0
            while True:
                info["state"] = "running"
                began = self._clock()
                try:
                    target(*args)
                    info["state"] = "finished"
                    return
                except Exception as exc:      # noqa: BLE001 - the whole point is to catch it
                    info["last_error"] = f"{type(exc).__name__}: {exc}"[:300]
                    info["last_error_at"] = self._clock()
                    log.exception("worker %s died", name)
                    if not restart:
                        info["state"] = "failed"
                        return
                    info["restarts"] += 1
                    info["state"] = "restarting"
                    # A worker that ran for a while before failing starts over
                    # quickly; one that fails at once backs off.
                    if self._clock() - began > 300:
                        backoff = 1.0
                    self._sleep(backoff)
                    backoff = min(max_backoff, backoff * 2)

        thread = threading.Thread(target=run, daemon=True, name=name)
        info["thread"] = thread
        thread.start()
        return thread

    def status(self):
        now = self._clock()
        out = []
        with self._lock:
            items = list(self._workers.values())
        for w in items:
            alive = bool(w["thread"] and w["thread"].is_alive())
            state = w["state"] if alive or w["state"] in ("finished", "failed") else "dead"
            out.append({"name": w["name"], "state": state, "alive": alive,
                        "restarts": w["restarts"], "last_error": w["last_error"],
                        "critical": w["critical"],
                        "uptime_s": int(now - w["started_at"])})
        return out

    def healthy(self):
        """False when a critical worker is not running."""
        return all(w["alive"] or w["state"] == "finished"
                   for w in self.status() if w["critical"])
