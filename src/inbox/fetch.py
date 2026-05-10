"""IMAP message fetching — bulk-FETCH on a single connection.

Two-phase: cheap header pull on all matching messages, then full body fetch
only for prefilter survivors. Both phases use bulk FETCH (chunked sequence
sets) so a sync of thousands of messages takes seconds, not minutes.
"""
import email
import html as html_module
import imaplib
import re
import time
from datetime import datetime
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime

from src.inbox.auth import ImapCredentials, open_imap

INBOX_FOLDER = "INBOX"
HEADER_FIELDS = "Subject From To Date Message-ID References In-Reply-To"
# Chunk sizes balance command-line length vs. round-trip count. Headers are
# tiny so we go big; bodies can be hundreds of KB each so we keep groups small.
HEADER_BULK_CHUNK = 500
BODY_BULK_CHUNK = 50


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


def _chunked(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _iter_bulk_fetch(msg_data: list):
    """Yield (seq_num_str, raw_bytes) per message in a bulk FETCH response.

    imaplib flattens multi-message FETCH responses to a list where each message
    is a `(descriptor_bytes, body_bytes)` tuple followed by a `b')'` terminator.
    The first whitespace-delimited token of the descriptor is the sequence number.
    """
    for item in msg_data:
        if not (isinstance(item, tuple) and len(item) >= 2):
            continue
        descriptor, raw = item[0], item[1]
        if not isinstance(descriptor, (bytes, bytearray)) or not raw:
            continue
        first_token = descriptor.split(b" ", 1)[0]
        try:
            seq = first_token.decode()
        except UnicodeDecodeError:
            continue
        yield seq, raw


def _msg_to_header_dict(msg: Message, seq: str) -> dict:
    msg_id = _strip_message_id(msg.get("Message-ID", "")) or f"seq-{seq}"
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
        "_seq": seq,
    }


def list_message_headers_since(creds: ImapCredentials, since_dt: datetime, max_results: int = 200) -> list[dict]:
    """Phase 1: cheap pull. Returns header-only dicts (no body_text, no snippet)."""
    conn = open_imap(creds)
    try:
        conn.select(INBOX_FOLDER, readonly=True)
        typ, data = conn.search(None, f'(SINCE "{_imap_date(since_dt)}")')
        if typ != "OK" or not data or not data[0]:
            return []
        seq_nums = list(reversed(data[0].split()))[:max_results]

        t0 = time.perf_counter()
        out: list[dict] = []
        for chunk in _chunked(seq_nums, HEADER_BULK_CHUNK):
            chunk_set = b",".join(chunk)
            typ, msg_data = conn.fetch(chunk_set, f"(BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})])")
            if typ != "OK":
                continue
            for seq, raw in _iter_bulk_fetch(msg_data):
                msg = email.message_from_bytes(raw)
                out.append(_msg_to_header_dict(msg, seq))
        elapsed = time.perf_counter() - t0
        print(f"[inbox] header bulk fetch: {len(out)} messages in {elapsed:.1f}s ({len(seq_nums) // HEADER_BULK_CHUNK + 1} chunks)")
        return out
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _open_select(creds: ImapCredentials):
    conn = open_imap(creds)
    conn.select(INBOX_FOLDER, readonly=True)
    return conn


def populate_bodies(creds: ImapCredentials, messages: list[dict]) -> None:
    """Phase 2: fetch full bodies for the given header-dicts in place.

    Resilient to mid-fetch connection drops — on a network blip we reconnect
    once for the failed chunk and retry. If it fails again, that chunk is
    skipped (those messages get classified on subject+sender alone).
    """
    if not messages:
        return
    by_seq = {m.get("_seq"): m for m in messages if m.get("_seq")}
    if not by_seq:
        return
    seqs = list(by_seq.keys())

    conn = _open_select(creds)
    try:
        t0 = time.perf_counter()
        skipped = 0
        for chunk_idx, chunk in enumerate(_chunked(seqs, BODY_BULK_CHUNK)):
            chunk_set = ",".join(chunk).encode()
            for attempt in range(2):
                try:
                    typ, msg_data = conn.fetch(chunk_set, "(BODY.PEEK[])")
                    if typ != "OK":
                        break
                    for seq, raw in _iter_bulk_fetch(msg_data):
                        target = by_seq.get(seq)
                        if target is None:
                            continue
                        full_msg = email.message_from_bytes(raw)
                        body = _extract_body(full_msg)
                        target["body_text"] = body
                        target["snippet"] = re.sub(r"\s+", " ", body)[:200]
                    break
                except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError) as e:
                    if attempt == 0:
                        print(f"[inbox] body chunk {chunk_idx} failed ({e}); reconnecting")
                        try:
                            conn.logout()
                        except Exception:
                            pass
                        conn = _open_select(creds)
                    else:
                        skipped += len(chunk)
                        print(f"[inbox] body chunk {chunk_idx} failed twice; skipping {len(chunk)} messages")
        elapsed = time.perf_counter() - t0
        if skipped:
            print(f"[inbox] body bulk fetch: {len(seqs) - skipped}/{len(seqs)} in {elapsed:.1f}s ({skipped} skipped)")
        else:
            print(f"[inbox] body bulk fetch: {len(seqs)} messages in {elapsed:.1f}s")
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def thread_url(message_id: str) -> str:
    """Best-effort deep link. Works in browser when signed into Gmail."""
    return f"https://mail.google.com/mail/u/0/#search/rfc822msgid:{message_id}"
