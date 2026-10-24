"""The optional AWS add-ons (garuda_core/cloud.py, scripts/aws_agent.py).

No test reaches AWS or needs boto3: cloud.reset() hands in fake clients.
"""
import json
import os
import sys
import time
from unittest.mock import MagicMock

import pytest

import conftest  # noqa: F401
import Garuda_web as gw
from basic_pipelines.garuda_core import cloud, mailer
from basic_pipelines.garuda_core.settings import Settings

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import aws_agent  # noqa: E402

AWS_VARS = ("DRISHTI_AWS_REGION", "DRISHTI_S3_BUCKET", "DRISHTI_SNS_TOPIC_ARN",
            "DRISHTI_CW_NAMESPACE", "GARUDA_MAIL_BACKEND", "DRISHTI_BACKUP_CAPTURES")
TOPIC = "arn:aws:sns:ap-south-1:123456789012:drishti-alerts"


@pytest.fixture(autouse=True)
def fake_aws(monkeypatch):
    """Every AWS variable unset, and clients that record their calls."""
    for name in AWS_VARS:
        monkeypatch.delenv(name, raising=False)
    made = {}

    def factory(service, region):
        made.setdefault(service, MagicMock(name=f"{service}@{region}"))
        return made[service]

    cloud.reset(factory)
    yield made
    cloud.reset()


def _on(monkeypatch, **extra):
    monkeypatch.setenv("DRISHTI_AWS_REGION", "ap-south-1")
    for k, v in extra.items():
        monkeypatch.setenv(k, v)


# ── off by default ───────────────────────────────────────────────────────────

def test_everything_is_off_without_a_region(fake_aws):
    assert not cloud.enabled() and not cloud.sns_enabled()
    assert cloud.publish_alert("s", "b") is False
    assert cloud.publish_alert_async("s", "b") is None
    with pytest.raises(cloud.CloudOff):
        cloud.put_metrics({"Heartbeat": 1})
    assert fake_aws == {}


def test_mail_stays_on_smtp_by_default(monkeypatch, fake_aws):
    smtp = MagicMock()
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", smtp)
    mailer.send("s", "b", sender="a@x.in", password="p", to="b@x.in")
    smtp.assert_called_once()
    assert "ses" not in fake_aws


# ── SES ──────────────────────────────────────────────────────────────────────

def test_ses_sends_the_built_message(monkeypatch, fake_aws):
    _on(monkeypatch, GARUDA_MAIL_BACKEND="ses")
    smtp = MagicMock()
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", smtp)
    mailer.send("Alert", "body", sender="pi@x.in", password="", to=["a@x.in", "b@x.in"],
                attachments=[("a.jpg", "image/jpeg", b"\xff\xd8")])
    call = fake_aws["ses"].send_raw_email.call_args.kwargs
    assert call["Source"] == "pi@x.in" and call["Destinations"] == ["a@x.in", "b@x.in"]
    assert b"Subject: Alert" in call["RawMessage"]["Data"]
    assert b"a.jpg" in call["RawMessage"]["Data"]
    smtp.assert_not_called()


def test_ses_failure_falls_back_to_smtp_when_a_password_is_set(monkeypatch, fake_aws):
    _on(monkeypatch, GARUDA_MAIL_BACKEND="ses")
    cloud.client("ses").send_raw_email.side_effect = RuntimeError("throttled")
    smtp = MagicMock()
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", smtp)
    mailer.send("s", "b", sender="pi@x.in", password="app-pass", to="a@x.in")
    smtp.assert_called_once()


def test_ses_failure_without_a_password_is_raised(monkeypatch, fake_aws):
    _on(monkeypatch, GARUDA_MAIL_BACKEND="ses")
    cloud.client("ses").send_raw_email.side_effect = RuntimeError("not verified")
    with pytest.raises(RuntimeError, match="not verified"):
        mailer.send("s", "b", sender="pi@x.in", password="", to="a@x.in")


def test_settings_accept_ses_without_a_gmail_password(monkeypatch):
    monkeypatch.setenv("EMAIL_SENDER", "pi@x.in")
    monkeypatch.setenv("EMAIL_RECIPIENTS", "a@x.in")
    monkeypatch.delenv("EMAIL_SENDER_PASS", raising=False)
    warn = "Alert email is not configured"
    assert any(warn in m for _, m in Settings.load().problems())
    monkeypatch.setenv("GARUDA_MAIL_BACKEND", "ses")
    assert not any(warn in m for _, m in Settings.load().problems())


