"""The live state of the service, grouped by concern.

Garuda_web holds one `STATE`; every other module reaches it as
`core.STATE.<group>.<field>`. The flat names the service grew up with
(`MODE_DND`, ...) still work on the Garuda_web module, for the tests and for
anything not yet moved over: forward() turns each one into a property that
reads and writes the field here, so there is one value, not two.

A forwarded name must not also exist in the module's own globals. Code inside
Garuda_web therefore uses STATE directly: a bare `MODE_DND` there is a
NameError, and `global MODE_DND` / `globals()[...]` would write a private copy
nothing else reads.
"""
import os
import threading
import time
import types
from collections import defaultdict
from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class Modes:
    """The six mode switches, their schedule and the user-defined modes."""
    dnd: bool = False
    email_off: bool = False
    idle: bool = False
    night: bool = False
    emergency: bool = False
    privacy: bool = True
    schedule: dict = field(default_factory=dict)   # {"night": {"start": "22:00", "end": "06:00"}, ...}
    custom: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    FLAGS: ClassVar[tuple] = ("dnd", "email_off", "idle", "night", "emergency", "privacy")

    def get(self, name: str) -> bool:
        if name not in self.FLAGS:
            raise KeyError(f"unknown mode: {name!r}")
        return getattr(self, name)

    def set(self, name: str, value) -> None:
        """Set one switch by name. The caller holds `lock` when it needs to."""
        if name not in self.FLAGS:
            raise KeyError(f"unknown mode: {name!r}")
        setattr(self, name, value)


def _env_list(name: str, default: str) -> list:
    return [r.strip() for r in os.environ.get(name, default).split(",") if r.strip()]


@dataclass
class Config:
    """What the owner sets: config.json holds all of it except the sender's
    password, which stays in .env and is never written to disk as JSON."""
    # Email: secrets from .env, the rest overridable through config.json.
    email_sender: str = field(default_factory=lambda: os.environ.get("EMAIL_SENDER", ""))
    email_sender_pass: str = field(default_factory=lambda: os.environ.get("EMAIL_SENDER_PASS", ""), repr=False)
    email_recipients: list = field(default_factory=lambda: _env_list("EMAIL_RECIPIENTS", "amarmanikantan@gmail.com"))
    email_cooldown: int = 60
    detection_threshold: float = 0.3
    danger_labels: list = field(default_factory=lambda: ["Knife", "scissors", "Hammer"])   # all non-person model outputs
    watch_labels: list = field(default_factory=lambda: ["Person", "person"])              # human: log silently, no alert
    known_devices: list = field(default_factory=list)                                     # [{name, mac}]
    # Yellow alarm when a person is seen in the dead hours.
    night_presence_window: dict = field(default_factory=lambda: {"start": "01:30", "end": "05:00", "enabled": True})
    custom_voice_commands: dict = field(default_factory=dict)


@dataclass
class Alerts:
    """The danger alert, the night-presence alarm, camera tamper and their anti-spam clocks."""
    active: bool = False
    end_time: float = 0.0   # epoch when current alert expires (3s visual banner)
    danger_trigger_info: str = ""   # detection text snapshot that fired the alert
    last_danger_conf: float = 0.0   # confidence of last danger detection (for logging)
    danger_active: bool = False   # True while danger label is continuously detected
    last_alert_time: object = None
    history: dict = field(default_factory=dict)   # {ISO-date: alert_count} — persisted to disk
    lock: object = field(default_factory=threading.Lock, repr=False, compare=False)   # guards active, end_time and danger_trigger_info
    last_email_sent_time: int = 0
    email_lock: object = field(default_factory=threading.Lock, repr=False, compare=False)
    last_tamper_email: float = 0.0   # last camera-tamper email time (anti-spam)
    night_presence_active: bool = False
    night_presence_end_time: float = 0.0
    night_presence_last_check: float = 0.0
    night_presence_lock: object = field(default_factory=threading.Lock, repr=False, compare=False)
    blind_frame_count: int = 0
    blind_alert_sent: bool = False


@dataclass
class Presence:
    """Whether the owner's phone is on the network, and the record of arrivals and departures."""
    owner_present: bool = False
    owner_last_seen: float = 0.0
    last_arp_cache: str = ""   # last raw ARP table read (refreshed by _presence_poller)
    log: list = field(default_factory=list)   # [{ts, event, device, mac}] — permanent presence record


@dataclass
class System:
    """The service's own health: connectivity, the dead man's switch, smoothed load figures, the microphone and the event loop."""
    net_online: bool = True   # tracked by connectivity monitor
    last_heartbeat: object = field(default_factory=time.time)   # updated by GET /api/heartbeat
    heartbeat_ever: bool = False   # True only after a real heartbeat is received
    deadman_alert_sent: bool = False
    deadman_last_alert: float = 0.0
    cpu_ema: float = 0.0
    ram_ema: float = 0.0
    temp_ema: float = 0.0
    cpu_cores_ema: list = field(default_factory=list)   # per-core EMA values (populated on first psutil call)
    voice_mic_ok: object = None
    voice_mic_detail: str = ""
    event_loop: object = None   # asyncio loop ref (set in lifespan)
    ws_trigger: object = None   # asyncio.Event — set to push WS immediately
    ws_broadcaster_task: object = None


