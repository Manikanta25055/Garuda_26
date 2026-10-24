"""Web Push without a vendor SDK: VAPID signing (RFC 8292) and aes128gcm
message encryption (RFC 8291), on the `cryptography` package.

The phone's browser gives the page a subscription: an endpoint on its push
service (Google, Apple, Mozilla) and two keys. The message is encrypted to
those keys, so the push service carries it without being able to read it,
pictures included.

Pure functions and one small key holder; sending is the caller's (one POST
with the headers `request()` returns).
"""
import base64
import json
import os
import struct
import time
from urllib.parse import urlsplit

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    AVAILABLE = True
except ImportError:                       # optional on the Pi: push is then off
    AVAILABLE = False

RECORD_SIZE = 4096


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hkdf(salt, ikm, info, length):
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def _raw_public(key) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.X962,
                                         serialization.PublicFormat.UncompressedPoint)


def encrypt(payload: bytes, p256dh: str, auth: str, *, salt=None, private_key=None) -> bytes:
    """The aes128gcm body for one subscription (RFC 8291, one record)."""
    ua_public = unb64u(p256dh)
    auth_secret = unb64u(auth)
    as_key = private_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _raw_public(as_key)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = as_key.exchange(ec.ECDH(), ua_key)
    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    if len(payload) > RECORD_SIZE - 17 - 86:
        raise ValueError("push payload too large")
    cipher = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    header = salt + struct.pack("!I", RECORD_SIZE) + bytes([len(as_public)]) + as_public
    return header + cipher


class Vapid:
    """The server's signing key, made once and kept in a 0600 file."""

    def __init__(self, path, subject="mailto:admin@localhost"):
        self.path, self.subject = path, subject
        self._key = None

    @property
    def key(self):
        if self._key is None:
            try:
                with open(self.path, "rb") as f:
                    self._key = serialization.load_pem_private_key(f.read(), password=None)
            except (OSError, ValueError):
                self._key = ec.generate_private_key(ec.SECP256R1())
                pem = self._key.private_bytes(serialization.Encoding.PEM,
                                              serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption())
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(pem)
        return self._key

    def public_key(self) -> str:
        """What the page passes to pushManager.subscribe as applicationServerKey."""
        return b64u(_raw_public(self.key))

    def authorization(self, endpoint, now=None) -> str:
        parts = urlsplit(endpoint)
        claims = {"aud": f"{parts.scheme}://{parts.netloc}",
                  "exp": int((now or time.time()) + 12 * 3600), "sub": self.subject}
        signing_input = (b64u(json.dumps({"typ": "JWT", "alg": "ES256"}).encode()) + "."
                         + b64u(json.dumps(claims, separators=(",", ":")).encode()))
        der = self.key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        jwt = signing_input + "." + b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        return f"vapid t={jwt}, k={self.public_key()}"


def request(vapid, subscription, message: dict, ttl=300, urgency="high"):
    """(url, headers, body) for one push of `message` (JSON) to `subscription`."""
    keys = subscription.get("keys") or {}
    body = encrypt(json.dumps(message, separators=(",", ":")).encode(), keys["p256dh"], keys["auth"])
    headers = {"Authorization": vapid.authorization(subscription["endpoint"]),
               "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
               "TTL": str(ttl), "Urgency": urgency}
    return subscription["endpoint"], headers, body
