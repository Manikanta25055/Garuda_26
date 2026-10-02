"""Support: helpers the routes lean on: MJPEG frames, clip pruning, log text, feedback storage, OTP mail, the eval token check and the Narada voice socket.

Moved out of Garuda_web.py (2026-10). Function bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`: the
live module, handed over once by bind(). Garuda_web imports these functions
back under the same names.
"""
import asyncio
import datetime
import hmac
import json
import os
import signal
import smtplib
import time

from fastapi import HTTPException, Request, WebSocket, WebSocketDisconnect

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


async def mjpeg_frames(request: Request):
    """The MJPEG body, shared by Garuda's /stream and Drishti's.

    Authentication is the caller's job — the two endpoints check different
    cookies. This only produces frames.
    """
    # The pipeline already encodes each published frame once (FramePublisher).
    # This used to encode the raw frame again, per viewer, on the event loop:
    # 15 JPEG encodes a second for every open camera view, each one stalling
    # the state socket and every other request while it ran.
    last_seq = -1
    still_valid = getattr(request.state, "stream_valid", None)
    next_check = time.time() + core._STREAM_RECHECK_S
    while True:
        if await request.is_disconnected():
            break
        if still_valid is not None and time.time() >= next_check:
            if not still_valid():
                break                      # signed out, or the account is gone
            next_check = time.time() + core._STREAM_RECHECK_S
        with core._frame_lock:
            seq = core._frame_seq
            jpeg = core._frame_buffer if seq != last_seq else None
        if jpeg is not None:
            last_seq = seq
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n")
        else:
            await asyncio.sleep(0.02)


def _prune_old_clips(keep: int = None):
    """Keep the newest `keep` clips (default _CLIPS_KEEP); nothing ever removed them before."""
    if keep is None:
        keep = core._CLIPS_KEEP
    try:
        clips = sorted((core._BASE / "system_logs").glob("clip_*.mp4*"), key=lambda p: p.stat().st_mtime)
        for old in clips[:-keep]:
            old.unlink(missing_ok=True)
    except Exception as exc:
        core.log_system_update(f"Clip cleanup failed: {exc}")


def _combined_log_text() -> str:
    core._do_flush_logs()   # include lines still waiting in the write buffer
    parts = []
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts.append(f"# Garuda Security System — Full Log Export")
    parts.append(f"# Generated: {stamp}")
    parts.append("")

    for title, filepath in [
        ("SYSTEM LOG", core.PERM_SYSTEM_LOG),
        ("VOICE LOG",  core.PERM_VOICE_LOG),
        ("DETECTION LOG", core.PERM_DETECTION_LOG),
        ("PRESENCE LOG", core.PRESENCE_LOG_FILE),
    ]:
        parts.append(f"{'='*60}")
        parts.append(f"  {title}")
        parts.append(f"{'='*60}")
        try:
            if filepath.endswith(".json"):
                # presence_log is JSON array
                if os.path.exists(filepath):
                    with open(filepath, encoding="utf-8") as f:
                        data = json.load(f)
                    for e in data:
                        parts.append(f"[{e.get('ts','')}] {e.get('event','').upper():8s} {e.get('device','')} ({e.get('mac','')})")
                else:
                    parts.append("(no entries)")
            else:
                if os.path.exists(filepath):
                    with open(filepath, encoding="utf-8", errors="replace") as f:
                        content = f.read().strip()
                    parts.append(content if content else "(no entries)")
                else:
                    parts.append("(no entries)")
        except Exception as ex:
            parts.append(f"(error reading log: {ex})")
        parts.append("")

    return "\n".join(parts)


def _load_feedback() -> list:
    entries = core._safe_json_load(core.FEEDBACK_FILE, None)
    if isinstance(entries, list):
        return entries
    backup_entries = core._safe_json_load(core.FEEDBACK_BACKUP_FILE, [])
    if isinstance(backup_entries, list):
        if backup_entries:
            try:
                core._atomic_json_write(core.FEEDBACK_FILE, backup_entries)
            except Exception as e:
                core.log_system_update(f"Failed to restore feedback from backup: {e}")
        return backup_entries
    return []


def _save_feedback(entries: list):
    try:
        core._atomic_json_write(core.FEEDBACK_FILE, entries)
        core._atomic_json_write(core.FEEDBACK_BACKUP_FILE, entries)
    except Exception as e:
        core.log_system_update(f"Failed to save feedback: {e}")


def stop_app():
    core.log_system_update("Stopping Garuda Web app.")
    core._do_flush_logs()   # write buffered logs before exit
    if core.app_gst is not None:
        try:
            core.app_gst.pipeline.set_state(core.Gst.State.NULL)
        except Exception:
            pass
    # This runs on a worker thread, where sys.exit() only ended that thread:
    # the camera stopped but the server stayed up, half alive. SIGTERM lets
    # uvicorn shut down in order (the lifespan flushes logs); the unit is
    # Restart=on-failure, so a clean exit stays stopped.
    os.kill(os.getpid(), signal.SIGTERM)


def send_otp_via_email(email, otp_code):
    body = f"Hello,\n\nYour OTP code is: {otp_code}\n\nUse this to complete your login."
    try:
        core._send_mail("Your Garuda OTP Code", body, to=email)
        core.log_system_update(f"OTP email sent to {email}")
        return True, None
    except smtplib.SMTPAuthenticationError:
        err = "SMTP auth failed. Check EMAIL_SENDER_PASS (must be a Gmail App Password)."
        core.log_system_update(err)
        return False, err
    except Exception as e:
        err = str(e)
        core.log_system_update(f"Email error: {err}")
        return False, err


def _require_eval_token(request: Request):
    """Token-gated access for the P1-4 evaluation harness."""
    expected = os.environ.get("GARUDA_EVAL_TOKEN", "")
    if not expected:
        raise HTTPException(404, "Not found")
    got = request.headers.get("X-Eval-Token", "")
    if not hmac.compare_digest(got.encode(), expected.encode()):
        raise HTTPException(403, "Bad eval token")


def _safe_json_load(filepath: str, default):
    try:
        if os.path.exists(filepath):
            with open(filepath, encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        core.log_system_update(f"Failed to read {os.path.basename(filepath)}: {e}")
    return default


def _product_for_host(host):
    host = (host or "").split(":")[0].lower()
    return "security" if host in core.SECURITY_ONLY_HOSTS else "home"


async def narada_voice_ws(websocket: WebSocket):
    """ElevenLabs connects here with each conversation's transcripts."""
    if not core.NARADA_VOICE.verify(websocket.headers):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        await core.NARADA_VOICE.serve(websocket)
    except WebSocketDisconnect:
        pass