@dataclass
class Camera:
    """The latest frame, the detection counters, the clip being recorded and the running pipeline."""
    frame_buffer: object = None
    frame_raw: object = None   # raw numpy BGR for WebRTC track
    frame_seq: int = 0   # incremented every new frame; lets MJPEG clients skip duplicates
    frame_ts: float = 0.0   # wall clock of the last frame; the only liveness signal we have
    frame_lock: object = field(default_factory=threading.Lock, repr=False, compare=False)
    total_frames: int = 0   # total inference frames (for avg FPS)
    detections_today: int = 0
    latest_detection_info: str = ""
    class_counts_today: dict = field(default_factory=dict)   # class_name → count since startup
    watch_last_logged: dict = field(default_factory=dict)   # label → last log timestamp (30s cooldown)
    label_consec_frames: dict = field(default_factory=dict)   # label → consecutive frames seen above threshold
    drishti_last_observe: float = 0.0
    clip_writer: object = None
    clip_lock: object = field(default_factory=threading.Lock, repr=False, compare=False)
    clip_start_time: float = 0.0
    clip_path: str = ""
    app_gst: object = None   # GStreamer app instance


@dataclass
class Auth:
    """Accounts, master keys, sign-in sessions, refresh tokens, one-time codes and the limits that guard them."""
    users: dict = field(default_factory=dict)   # populated from users.json at startup; no hardcoded defaults
    master_keys: list = field(default_factory=list)   # loaded from MASTER_KEYS_FILE at startup
    sessions: dict = field(default_factory=dict)
    refresh_tokens: dict = field(default_factory=dict)
    persisted_refresh: dict = field(default_factory=dict)   # sha256(token) → record, loaded at start-up
    refresh_dirty: bool = False
    login_failures: dict = field(default_factory=dict)   # IP → {"count": int, "lockout_until": float}
    rate_store: dict = field(default_factory=lambda: defaultdict(list))   # IP → [timestamps]
    admin_otp: object = None
    admin_otp_user: str | None = None   # server-side stored username for OTP step 2
    admin_otp_ts: float = 0   # epoch when admin OTP was generated
    admin_otp_attempts: int = 0   # failed verify attempts; cleared on success or expiry
    forgot_otp_store: dict = field(default_factory=dict)   # username → {otp, ts, attempts}  (per-user, no race condition)
    user_forgot_otp: str | None = None   # test-facing alias: last generated forgot OTP string
    forgot_otp_user: str | None = None
    forgot_otp_ts: float = 0.0
    forgot_otp_attempts: int = 0
    master_key_otp: str | None = None
    master_otp_ts: float = 0.0
    master_otp_attempts: int = 0


class State:
    def __init__(self):
        self.modes = Modes()
        self.config = Config()
        self.alerts = Alerts()
        self.presence = Presence()
        self.system = System()
        self.camera = Camera()
        self.auth = Auth()


