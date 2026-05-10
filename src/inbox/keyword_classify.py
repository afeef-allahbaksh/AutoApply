"""Pure-regex email classifier — zero API calls.

Trades recall for cost. Catches the common patterns ("thank you for applying",
"phone screen", "unfortunately", etc.) but will miss recruiter cold-outreach
threads that don't use the canonical phrasing. Use this when the LLM classifier
is unavailable (out of credits) or when the user wants free mode for cheap
incremental syncs.

Output shape matches `src.inbox.classify.classify_messages` so the rest of the
sync pipeline doesn't care which classifier produced the decisions.
"""
import re


# Status detection — ordered by specificity. First match wins, so high-priority
# states (offer, rejected) come before generic ones (applied) so a "thanks for
# applying earlier, but unfortunately…" rejection isn't misread as applied.
STATUS_PATTERNS: list[tuple[str, list[str]]] = [
    ("offer", [
        r"\boffer letter\b",
        r"\boffer of employment\b",
        r"\bwe(?:'d| would) like to extend (?:an |the )?offer\b",
        r"\bpleased to (?:offer|extend)\b",
        r"\bexcited to (?:offer|extend)\b",
        r"\bextend(?:ing)? you an offer\b",
    ]),
    ("rejected", [
        r"\bunfortunately\b",
        r"\bregretfully\b",
        r"\bwe (?:will|won'?t|cannot) be moving forward\b",
        r"\bnot (?:moving forward|proceeding)\b",
        r"\bdecided to (?:move forward|proceed) with other\b",
        r"\bdecided to pursue (?:other|another)\b",
        r"\bnot (?:selected|the right fit|a match)\b",
        r"\bother candidates whose\b",
    ]),
    ("onsite", [
        r"\bon[-\s]?site\b",
        r"\bfinal round\b",
        r"\bfinal interview\b",
        r"\binterview loop\b",
        r"\bvirtual onsite\b",
        r"\bpanel interview\b",
        r"\bsuperday\b",
    ]),
    ("technical", [
        r"\btechnical interview\b",
        r"\btechnical assessment\b",
        r"\btechnical (?:screen|screening)\b",
        r"\bcoding (?:interview|challenge|test|exercise|assessment)\b",
        r"\btake[-\s]?home\b",
        r"\btake[-\s]?home (?:project|assignment|exercise)\b",
        r"\btech screen\b",
        r"\bsystem design (?:interview|round)\b",
    ]),
    ("screen", [
        r"\bphone (?:screen|call|interview)\b",
        r"\b(?:initial|introductory) (?:conversation|chat|call|interview)\b",
        r"\bscreening (?:call|interview)\b",
        r"\bfirst (?:round|interview)\b",
        r"\blet'?s (?:chat|talk|connect)\b",
        r"\bschedule (?:a |an )?(?:call|chat|interview|meeting)\b",
        r"\binterview (?:invitation|invite|request)\b",
        r"\b(?:would you be|are you) (?:available|free) to chat\b",
        r"\bnext steps\b",
    ]),
    ("applied", [
        r"\bthank(?:s| you) for applying\b",
        r"\bthank(?:s| you) for your (?:application|interest)\b",
        r"\bwe(?:'ve| have)? received your application\b",
        r"\byour application has been (?:received|submitted)\b",
        r"\bapplication (?:received|submitted|confirmation)\b",
        r"\bthanks for submitting\b",
    ]),
]
_COMPILED = [
    (status, [re.compile(p, re.IGNORECASE) for p in patterns])
    for status, patterns in STATUS_PATTERNS
]

# Sender domains that are themselves a positive signal — even if no keyword
# matches, an email from one of these is probably an application confirmation.
ATS_SENDER_DOMAINS = (
    "greenhouse.io", "greenhouse-mail.io", "boards.greenhouse.io",
    "lever.co", "hire.lever.co",
    "ashbyhq.com", "ashbyhq.io",
    "workable.com", "workablemail.com",
    "myworkdayjobs.com",
    "smartrecruiters.com", "smartrecruiters-mail.com",
    "bamboohr.com",
    "breezy.hr", "jazzhr.com", "recruitee.com", "icims.com",
    "jobvite.com", "successfactors.com",
)
# Domain "stems" used for company extraction — when sender is from one of these
# we look at the display name instead of the domain.
_ATS_DOMAIN_STEMS = {
    "greenhouse", "lever", "ashbyhq", "workable", "myworkdayjobs",
    "smartrecruiters", "bamboohr", "icims", "jobvite",
}

# Body length cap for pattern matching — same trade as the LLM classifier.
BODY_CHARS = 2000

