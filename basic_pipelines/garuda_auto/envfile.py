"""Update NAME=value lines in a .env file in place, atomically, mode 0600.

Used by scripts/set_nim_key.py and by the admin AI settings so a key entered
in the web app survives a restart. Values are written verbatim; nothing is
ever read back out to a client.
"""
import os
import tempfile
from pathlib import Path


def set_vars(env_path, updates):
    # A value with a line break would write extra NAME=value lines -- an
    # admin-entered "key" could switch GARUDA_EVAL_OTP_BYPASS back on.
    for name, value in updates.items():
        if not name.isidentifier() or any(c in str(value) for c in "\r\n\x00"):
            raise ValueError(f"refusing to write {name}: invalid characters")
    env_path = Path(env_path)
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    done = set()
    for i, line in enumerate(lines):
        name = line.split("=", 1)[0].strip()
        if name in updates:
            lines[i] = f"{name}={updates[name]}"
            done.add(name)
    lines += [f"{k}={v}" for k, v in updates.items() if k not in done]
    fd, tmp = tempfile.mkstemp(dir=env_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, env_path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
