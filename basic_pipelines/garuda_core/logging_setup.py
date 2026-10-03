"""One logging configuration for the whole service.

Before this, the modules under garuda_auto/ called logging.getLogger() and
nothing ever configured logging: warnings went to stderr with no time on them
and everything below a warning was dropped. Garuda_web itself used print().
Now every line has a time, a level, the module and the id of the request that
caused it, and goes to a file that rotates instead of growing.
"""
import contextvars
import logging
import logging.handlers
import os

# Set by the request-id middleware; "-" outside a request (background loops).
request_id = contextvars.ContextVar("garuda_request_id", default="-")

FORMAT = "%(asctime)s %(levelname)-7s %(name)s [%(request_id)s] %(message)s"
_configured = False


class _RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = request_id.get()
        return True


def configure(log_dir, level="INFO", filename="garuda.log", max_bytes=5 * 1024 * 1024, backups=3):
    """Idempotent. Returns the path of the log file (or None if it cannot be written)."""
    global _configured
    root = logging.getLogger()
    if _configured:
        return getattr(configure, "path", None)
    _configured = True
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    formatter = logging.Formatter(FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
    flt = _RequestIdFilter()

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    stream.addFilter(flt)
    stream.setLevel(logging.WARNING)          # journal/stderr keeps only what needs attention
    root.addHandler(stream)

    path = None
    try:
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, filename)
        file_handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.addFilter(flt)
        root.addHandler(file_handler)
    except OSError:
        path = None                            # a read-only disk must not stop the service
    # Third-party chatter that drowns the file at INFO.
    for noisy in ("httpx", "httpcore", "urllib3", "aioice", "aiortc", "asyncio", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    configure.path = path
    return path
