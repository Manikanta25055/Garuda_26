"""Optional AWS extras: offsite backup, SES mail, SNS alerts, CloudWatch health.

Drishti detects, alerts and records on the Pi with no network at all, and
nothing here is on that path. Each piece is off until its variable is set in
.env; a missing boto3 or a missing network is a log line, never an error in
the caller, and no call waits more than a few seconds on AWS.

    DRISHTI_AWS_REGION      ap-south-1; nothing below works without it
    DRISHTI_S3_BUCKET       nightly backup (scripts/aws_agent.py backup)
    DRISHTI_SNS_TOPIC_ARN   alert push / SMS sent beside the alert email
    DRISHTI_CW_NAMESPACE    health metrics (scripts/aws_agent.py heartbeat), default Drishti
    GARUDA_MAIL_BACKEND     "ses" sends email through SES (see mailer.py)

Credentials are the standard AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY of the
Pi's own IAM user, which infra/aws/drishti.yaml creates able to do exactly
these four things and nothing else: it can upload a backup but not read or
delete one.
"""
import logging
import os
import threading

log = logging.getLogger(__name__)

DEFAULT_NAMESPACE = "Drishti"
SNS_SUBJECT_MAX = 99           # SNS: under 100 characters, ASCII, no line breaks

_clients = {}
_lock = threading.Lock()
_factory = None                # (service, region) -> client; tests replace it


class CloudOff(RuntimeError):
    """The piece asked for is not configured on this Pi."""


def region():
    return os.environ.get("DRISHTI_AWS_REGION", "").strip()


def bucket():
    return os.environ.get("DRISHTI_S3_BUCKET", "").strip()


def topic_arn():
    return os.environ.get("DRISHTI_SNS_TOPIC_ARN", "").strip()


def namespace():
    return os.environ.get("DRISHTI_CW_NAMESPACE", "").strip() or DEFAULT_NAMESPACE


def enabled():
    return bool(region())


def sns_enabled():
    return enabled() and bool(topic_arn())


def ses_selected():
    return os.environ.get("GARUDA_MAIL_BACKEND", "smtp").strip().lower() == "ses"


def _boto3_client(service, region_name):
    import boto3
    from botocore.config import Config
    return boto3.client(service, region_name=region_name,
                        config=Config(connect_timeout=5, read_timeout=15,
                                      retries={"max_attempts": 2, "mode": "standard"}))


def client(service):
    if not enabled():
        raise CloudOff("DRISHTI_AWS_REGION is not set")
    with _lock:
        c = _clients.get(service)
        if c is None:
            c = (_factory or _boto3_client)(service, region())
            _clients[service] = c
        return c


def reset(factory=None):
    """Forget the clients made so far; with `factory`, make new ones with it."""
    global _factory
    with _lock:
        _clients.clear()
        _factory = factory


# ── email (SES) ──────────────────────────────────────────────────────────────

def send_raw_email(msg, sender, recipients):
    """Send a built email.message through SES. Raises on any failure."""
    client("ses").send_raw_email(Source=sender, Destinations=list(recipients),
                                 RawMessage={"Data": msg.as_bytes()})


# ── alerts (SNS) ─────────────────────────────────────────────────────────────

def _sns_subject(subject):
    text = " ".join(str(subject).replace("—", "-").replace("–", "-").split())
    text = text.encode("ascii", "ignore").decode().strip()
    return (text or "Drishti alert")[:SNS_SUBJECT_MAX]


def publish_alert(subject, message):
    """One message to the alert topic. True when sent; never raises."""
    if not sns_enabled():
        return False
    try:
        client("sns").publish(TopicArn=topic_arn(), Subject=_sns_subject(subject),
                              Message=str(message))
        return True
    except Exception as exc:
        log.warning("SNS alert not sent: %s", exc)
        return False


def publish_alert_async(subject, message):
    """publish_alert on its own thread, so an alert never waits on the network."""
    if not sns_enabled():
        return None
    t = threading.Thread(target=publish_alert, args=(subject, message),
                         name="sns-alert", daemon=True)
    t.start()
    return t


# ── health (CloudWatch) ──────────────────────────────────────────────────────

UNITS = {"Heartbeat": "Count", "ServiceUp": "Count", "CpuTempC": "None",
         "DiskUsedPercent": "Percent"}


def put_metrics(values):
    """{name: number} to CloudWatch, None values left out. Raises on failure."""
    data = [{"MetricName": name, "Value": float(v), "Unit": UNITS.get(name, "None")}
            for name, v in values.items() if v is not None]
    if data:
        client("cloudwatch").put_metric_data(Namespace=namespace(), MetricData=data)
    return len(data)


# ── backup (S3) ──────────────────────────────────────────────────────────────

def upload_file(path, key):
    """One file to the backup bucket. Raises on failure."""
    if not bucket():
        raise CloudOff("DRISHTI_S3_BUCKET is not set")
    client("s3").upload_file(str(path), bucket(), key,
                             ExtraArgs={"ServerSideEncryption": "AES256"})
