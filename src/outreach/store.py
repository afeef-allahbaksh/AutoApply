"""Outreach record persistence.

`outreach.json` lives under each profile dir and holds the list of cold-outreach
records. Schema (id, company, contact_*, draft_*, status, timestamps,
linked_job_idx) is documented as-is — there's no jsonschema validator for
outreach records (deliberate: the data is fully UI-driven, so the form is the
schema).

Writes go through `Profile.save_outreach` for atomicity. `load_outreach` and
`save_outreach` are kept as thin module-level wrappers so existing callers
(the cold-email route, tests) don't have to switch import paths.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from src.profile_loader import PROFILES_DIR, _atomic_write_json

STATUSES = ("draft", "sent", "replied", "no_reply", "closed")
STATUS_BADGE = {
    "draft": "badge-neutral",
    "sent": "badge-info",
    "replied": "badge-success",
    "no_reply": "badge-warning",
    "closed": "badge-neutral",
}


def outreach_path(profile_name: str) -> Path:
    return PROFILES_DIR / profile_name / "outreach.json"


def load_outreach(profile_name: str) -> list[dict]:
    if not profile_name:
        return []
    p = outreach_path(profile_name)
    if not p.exists():
        return []
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def save_outreach(profile_name: str, records: list[dict]) -> None:
    """Atomic write to `outreach.json` (no schema — UI-driven shape)."""
    _atomic_write_json(outreach_path(profile_name), records)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
