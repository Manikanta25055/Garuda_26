"""Logs: the in-memory logs, their flush to disk, rotation and the permanent detection log.

Moved out of Garuda_web.py (2026-10). Function bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`: the
live module, handed over once by bind(). Garuda_web imports these functions
back under the same names.
"""
import datetime
import os
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


def _load_logs_from_disk():
    """Populate in-memory log lists from permanent files on startup (last 500 lines each)."""
    def _tail(path, n=500):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            return [l.rstrip("\n") for l in lines[-n:]]
        except FileNotFoundError:
            return []
        except Exception:
            return []
    core.system_updates_log[:] = _tail(core.PERM_SYSTEM_LOG)
    core.voice_assistant_log[:] = _tail(core.PERM_VOICE_LOG)
    core._detection_log[:] = _tail(core.PERM_DETECTION_LOG)


def _rotate_log(filepath: str):
    """Rename filepath → filepath.1, discarding any previous .1 file."""
    try:
        rotated = filepath + ".1"
        if os.path.exists(rotated):
            os.unlink(rotated)
        os.rename(filepath, rotated)
    except Exception:
        pass


def _do_flush_logs():
    """Write all buffered log lines to disk. Called by the flush thread and on shutdown."""
    with core._log_buffer_lock:
        snapshots = {path: buf[:] for path, buf in core._log_buffer.items() if buf}
        for path in snapshots:
            core._log_buffer[path].clear()
    for path, lines in snapshots.items():
        if not lines:
            continue
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            # Rotate if over size cap
            try:
                if os.path.getsize(path) > core._LOG_MAX_SIZE_BYTES:
                    core._rotate_log(path)
            except FileNotFoundError:
                pass
            with open(path, "a", encoding="utf-8") as f:
                for line in lines:
                    f.write(line + "\n")
        except Exception:
            pass


def _flush_log_thread():
    """Background daemon thread: flush log buffer on interval."""
    while True:
        time.sleep(core._LOG_FLUSH_INTERVAL)
        core._do_flush_logs()


def _perm_write(filepath: str, line: str):
    """Buffer a log line in RAM; flushed to disk every _LOG_FLUSH_INTERVAL seconds."""
    with core._log_buffer_lock:
        core._log_buffer[filepath].append(line)


def _append_detection_perm(event_type: str, label: str, confidence: float, info: str = ""):
    """Append one detection event to in-memory list, permanent file, and SQLite queue."""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] [{event_type.upper()}] {label} conf={confidence:.2f}"
    if info:
        line += f" — {info}"
    core._detection_log.append(line)
    if len(core._detection_log) > 500:
        core._detection_log[:] = core._detection_log[-500:]
    core._perm_write(core.PERM_DETECTION_LOG, line)
    core.queue_event(event_type.upper(), label, confidence, info)


def log_system_update(message):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {message}"
    core.system_updates_log.append(entry)
    if len(core.system_updates_log) > 500:
        core.system_updates_log[:] = core.system_updates_log[-500:]
    core._perm_write(core.PERM_SYSTEM_LOG, entry)
    # Also into the one service log (garuda.log), next to the request and
    # worker lines, so there is a single file to read when something is wrong.
    core._syslog.info("%s", message)


def append_voice_log(message, user_name=None):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {message}"
    core.voice_assistant_log.append(entry)
    if len(core.voice_assistant_log) > 500:
        core.voice_assistant_log[:] = core.voice_assistant_log[-500:]
    core._perm_write(core.PERM_VOICE_LOG, entry)
    core._remember_user_activity(user_name, "narada_activity", entry)


def append_voice_response(message, user_name=None):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {message}"
    core.voice_responses.append(entry)
    if len(core.voice_responses) > 500:
        core.voice_responses[:] = core.voice_responses[-500:]
    core._perm_write(core.PERM_VOICE_LOG, "→ " + entry)
    core._remember_user_activity(user_name, "narada_activity", entry)