# The flat names Garuda_web grew up with, and where each one lives now. Garuda_web
# forwards them (see forward() below) so the test suite, which patches these
# names, and the state object are one value.
FLAT_NAMES = {
    # modes
    "MODE_DND": ("modes", "dnd"), "MODE_EMAIL_OFF": ("modes", "email_off"),
    "MODE_IDLE": ("modes", "idle"), "MODE_NIGHT": ("modes", "night"),
    "MODE_EMERGENCY": ("modes", "emergency"), "MODE_PRIVACY": ("modes", "privacy"),
    "MODE_SCHEDULE": ("modes", "schedule"), "CUSTOM_MODES": ("modes", "custom"),
    "_mode_lock": ("modes", "lock"),
    # config
    "EMAIL_SENDER": ("config", "email_sender"), "EMAIL_SENDER_PASS": ("config", "email_sender_pass"),
    "EMAIL_RECIPIENTS": ("config", "email_recipients"), "EMAIL_COOLDOWN": ("config", "email_cooldown"),
    "DETECTION_THRESHOLD": ("config", "detection_threshold"),
    "DANGER_LABELS": ("config", "danger_labels"), "WATCH_LABELS": ("config", "watch_labels"),
    "KNOWN_DEVICES": ("config", "known_devices"),
    "NIGHT_PRESENCE_WINDOW": ("config", "night_presence_window"),
    "CUSTOM_VOICE_COMMANDS": ("config", "custom_voice_commands"),
    # alerts
    "_alert_active": ("alerts", "active"), "_alert_end_time": ("alerts", "end_time"),
    "_danger_trigger_info": ("alerts", "danger_trigger_info"),
    "_last_danger_conf": ("alerts", "last_danger_conf"), "_danger_active": ("alerts", "danger_active"),
    "_last_alert_time": ("alerts", "last_alert_time"), "_alert_history": ("alerts", "history"),
    "_alert_lock": ("alerts", "lock"), "last_email_sent_time": ("alerts", "last_email_sent_time"),
    "_email_lock": ("alerts", "email_lock"), "_last_tamper_email": ("alerts", "last_tamper_email"),
    "_night_presence_alert_active": ("alerts", "night_presence_active"),
    "_night_presence_alert_end_time": ("alerts", "night_presence_end_time"),
    "_np_last_check": ("alerts", "night_presence_last_check"),
    "_np_lock": ("alerts", "night_presence_lock"),
    "_blind_frame_count": ("alerts", "blind_frame_count"),
    "_blind_alert_sent": ("alerts", "blind_alert_sent"),
    # presence
    "_owner_present": ("presence", "owner_present"),
    "_owner_last_seen": ("presence", "owner_last_seen"),
    "_last_arp_cache": ("presence", "last_arp_cache"), "_presence_log": ("presence", "log"),
    # system
    "_net_online": ("system", "net_online"), "_last_heartbeat": ("system", "last_heartbeat"),
    "_heartbeat_ever": ("system", "heartbeat_ever"),
    "_deadman_alert_sent": ("system", "deadman_alert_sent"),
    "_deadman_last_alert": ("system", "deadman_last_alert"), "_cpu_ema": ("system", "cpu_ema"),
    "_ram_ema": ("system", "ram_ema"), "_temp_ema": ("system", "temp_ema"),
    "_cpu_cores_ema": ("system", "cpu_cores_ema"), "_voice_mic_ok": ("system", "voice_mic_ok"),
    "_voice_mic_detail": ("system", "voice_mic_detail"), "_event_loop": ("system", "event_loop"),
    "_ws_trigger": ("system", "ws_trigger"), "_ws_broadcaster_task": ("system", "ws_broadcaster_task"),
    # camera
    "_frame_buffer": ("camera", "frame_buffer"), "_frame_raw": ("camera", "frame_raw"),
    "_frame_seq": ("camera", "frame_seq"), "_frame_ts": ("camera", "frame_ts"),
    "_frame_lock": ("camera", "frame_lock"), "_total_frames": ("camera", "total_frames"),
    "_detections_today": ("camera", "detections_today"),
    "latest_detection_info": ("camera", "latest_detection_info"),
    "_class_counts_today": ("camera", "class_counts_today"),
    "_watch_last_logged": ("camera", "watch_last_logged"),
    "_label_consec_frames": ("camera", "label_consec_frames"),
    "_drishti_last_observe": ("camera", "drishti_last_observe"),
    "_clip_writer": ("camera", "clip_writer"), "_clip_lock": ("camera", "clip_lock"),
    "_clip_start_time": ("camera", "clip_start_time"), "_clip_path": ("camera", "clip_path"),
    "app_gst": ("camera", "app_gst"),
    # auth
    "USERS": ("auth", "users"), "MASTER_KEYS": ("auth", "master_keys"),
    "_sessions": ("auth", "sessions"), "_refresh_tokens": ("auth", "refresh_tokens"),
    "_persisted_refresh": ("auth", "persisted_refresh"), "_refresh_dirty": ("auth", "refresh_dirty"),
    "_login_failures": ("auth", "login_failures"), "_rate_store": ("auth", "rate_store"),
    "ADMIN_OTP": ("auth", "admin_otp"), "_admin_otp_user": ("auth", "admin_otp_user"),
    "_admin_otp_ts": ("auth", "admin_otp_ts"), "_admin_otp_attempts": ("auth", "admin_otp_attempts"),
    "_forgot_otp_store": ("auth", "forgot_otp_store"), "USER_FORGOT_OTP": ("auth", "user_forgot_otp"),
    "_forgot_otp_user": ("auth", "forgot_otp_user"), "_forgot_otp_ts": ("auth", "forgot_otp_ts"),
    "_forgot_otp_attempts": ("auth", "forgot_otp_attempts"),
    "MASTER_KEY_OTP": ("auth", "master_key_otp"), "_master_otp_ts": ("auth", "master_otp_ts"),
    "_master_otp_attempts": ("auth", "master_otp_attempts"),
}


def forward(module, names: dict) -> None:
    """Make `module.<old name>` read and write `module.STATE.<group>.<field>`.

    `names` maps each old flat name to (group, field). May be called again for
    another group; the module keeps one class and gains the new properties.
    """
    cls = type(module)
    if cls is types.ModuleType:
        cls = type("_StateForwardingModule", (types.ModuleType,), {"_forwards": {}})
        module.__class__ = cls
    for old, (group, attr) in names.items():
        if old in vars(module):
            raise RuntimeError(f"{old} is still a global of {module.__name__}; remove it before forwarding")

        def fget(self, _g=group, _a=attr):
            return getattr(getattr(self.STATE, _g), _a)

        def fset(self, value, _g=group, _a=attr):
            setattr(getattr(self.STATE, _g), _a, value)

        setattr(cls, old, property(fget, fset))
        cls._forwards[old] = (group, attr)
