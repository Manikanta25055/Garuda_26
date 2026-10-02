"""A daily snapshot of the state that cannot be recreated.

Accounts, settings, master keys, devices, rules, schedules and scenes are a
handful of small JSON files on one SD card. Atomic writes protect each file
from a power cut; nothing protected them from a bad edit, a bug or a dying
card. One compressed archive a day, a fortnight of them, costs a few hundred
kilobytes.

Restore is deliberate, by hand: stop the service, unpack the archive over
system_logs/, start it again. Archives hold password hashes and are written
0600 inside the data directory, which git ignores.
"""
import logging
import os
import tarfile
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Small, precious, and not derivable from anything else.
STATE_FILES = ("users.json", "config.json", "master_keys.json", "devices.json", "rules.json",
               "schedules.json", "scenes.json", "home_settings.json", "alert_history.json",
               "feedback.json", "narada_memory.json")
PREFIX = "garuda-state-"


class BackupManager:
    def __init__(self, data_dir, keep=14, clock=time.time):
        self.data_dir = Path(data_dir)
        self.dir = self.data_dir / "backups" / "auto"
        self.keep = max(1, int(keep))
        self._clock = clock
        self.last_error = ""

    def list(self):
        try:
            files = sorted(self.dir.glob(f"{PREFIX}*.tar.gz"), key=lambda p: p.name, reverse=True)
        except OSError:
            return []
        return [{"name": p.name, "bytes": p.stat().st_size,
                 "created_at": int(p.stat().st_mtime)} for p in files]

    def create(self, label=None):
        """Write one archive now. Returns its entry, or None when there was nothing to save."""
        present = [self.data_dir / name for name in STATE_FILES if (self.data_dir / name).is_file()]
        if not present:
            return None
        stamp = label or time.strftime("%Y%m%d-%H%M%S", time.localtime(self._clock()))
        self.dir.mkdir(parents=True, exist_ok=True)
        final = self.dir / f"{PREFIX}{stamp}.tar.gz"
        tmp = final.with_suffix(".tmp")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
                for path in present:
                    tar.add(path, arcname=path.name)
            os.replace(tmp, final)
            self.last_error = ""
        except OSError as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        self._prune()
        return {"name": final.name, "bytes": final.stat().st_size, "files": [p.name for p in present],
                "created_at": int(final.stat().st_mtime)}

    def _prune(self):
        for old in self.list()[self.keep:]:
            try:
                (self.dir / old["name"]).unlink()
            except OSError:
                pass

    def due(self, every_s=86400):
        newest = self.list()[:1]
        return not newest or self._clock() - newest[0]["created_at"] >= every_s

    def run_forever(self, stop=None, check_every=3600):
        """Worker loop: one archive a day, checked hourly (and once at start-up)."""
        while True:
            try:
                if self.due():
                    made = self.create()
                    if made:
                        log.info("state backup written: %s (%d bytes)", made["name"], made["bytes"])
            except OSError as exc:
                log.warning("state backup failed: %s", exc)
            if stop is not None:
                if stop.wait(check_every):
                    return
            else:
                time.sleep(check_every)
