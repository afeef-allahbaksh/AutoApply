"""IMAP message fetching — list + parse messages received since a timestamp."""
import email
import html as html_module
import re
from datetime import datetime
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime

from src.inbox.auth import ImapCredentials, open_imap

INBOX_FOLDER = "INBOX"


def _html_to_text(html: str) -> str:
    """Cheap HTML-to-text — strips tags, decodes entities, collapses whitespace."""
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", html)
    text = html_module.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def _extract_body(msg: Message) -> str:
    """Walk MIME tree; prefer text/plain, fall back to text/html stripped."""
    text_plain: str | None = None
    text_html: str | None = None
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain" and text_plain is None:
            text_plain = _decode_part(part)
        elif ctype == "text/html" and text_html is None:
            text_html = _decode_part(part)
    if text_plain:
        return text_plain
    if text_html:
        return _html_to_text(text_html)
    return ""


def _strip_message_id(raw: str) -> str:
    """Message-IDs are wrapped in angle brackets per RFC 822; unwrap."""
    return raw.strip().lstrip("<").rstrip(">").strip()


def _thread_root_id(msg: Message, fallback: str) -> str:
    """Approximate Gmail's thread_id by walking References / In-Reply-To.

    For the first message in a thread, References is empty and we fall back to
    the message's own Message-ID — same value Gmail's thread_id would key on.
    Replies share the root via References[0], so they group together.
    """
    refs = msg.get("References", "").strip()
    if refs:
        first = refs.split()[0]
        return _strip_message_id(first)
    in_reply = msg.get("In-Reply-To", "").strip()
    if in_reply:
        return _strip_message_id(in_reply)
    return fallback


def _imap_date(dt: datetime) -> str:
    """IMAP SEARCH SINCE expects DD-MMM-YYYY (English month abbreviations)."""
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{dt.day:02d}-{months[dt.month - 1]}-{dt.year}"


def list_messages_since(creds: ImapCredentials, since_dt: datetime, max_results: int = 200) -> list[dict]:
    """Return parsed messages received after since_dt.

    Capped at max_results so a stale sync doesn't take forever. Each message dict:
      id, thread_id, subject, from, from_email, to, received_at, snippet, body_text, labels
    """
    conn = open_imap(creds)
    try:
        conn.select(INBOX_FOLDER, readonly=True)
        typ, data = conn.search(None, f'(SINCE "{_imap_date(since_dt)}")')
        if typ != "OK" or not data or not data[0]:
            return []
        ids = data[0].split()
        # Newest first; cap.
        ids = list(reversed(ids))[:max_results]

        messages: list[dict] = []
        for seq_id in ids:
            typ, msg_data = conn.fetch(seq_id, "(RFC822)")
            if typ != "OK" or not msg_data:
                continue
            raw_bytes = next(
                (item[1] for item in msg_data if isinstance(item, tuple) and len(item) >= 2),
                None,
            )
            if not raw_bytes:
                continue
            msg = email.message_from_bytes(raw_bytes)
            messages.append(_to_dict(msg))
        return messages
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _to_dict(msg: Message) -> dict:
    msg_id = _strip_message_id(msg.get("Message-ID", "")) or f"no-id-{id(msg)}"
    thread_id = _thread_root_id(msg, fallback=msg_id)
    from_raw = msg.get("From", "")
    _, from_email = parseaddr(from_raw)
    received_at = ""
    date_hdr = msg.get("Date", "")
    if date_hdr:
        try:
            received_at = parsedate_to_datetime(date_hdr).isoformat()
        except (TypeError, ValueError):
            pass
    body = _extract_body(msg)
    snippet = re.sub(r"\s+", " ", body)[:200]
    return {
        "id": msg_id,
        "thread_id": thread_id,
        "subject": msg.get("Subject", "") or "",
        "from": from_raw,
        "from_email": (from_email or "").lower(),
        "to": msg.get("To", "") or "",
        "received_at": received_at,
        "snippet": snippet,
        "body_text": body,
        "labels": [],
    }


def thread_url(message_id: str) -> str:
    """Best-effort deep link. Works in browser when signed into Gmail; for other
    providers users can copy the Message-ID and search their client manually."""
    return f"https://mail.google.com/mail/u/0/#search/rfc822msgid:{message_id}"
