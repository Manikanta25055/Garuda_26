"""State: the dashboard state snapshot, its push to browser sockets, and the readiness probes.

Moved out of Garuda_web.py (2026-10). Function bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`: the
live module, handed over once by bind(). Garuda_web imports these functions
back under the same names.
"""
import asyncio
import datetime
import os
import sqlite3
import time

from fastapi import Request

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


def _home_state_summary():
    """Devices on, notices and presence for the dashboard; never raises."""
    try:
        return core.HOME.summary()
    except Exception as exc:
        return {"error": type(exc).__name__}


def _recent_alert_history(days: int = 120) -> dict:
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    return {day: n for day, n in core.STATE.alerts.history.items() if str(day) >= cutoff}


def get_state_dict():
    # Expire alert once the wall-clock timer runs out
    _just_cleared = False
    with core.STATE.alerts.lock:
        if core.STATE.alerts.active and core.STATE.alerts.end_time > 0 and time.time() >= core.STATE.alerts.end_time:
            core.STATE.alerts.active = False
            core.STATE.alerts.end_time = 0.0
            core.STATE.alerts.danger_trigger_info = ""
            _just_cleared = True
    if _just_cleared:
        core.push_urgent_ws()   # push cleared state outside lock to avoid deadlock

    uptime = int(time.time() - core._app_start_time)
    hours, rem = divmod(uptime, 3600)
    mins, secs = divmod(rem, 60)
    uptime_str = f"{hours:02d}:{mins:02d}:{secs:02d}"

    # System health (psutil) — EMA-smoothed to avoid jitter
    cpu_pct = None
    ram_pct = None
    cpu_temp = None
    cpu_cores = []
    ram_used_gb = None
    ram_total_gb = None
    if core.psutil:
        raw_cpu = core.psutil.cpu_percent(interval=None)
        vm = core.psutil.virtual_memory()
        raw_ram = vm.percent
        core.STATE.system.cpu_ema = core._EMA_A * raw_cpu + (1 - core._EMA_A) * core.STATE.system.cpu_ema
        core.STATE.system.ram_ema = core._EMA_A * raw_ram + (1 - core._EMA_A) * core.STATE.system.ram_ema
        cpu_pct = round(core.STATE.system.cpu_ema, 1)
        ram_pct = round(core.STATE.system.ram_ema, 1)
        ram_used_gb  = round(vm.used  / (1024 ** 3), 1)
        ram_total_gb = round(vm.total / (1024 ** 3), 1)
        # Per-core EMA
        raw_cores = core.psutil.cpu_percent(percpu=True, interval=None)
        if not core.STATE.system.cpu_cores_ema:
            core.STATE.system.cpu_cores_ema.extend(raw_cores)
        else:
            for i, v in enumerate(raw_cores):
                if i < len(core.STATE.system.cpu_cores_ema):
                    core.STATE.system.cpu_cores_ema[i] = core._EMA_A * v + (1 - core._EMA_A) * core.STATE.system.cpu_cores_ema[i]
        cpu_cores = [round(v, 1) for v in core.STATE.system.cpu_cores_ema]
        try:
            temps = core.psutil.sensors_temperatures()
            if temps:
                for sensor_name in ('cpu_thermal', 'coretemp', 'k10temp', 'acpitz'):
                    if sensor_name in temps and temps[sensor_name]:
                        raw_temp = temps[sensor_name][0].current
                        core.STATE.system.temp_ema = core._EMA_A * raw_temp + (1 - core._EMA_A) * core.STATE.system.temp_ema
                        cpu_temp = round(core.STATE.system.temp_ema, 1)
                        break
        except Exception:
            pass

    inference_fps = round(core.STATE.camera.total_frames / max(1, uptime), 1) if uptime > 0 else 0.0

    # ── Disk usage ──
    disk_pct = None
    disk_used_gb = None
    disk_total_gb = None
    if core.psutil:
        try:
            du = core.psutil.disk_usage('/')
            disk_pct = round(du.percent, 1)
            disk_used_gb = round(du.used / (1024 ** 3), 1)
            disk_total_gb = round(du.total / (1024 ** 3), 1)
        except Exception:
            pass

    # ── Network status ──
    net_connected = False
    net_iface = None
    if core.psutil:
        try:
            stats = core.psutil.net_if_stats()
            for iface in ('wlan0', 'eth0', 'end0'):
                if iface in stats and stats[iface].isup:
                    net_connected = True
                    net_iface = iface
                    break
        except Exception:
            pass

    # ── Thermal throttling (RPi5: >80°C = throttled) ──
    throttled = False
    if cpu_temp and cpu_temp >= 80:
        throttled = True

    # ── Security health ──
    # If no HEARTBEAT_KEY is configured, watchdog is N/A (always OK)
    _hb_key = os.environ.get("HEARTBEAT_KEY", "")
    watchdog_ok = True if not _hb_key else (time.time() - core.STATE.system.last_heartbeat) < core._DEADMAN_TIMEOUT
    camera_blind = core.STATE.alerts.blind_alert_sent

    # Expire night presence alert if window ended
    with core.STATE.alerts.night_presence_lock:
        if core.STATE.alerts.night_presence_active and time.time() > core.STATE.alerts.night_presence_end_time:
            core.STATE.alerts.night_presence_active = False
    np_alert = core.STATE.alerts.night_presence_active

    return {
        "modes": {
            "dnd": core.STATE.modes.dnd,
            "email_off": core.STATE.modes.email_off,
            "idle": core.STATE.modes.idle,
            "night": core.STATE.modes.night,
            "emergency": core.STATE.modes.emergency,
            "privacy": core.STATE.modes.privacy,
        },
        "alert_active": core.STATE.alerts.active,
        "night_presence_alert": np_alert,
        "danger_info": core.STATE.alerts.danger_trigger_info,   # only non-empty during a danger alert
        "last_alert": core.STATE.alerts.last_alert_time.isoformat() if core.STATE.alerts.last_alert_time else None,
        "uptime": uptime_str,
        "uptime_seconds": uptime,
        "system_log": core.system_updates_log[-50:],
        "voice_log": core.voice_assistant_log[-30:],
        "voice_mic": {"ok": core.STATE.system.voice_mic_ok, "detail": core.STATE.system.voice_mic_detail},
        "voice_responses": core.voice_responses[-30:],
        "detection_threshold": core.STATE.config.detection_threshold,
        "cpu_percent": cpu_pct,
        "cpu_cores": cpu_cores,
        "ram_percent": ram_pct,
        "ram_used_gb": ram_used_gb,
        "ram_total_gb": ram_total_gb,
        "cpu_temp": cpu_temp,
        "inference_fps": inference_fps,
        "owner_present": core.STATE.presence.owner_present,
        "home": core._home_state_summary(),
        "owner_name": (core._present_device() or {}).get("name"),
        "known_devices": [
            {"name": d.get("name", ""), "mac": core._device_mac(d),
             "online": core._mac_online(core._device_mac(d))}
            for d in core.STATE.config.known_devices
        ],
        # The heatmap shows 13 weeks; the full history (one key per day, for
        # ever) was being sent to every client every two seconds.
        "alert_history": core._recent_alert_history(),
        # Security health
        "watchdog_ok": watchdog_ok,
        "camera_blind": camera_blind,
        "throttled": throttled,
        # Extended hardware
        "disk_percent": disk_pct,
        "disk_used_gb": disk_used_gb,
        "disk_total_gb": disk_total_gb,
        "net_connected": net_connected,
        "net_iface": net_iface,
        # Log counts for badge display (avoid sending full arrays over WS)
        "detection_log_count": len(core._detection_log),
        "presence_log_count": len(core.STATE.presence.log),
        # Offline queue
        "net_online": core.STATE.system.net_online,
        "pending_sync": core.get_pending_count(max_age=10.0),
        # Clip recording state (lets JS reset button when server auto-stops)
        "clip_recording": core.STATE.camera.clip_writer is not None,
    }