# ── SNS ──────────────────────────────────────────────────────────────────────

def test_sns_subject_is_ascii_and_short(monkeypatch, fake_aws):
    _on(monkeypatch, DRISHTI_SNS_TOPIC_ARN=TOPIC)
    assert cloud.publish_alert("EMERGENCY: Danger Object Detected — knife\n" + "x" * 200, "b")
    call = fake_aws["sns"].publish.call_args.kwargs
    assert call["TopicArn"] == TOPIC
    assert call["Subject"].isascii() and "\n" not in call["Subject"]
    assert call["Subject"].startswith("EMERGENCY: Danger Object Detected - knife")
    assert len(call["Subject"]) < 100


def test_sns_failure_is_swallowed(monkeypatch, fake_aws):
    _on(monkeypatch, DRISHTI_SNS_TOPIC_ARN=TOPIC)
    cloud.client("sns").publish.side_effect = OSError("no network")
    assert cloud.publish_alert("s", "b") is False


def test_tamper_alert_reaches_sns_even_with_no_email(monkeypatch, fake_aws):
    _on(monkeypatch, DRISHTI_SNS_TOPIC_ARN=TOPIC)
    monkeypatch.setattr(gw.STATE.alerts, "last_tamper_email", 0.0)
    monkeypatch.setattr(gw.STATE.config, "email_sender", "")
    send = MagicMock()
    monkeypatch.setattr(gw, "_send_mail", send)
    gw._send_tamper_email()
    for _ in range(50):
        if fake_aws.get("sns") and fake_aws["sns"].publish.called:
            break
        time.sleep(0.02)
    assert "TAMPER" in fake_aws["sns"].publish.call_args.kwargs["Subject"]
    send.assert_not_called()


def test_danger_alert_publishes_beside_the_email(monkeypatch, fake_aws):
    _on(monkeypatch, DRISHTI_SNS_TOPIC_ARN=TOPIC)
    for mode in ("email_off", "idle", "emergency", "night"):
        monkeypatch.setattr(gw.STATE.modes, mode, False)
    monkeypatch.setattr(gw.STATE.alerts, "last_email_sent_time", 0.0)
    send = MagicMock()
    monkeypatch.setattr(gw, "_send_mail", send)
    published = MagicMock()
    monkeypatch.setattr(gw._cloud, "publish_alert_async", published)
    gw.send_email_alert()
    send.assert_called_once()
    assert published.call_args.args[0] == send.call_args.args[0]


# ── CloudWatch heartbeat ─────────────────────────────────────────────────────

def test_heartbeat_sends_the_drishti_metrics(monkeypatch, fake_aws):
    _on(monkeypatch)
    monkeypatch.setattr(aws_agent, "service_up", lambda: 1)
    monkeypatch.setattr(aws_agent, "cpu_temp_c", lambda: 61.5)
    values, ok = aws_agent.heartbeat()
    assert ok
    call = fake_aws["cloudwatch"].put_metric_data.call_args.kwargs
    assert call["Namespace"] == "Drishti"
    got = {d["MetricName"]: d["Value"] for d in call["MetricData"]}
    assert got["Heartbeat"] == 1 and got["ServiceUp"] == 1 and got["CpuTempC"] == 61.5


def test_heartbeat_leaves_out_a_missing_reading_and_survives_no_network(monkeypatch, fake_aws):
    _on(monkeypatch)
    monkeypatch.setattr(aws_agent, "cpu_temp_c", lambda: None)
    monkeypatch.setattr(aws_agent, "service_up", lambda: 0)
    cloud.client("cloudwatch").put_metric_data.side_effect = OSError("offline")
    values, ok = aws_agent.heartbeat()
    assert not ok
    names = [d["MetricName"] for d in
             fake_aws["cloudwatch"].put_metric_data.call_args.kwargs["MetricData"]]
    assert "CpuTempC" not in names


def test_agent_does_nothing_when_off(fake_aws):
    assert aws_agent.main(["heartbeat"]) == 0
    assert fake_aws == {}


# ── S3 backup ────────────────────────────────────────────────────────────────

