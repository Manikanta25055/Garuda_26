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
import types
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


class State:
    def __init__(self):
        self.modes = Modes()
        self.config = Config()


def forward(module, names: dict) -> None:
    """Make `module.<old name>` read and write `module.STATE.<group>.<field>`.

    `names` maps each old flat name to (group, field). May be called again for
    another group; the module keeps one class and gains the new properties.
    """
    cls = type(module)
    if cls is types.ModuleType:
        cls = type("_StateForwardingModule", (types.ModuleType,), {})
        module.__class__ = cls
    for old, (group, attr) in names.items():
        if old in vars(module):
            raise RuntimeError(f"{old} is still a global of {module.__name__}; remove it before forwarding")

        def fget(self, _g=group, _a=attr):
            return getattr(getattr(self.STATE, _g), _a)

        def fset(self, value, _g=group, _a=attr):
            setattr(getattr(self.STATE, _g), _a, value)

        setattr(cls, old, property(fget, fset))
