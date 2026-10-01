"""Credentials: how passwords, master keys and one-time codes are made and checked.

Pure functions, no state and no I/O: everything here can be called from any
thread and tested without the rest of the service. Moved out of Garuda_web.py
unchanged (2026-10); that module still exports every name.
"""
import hashlib
import hmac
import os
import secrets

_PBKDF2_ITERS = 600000  # OWASP 2024 recommendation for PBKDF2-SHA256


def _hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac('sha256', pw.encode(), salt, _PBKDF2_ITERS)
    return f"pbkdf2:sha256:{_PBKDF2_ITERS}:{salt.hex()}:{dk.hex()}"


def _verify_password(pw: str, stored: str) -> bool:
    if stored.startswith("pbkdf2:"):
        parts = stored.split(":")
        if len(parts) != 5:
            return False
        _, algo, iters, salt_hex, dk_hex = parts
        try:
            dk = hashlib.pbkdf2_hmac(algo, pw.encode(), bytes.fromhex(salt_hex), int(iters))
        except (ValueError, TypeError):
            return False      # a damaged record must fail closed, not raise a 500
        return hmac.compare_digest(dk.hex(), dk_hex)
    # Plaintext fallback for migration. Compared as bytes: compare_digest on
    # str raises TypeError for any non-ASCII character.
    return hmac.compare_digest(pw.encode(), str(stored).encode())


# Verified against when the username does not exist, so an unknown account
# costs the same time as a wrong password and cannot be told apart by timing.
_DUMMY_PASSWORD_HASH = _hash_password(secrets.token_hex(16))


def _validate_password_strength(pw: str) -> str | None:
    """Return an error string if password fails requirements, else None."""
    if not pw or not pw.strip():
        return "Password cannot be empty."
    p = pw.strip()
    if len(p) < 8:
        return "Password must be at least 8 characters."
    if len(p) > 256:
        return "Password must be at most 256 characters."
    if not any(c.isupper() for c in p):
        return "Password must contain at least one uppercase letter."
    if not any(c.islower() for c in p):
        return "Password must contain at least one lowercase letter."
    if not any(c.isdigit() for c in p):
        return "Password must contain at least one digit."
    return None


# A master key is a full admin sign-in, and the file held them as typed. They
# are now kept as salted hashes ("mk1$salt$hash$last4"): the last four
# characters stay so the settings page can still tell the keys apart. Keys are
# long and random by rule (12+ characters, four classes), so 120k PBKDF2
# rounds is ample and keeps a check with several keys quick on the Pi.
_MK_PREFIX = "mk1$"


_MK_ITERS = 120_000


def _mk_is_hashed(entry) -> bool:
    return isinstance(entry, str) and entry.startswith(_MK_PREFIX)


def _mk_hash(key: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", key.encode(), salt, _MK_ITERS)
    tail = key[-4:] if len(key) > 4 else ""
    return f"{_MK_PREFIX}{salt.hex()}${dk.hex()}${tail}"


def _mk_check(key: str, entry) -> bool:
    """True when `key` is the master key stored as `entry` (hashed or legacy plaintext)."""
    if not key or not isinstance(entry, str):
        return False
    if not _mk_is_hashed(entry):
        return hmac.compare_digest(key.encode(), entry.encode())
    try:
        _, salt_hex, dk_hex, _tail = entry.split("$", 3)
        dk = hashlib.pbkdf2_hmac("sha256", key.encode(), bytes.fromhex(salt_hex), _MK_ITERS)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), dk_hex)


def _mk_mask(entry) -> str:
    dots = "\u2022" * 8
    if _mk_is_hashed(entry):
        tail = entry.split("$", 3)[3] if entry.count("$") >= 3 else ""
        return dots + tail
    entry = str(entry)
    return ("\u2022" * (len(entry) - 4) + entry[-4:]) if len(entry) > 4 else "\u2022" * 4


def _master_key_matches(key: str, valid_keys) -> bool:
    """Constant-time check of `key` against every valid key (bytes: no TypeError on non-ASCII)."""
    if not key or len(key) > 256:
        return False
    hit = False
    for candidate in list(valid_keys):
        if _mk_check(key, candidate):
            hit = True
    return hit


def generate_otp_code(length=6):
    return "".join(str(secrets.randbelow(10)) for _ in range(length))


def _rt_digest(token: str) -> str:
    return hashlib.sha256(str(token).encode()).hexdigest()