def _tree(tmp_path):
    root, data = tmp_path / "repo", tmp_path / "data"
    (root / "resources" / "models").mkdir(parents=True)
    (root / "resources" / "best_v5.hef").write_bytes(b"hef")
    (root / "resources" / "models" / "best_v5.pt").write_bytes(b"pt")
    (root / "resources" / "notes.txt").write_text("not a model")
    (data / "backups" / "auto").mkdir(parents=True)
    (data / "backups" / "auto" / "garuda-state-20261023-030000.tar.gz").write_bytes(b"tgz")
    cap = data / "captures"
    cap.mkdir()
    for name in ("a.jpg", "b.jpg", "c.wav"):
        (cap / name).write_bytes(b"x")
    (cap / "index.json").write_text(json.dumps([
        {"id": "a", "file": "a.jpg", "status": "saved", "keep": True, "alert": ""},
        {"id": "b", "file": "b.jpg", "status": "saved", "keep": False, "alert": ""},
        {"id": "c", "file": "c.wav", "status": "saved", "keep": False, "alert": "knife"},
    ]))
    return root, data


def test_backup_picks_state_and_models_but_not_captures_by_default(tmp_path):
    root, data = _tree(tmp_path)
    keys = sorted(k for _, k in aws_agent.backup_candidates(root, data))
    assert keys == ["models/best_v5.hef", "models/models/best_v5.pt",
                    "state/garuda-state-20261023-030000.tar.gz"]


def test_backup_captures_only_kept_or_alert_ones_when_opted_in(tmp_path):
    root, data = _tree(tmp_path)
    keys = {k for _, k in aws_agent.backup_candidates(root, data, captures=True)}
    assert {"captures/a.jpg", "captures/c.wav"} <= keys and "captures/b.jpg" not in keys


def test_backup_sends_only_what_changed(monkeypatch, tmp_path, fake_aws):
    _on(monkeypatch, DRISHTI_S3_BUCKET="drishti-backup")
    root, data = _tree(tmp_path)
    sent, skipped, failed = aws_agent.backup(data=data, root=root)
    assert len(sent) == 3 and not skipped and not failed
    s3 = fake_aws["s3"]
    assert s3.upload_file.call_count == 3
    assert s3.upload_file.call_args.args[1] == "drishti-backup"

    hef = root / "resources" / "best_v5.hef"
    hef.write_bytes(b"a newer model")
    sent, skipped, failed = aws_agent.backup(data=data, root=root)
    assert sent == ["models/best_v5.hef"] and len(skipped) == 2


def test_backup_retries_a_failed_file_next_time(monkeypatch, tmp_path, fake_aws):
    _on(monkeypatch, DRISHTI_S3_BUCKET="drishti-backup")
    root, data = _tree(tmp_path)
    cloud.client("s3").upload_file.side_effect = OSError("offline")
    sent, _, failed = aws_agent.backup(data=data, root=root)
    assert not sent and len(failed) == 3
    cloud.client("s3").upload_file.side_effect = None
    sent, _, failed = aws_agent.backup(data=data, root=root)
    assert len(sent) == 3 and not failed


def test_backup_dry_run_uploads_nothing(monkeypatch, tmp_path, fake_aws):
    _on(monkeypatch, DRISHTI_S3_BUCKET="drishti-backup")
    root, data = _tree(tmp_path)
    sent, _, _ = aws_agent.backup(dry_run=True, data=data, root=root)
    assert len(sent) == 3 and "s3" not in fake_aws
    assert not (data / aws_agent.MANIFEST).exists()


class _Ready:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self, *a): return json.dumps(self.body).encode()


def test_camera_up_reads_the_readiness_check(monkeypatch):
    import io
    import urllib.error
    monkeypatch.setattr(aws_agent.urllib.request, "urlopen",
                        lambda url, timeout: _Ready({"ready": True, "checks": {"camera": True}}))
    assert aws_agent.camera_up() == 1

    def not_ready(url, timeout):                  # 503: the body still says which check failed
        raise urllib.error.HTTPError(url, 503, "not ready", {},
                                     io.BytesIO(b'{"ready": false, "checks": {"camera": false}}'))
    monkeypatch.setattr(aws_agent.urllib.request, "urlopen", not_ready)
    assert aws_agent.camera_up() == 0

    def down(url, timeout):
        raise OSError("refused")
    monkeypatch.setattr(aws_agent.urllib.request, "urlopen", down)
    assert aws_agent.camera_up() is None          # the service alarm covers a dead service


def test_the_stack_alarms_when_the_camera_stops():
    text = open(os.path.join(os.path.dirname(__file__), "..", "infra", "aws", "drishti.yaml")).read()
    assert "MetricName: CameraUp" in text and "drishti-camera-down" in text
