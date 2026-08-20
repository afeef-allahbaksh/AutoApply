"""IMAP message fetching — one read-only connection, addressed by UID.

Everything here is UID-based on purpose. IMAP *sequence* numbers are only valid
for the lifetime of one mailbox session and shift whenever a message is
expunged, so header-fetching on one connection and body-fetching on another (as
this module used to do) can staple a body onto the wrong header — silently, and
with no error to notice. UIDs are stable for the life of the mailbox, guarded by
UIDVALIDITY.

`Mailbox` owns a single authenticated connection for the whole sync: search,
paged header pulls, then bodies for the survivors. Two-phase is still the shape
— headers are tiny and let the caller drop known/noise messages before paying
for full bodies.
"""
import email
import html as html_module
import imaplib
import re
import time
from datetime import datetime
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from typing import Iterator

from src.inbox.auth import ImapCredentials, open_imap

INBOX_FOLDER = "INBOX"
HEADER_FIELDS = "Subject From To Date Message-ID References In-Reply-To"
# Chunk sizes balance command-line length vs. round-trip count. Headers are
# tiny so we go big; bodies can be hundreds of KB each so we keep groups small.
HEADER_BULK_CHUNK = 500
BODY_BULK_CHUNK = 50
# Coverage-commit granularity for the sync's paging loop. Smaller than the bulk
# chunk so a budget cut-off wastes less work; still one round-trip per page.
HEADER_PAGE = 200

_UID_RE = re.compile(rb"UID\s+(\d+)")


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


def _iter_bulk_fetch(msg_data: list) -> Iterator[tuple[int, bytes]]:
    """Yield (uid, raw_bytes) per message in a bulk UID FETCH response.

    imaplib flattens multi-message FETCH responses to a list where each message
    is a `(descriptor_bytes, body_bytes)` tuple followed by a `b')'` terminator.
    Servers must include `UID n` in the descriptor of a UID FETCH response
    (RFC 3501 §6.4.8), which is what we key on — the leading token is the
    sequence number and is deliberately ignored.
    """
    for item in msg_data:
        if not (isinstance(item, tuple) and len(item) >= 2):
            continue
        descriptor, raw = item[0], item[1]
        if not isinstance(descriptor, (bytes, bytearray)) or not raw:
            continue
        m = _UID_RE.search(descriptor)
        if not m:
            continue
        yield int(m.group(1)), raw


def _msg_to_header_dict(msg: Message, uid: int) -> dict:
    # The uid fallback keeps identity stable across syncs for the rare message
    # with no Message-ID; a sequence-number fallback would change every run and
    # get reprocessed forever.
    msg_id = _strip_message_id(msg.get("Message-ID", "")) or f"uid-{uid}"
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
        "uid": uid,
        "thread_id": thread_id,
        "subject": msg.get("Subject", "") or "",
        "from": from_raw,
        "from_email": (from_email or "").lower(),
        "to": msg.get("To", "") or "",
        "received_at": received_at,
        "snippet": "",
        "body_text": "",
        "labels": [],
    }


