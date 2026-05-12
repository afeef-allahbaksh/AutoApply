"""Shared kanban / pipeline taxonomy used by multiple route modules."""
import json

from fastapi import HTTPException

from src.profile_loader import PROFILES_DIR, Profile

ALL_STATUSES = [
    "applied", "screen", "technical", "onsite", "offer",
    "rejected", "failed", "review_pending", "skipped",
]

PIPELINE = ["applied", "screen", "technical", "onsite", "offer"]
KANBAN_COLUMNS = PIPELINE + ["rejected"]

# Maps used by templates so a status rename only touches one place.
STATUS_BADGE_CLASS = {
    "applied": "badge-info",
    "screen": "badge-info",
    "technical": "badge-info",
    "onsite": "badge-info",
    "offer": "badge-success",
    "rejected": "badge-danger",
    "failed": "badge-danger",
    "review_pending": "badge-warning",
    "skipped": "badge-neutral",
}

COLUMN_HEADERS = {
    "applied": "Applied",
    "screen": "Screen",
    "technical": "Technical",
    "onsite": "Onsite",
    "offer": "Offer",
    "rejected": "Rejected",
}


def load_applications(profile_name: str) -> list:
    try:
        profile = Profile(profile_name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return list(profile.applications)


def load_jobs(profile_name: str) -> list[dict]:
    """Read profiles/{name}/jobs.json. Returns empty list for any failure mode
    (missing profile_name, missing file, malformed JSON) so callers can render
    a sensible empty state instead of crashing the route."""
    if not profile_name:
        return []
    p = PROFILES_DIR / profile_name / "jobs.json"
    if not p.exists():
        return []
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def _enrich_and_filter(apps: list, search: str = "") -> list:
    """Attach the original index as `_idx` (needed by mutation routes) and
    apply the company/role substring filter shared between kanban + timeline."""
    enriched = [{**a, "_idx": i} for i, a in enumerate(apps)]
    if search:
        s = search.lower()
        enriched = [
            a for a in enriched
            if s in a.get("company", "").lower() or s in a.get("role", "").lower()
        ]
    return enriched


def _sort_key(a: dict) -> str:
    return a.get("status_updated_at") or a.get("date") or ""


def kanban_groups(apps: list, search: str = "") -> tuple[dict, list]:
    """Bucket applications by status. Returns (columns, closed)."""
    enriched = _enrich_and_filter(apps, search)
    columns = {col: [] for col in KANBAN_COLUMNS}
    closed = []
    for a in enriched:
        st = a.get("status")
        (columns[st] if st in columns else closed).append(a)
    for col in columns:
        columns[col].sort(key=_sort_key, reverse=True)
    closed.sort(key=_sort_key, reverse=True)
    return columns, closed


def timeline_rows(apps: list, search: str = "") -> list:
    """Flat chronological view of applications — most recent activity first
    (by status_updated_at, falling back to date). Uses the same search filter
    as kanban_groups so toggling views preserves what the user is looking at."""
    enriched = _enrich_and_filter(apps, search)
    enriched.sort(key=_sort_key, reverse=True)
    return enriched
