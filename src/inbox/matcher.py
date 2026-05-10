"""Match a classified email to an existing applications.json entry."""
import re

_STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "or", "to", "at", "in", "on",
    "engineer", "engineering", "software", "developer", "intern", "i", "ii", "iii",
    "junior", "senior", "staff", "lead", "principal", "remote", "us", "usa",
}

# Sender-domain stems that don't represent the actual employer — these are ATS
# relays / outsourced talent platforms, so "@greenhouse.io" tells us nothing
# about which company sent the mail.
_ATS_RELAY_STEMS = {
    "greenhouse", "greenhouse-mail", "lever", "ashbyhq", "workable",
    "workablemail", "myworkdayjobs", "smartrecruiters", "bamboohr",
    "icims", "jobvite", "avature", "successfactors", "breezy", "jazzhr",
    "recruitee",
}


def _normalize_company(name: str) -> str:
    """Lower, strip suffixes (Inc, LLC, Corp), collapse whitespace."""
    if not name:
        return ""
    s = name.lower().strip()
    # Strip common suffixes
    s = re.sub(r",?\s+(inc|llc|ltd|corp|corporation|co|gmbh|sa|ag|plc|holdings|labs)\.?$", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _role_tokens(role: str) -> set[str]:
    if not role:
        return set()
    s = role.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    tokens = {t for t in s.split() if t and t not in _STOPWORDS}
    return tokens


def _company_candidates_from_message(msg: dict | None, applications: list[dict]) -> list[str]:
    """Pull additional company candidates from the raw message — used as
    fallbacks when the classifier-extracted company is missing or didn't match.

    Three sources, each robust to one kind of failure:
      1. Sender domain stem (skips ATS relays where the domain is meaningless)
      2. Subject prefix like "Bloomberg - Thank you for your application"
      3. Body scan for any known company name from applications.json
    """
    if not msg:
        return []
    out: list[str] = []

    sender = (msg.get("from_email") or "").lower()
    if "@" in sender:
        domain = sender.rsplit("@", 1)[-1]
        parts = [p for p in domain.split(".") if p]
        if len(parts) >= 2 and parts[-2] not in _ATS_RELAY_STEMS:
            out.append(_normalize_company(parts[-2]))

    subject = msg.get("subject", "") or ""
    prefix = re.match(r"^([A-Z][\w&.,'\s-]{2,40}?)\s*[-–:]\s+", subject)
    if prefix:
        out.append(_normalize_company(prefix.group(1).rstrip(",.")))

    body = (msg.get("body_text") or msg.get("snippet") or "")[:2000]
    for app in applications:
        name = (app.get("company") or "").strip()
        if not name or len(name) < 3:
            continue
        if re.search(rf"\b{re.escape(name)}\b", body, re.IGNORECASE):
            out.append(_normalize_company(name))

    return [c for c in out if c]


def match_application(
    classified: dict,
    applications: list[dict],
    *,
    thread_id: str = "",
    msg: dict | None = None,
) -> dict:
    """Decide whether a classified email points at an existing entry.

    `msg` (optional) lets the matcher pull additional company candidates from
    the raw email — sender domain, subject prefix, body scan against known
    applications. Helps when the classifier extracts the wrong company or the
    confirmation/rejection arrive from different sender systems (e.g. Bloomberg
    confirmations from `bloomberg.avature.net`, rejections from Bloomberg LP).

    Returns:
        {"resolution": "match", "target_idx": int}
        {"resolution": "ambiguous", "candidates": [int, ...]}
        {"resolution": "no_match"}
    """
    if thread_id:
        for i, app in enumerate(applications):
            if thread_id in (app.get("email_thread_ids") or []):
                return {"resolution": "match", "target_idx": i}

    # Collect candidate company strings from every available source. Order is
    # preserved (classifier first, then message-derived) for debugging clarity,
    # but matching treats them as a set.
    candidates: list[str] = []
    cls_company = _normalize_company(classified.get("company", ""))
    if cls_company:
        candidates.append(cls_company)
    candidates.extend(_company_candidates_from_message(msg, applications))
    seen: set[str] = set()
    candidates = [c for c in candidates if not (c in seen or seen.add(c))]
    if not candidates:
        return {"resolution": "no_match"}

    company_hits: list[int] = []
    matched: set[int] = set()
    for tc in candidates:
        for i, app in enumerate(applications):
            if i in matched:
                continue
            norm = _normalize_company(app.get("company", ""))
            if not norm:
                continue
            if norm == tc or tc in norm or norm in tc:
                company_hits.append(i)
                matched.add(i)

    if not company_hits:
        return {"resolution": "no_match"}
    if len(company_hits) == 1:
        return {"resolution": "match", "target_idx": company_hits[0]}

    target_role_tokens = _role_tokens(classified.get("role", ""))
    if target_role_tokens:
        scored = []
        for idx in company_hits:
            app_tokens = _role_tokens(applications[idx].get("role", ""))
            overlap = len(app_tokens & target_role_tokens)
            scored.append((overlap, idx))
        scored.sort(reverse=True)
        if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            return {"resolution": "match", "target_idx": scored[0][1]}

    return {"resolution": "ambiguous", "candidates": company_hits}
