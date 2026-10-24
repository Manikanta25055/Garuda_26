"""Phone alerts: Web Push encryption, VAPID, subscriptions and the alert watcher."""
import json
import os
import struct
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'basic_pipelines'))
import Garuda_web as gw  # noqa: E402
from basic_pipelines.garuda_core import webpush  # noqa: E402
from basic_pipelines.garuda_routes import push  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402
from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # noqa: E402


class Phone:
    """What a browser does with a subscription: hold the keys and decrypt (RFC 8291)."""

    def __init__(self, endpoint="https://push.example.com/send/abc"):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = os.urandom(16)
        self.public = self.key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.sub = {"endpoint": endpoint,
                    "keys": {"p256dh": webpush.b64u(self.public), "auth": webpush.b64u(self.auth)}}

    def decrypt(self, body):
        salt, rs, idlen = body[:16], struct.unpack("!I", body[16:20])[0], body[20]
        as_public = body[21:21 + idlen]
        assert rs == 4096
        shared = self.key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), as_public))
        hk = lambda salt, ikm, info, n: HKDF(hashes.SHA256(), n, salt, info).derive(ikm)  # noqa: E731
        ikm = hk(self.auth, shared, b"WebPush: info\x00" + self.public + as_public, 32)
        cek = hk(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
        nonce = hk(salt, ikm, b"Content-Encoding: nonce\x00", 12)
        plain = AESGCM(cek).decrypt(nonce, body[21 + idlen:], None)
        assert plain.endswith(b"\x02")
        return plain[:-1]


def test_a_message_is_readable_only_with_the_phones_keys():
    phone = Phone()
    body = webpush.encrypt(b'{"title":"hi"}', phone.sub["keys"]["p256dh"], phone.sub["keys"]["auth"])
    assert phone.decrypt(body) == b'{"title":"hi"}'
    with pytest.raises(Exception):
        Phone().decrypt(body)


def test_the_vapid_token_is_signed_by_the_key_the_page_was_given(tmp_path):
    vapid = webpush.Vapid(str(tmp_path / "v.pem"), "mailto:a@b.c")
    auth = vapid.authorization("https://fcm.googleapis.com/fcm/send/xyz", now=1000)
    token, key = auth[len("vapid t="):].split(", k=")
    assert key == vapid.public_key()
    head, claims, sig = token.split(".")
    assert json.loads(webpush.unb64u(claims)) == {"aud": "https://fcm.googleapis.com",
                                                  "exp": 1000 + 12 * 3600, "sub": "mailto:a@b.c"}
    raw = webpush.unb64u(sig)
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), webpush.unb64u(key))
    pub.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
               f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    assert oct(os.stat(tmp_path / "v.pem").st_mode & 0o777) == "0o600"
    # The same key after a restart: phones subscribed to it keep working.
    assert webpush.Vapid(str(tmp_path / "v.pem")).public_key() == key


def test_a_camera_frame_fits_in_the_message():
    frame = np.random.randint(0, 255, (720, 1280, 3), np.uint8)
    image = push.thumbnail(frame)
    assert image is None or len(image) < 3400
    flat = np.full((720, 1280, 3), 90, np.uint8)
    assert push.thumbnail(flat).startswith("data:image/jpeg;base64,")


class Post:
    def __init__(self, status=201):
        self.status, self.calls = status, []

    def __call__(self, url, headers, data, timeout):
        self.calls.append((url, headers, data))
        return type("R", (), {"status_code": self.status})()


def test_subscribe_test_and_unsubscribe(app_client, user_headers, monkeypatch):
    phone = Phone()
    key = app_client.get('/api/push/key', headers=user_headers).json()['key']
    assert key == gw.PUSH_VAPID.public_key()
    r = app_client.post('/api/push/subscribe', json={'subscription': phone.sub, 'kinds': ['alert', 'tamper']},
                        headers=user_headers)
    assert r.status_code == 200 and r.json()['kinds'] == ['alert', 'tamper']
    post = Post()
    monkeypatch.setattr(push.requests, 'post', post)
    assert app_client.post('/api/push/test', headers=user_headers).json()['delivered'] == 1
    url, headers, data = post.calls[0]
    assert url == phone.sub['endpoint'] and headers['Content-Encoding'] == 'aes128gcm'
    msg = json.loads(phone.decrypt(data))
    assert msg['kind'] == 'test' and msg['url'] == '/#events'
    assert app_client.post('/api/push/unsubscribe', json={'endpoint': phone.sub['endpoint']},
                           headers=user_headers).json()['ok'] is True
    assert gw.PUSH.subs == {}


def test_a_subscription_must_look_like_one(app_client, user_headers):
    bad = {'endpoint': 'http://evil.example/', 'keys': {'p256dh': 'x', 'auth': 'y'}}
    assert app_client.post('/api/push/subscribe', json={'subscription': bad},
                           headers=user_headers).status_code == 400


def test_push_needs_a_sign_in(app_client):
    assert app_client.get('/api/push/key').status_code == 401
    assert app_client.post('/api/push/test').status_code == 401


def test_a_gone_phone_is_forgotten_and_kinds_are_respected(app_client):
    a, b = Phone("https://push.example.com/a"), Phone("https://push.example.com/b")
    gw.PUSH.add(a.sub, 'admin', ['alert'])
    gw.PUSH.add(b.sub, 'user', ['tamper'])
    post = Post(410)
    assert push.send(gw, gw.PUSH, gw.PUSH_VAPID, 'alert', 'knife seen', post=post) == 0
    assert [c[0] for c in post.calls] == [a.sub['endpoint']]
    assert list(gw.PUSH.subs) == [b.sub['endpoint']]


def test_silence_holds_pushes_for_half_an_hour(app_client, user_headers):
    gw.PUSH.add(Phone().sub, 'user', list(push.KINDS))
    assert app_client.post('/api/push/silence', headers=user_headers).status_code == 200
    post = Post()
    assert push.send(gw, gw.PUSH, gw.PUSH_VAPID, 'alert', 'x', post=post) == 0 and post.calls == []
    assert push.send(gw, gw.PUSH, gw.PUSH_VAPID, 'test', 'x', post=post) == 1   # a test still goes


class _Stop(Exception):
    pass


def test_the_watcher_pushes_once_when_an_alert_starts(app_client, monkeypatch):
    phone = Phone()
    gw.PUSH.add(phone.sub, 'admin', list(push.KINDS))
    sent = []
    monkeypatch.setattr(push, 'send', lambda core, store, vapid, kind, body, image=None: sent.append(kind))
    monkeypatch.setattr(push.threading, 'Thread',
                        lambda target, args, daemon: type('T', (), {'start': lambda s: target(*args)})())
    steps = iter([(False, False), (True, False), (True, False), (False, True), (False, True)])

    def tick(_):
        try:
            gw.STATE.alerts.active, gw.STATE.alerts.night_presence_active = next(steps)
        except StopIteration:
            raise _Stop
    monkeypatch.setattr(gw.STATE.alerts, 'active', False)
    monkeypatch.setattr(gw.STATE.alerts, 'night_presence_active', False)
    monkeypatch.setattr(gw.STATE.alerts, 'blind_alert_sent', False)
    with pytest.raises(_Stop):
        push.watch_alerts(gw, gw.PUSH, gw.PUSH_VAPID, sleep=tick)
    assert sent == ['alert', 'presence']
