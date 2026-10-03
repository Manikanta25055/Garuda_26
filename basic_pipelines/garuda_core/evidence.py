"""Evidence clips: encrypt one, and copy the ciphertext off the device.

The two operations, with everything they need passed in. Which key, which
server and where to log are the caller's business (Garuda_web reads them from
its own configuration on every call), so this module has no state and can be
tested with a temporary file and a fake server.

Moved out of Garuda_web.py (2026-10): the bodies are unchanged except that
module globals became parameters.
"""
import os


def encrypt_file(src_path: str, key, log) -> str | None:
    """AES-256-GCM encrypt src_path → src_path.enc. Returns encrypted path or None on failure."""
    if key is None:
        log("[EXFIL] AES key not set — skipping encryption.")
        return None
    if len(key) != 32:
        log("[EXFIL] EXFIL_AES_KEY must be exactly 32 bytes (64 hex chars).")
        return None
    enc_path = src_path + ".enc"
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(12)   # 96-bit nonce (recommended for GCM)
        with open(src_path, "rb") as f:
            plaintext = f.read()
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
        # Layout: [12-byte nonce][ciphertext+16-byte GCM tag]
        with open(enc_path, "wb") as f:
            f.write(nonce + ciphertext)
        return enc_path
    except ImportError:
        log("[EXFIL] cryptography package not installed — run: pip install cryptography")
        return None
    except Exception as e:
        log(f"[EXFIL] Encryption failed: {e}")
        return None


def sftp_upload(local_path: str, remote_filename: str, *, host, port, user, key_path,
                password, remote_dir, log) -> bool:
    """SFTP-upload local_path to the configured SSH server. Returns True on success."""
    if not host or not user:
        return False
    try:
        import paramiko
    except ImportError:
        log("[EXFIL] paramiko not installed — run: pip install paramiko")
        return False
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs: dict = {"hostname": host, "port": port,
                        "username": user, "timeout": 30}
        if key_path and os.path.exists(key_path):
            kwargs["key_filename"] = key_path
        elif password:
            kwargs["password"] = password
        ssh.connect(**kwargs)
        sftp = ssh.open_sftp()
        remote_dir = remote_dir.rstrip("/")
        try:
            sftp.mkdir(remote_dir)
        except IOError:
            pass   # already exists
        sftp.put(local_path, remote_dir + "/" + remote_filename)
        sftp.close()
        ssh.close()
        return True
    except Exception as e:
        log(f"[EXFIL] SSH upload failed: {e}")
        return False
