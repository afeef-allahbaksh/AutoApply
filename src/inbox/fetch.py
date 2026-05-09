"""Gmail message fetching — list + parse messages received since a timestamp."""
import base64
import binascii
import html as html_module
import re
from datetime import datetime
from email.utils import parseaddr, parsedate_to_datetime

from googleapiclient.discovery import build

# Gmail search query suffix — exclude spam/trash but keep promotions/updates,
# since application emails often land outside the Primary tab.
DEFAULT_QUERY_TAIL = "-in:spam -in:trash"


def _b64url_decode(data: str) -> bytes:
    if not data:
        return b""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode())
    except (binascii.Error, ValueError):
        return b""


def _html_to_text(html: str) -> str:
    """Cheap HTML-to-text — strips tags, decodes entities, collapses whitespace."""
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", html)
    text = html_module.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_body(payload: dict) -> str:
    """Walk MIME tree; prefer text/plain, fall back to text/html stripped."""
    text_plain: str | None = None
    text_html: str | None = None

    def walk(node):
        nonlocal text_plain, text_html
        mime = node.get("mimeType", "")
        body = node.get("body", {}) or {}
        data = body.get("data", "")
        if data:
            decoded = _b64url_decode(data).decode("utf-8", errors="replace")
            if mime == "text/plain" and text_plain is None:
                text_plain = decoded
            elif mime == "text/html" and text_html is None:
                text_html = decoded
        for part in node.get("parts", []) or []:
            walk(part)

    walk(payload)
    if text_plain:
        return text_plain
    if text_html:
        return _html_to_text(text_html)
    return ""


def _headers_to_dict(headers: list) -> dict:
    return {h["name"].lower(): h["value"] for h in headers}


def list_messages_since(creds, since_dt: datetime, max_results: int = 200, extra_query: str = "") -> list[dict]:
    """Return parsed Gmail messages received after since_dt.

    Capped at max_results so a stale sync doesn't take forever. Each message dict:
      id, thread_id, subject, from, from_email, to, received_at, snippet, body_text, labels
    """
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    epoch = int(since_dt.timestamp())
    query = f"after:{epoch} {DEFAULT_QUERY_TAIL}"
    if extra_query:
        query = f"{query} {extra_query}"

    messages: list[dict] = []
    page_token = None
    fetched = 0
    while fetched < max_results:
        result = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=min(100, max_results - fetched),
            pageToken=page_token,
        ).execute()
        items = result.get("messages", []) or []
        for stub in items:
            full = service.users().messages().get(
                userId="me", id=stub["id"], format="full"
            ).execute()
            payload = full.get("payload", {}) or {}
            headers = _headers_to_dict(payload.get("headers", []) or [])
            from_raw = headers.get("from", "")
            _, from_email = parseaddr(from_raw)
            received_at = ""
            if headers.get("date"):
                try:
                    received_at = parsedate_to_datetime(headers["date"]).isoformat()
                except (TypeError, ValueError):
                    pass
            messages.append({
                "id": full["id"],
                "thread_id": full.get("threadId", ""),
                "subject": headers.get("subject", ""),
                "from": from_raw,
                "from_email": from_email.lower(),
                "to": headers.get("to", ""),
                "received_at": received_at,
                "snippet": full.get("snippet", ""),
                "body_text": _extract_body(payload),
                "labels": full.get("labelIds", []) or [],
            })
            fetched += 1
            if fetched >= max_results:
                break
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return messages


def thread_url(message_id: str) -> str:
    """Deep link to a Gmail thread by message id (works in browser when signed in)."""
    return f"https://mail.google.com/mail/u/0/#inbox/{message_id}"
