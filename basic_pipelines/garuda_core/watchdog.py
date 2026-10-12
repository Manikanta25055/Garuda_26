"""Tell systemd the service is alive, so a hung one is restarted.

The unit sets WatchdogSec. While the event loop turns, `heartbeat()` sends
WATCHDOG=1; if the loop stops answering, the pings stop and systemd kills and
restarts the service. It asks only "is the process still turning", never "is
the camera working": a camera unplugged on the bench must not put the service
into a restart loop. /api/ready answers the second question.

Outside systemd (tests, a terminal) NOTIFY_SOCKET is unset and this does nothing.
"""
import asyncio
import os
import socket


def notify(message):
    """Send one sd_notify message. True when it was sent."""
    path = os.environ.get("NOTIFY_SOCKET")
    if not path:
        return False
    if path.startswith("@"):                    # abstract socket
        path = "\0" + path[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(message.encode(), path)
        return True
    except OSError:
        return False


def interval_s(default=20.0):
    """A third of the unit's WatchdogSec, so two lost pings are survivable."""
    try:
        return max(1.0, int(os.environ["WATCHDOG_USEC"]) / 3_000_000)
    except (KeyError, ValueError):
        return default


async def heartbeat():
    notify("READY=1")
    while True:
        notify("WATCHDOG=1")
        await asyncio.sleep(interval_s())
