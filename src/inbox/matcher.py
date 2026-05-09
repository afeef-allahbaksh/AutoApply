"""Match a classified email to an existing applications.json entry."""
import re

_STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "or", "to", "at", "in", "on",
    "engineer", "engineering", "software", "developer", "intern", "i", "ii", "iii",
    "junior", "senior", "staff", "lead", "principal", "remote", "us", "usa",
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


def match_application(
    classified: dict,
    applications: list[dict],
    *,
    thread_id: str = "",
) -> dict:
    """Decide whether a classified email points at an existing entry.

    Returns:
        {"resolution": "match", "target_idx": int}
        {"resolution": "ambiguous", "candidates": [int, ...]}
        {"resolution": "no_match"}
    """
    # 1. Thread-id match — strongest signal. If any existing entry already linked this thread, reuse.
    if thread_id:
        for i, app in enumerate(applications):
            if thread_id in (app.get("email_thread_ids") or []):
                return {"resolution": "match", "target_idx": i}

    target_company = _normalize_company(classified.get("company", ""))
    if not target_company:
        return {"resolution": "no_match"}

    # 2. Company match (case-insensitive substring or normalized equality)
    company_hits: list[int] = []
    for i, app in enumerate(applications):
        norm = _normalize_company(app.get("company", ""))
        if not norm:
            continue
        if norm == target_company or target_company in norm or norm in target_company:
            company_hits.append(i)

    if not company_hits:
        return {"resolution": "no_match"}
    if len(company_hits) == 1:
        return {"resolution": "match", "target_idx": company_hits[0]}

    # 3. Multiple company hits — narrow by role token overlap
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
