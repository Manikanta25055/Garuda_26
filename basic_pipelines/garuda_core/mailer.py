"""Sending one plain-text email.

Five places in Garuda_web.py each built a message and opened their own SMTP
connection with the same six lines. This is those six lines, once. Who the
mail is from, who it goes to, and whether it should be sent at all (modes,
cooldowns) stay with the caller; a failure is raised, not swallowed, so each
caller keeps its own log line and its own way of carrying on.
"""
import smtplib
import ssl
from email.mime.text import MIMEText

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
TIMEOUT_S = 10


def send(subject, body, *, sender, password, to, host=SMTP_HOST, port=SMTP_PORT,
         timeout=TIMEOUT_S):
    """Send `body` to `to` (one address or a list). Raises on any failure."""
    recipients = [to] if isinstance(to, str) else list(to)
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = ", ".join(recipients)
    # Looked up on the module at call time: the test suite replaces
    # smtplib.SMTP_SSL so that nothing is ever really sent.
    # A default context checks the server's certificate and host name;
    # without one, smtplib accepts any certificate.
    with smtplib.SMTP_SSL(host, port, timeout=timeout,
                          context=ssl.create_default_context()) as server:
        server.login(sender, password)
        server.send_message(msg)
