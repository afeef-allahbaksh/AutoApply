"""IMAP message fetching — two-phase: cheap header pull, then bodies on demand.

Inbox-wide syncs would spend 90% of their time pulling bodies of newsletters and
alerts only to throw them away. Splitting the fetch lets the prefilter run on
headers (subject + from), which is what it actually needs, and the full RFC822
body is only paid for on the survivors.
"""
import email
import html as html_module
import re
from datetime import datetime
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime

from src.inbox.auth import ImapCredentials, open_imap

INBOX_FOLDER = "INBOX"
HEADER_FIELDS = "Subject From To Date Message-ID References In-Reply-To"


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
    return raw.strip().lstrip("<").rstrip(">").strip()


def _thread_root_id(msg: Message, fallback: str) -> str:
    """Approximate Gmail's thread_id by walking References / In-Reply-To.

    For the first message in a thread, References is empty and we fall back to
    the message's own Message-ID. Replies share the root via References[0].
    """
    refs = msg.get("References", "").strip()
    if refs:
        return _strip_message_id(refs.split()[0])
    in_reply = msg.get("In-Reply-To", "").strip()
    if in_reply:
        return _strip_message_id(in_reply)
    return fallback


def _imap_date(dt: datetime) -> str:
    """IMAP SEARCH SINCE expects DD-MMM-YYYY (English month abbreviations)."""
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{dt.day:02d}-{months[dt.month - 1]}-{dt.year}"


def _parse_fetch_response(msg_data: list) -> bytes | None:
    return next(
        (item[1] for item in msg_data if isinstance(item, tuple) and len(item) >= 2),
        None,
    )


def _msg_to_header_dict(msg: Message, uid: bytes) -> dict:
    msg_id = _strip_message_id(msg.get("Message-ID", "")) or f"uid-{uid.decode()}"
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
    return {
        "id": msg_id,
        "thread_id": thread_id,
        "subject": msg.get("Subject", "") or "",
        "from": from_raw,
        "from_email": (from_email or "").lower(),
        "to": msg.get("To", "") or "",
        "received_at": received_at,
        "snippet": "",
        "body_text": "",
        "labels": [],
        "_uid": uid.decode(),
    }


def list_message_headers_since(creds: ImapCredentials, since_dt: datetime, max_results: int = 200) -> list[dict]:
    """Phase 1: cheap pull. Returns header-only dicts (no body_text, no snippet).

    Use the result with the prefilter, then call `populate_bodies(creds, survivors)`
    to fetch full bodies only for messages worth classifying.
    """
    conn = open_imap(creds)
    try:
        conn.select(INBOX_FOLDER, readonly=True)
        typ, data = conn.search(None, f'(SINCE "{_imap_date(since_dt)}")')
        if typ != "OK" or not data or not data[0]:
            return []
        uids = list(reversed(data[0].split()))[:max_results]

        out: list[dict] = []
        for uid in uids:
            typ, msg_data = conn.fetch(uid, f"(BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})])")
            if typ != "OK" or not msg_data:
                continue
            raw = _parse_fetch_response(msg_data)
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            out.append(_msg_to_header_dict(msg, uid))
        return out
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def populate_bodies(creds: ImapCredentials, messages: list[dict]) -> None:
    """Phase 2: fetch full bodies for the given header-dicts in place.

    Each dict gets `body_text` and `snippet` populated. Dicts without `_uid` are
    skipped silently — they came from somewhere other than `list_message_headers_since`.
    """
    if not messages:
        return
    conn = open_imap(creds)
    try:
        conn.select(INBOX_FOLDER, readonly=True)
        for m in messages:
            uid = m.get("_uid")
            if not uid:
                continue
            typ, msg_data = conn.fetch(uid.encode(), "(BODY.PEEK[])")
            if typ != "OK" or not msg_data:
                continue
            raw = _parse_fetch_response(msg_data)
            if not raw:
                continue
            full_msg = email.message_from_bytes(raw)
            body = _extract_body(full_msg)
            m["body_text"] = body
            m["snippet"] = re.sub(r"\s+", " ", body)[:200]
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def thread_url(message_id: str) -> str:
    """Best-effort deep link. Works in browser when signed into Gmail."""
    return f"https://mail.google.com/mail/u/0/#search/rfc822msgid:{message_id}"
