"""Outbound SMTP sender for cold outreach.

Reuses the IMAP credentials stored in `profiles/{name}/imap/credentials.json` —
Gmail / Outlook / Yahoo app passwords all work for both IMAP and SMTP, so the
user doesn't need to configure a second secret. The SMTP host is derived from
the IMAP host (`imap.gmail.com` → `smtp.gmail.com`); port 587 with STARTTLS is
the modern default and works for every major provider we support.

This is the first place AutoApply *writes* via the user's mailbox (IMAP is
deliberately read-only). The route layer is responsible for confirmation UX;
this module just sends.
"""
import smtplib
import socket
import ssl
from email.message import EmailMessage

import certifi

from src.inbox import auth as inbox_auth

SMTP_TIMEOUT = 20
SMTP_PORT_STARTTLS = 587


def _ssl_context() -> ssl.SSLContext:
    """SSL context backed by the certifi CA bundle.

    The python.org macOS distribution doesn't know about the system root
    certificates by default — `ssl.create_default_context()` with no args
    fails with `CERTIFICATE_VERIFY_FAILED` against Gmail / most public
    SMTP/IMAP servers. `certifi` ships a vetted root bundle that works
    everywhere and is already a transitive dep via `requests`.
    """
    return ssl.create_default_context(cafile=certifi.where())


def _derive_smtp_host(imap_host: str) -> str:
    """Heuristic: replace `imap.` with `smtp.`. Works for Gmail/Outlook/Yahoo
    and any provider that follows the convention. For providers that don't
    (ProtonMail Bridge uses 127.0.0.1 for both), we return the host unchanged
    and let the user override via env var down the line."""
    if imap_host.startswith("imap."):
        return "smtp." + imap_host[len("imap."):]
    if imap_host.startswith("imaps."):
        return "smtp." + imap_host[len("imaps."):]
    return imap_host


def send_email(
    profile_name: str,
    to: str,
    subject: str,
    body: str,
) -> tuple[bool, str]:
    """Send a plain-text email from the profile's connected inbox account.

    Returns (ok, error_message). On success, error_message is "". On any
    failure, ok is False and error_message is a short human-readable string
    safe to surface in the UI (no stack traces).

    Validates that the inbox is connected; refuses to send blank fields.
    """
    to = (to or "").strip()
    subject = (subject or "").strip()
    body = (body or "").strip()
    if not to:
        return False, "No recipient email — fill in the Contact email field."
    if not subject:
        return False, "No subject — generate or write a draft first."
    if not body:
        return False, "Empty body — generate or write a draft first."

    creds = inbox_auth.load_credentials(profile_name)
    if creds is None:
        return False, "Inbox not connected. Connect your email in Settings → Inbox sync first."

    msg = EmailMessage()
    msg["From"] = creds.email
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    smtp_host = _derive_smtp_host(creds.server)
    try:
        with smtplib.SMTP(smtp_host, SMTP_PORT_STARTTLS, timeout=SMTP_TIMEOUT) as smtp:
            smtp.ehlo()
            smtp.starttls(context=_ssl_context())
            smtp.ehlo()
            smtp.login(creds.email, creds.password)
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        return False, (
            f"Inbox rejected the password ({getattr(e, 'smtp_code', '?')}). "
            "Re-generate your app password and reconnect in Settings."
        )
    except smtplib.SMTPRecipientsRefused:
        return False, f"Recipient address rejected: {to}"
    except smtplib.SMTPException as e:
        return False, f"SMTP server error: {str(e)[:160]}"
    except (socket.gaierror, socket.timeout, OSError) as e:
        return False, f"Could not reach {smtp_host}:{SMTP_PORT_STARTTLS} ({e})"

    return True, ""
