"""Phone alerts: Web Push to each phone that turned them on, with a picture.

Owners say e-mail alerts arrive minutes late; push arrives in seconds. Each
phone subscribes from Settings › Notifications. A watcher looks at the alert
state twice a second and pushes on the moment an alert starts, so the
detection code did not change:

    alert        a danger object (the e-mail alert's own trigger)
    presence     a person in the night window, or anywhere while armed Away
    tamper       the camera covered or blinded

The picture is a small JPEG inside the encrypted message (the push service
cannot read it), so the phone shows it without reaching back to the Pi.
"Silence 30 min" on the notification stops pushes for half an hour.
"""
import asyncio
import ipaddress
import os
import threading
import time
import urllib.parse

import cv2
import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

try:
    from ..garuda_core import webpush
    from ..garuda_auto import jsonfile
except ImportError:                                # script mode (python Garuda_web.py)
    from basic_pipelines.garuda_core import webpush
    from basic_pipelines.garuda_auto import jsonfile

KINDS = ("alert", "presence", "tamper")
TITLES = {"alert": "Danger detected", "presence": "Person seen", "tamper": "Camera covered"}
THUMB_BYTES = 2400                 # leaves room in the 4 KB message for the words
SILENCE_S = 30 * 60
MAX_SUBS = 100                     # phones in all
MAX_SUBS_PER_USER = 10
_INSIDE_NAMES = (".local", ".lan", ".internal", ".home", ".arpa", ".localhost", ".localdomain")


def endpoint_ok(url):
    """True for an address a browser's push service could have: https, a public
    host name, the standard port.

    The Pi sends a request to this address on every alert. Any signed-in
    person could subscribe with one that pointed at the router or another
    machine in the house, and the Pi would post to it from inside the network.
    """
    if not isinstance(url, str) or len(url) > 1024:
        return False
    try:
        parts = urllib.parse.urlsplit(url)
        host, port = (parts.hostname or "").lower(), parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or not host or parts.username or parts.password:
        return False
    if port not in (None, 443):
        return False
    try:
        ipaddress.ip_address(host)
        return False                               # a bare address, never a push service
    except ValueError:
        pass
    return "." in host and host != "localhost" and not host.endswith(_INSIDE_NAMES)


def _usable(entry):
    """True for a saved subscription send() can read without raising."""
    sub = entry.get("sub") if isinstance(entry, dict) else None
    return (isinstance(sub, dict) and endpoint_ok(sub.get("endpoint"))
            and isinstance(sub.get("keys"), dict) and isinstance(entry.get("kinds", []), list))


class PushStore:
    """Subscriptions by endpoint, who made each, and which kinds it wants."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.subs = {}
        self.silenced_until = 0.0
        saved = jsonfile.load(path, {}).get("subs")
        if isinstance(saved, dict):
            self.subs = {k: v for k, v in saved.items() if _usable(v)}

    def _save(self):
        jsonfile.save(self.path, {"subs": self.subs})      # the temp file is made 0600
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def add(self, sub, username, kinds):
        """Keep one phone's subscription. Returns "" or why it was refused."""
        with self.lock:
            if sub["endpoint"] not in self.subs:
                if len(self.subs) >= MAX_SUBS:
                    return "Too many phones are subscribed already."
                if sum(1 for v in self.subs.values() if v.get("user") == username) >= MAX_SUBS_PER_USER:
                    return "You have too many phones subscribed; turn one off first."
            self.subs[sub["endpoint"]] = {"sub": sub, "user": username, "kinds": list(kinds),
                                          "added": time.time()}
            self._save()
        return ""

    def remove(self, endpoint):
        with self.lock:
            gone = self.subs.pop(endpoint, None) is not None
            if gone:
                self._save()
            return gone

    def targets(self, kind):
        """Every phone that wants `kind`; a test goes to all of them."""
        with self.lock:
            return [dict(v) for v in self.subs.values()
                    if kind == "test" or kind in v.get("kinds", KINDS)]


def thumbnail(frame):
    """A JPEG data URL small enough for the message, or None."""
    if frame is None:
        return None
    h, w = frame.shape[:2]
    for width, quality in ((240, 55), (200, 45), (160, 40), (120, 35)):
        small = cv2.resize(frame, (width, max(1, int(h * width / w))))
        ok, jpeg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if ok and len(jpeg) <= THUMB_BYTES:
            return "data:image/jpeg;base64," + webpush.base64.b64encode(jpeg.tobytes()).decode()
    return None


