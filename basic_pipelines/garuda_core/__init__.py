"""The base the Garuda service stands on.

Garuda_web.py grew as one file that does everything. The pieces that every
service needs, whatever it does, live here instead, each small enough to read
in one sitting and to test without a camera:

  settings       every environment variable, read once, typed and checked
  logging_setup  one log format, one rotating file, a request id on each line
  http           request ids, the /api/v1 prefix, one error shape, access log
  workers        background loops that are restarted and reported when they die
  backup         a daily snapshot of the state that cannot be recreated
  system_api     health, readiness, version and diagnostics endpoints

Nothing in here imports Garuda_web: that module runs as a script, so importing
it would create a second copy with empty state. What these modules need from
it is passed in.
"""
import subprocess
from pathlib import Path

# The contract clients are written against. Bump only for a breaking change;
# every route is also reachable under /api/v<API_VERSION>/.
API_VERSION = "1"

_ROOT = Path(__file__).resolve().parent.parent.parent


def _git(*args):
    try:
        return subprocess.check_output(["git", "-C", str(_ROOT), *args], text=True,
                                       stderr=subprocess.DEVNULL, timeout=3).strip()
    except Exception:
        return ""


# Read once at import: what is checked out is what is running.
BUILD = {
    "commit": _git("rev-parse", "--short", "HEAD") or "unknown",
    "branch": _git("rev-parse", "--abbrev-ref", "HEAD") or "unknown",
    "committed_at": _git("log", "-1", "--format=%cI") or "",
}
