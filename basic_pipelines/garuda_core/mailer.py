"""Sending one plain-text email, with files attached if asked.

Five places in Garuda_web.py each built a message and opened their own SMTP
connection with the same six lines. This is those six lines, once. Who the
mail is from, who it goes to, and whether it should be sent at all (modes,
cooldowns) stay with the caller; a failure is raised, not swallowed, so each
caller keeps its own log line and its own way of carrying on.

With GARUDA_MAIL_BACKEND=ses (and the AWS settings in cloud.py) the same
message goes through Amazon SES instead, on a key that can only send from
the sender's address. Should SES fail and a Gmail app password still be set,
the message is sent the old way, so a bad AWS day costs no alert.
"""
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.mime.text import MIMEText

from . import cloud

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
TIMEOUT_S = 10


def build(subject, body, *, sender, to, attachments=()):
    """The message. attachments are (filename, mime type, bytes)."""
    recipients = [to] if isinstance(to, str) else list(to)
    if attachments:
        msg = EmailMessage()
        msg.set_content(body)
        for filename, mime, data in attachments:
            maintype, _, subtype = (mime or "application/octet-stream").partition("/")
            msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream",
                               filename=filename)
    else:
        msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = ", ".join(recipients)
    return msg


def send(subject, body, *, sender, password, to, host=SMTP_HOST, port=SMTP_PORT,
         timeout=TIMEOUT_S, attachments=()):
    """Send `body` to `to` (one address or a list). Raises on any failure."""
    msg = build(subject, body, sender=sender, to=to, attachments=attachments)
    if cloud.ses_selected():
        try:
            cloud.send_raw_email(msg, sender, [to] if isinstance(to, str) else list(to))
            return
        except Exception as exc:
            if not password:
                raise
            log.warning("SES send failed (%s); sending through SMTP instead", exc)
    # Looked up on the module at call time: the test suite replaces
    # smtplib.SMTP_SSL so that nothing is ever really sent.
    # A default context checks the server's certificate and host name;
    # without one, smtplib accepts any certificate.
    with smtplib.SMTP_SSL(host, port, timeout=timeout if not attachments else max(timeout, 60),
                          context=ssl.create_default_context()) as server:
        server.login(sender, password)
        server.send_message(msg)