def _state_for_role(payload: dict, role: str) -> dict:
    """The state push, trimmed for a non-admin viewer.

    Everyone got the admin's view: registered phone MAC addresses, who signed
    in from where, lockout notices with client addresses. The dashboard for a
    'user' shows none of that, so it is not sent.
    """
    if role == "admin":
        return payload
    slim = {k: v for k, v in payload.items() if k not in core._ADMIN_ONLY_STATE}
    slim["system_log"] = [line for line in payload.get("system_log", [])
                          if not any(tag in line for tag in core._ADMIN_ONLY_LOG_TAGS)]
    return slim


def push_urgent_ws():
    """Signal the WS broadcaster to push state immediately (cross-thread safe)."""
    if core.STATE.system.event_loop and core.STATE.system.ws_trigger:
        core.STATE.system.event_loop.call_soon_threadsafe(core.STATE.system.ws_trigger.set)


def _ws_connect_allowed(websocket) -> bool:
    """Per-client limit on opening sockets.

    This used websocket.client.host, which behind the tunnel is 127.0.0.1 for
    everybody, and the same 30-a-minute bucket as anonymous HTTP: every
    phone and laptop in the house shared one small allowance, and a browser
    reconnecting in a loop could lock all of them out (close code 4029).
    """
    ip = core._get_client_ip(websocket)
    now = time.time()
    stamps = core.STATE.auth.rate_store[f"ws:{ip}"]
    stamps[:] = [t for t in stamps if now - t < core._RATE_WINDOW]
    if len(stamps) >= core._WS_CONNECT_LIMIT:
        return False
    stamps.append(now)
    return True