class Mailbox:
    """One authenticated read-only IMAP session, addressed by UID.

    Use as a context manager. `reconnect()` exists because long syncs outlive
    Gmail's idle tolerance; UID addressing is what makes reconnecting safe.
    """

    def __init__(self, creds: ImapCredentials, folder: str = INBOX_FOLDER):
        self.creds = creds
        self.folder = folder
        self.conn: imaplib.IMAP4_SSL | None = None

    def __enter__(self) -> "Mailbox":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def connect(self) -> None:
        self.conn = open_imap(self.creds)
        self.conn.select(self.folder, readonly=True)

    def reconnect(self) -> None:
        self.close()
        self.connect()

    def close(self) -> None:
        if self.conn is None:
            return
        try:
            self.conn.logout()
        except (imaplib.IMAP4.error, OSError):
            # Best-effort cleanup; idle connections expire server-side anyway.
            pass
        self.conn = None

    def status(self) -> tuple[int, int]:
        """Return (uidvalidity, uidnext) for the selected folder.

        UIDVALIDITY changing means the server re-issued the whole UID space, so
        every UID we stored is meaningless and coverage must reset.
        """
        typ, data = self.conn.status(self.folder, "(UIDVALIDITY UIDNEXT)")
        if typ != "OK" or not data:
            raise RuntimeError(f"IMAP STATUS failed for {self.folder}")
        raw = data[0] if isinstance(data[0], (bytes, bytearray)) else b""
        uidvalidity = re.search(rb"UIDVALIDITY\s+(\d+)", raw)
        uidnext = re.search(rb"UIDNEXT\s+(\d+)", raw)
        return (
            int(uidvalidity.group(1)) if uidvalidity else 0,
            int(uidnext.group(1)) if uidnext else 0,
        )

    def search_uids(self, criteria: str) -> list[int]:
        """Run `UID SEARCH <criteria>`. Returns UIDs ascending."""
        typ, data = self.conn.uid("SEARCH", None, criteria)
        if typ != "OK" or not data or not data[0]:
            return []
        return sorted(int(tok) for tok in data[0].split())

    def iter_header_pages(
        self, uids_desc: list[int], page_size: int = HEADER_PAGE,
    ) -> Iterator[list[dict]]:
        """Yield pages of header-only dicts, newest UID first.

        Paging (rather than one big slice) is what lets the caller stop on a
        budget while still knowing exactly how far down it got.
        """
        for page in _chunked(uids_desc, page_size):
            out: list[dict] = []
            for chunk in _chunked(page, HEADER_BULK_CHUNK):
                chunk_set = ",".join(str(u) for u in chunk)
                typ, msg_data = self.conn.uid(
                    "FETCH", chunk_set, f"(BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})])",
                )
                if typ != "OK":
                    continue
                for uid, raw in _iter_bulk_fetch(msg_data):
                    out.append(_msg_to_header_dict(email.message_from_bytes(raw), uid))
            out.sort(key=lambda m: m["uid"], reverse=True)
            yield out

    def populate_bodies(self, messages: list[dict]) -> None:
        """Fetch full bodies for the given header-dicts in place.

        Resilient to mid-fetch connection drops — on a network blip we reconnect
        once for the failed chunk and retry. If it fails again, that chunk is
        skipped (those messages get classified on subject+sender alone).
        """
        by_uid = {m["uid"]: m for m in messages if m.get("uid")}
        if not by_uid:
            return
        uids = list(by_uid.keys())
        t0 = time.perf_counter()
        skipped = 0
        for chunk_idx, chunk in enumerate(_chunked(uids, BODY_BULK_CHUNK)):
            chunk_set = ",".join(str(u) for u in chunk)
            for attempt in range(2):
                try:
                    typ, msg_data = self.conn.uid("FETCH", chunk_set, "(BODY.PEEK[])")
                    if typ != "OK":
                        break
                    for uid, raw in _iter_bulk_fetch(msg_data):
                        target = by_uid.get(uid)
                        if target is None:
                            continue
                        body = _extract_body(email.message_from_bytes(raw))
                        target["body_text"] = body
                        target["snippet"] = re.sub(r"\s+", " ", body)[:200]
                    break
                except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError) as e:
                    if attempt == 0:
                        print(f"[inbox] body chunk {chunk_idx} failed ({e}); reconnecting")
                        self.reconnect()
                    else:
                        skipped += len(chunk)
                        print(f"[inbox] body chunk {chunk_idx} failed twice; skipping {len(chunk)} messages")
        elapsed = time.perf_counter() - t0
        if skipped:
            print(f"[inbox] body bulk fetch: {len(uids) - skipped}/{len(uids)} in {elapsed:.1f}s ({skipped} skipped)")
        else:
            print(f"[inbox] body bulk fetch: {len(uids)} messages in {elapsed:.1f}s")


def resolve_horizon_uid(mailbox: Mailbox, since_dt: datetime, uidnext: int) -> int:
    """Lowest UID at or after `since_dt` — the oldest message a sync will ever
    look at. IMAP SINCE is date-granular, which only ever widens the window.

    Empty result means nothing in the mailbox is that recent, so the horizon
    collapses to UIDNEXT (nothing to scan) rather than 1 (scan all history).
    """
    uids = mailbox.search_uids(f'SINCE "{_imap_date(since_dt)}"')
    return uids[0] if uids else max(uidnext, 1)


def thread_url(message_id: str) -> str:
    """Best-effort deep link. Works in browser when signed into Gmail."""
    return f"https://mail.google.com/mail/u/0/#search/rfc822msgid:{message_id}"
