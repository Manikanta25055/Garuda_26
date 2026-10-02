"""Owner presence: ARP probing, device matching and the presence poller.

Moved out of Garuda_web.py (2026-10). Function bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`: the
live module, handed over once by bind(). Garuda_web imports these functions
back under the same names.
"""
import ipaddress
import socket
import subprocess
import time

core = None


def bind(module):
    """Called by Garuda_web with itself, before anything here runs."""
    global core
    if core is not None and core is not module:
        # A second copy of Garuda_web (imported under another name) would
        # silently take over the state every function here reads.
        raise RuntimeError(f"{__name__} is already bound to {core.__name__}; "
                           f"refusing a second copy, {module.__name__}")
    core = module


def _get_local_subnet() -> str:
    """Return the first local subnet (e.g. '192.168.1.0/24') from ip route."""
    try:
        out = subprocess.check_output(['ip', 'route'], text=True, timeout=3)
        for line in out.splitlines():
            parts = line.split()
            # Lines like: "192.168.1.0/24 dev wlan0 ..."
            if parts and '/' in parts[0] and parts[0][0].isdigit():
                return parts[0]
    except Exception:
        pass
    return ''


def _probe_subnet_for_arp(subnet: str):
    """Send a UDP datagram to every host in subnet to force ARP table population.

    The packets are sent to port 9 (discard service) so remote hosts ignore them,
    but the kernel must resolve each MAC via ARP before sending — populating the
    local ARP cache so /proc/net/arp reflects every reachable device.
    """
    try:
        net = ipaddress.IPv4Network(subnet, strict=False)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        for host in net.hosts():
            try:
                sock.sendto(b'\x00', (str(host), 9))
            except Exception:
                pass
        sock.close()
    except Exception:
        pass


def _device_mac(device) -> str:
    return str((device or {}).get("mac") or "").strip().lower()


def _mac_online(mac: str) -> bool:
    """True when `mac` is in the last ARP read as a complete (0x2) entry.

    A substring test over the raw table also matched stale and incomplete
    rows, and an empty MAC matched everything, which read as "owner is home".
    """
    if not mac:
        return False
    for line in core._last_arp_cache.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] == "0x2" and parts[3] == mac:
            return True
    return False


def _present_device():
    """The first registered device seen on the network, or None."""
    return next((d for d in core.KNOWN_DEVICES if core._mac_online(core._device_mac(d))), None)


def _check_device_presence() -> bool:
    """Return True if any registered device MAC appears in the kernel ARP table."""
    try:
        with open('/proc/net/arp') as f:
            core._last_arp_cache = f.read().lower()
        return core._present_device() is not None
    except Exception:
        return False


def _presence_poller():
    """Background thread: poll ARP table every 30s to detect owner's phone.

    Before reading /proc/net/arp we send UDP probes to every host in the local
    subnet.  This forces ARP resolution so the table contains all active devices,
    not just those that have recently communicated with the Pi directly.
    """
    _subnet = ''
    first = True
    while True:
        if not first:
            time.sleep(30)
        first = False
        if not core.KNOWN_DEVICES:
            continue
        # One bad cycle (a malformed device entry, a failed probe) must not end
        # the thread: presence would then stay frozen until the next restart.
        try:
            # Discover subnet once (lazy) and reprobe each cycle
            if not _subnet:
                _subnet = core._get_local_subnet()
            if _subnet:
                core._probe_subnet_for_arp(_subnet)
                time.sleep(2)   # allow ARP responses to arrive
            found = core._check_device_presence()
            core.log_system_update(
                f"[PRESENCE] {'Match' if found else 'No match'} — "
                f"{len([l for l in core._last_arp_cache.splitlines() if '0x2' in l])} active ARP entries"
            )
            if found:
                core._owner_last_seen = time.time()
                if not core._owner_present:
                    core._owner_present = True
                    seen = core._present_device() or {}
                    dev, mac = seen.get("name", "Unknown"), core._device_mac(seen)
                    core._append_presence_log("arrived", dev, mac)
                    core.log_system_update(f"[OWNER] {dev} arrived — device detected on network.")
                    core.push_urgent_ws()
            elif core._owner_present and (time.time() - core._owner_last_seen > core.OWNER_AWAY_GRACE):
                core._owner_present = False
                dev = next((d.get("name", "Unknown") for d in core.KNOWN_DEVICES), "Unknown")
                core._append_presence_log("left", dev, "")
                core.log_system_update(f"[OWNER] {dev} away — device not seen for {core.OWNER_AWAY_GRACE}s.")
                core.push_urgent_ws()
        except Exception as exc:
            core.log_system_update(f"[PRESENCE] poll failed: {type(exc).__name__}: {exc}")


def _do_presence_check():
    """Blocking presence check — run in thread executor from async endpoints."""
    subnet = core._get_local_subnet()
    if subnet:
        core._probe_subnet_for_arp(subnet)
        time.sleep(2)
    found = core._check_device_presence()
    if found:
        core._owner_last_seen = time.time()
        if not core._owner_present:
            core._owner_present = True
            seen = core._present_device() or {}
            dev, mac = seen.get("name", "Unknown"), core._device_mac(seen)
            core._append_presence_log("arrived", dev, mac)
            core.log_system_update(f"[OWNER] {dev} arrived (manual refresh).")
    elif core._owner_present and (time.time() - core._owner_last_seen > core.OWNER_AWAY_GRACE):
        core._owner_present = False
        dev = next((d.get("name", "Unknown") for d in core.KNOWN_DEVICES), "Unknown")
        core._append_presence_log("left", dev, "")
        core.log_system_update(f"[OWNER] {dev} away (manual refresh — device not found).")