async def _ws_send(ws, payload):
    # One stalled phone must not hold the push to everyone else.
    await asyncio.wait_for(ws.send_json(payload), timeout=5.0)


async def _ws_broadcaster():
    """Background task: push state immediately on events, or every 2s as heartbeat."""
    _prune_counter = 0
    _maintenance_counter = 0
    while True:
        try:
            await asyncio.wait_for(core.STATE.system.ws_trigger.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass
        core.STATE.system.ws_trigger.clear()
        # Anything raised in here used to end this task for good: no client
        # got another update until the service was restarted. One bad tick is
        # now logged and the next one runs.
        try:
            if core.STATE.auth.refresh_dirty:
                await asyncio.to_thread(core._save_refresh_tokens)
            # Prune expired sessions every ~5 minutes (150 ticks × 2s)
            _prune_counter += 1
            if _prune_counter >= 150:
                _prune_counter = 0
                core._prune_expired_sessions()
                core._prune_rate_state()
                _maintenance_counter += 1
                if _maintenance_counter >= 12:        # about hourly
                    _maintenance_counter = 0
                    await asyncio.to_thread(core.prune_synced_events)
            # psutil, the event database and the home summary all touch the
            # disk: built on a worker thread so the loop keeps serving.
            payload = await asyncio.to_thread(core.get_state_dict)   # always run — handles alert expiry even without clients
            if not core._ws_clients:
                continue
            clients = list(core._ws_clients.items())
            # Connections whose owner has signed out everywhere are closed.
            stale = [ws for ws, meta in clients if not core._user_signed_in(meta["username"])]
            for ws in stale:
                core._ws_clients.pop(ws, None)
                try:
                    await ws.close(code=4001)
                except Exception:
                    pass
            clients = [(ws, meta) for ws, meta in clients if ws not in stale]
            user_payload = None
            sends = []
            for ws, meta in clients:
                if meta["role"] == "admin":
                    sends.append(core._ws_send(ws, payload))
                else:
                    if user_payload is None:
                        user_payload = core._state_for_role(payload, "user")
                    sends.append(core._ws_send(ws, user_payload))
            results = await asyncio.gather(*sends, return_exceptions=True)
            for (ws, _meta), result in zip(clients, results):
                if isinstance(result, Exception):
                    core._ws_clients.pop(ws, None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            core.log_system_update(f"[WS] broadcast failed: {type(exc).__name__}: {exc}")


def _client_meta(request: Request) -> dict:
    """What a client needs to adapt itself: which product this address is,
    and which optional parts this server actually has."""
    product = core._product_for_host(request.headers.get("host"))
    return {
        "product": product,
        "name": "Garuda" if product == "security" else "Drishti",
        "features": {
            "home_automation": product != "security",
            "voice": bool(core.NARADA_VOICE.configured),
            "assistant": bool(core.NIM_CHAT.configured),
            "webrtc": bool(core._WEBRTC_AVAILABLE),
            "clips": True,
            "email_alerts": bool(core.STATE.config.email_sender and core.STATE.config.email_sender_pass and core.STATE.config.email_recipients),
        },
        "auth": {"access_token_s": core._ACCESS_DURATION, "refresh_token_s": core._REFRESH_DURATION,
                 "header": "X-Garuda-Token", "refresh_header": "X-Garuda-Refresh"},
    }


def _system_extra() -> dict:
    return {"sessions": {"active": len(core.STATE.auth.sessions),
                         "refresh_tokens": len(core.STATE.auth.refresh_tokens) + len(core.STATE.auth.persisted_refresh),
                         "websockets": len(core._ws_clients), "video_peers": len(core._pc_set)},
            "log_file": getattr(core._core_logging.configure, "path", None)}


def _probe_camera():
    age = time.time() - core.STATE.camera.frame_ts
    return (age < 5.0, "delivering frames" if age < 5.0 else
            ("no frame yet" if not core.STATE.camera.frame_ts else f"no frame for {int(age)} s"))


def _probe_events_db():
    conn = sqlite3.connect(core.EVENTS_DB, timeout=2)
    try:
        conn.execute("SELECT 1 FROM events LIMIT 1").fetchall()
    finally:
        conn.close()
    return True, "ok"


def _probe_disk():
    usage = __import__("shutil").disk_usage(str(core._BASE))
    free_mb = usage.free // (1024 * 1024)
    return free_mb >= 200, f"{free_mb} MB free"


def _probe_workers():
    bad = [w["name"] for w in core.SUPERVISOR.status() if w["critical"] and not w["alive"]]
    return (not bad, "all running" if not bad else "stopped: " + ", ".join(bad))


def _probe_rules():
    health = core.DRISHTI_RUNTIME.health()
    return bool(health.get("running")), health.get("last_error") or "running"
