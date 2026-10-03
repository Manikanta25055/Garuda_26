"""The event queue: every detection and presence change, in SQLite.

The camera thread writes to it, the dashboard reads from it, and a client that
was offline asks it what it missed. One lock serialises access, as before.

Moved out of Garuda_web.py (2026-10). The bodies are unchanged except that
the database path is a parameter: Garuda_web owns where the file lives and
passes its EVENTS_DB on every call (the tests point that at a scratch file).
"""
import datetime
import os
import sqlite3
import threading
import time

KEEP_DAYS = 30
_pending_cache = {"at": 0.0, "count": 0}
_lock = threading.Lock()

# (version, [SQL]) applied in order to a database older than that version.
# Version 1 is the table as first shipped; add new entries, never edit old ones.
MIGRATIONS = [
    (1, []),
]

def init_db(path):
    """Create events table if not exists."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            label TEXT,
            confidence REAL DEFAULT 0,
            info TEXT DEFAULT '',
            synced INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_synced ON events(synced)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp)")
    # WAL: a reader (the state push) no longer waits for a writer (the camera
    # thread logging a detection), and a power cut cannot leave a half-written
    # page. NORMAL sync is the documented safe pairing with WAL.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.DatabaseError:
        pass
    # The schema carries its own version, so a later change can migrate an
    # existing database instead of guessing what it looks like.
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for target, statements in MIGRATIONS:
        if version < target:
            for statement in statements:
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {int(target)}")
    conn.commit()
    conn.close()

def insert(path, event_type: str, label: str = "", confidence: float = 0.0, info: str = "",
           on_error=None):
    """Insert an event into the SQLite queue. Thread-safe."""
    stamp = datetime.datetime.now().isoformat()
    with _lock:
        try:
            conn = sqlite3.connect(path, timeout=5)
            conn.execute(
                "INSERT INTO events (timestamp, event_type, label, confidence, info) VALUES (?, ?, ?, ?, ?)",
                (stamp, event_type, label, confidence, info))
            conn.commit()
            conn.close()
            _pending_cache["at"] = 0.0
        except Exception as e:
            if on_error is not None:
                on_error(f"[QUEUE] DB write error: {e}")

def since(path, since_ts: str = "", limit: int = 500) -> list:
    """Return events after the given ISO timestamp, oldest-first."""
    with _lock:
        try:
            conn = sqlite3.connect(path, timeout=5)
            conn.row_factory = sqlite3.Row
            if since_ts:
                rows = conn.execute(
                    "SELECT * FROM events WHERE timestamp > ? ORDER BY timestamp ASC LIMIT ?",
                    (since_ts, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM events ORDER BY timestamp ASC LIMIT ?",
                    (limit,)).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []

def unsynced(path, limit: int = 1000) -> list:
    """Unsynced events, oldest first.

    The pending endpoint used to take the oldest 1000 rows of the whole table
    and filter them: once 1000 synced rows existed, nothing newer was ever
    returned or marked, and the pending count only went up.
    """
    with _lock:
        try:
            conn = sqlite3.connect(path, timeout=5)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM events WHERE synced = 0 ORDER BY id ASC LIMIT ?",
                (max(1, int(limit)),)).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []

def prune_synced(path, days: int = KEEP_DAYS) -> int:
    """Delete synced events older than `days`; the table had no upper bound."""
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
    with _lock:
        try:
            conn = sqlite3.connect(path, timeout=5)
            cur = conn.execute("DELETE FROM events WHERE synced = 1 AND timestamp < ?", (cutoff,))
            conn.commit()
            conn.close()
            return cur.rowcount or 0
        except Exception:
            return 0

def pending_count(path, max_age: float = 0.0) -> int:
    """Return count of unsynced events.

    `max_age` lets the state broadcaster reuse a recent answer instead of
    opening the database on every two-second tick.
    """
    now = time.time()
    if max_age and now - _pending_cache["at"] < max_age:
        return _pending_cache["count"]
    with _lock:
        try:
            conn = sqlite3.connect(path, timeout=5)
            count = conn.execute("SELECT COUNT(*) FROM events WHERE synced = 0").fetchone()[0]
            conn.close()
        except Exception:
            return 0
    _pending_cache["at"], _pending_cache["count"] = now, count
    return count

def mark_synced(path, up_to_id: int):
    """Mark all events up to and including the given ID as synced."""
    with _lock:
        try:
            conn = sqlite3.connect(path, timeout=5)
            conn.execute("UPDATE events SET synced = 1 WHERE id <= ?", (up_to_id,))
            conn.commit()
            conn.close()
            _pending_cache["at"] = 0.0
        except Exception:
            pass


def total(path) -> int:
    """Every event in the table, synced or not."""
    with _lock:
        try:
            conn = sqlite3.connect(path, timeout=5)
            count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0