def send(core, store, vapid, kind, body, image=None, post=None):
    """Push one message to every phone that wants `kind`. Returns how many took it."""
    post = post or requests.post
    if not webpush.AVAILABLE:
        return 0
    if kind != "test" and time.time() < store.silenced_until:
        return 0
    message = {"title": TITLES.get(kind, "Garuda"), "body": body, "kind": kind,
               "tag": f"garuda-{kind}", "url": "/#events", "ts": int(time.time())}
    if image:
        message["image"] = image
    delivered = 0
    for entry in store.targets(kind):
        try:
            try:
                url, headers, data = webpush.request(vapid, entry["sub"], message)
            except ValueError:                     # picture made it too big: send the words
                message.pop("image", None)
                url, headers, data = webpush.request(vapid, entry["sub"], message)
            r = post(url, headers=headers, data=data, timeout=10)
            if r.status_code in (404, 410):        # the phone unsubscribed or the browser was reset
                store.remove(entry["sub"]["endpoint"])
            elif r.status_code < 300:
                delivered += 1
            else:
                core.log_system_update(f"[PUSH] {r.status_code} from push service for {entry['user']}: "
                                       f"{getattr(r, 'text', '')[:120]}")
        except Exception as exc:
            core.log_system_update(f"[PUSH] send failed: {type(exc).__name__}")
    return delivered


def _snapshot(core):
    with core.STATE.camera.frame_lock:
        raw = core.STATE.camera.frame_raw
    return thumbnail(raw) if raw is not None else None


def watch_alerts(core, store, vapid, every=0.5, sleep=time.sleep):
    """Push on the moment each kind of alert starts."""
    was = {k: False for k in KINDS}
    while True:
        a = core.STATE.alerts
        now = {"alert": bool(a.active), "presence": bool(a.night_presence_active),
               "tamper": bool(a.blind_alert_sent)}
        for kind in KINDS:
            if now[kind] and not was[kind] and store.subs:
                if kind == "alert":
                    body = f"{a.danger_trigger_info or ', '.join(core.STATE.config.danger_labels)} seen. Tap to see it."
                elif kind == "presence":
                    body = "A person is on camera while the house is armed. Tap to see it."
                else:
                    body = "The camera looks covered or blocked."
                image = _snapshot(core) if kind != "tamper" else None
                threading.Thread(target=send, args=(core, store, vapid, kind, body, image),
                                 daemon=True).start()
        was = now
        sleep(every)


class SubscribeRequest(BaseModel):
    subscription: dict
    kinds: list = list(KINDS)

class EndpointRequest(BaseModel):
    endpoint: str


def build_push_router(core, store, vapid):
    router = APIRouter()

    def _ready():
        if not webpush.AVAILABLE:
            raise HTTPException(503, "Phone alerts need the Python 'cryptography' package on the Pi.")

    @router.get("/api/push/key")
    async def push_key(session=Depends(core.require_session)):
        _ready()
        return {"key": vapid.public_key(), "silenced_until": store.silenced_until}

    @router.post("/api/push/subscribe")
    async def subscribe(data: SubscribeRequest, session=Depends(core.require_session)):
        _ready()
        sub = data.subscription
        keys = sub.get("keys")
        keys = keys if isinstance(keys, dict) else {}
        p256dh, auth = keys.get("p256dh"), keys.get("auth")
        if (not endpoint_ok(sub.get("endpoint"))
                or not isinstance(p256dh, str) or not 0 < len(p256dh) <= 256
                or not isinstance(auth, str) or not 0 < len(auth) <= 128):
            raise HTTPException(400, "That is not a push subscription.")
        kinds = [k for k in data.kinds if isinstance(k, str) and k in KINDS] or list(KINDS)
        refused = store.add({"endpoint": sub["endpoint"], "keys": {"p256dh": p256dh, "auth": auth}},
                            session["username"], kinds)
        if refused:
            raise HTTPException(400, refused)
        core.log_system_update(f"[PUSH] phone alerts on for {session['username']}")
        return {"ok": True, "kinds": kinds}

    @router.post("/api/push/unsubscribe")
    async def unsubscribe(data: EndpointRequest, session=Depends(core.require_session)):
        return {"ok": store.remove(data.endpoint)}

    @router.post("/api/push/test")
    async def test_push(session=Depends(core.require_session)):
        _ready()
        image = _snapshot(core)
        n = await asyncio.to_thread(
            send, core, store, vapid, "test", "Phone alerts work. This is what an alert looks like.", image)
        return {"ok": n > 0, "delivered": n}

    @router.post("/api/push/silence")
    async def silence(session=Depends(core.require_session)):
        store.silenced_until = time.time() + SILENCE_S
        core.log_system_update(f"[PUSH] phone alerts silenced for 30 min by {session['username']}")
        return {"ok": True, "silenced_until": store.silenced_until}

    return router
