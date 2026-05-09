"""Shared kanban / pipeline taxonomy used by multiple route modules."""
from fastapi import HTTPException

from src.profile_loader import Profile

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
        raise HTTPException(status_code=404, detail=str(e))
    return list(profile.applications)


def kanban_groups(apps: list, search: str = "") -> tuple[dict, list]:
    """Bucket applications by status. Returns (columns, closed)."""
    enriched = [{**a, "_idx": i} for i, a in enumerate(apps)]
    if search:
        s = search.lower()
        enriched = [
            a for a in enriched
            if s in a.get("company", "").lower() or s in a.get("role", "").lower()
        ]

    columns = {col: [] for col in KANBAN_COLUMNS}
    closed = []
    for a in enriched:
        st = a.get("status")
        (columns[st] if st in columns else closed).append(a)

    def sort_key(a):
        return a.get("status_updated_at") or a.get("date") or ""

    for col in columns:
        columns[col].sort(key=sort_key, reverse=True)
    closed.sort(key=sort_key, reverse=True)
    return columns, closed
