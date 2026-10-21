"""Fixes for the security-scan findings: one test (or a few) per fix."""
import ssl
import sys
import types
from unittest.mock import MagicMock

import pytest

import conftest  # noqa: F401
import Garuda_web as gw
from basic_pipelines.garuda_core import evidence, mailer


# ── mail: the server certificate is checked ──────────────────────────────────

def test_mail_uses_a_verifying_tls_context(monkeypatch):
    smtp = MagicMock()
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", smtp)
    mailer.send("s", "b", sender="a@x", password="p", to="b@x")
    ctx = smtp.call_args.kwargs["context"]
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


# ── evidence upload: an unknown SSH host key is refused ──────────────────────

def test_sftp_refuses_unknown_host_keys(monkeypatch, tmp_path):
    client = MagicMock()
    fake = types.SimpleNamespace(SSHClient=MagicMock(return_value=client),
                                 RejectPolicy=MagicMock(name="Reject"),
                                 AutoAddPolicy=MagicMock(name="AutoAdd"))
    monkeypatch.setitem(sys.modules, "paramiko", fake)
    known = tmp_path / "known_hosts"
    known.write_text("")
    evidence.sftp_upload(str(known), "f", host="h", port=22, user="u", key_path="",
                         password="p", remote_dir="d", log=lambda m: None,
                         known_hosts=str(known))
    client.load_system_host_keys.assert_called_once()
    client.load_host_keys.assert_called_once_with(str(known))
    client.set_missing_host_key_policy.assert_called_once_with(fake.RejectPolicy())
    fake.AutoAddPolicy.assert_not_called()
