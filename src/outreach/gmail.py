"""Gmail compose-URL builder + domain normalization for the email-pattern suggester.

`gmail_compose_url` is a fallback — the primary send path is `outreach.sender`
(SMTP via app password). The Gmail URL is offered as a secondary action so
users with attachments or a different sending account can still ship.
"""
from urllib.parse import quote


def gmail_compose_url(record: dict) -> str:
    """Construct the Gmail compose URL that prefills To/Subject/Body.

    Returns a URL the user opens themselves — AutoApply does not auto-click.
    """
    to = record.get("contact_email", "")
    su = record.get("draft_subject", "")
    body = record.get("draft_body", "")
    return (
        "https://mail.google.com/mail/?view=cm&fs=1"
        f"&to={quote(to, safe='')}"
        f"&su={quote(su, safe='')}"
        f"&body={quote(body, safe='')}"
    )


def clean_domain(raw: str) -> str:
    """Normalize a user-typed domain: strip protocol, www, trailing path/slash,
    spaces. The email-pattern suggester only needs the bare host
    (e.g. `anthropic.com`)."""
    d = (raw or "").strip().lower()
    if not d:
        return ""
    for prefix in ("https://", "http://"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    if d.startswith("www."):
        d = d[4:]
    d = d.split("/", 1)[0].strip()
    return d
