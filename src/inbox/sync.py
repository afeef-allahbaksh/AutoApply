"""Sync orchestrator — fetch -> prefilter -> classify -> match -> write proposals."""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.inbox import auth, classify, fetch, matcher
from src.profile_loader import Profile
from src.schemas import validate_inbox_state, validate_proposals

INITIAL_LOOKBACK_DAYS = 30
DEFAULT_MAX_MESSAGES = 200
MIN_CONFIDENCE = 0.6

# Prefilter — skip these obvious-noise senders before sending to Claude.
PREFILTER_IGNORE_SENDERS = {
    "noreply@linkedin.com",
    "jobs-noreply@linkedin.com",
    "jobalerts-noreply@linkedin.com",
    "messages-noreply@linkedin.com",
    "hit-reply@linkedin.com",
    "inmail-hit-reply@linkedin.com",
    "no-reply@indeed.com",
    "indeedapply@indeed.com",
    "alert@indeed.com",
    "noreply@glassdoor.com",
    "no-reply@hired.com",
    "noreply@daily.dev",
}

NOISE_SUBJECT_PATTERNS = [
    r"\d+\s+new\s+jobs?",
    r"jobs you might like",
    r"new jobs? match",
    r"daily digest",
    r"weekly digest",
    r"newsletter",
    r"unread messages",
    r"connection request",
    r"endorsed your",
    r"your application was viewed",
]
NOISE_REGEX = re.compile("|".join(NOISE_SUBJECT_PATTERNS), re.IGNORECASE)


def state_path(profile_name: str) -> Path:
    return auth.gmail_dir(profile_name) / "state.json"


def proposals_path(profile_name: str) -> Path:
    return auth.gmail_dir(profile_name) / "proposals.json"


def load_state(profile_name: str) -> dict:
    p = state_path(profile_name)
    if not p.exists():
        return {"last_sync_at": "", "processed_message_ids": []}
    with open(p) as f:
        return json.load(f)


def save_state(profile_name: str, state: dict) -> None:
    validate_inbox_state(state)
    with open(state_path(profile_name), "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def load_proposals(profile_name: str) -> list[dict]:
    p = proposals_path(profile_name)
    if not p.exists():
        return []
    with open(p) as f:
        return json.load(f)


def save_proposals(profile_name: str, proposals: list[dict]) -> None:
    validate_proposals(proposals)
    with open(proposals_path(profile_name), "w") as f:
        json.dump(proposals, f, indent=2)
        f.write("\n")


def remove_proposal(profile_name: str, proposal_id: str) -> dict | None:
    proposals = load_proposals(profile_name)
    found = None
    remaining = []
    for p in proposals:
        if p["id"] == proposal_id and found is None:
            found = p
        else:
            remaining.append(p)
    if found is not None:
        save_proposals(profile_name, remaining)
    return found


def _prefilter(msg: dict) -> bool:
    if msg.get("from_email", "") in PREFILTER_IGNORE_SENDERS:
        return False
    if NOISE_REGEX.search(msg.get("subject", "")):
        return False
    return True


def _build_proposal(msg: dict, classified: dict, match: dict) -> dict:
    return {
        "id": msg["id"],
        "thread_id": msg.get("thread_id", ""),
        "message_subject": msg.get("subject", ""),
        "message_from": msg.get("from", ""),
        "message_received_at": msg.get("received_at", ""),
        "message_url": fetch.thread_url(msg["id"]),
        "action_type": classified["action_type"],
        "company": classified.get("company", ""),
        "role": classified.get("role", ""),
        "proposed_status": classified.get("status", ""),
        "confidence": classified["confidence"],
        "reasoning": classified.get("reasoning", ""),
        "resolution": match["resolution"],
        "target_idx": match.get("target_idx"),
        "candidates": match.get("candidates", []),
    }


def sync_now(profile_name: str, max_messages: int = DEFAULT_MAX_MESSAGES) -> dict:
    """Run a full sync. Returns a summary dict with counts.

    Steps: fetch new messages -> prefilter -> classify -> match -> write proposals.
    """
    creds = auth.load_credentials(profile_name)
    if not creds:
        return {"ok": False, "error": "Gmail not connected. Connect on the Settings page first."}

    state = load_state(profile_name)
    processed_ids = set(state.get("processed_message_ids", []))

    last_sync_str = state.get("last_sync_at") or ""
    if last_sync_str:
        since_dt = datetime.fromisoformat(last_sync_str)
    else:
        since_dt = datetime.now(timezone.utc) - timedelta(days=INITIAL_LOOKBACK_DAYS)

    try:
        messages = fetch.list_messages_since(creds, since_dt, max_results=max_messages)
    except Exception as e:
        return {"ok": False, "error": f"Gmail fetch failed: {e}"}

    new_messages = [m for m in messages if m["id"] not in processed_ids]
    survivors = [m for m in new_messages if _prefilter(m)]

    classified = classify.classify_messages(survivors) if survivors else []

    profile = Profile(profile_name)
    applications = list(profile.applications)

    existing_proposals = load_proposals(profile_name)
    existing_ids = {p["id"] for p in existing_proposals}
    new_proposals = []

    for msg, c in zip(survivors, classified):
        if c["action_type"] == "ignore":
            continue
        if c["confidence"] < MIN_CONFIDENCE:
            continue
        if msg["id"] in existing_ids:
            continue
        match = matcher.match_application(c, applications, thread_id=msg.get("thread_id", ""))
        if c["action_type"] == "status_update" and match["resolution"] == "no_match":
            # Don't propose status updates we can't anchor to an entry.
            continue
        new_proposals.append(_build_proposal(msg, c, match))
        existing_ids.add(msg["id"])

    if new_proposals:
        save_proposals(profile_name, existing_proposals + new_proposals)

    state["last_sync_at"] = datetime.now(timezone.utc).isoformat()
    state["processed_message_ids"] = list(processed_ids | {m["id"] for m in new_messages})
    save_state(profile_name, state)

    return {
        "ok": True,
        "messages_seen": len(new_messages),
        "after_prefilter": len(survivors),
        "classified": len(classified),
        "ignored_count": sum(1 for c in classified if c["action_type"] == "ignore"),
        "new_proposals": len(new_proposals),
    }
