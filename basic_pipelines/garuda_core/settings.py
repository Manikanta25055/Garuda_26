"""Every environment variable the service reads, in one typed place.

Reading os.environ wherever a value is needed has two costs: nobody can say
what the full list is, and a typo or a half-filled .env shows up later as a
feature that quietly does nothing. `Settings.load()` reads them once and
`problems()` says, at start-up and on the diagnostics page, what is missing or
malformed and what that will cost.
"""
import os
import re
from dataclasses import dataclass, field

_TRUE = ("1", "true", "yes", "on")


def _flag(name, default="0"):
    return os.environ.get(name, default).strip().lower() in _TRUE


def _int(name, default):
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _list(name):
    return [v.strip() for v in os.environ.get(name, "").split(",") if v.strip()]


@dataclass(frozen=True)
class Settings:
    # Web
    host: str = "127.0.0.1"
    port: int = 8080
    secure_cookies: bool = False
    vercel_project: str = "garuda-26"
    security_hosts: tuple = ("garuda.veeramanikanta.in",)
    log_level: str = "INFO"
    # Mail
    email_sender: str = ""
    email_password_set: bool = False
    email_recipients: tuple = ()
    # AI
    nim_key_set: bool = False
    nim_model: str = ""
    elevenlabs_key_set: bool = False
    elevenlabs_engine_set: bool = False
    # Safety nets
    master_key_bootstrap_set: bool = False
    heartbeat_key_set: bool = False
    deadman_enabled: bool = False
    # Evaluation harness (must be off in normal service)
    eval_token_set: bool = False
    eval_otp_bypass: bool = False
    # Evidence upload
    exfil_host: str = ""
    exfil_key_len: int = 0
    # Housekeeping
    backup_keep: int = 14
    extra: dict = field(default_factory=dict)

    @classmethod
    def load(cls):
        return cls(
            host=os.environ.get("GARUDA_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=_int("GARUDA_PORT", 8080),
            secure_cookies=_flag("SECURE_COOKIES"),
            vercel_project=os.environ.get("GARUDA_VERCEL_PROJECT", "garuda-26").strip() or "garuda-26",
            security_hosts=tuple(_list("GARUDA_SECURITY_HOSTS") or ["garuda.veeramanikanta.in"]),
            log_level=(os.environ.get("GARUDA_LOG_LEVEL", "INFO").strip().upper() or "INFO"),
            email_sender=os.environ.get("EMAIL_SENDER", "").strip(),
            email_password_set=bool(os.environ.get("EMAIL_SENDER_PASS", "").strip()),
            email_recipients=tuple(_list("EMAIL_RECIPIENTS")),
            nim_key_set=bool(os.environ.get("NIM_API_KEY", "").strip()),
            nim_model=os.environ.get("NIM_MODEL", "").strip(),
            elevenlabs_key_set=bool(os.environ.get("ELEVENLABS_API_KEY", "").strip()),
            elevenlabs_engine_set=bool(os.environ.get("ELEVENLABS_SPEECH_ENGINE_ID", "").strip()),
            master_key_bootstrap_set=bool(os.environ.get("MASTER_KEY", "").strip()),
            heartbeat_key_set=bool(os.environ.get("HEARTBEAT_KEY", "").strip()),
            deadman_enabled=os.environ.get("DEADMAN_ENABLED", "0") == "1",
            eval_token_set=bool(os.environ.get("GARUDA_EVAL_TOKEN", "").strip()),
            eval_otp_bypass=os.environ.get("GARUDA_EVAL_OTP_BYPASS", "") == "1",
            exfil_host=os.environ.get("EXFIL_HOST", "").strip(),
            exfil_key_len=len(os.environ.get("EXFIL_AES_KEY", "").strip()),
            backup_keep=max(1, _int("GARUDA_BACKUP_KEEP", 14)),
        )

    def problems(self):
        """[(severity, message)] with severity 'error' | 'warning' | 'info'.

        Never includes a secret's value, only whether it is set.
        """
        out = []
        mail = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
        if not self.email_sender or not self.email_password_set:
            out.append(("warning", "Alert email is not configured (EMAIL_SENDER / EMAIL_SENDER_PASS): "
                                   "no alert, OTP or tamper email can be sent, so admin sign-in by OTP will fail."))
        elif not mail.match(self.email_sender):
            out.append(("error", "EMAIL_SENDER is not an email address."))
        if not self.email_recipients:
            out.append(("warning", "EMAIL_RECIPIENTS is empty: alerts have nowhere to go."))
        for addr in self.email_recipients:
            if not mail.match(addr):
                out.append(("error", f"EMAIL_RECIPIENTS has an invalid address: {addr}"))
        if not self.secure_cookies:
            out.append(("info", "SECURE_COOKIES is off; cookies are still marked Secure when the "
                                "proxy reports HTTPS."))
        if not self.nim_key_set:
            out.append(("warning", "NIM_API_KEY is not set: Narada cannot act or answer."))
        if self.elevenlabs_key_set != self.elevenlabs_engine_set:
            out.append(("warning", "Narada voice is half configured: both ELEVENLABS_API_KEY and "
                                   "ELEVENLABS_SPEECH_ENGINE_ID are needed (scripts/setup_narada_voice.py)."))
        if self.eval_otp_bypass:
            out.append(("error", "GARUDA_EVAL_OTP_BYPASS=1: an admin can sign in without the email "
                                 "code. This is for the test harness only; remove it."))
        if self.eval_token_set:
            out.append(("warning", "GARUDA_EVAL_TOKEN is set: the /api/eval endpoints are live and "
                                   "skip the rate limit for whoever holds the token."))
        if self.exfil_host and self.exfil_key_len != 64:
            out.append(("error", "EXFIL_HOST is set but EXFIL_AES_KEY is not 64 hex characters: "
                                 "clips will not be uploaded."))
        if self.deadman_enabled and not self.heartbeat_key_set:
            out.append(("warning", "DEADMAN_ENABLED=1 without HEARTBEAT_KEY: anyone who can reach "
                                   "/api/heartbeat can keep the dead-man switch quiet."))
        if self.host not in ("127.0.0.1", "localhost", "::1"):
            out.append(("warning", f"Listening on {self.host}: the plain-HTTP port is reachable "
                                   "without the tunnel."))
        return out

    def public(self):
        """What the diagnostics page may show: no secret values."""
        return {k: (list(v) if isinstance(v, tuple) else v)
                for k, v in self.__dict__.items() if k != "extra"}
