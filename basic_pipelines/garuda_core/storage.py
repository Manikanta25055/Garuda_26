"""Durable writes for the small JSON files the service keeps.

Moved out of Garuda_web.py unchanged (2026-10); that module still exports the
name, and the test suite swaps it there for a faster version.
"""
import json
import os
import tempfile

def _atomic_json_write(filepath: str, data):
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(filepath) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, filepath)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