_ROLE_PATTERNS = [
    re.compile(r"for the ([A-Z][\w\s,&/-]{3,60}?)\s+(?:position|role|job|opportunity)", re.IGNORECASE),
    re.compile(r"applying (?:to|for) the ([A-Z][\w\s,&/-]{3,60}?)\s+(?:position|role|job)", re.IGNORECASE),
    re.compile(r"the ([A-Z][\w\s,&/-]{3,60}?)\s+(?:role|position|opening) at", re.IGNORECASE),
    re.compile(r"\bre:\s+([A-Z][\w\s,&/-]{3,60}?)\s+\(", re.IGNORECASE),
]


def _ignore(msg: dict, reason: str) -> dict:
    return {
        "id": msg["id"],
        "action_type": "ignore",
        "company": "",
        "role": "",
        "status": "",
        "confidence": 0.0,
        "reasoning": reason,
    }


def _detect_status(text: str) -> tuple[str | None, str]:
    """Return (status, matched_pattern_or_empty)."""
    for status, patterns in _COMPILED:
        for pat in patterns:
            if pat.search(text):
                return status, pat.pattern
    return None, ""


def _extract_company(sender_display: str, sender_email: str, body_excerpt: str, known_companies: list[dict]) -> str:
    """Best-effort company extraction. Tries known companies first (highest
    signal), falls back to sender domain, then sender display name."""
    haystack = f"{sender_display}\n{body_excerpt}".lower()
    for c in known_companies:
        name = (c.get("name") or "").strip()
        if not name or len(name) < 3:
            continue
        if name.lower() in haystack:
            return name

    domain = sender_email.split("@")[-1] if "@" in sender_email else ""
    parts = [p for p in domain.split(".") if p]
    if len(parts) >= 2:
        stem = parts[-2].lower()
        if stem in _ATS_DOMAIN_STEMS:
            # ATS-relayed mail — domain doesn't tell us the company. Try display.
            display = re.sub(r"<[^>]+>", "", sender_display).strip().strip('"').strip()
            display_lower = display.lower()
            if display and "noreply" not in display_lower and "no-reply" not in display_lower:
                # Strip "via Greenhouse" / "Talent Team" type suffixes.
                display = re.sub(r"\s+(via|through)\s+\w+.*$", "", display, flags=re.IGNORECASE)
                display = re.sub(r"\s+(recruiting|talent|careers|hr)\s+team\s*$", "", display, flags=re.IGNORECASE)
                return display.strip()
            return ""
        return parts[-2].title()
    return ""


def _extract_role(subject: str, body: str) -> str:
    text = f"{subject}\n{body[:500]}"
    for pat in _ROLE_PATTERNS:
        m = pat.search(text)
        if m:
            role = m.group(1).strip().rstrip(",.")
            return role
    return ""


def classify_one(msg: dict, known_companies: list[dict] | None = None) -> dict:
    subject = msg.get("subject", "") or ""
    body = (msg.get("body_text") or msg.get("snippet", "") or "")[:BODY_CHARS]
    sender = (msg.get("from_email") or "").lower()
    sender_display = msg.get("from", "") or ""

    text = f"{subject}\n{body}"
    status, matched_pattern = _detect_status(text)
    is_ats = any(d in sender for d in ATS_SENDER_DOMAINS)

    if not status and not is_ats:
        return _ignore(msg, "no application keyword and not from ATS sender")

    if not status and is_ats:
        # ATS domain but no specific status keyword — likely a generic confirmation.
        status = "applied"
        matched_pattern = "ats sender heuristic"

    company = _extract_company(sender_display, sender, body, known_companies or [])
    role = _extract_role(subject, body)
    action_type = "new_application" if status == "applied" else "status_update"

    # Confidence floor matches the LLM classifier's MIN_CONFIDENCE (0.6) so the
    # sync orchestrator's threshold filter still works without changes.
    if is_ats and status:
        confidence = 0.85
    elif status:
        confidence = 0.7
    else:
        confidence = 0.0

    return {
        "id": msg["id"],
        "action_type": action_type,
        "company": company,
        "role": role,
        "status": status,
        "confidence": confidence,
        "reasoning": f"keyword match: {matched_pattern[:80]}" if matched_pattern else "ats sender",
    }


def classify_messages(messages: list[dict], known_companies: list[dict] | None = None) -> list[dict]:
    """Pure-regex classification — no API calls. Same return shape as the LLM
    classifier so the sync orchestrator can use either interchangeably."""
    return [classify_one(m, known_companies) for m in messages]
