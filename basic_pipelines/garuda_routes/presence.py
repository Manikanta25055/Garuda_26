"""Owner presence: the known devices, the ARP scan and a manual presence check; plus the test email (admin only).

Moved out of Garuda_web.py (2026-10). Handler bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`.
"""
import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel


class DeviceAddRequest(BaseModel):
    name: str
    mac: str

class DeviceDeleteRequest(BaseModel):
    mac: str


def build_presence_router(core):
    router = APIRouter()

    @router.get("/api/devices")
    async def get_devices(session=Depends(core.require_admin)):
        return {"devices": core.STATE.config.known_devices, "owner_present": core._owner_present}

    @router.get("/api/arp")
    async def get_arp_table(session=Depends(core.require_admin)):
        """Return all active ARP entries so admin can identify device MACs."""
        entries = []
        try:
            with open('/proc/net/arp') as f:
                for line in f.readlines()[1:]:   # skip header
                    parts = line.split()
                    if len(parts) >= 4 and parts[2] == '0x2':  # 0x2 = complete entry
                        entries.append({"ip": parts[0], "mac": parts[3]})
        except Exception as e:
            raise HTTPException(500, str(e))
        registered_macs = {core._device_mac(d) for d in core.STATE.config.known_devices}
        for e in entries:
            e["registered"] = e["mac"].lower() in registered_macs
        return {"entries": entries}

    @router.post("/api/presence_refresh")
    async def presence_refresh(session=Depends(core.require_admin)):
        """Trigger an immediate ARP presence check without waiting for the 30s poller."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, core._do_presence_check)
        core.push_urgent_ws()
        return {"owner_present": core._owner_present}

    @router.post("/api/devices/add")
    async def add_device(data: DeviceAddRequest, session=Depends(core.require_admin)):
        mac = data.mac.strip().lower()
        if not re.match(r'^([0-9a-f]{2}:){5}[0-9a-f]{2}$', mac):
            raise HTTPException(400, "Invalid MAC address format (use aa:bb:cc:dd:ee:ff)")
        if any(core._device_mac(d) == mac for d in core.STATE.config.known_devices):
            raise HTTPException(400, "Device with this MAC already registered")
        name = data.name.strip()[:64]
        if not name:
            raise HTTPException(400, "Device name is required.")
        if len(core.STATE.config.known_devices) >= 32:
            raise HTTPException(400, "Maximum 32 known devices.")
        core.STATE.config.known_devices.append({"name": name, "mac": mac})
        await core._async_save_config()
        core.log_system_update(f"Known device added: {name} ({mac})")
        return {"ok": True, "devices": core.STATE.config.known_devices}

    @router.post("/api/devices/delete")
    async def delete_device(data: DeviceDeleteRequest, session=Depends(core.require_admin)):
        mac = data.mac.strip().lower()
        before = len(core.STATE.config.known_devices)
        core.STATE.config.known_devices[:] = [d for d in core.STATE.config.known_devices if core._device_mac(d) != mac]
        if len(core.STATE.config.known_devices) == before:
            raise HTTPException(404, "Device not found")
        await core._async_save_config()
        core.log_system_update(f"Known device removed: {mac}")
        return {"ok": True, "devices": core.STATE.config.known_devices}

    @router.post("/api/email/test")
    async def test_email(session=Depends(core.require_admin)):
        dest = core.STATE.config.email_recipients[0] if core.STATE.config.email_recipients else core.STATE.config.email_sender
        ok, err = await asyncio.to_thread(core.send_otp_via_email, dest, "TEST-123")
        if not ok:
            return {"ok": False, "error": err}
        return {"ok": True}

    return router
