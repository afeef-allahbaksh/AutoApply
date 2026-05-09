"""Sync orchestrator — fetch -> prefilter -> classify -> match -> write proposals."""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.inbox import auth, classify, fetch, matcher
from src.profile_loader import Profile
from src.schemas import validate_inbox_state, validate_proposals

INITIAL_LOOKBACK_DAYS = 365
DEFAULT_MAX_MESSAGES = 1000
DEEP_LOOKBACK_DAYS = 1825
DEEP_MAX_MESSAGES = 5000
MIN_CONFIDENCE = 0.6
# Cap processed_message_ids so the state.json doesn't grow unbounded over months
# of syncs. New messages are queried by `after:` timestamp, so the dedup window
# only needs to cover the lookback period of the most recent sync.
MAX_PROCESSED_IDS = 10000

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
    return auth.imap_dir(profile_name) / "state.json"


def proposals_path(profile_name: str) -> Path:
    return auth.imap_dir(profile_name) / "proposals.json"


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


def enrich_proposals(profile_name: str) -> list[dict]:
    """Return proposals with candidate labels resolved for ambiguous matches.

    The labels include company + role so the review queue can render a meaningful
    picker — proposals.json stores only candidate indices.
    """
    proposals = load_proposals(profile_name)
    if not proposals:
        return []
    profile = Profile(profile_name)
    apps = list(profile.applications)
    out = []
    for p in proposals:
        e = dict(p)
        if p.get("resolution") == "ambiguous":
            labels = []
            for cidx in p.get("candidates") or []:
                if 0 <= cidx < len(apps):
                    role = (apps[cidx].get("role") or "")[:40]
                    labels.append({"idx": cidx, "label": f"{apps[cidx].get('company', '?')} · {role}"})
            e["candidate_labels"] = labels
        out.append(e)
    return out


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


def sync_now(profile_name: str, deep: bool = False) -> dict:
    """Run a full sync. Returns a summary dict with counts.

    Steps: header-fetch -> prefilter -> body-fetch survivors -> classify -> match -> proposals.

    `deep=True` ignores last_sync_at and pulls 5 years / 5000 messages. Use it once on
    initial setup to backfill history; thereafter the incremental defaults are enough.
    """
    creds = auth.load_credentials(profile_name)
    if not creds:
        return {"ok": False, "error": "Inbox not connected. Connect on the Settings page first."}

    state = load_state(profile_name)
    processed_ids = set(state.get("processed_message_ids", []))

    if deep:
        since_dt = datetime.now(timezone.utc) - timedelta(days=DEEP_LOOKBACK_DAYS)
        max_messages = DEEP_MAX_MESSAGES
    else:
        last_sync_str = state.get("last_sync_at") or ""
        if last_sync_str:
            since_dt = datetime.fromisoformat(last_sync_str)
        else:
            since_dt = datetime.now(timezone.utc) - timedelta(days=INITIAL_LOOKBACK_DAYS)
        max_messages = DEFAULT_MAX_MESSAGES

    print(f"[inbox] sync starting (deep={deep}, since={since_dt.date()}, max={max_messages})")
    try:
        headers = fetch.list_message_headers_since(creds, since_dt, max_results=max_messages)
    except Exception as e:
        return {"ok": False, "error": f"Inbox header fetch failed: {e}"}
    print(f"[inbox] fetched {len(headers)} headers")

    new_messages = [m for m in headers if m["id"] not in processed_ids]
    survivors = [m for m in new_messages if _prefilter(m)]
    print(f"[inbox] {len(new_messages)} new, {len(survivors)} survived prefilter")

    if survivors:
        try:
            fetch.populate_bodies(creds, survivors)
        except Exception as e:
            return {"ok": False, "error": f"Inbox body fetch failed: {e}"}
        print(f"[inbox] bodies fetched; classifying...")

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
    combined_ids = list(processed_ids | {m["id"] for m in new_messages})
    state["processed_message_ids"] = combined_ids[-MAX_PROCESSED_IDS:]
    save_state(profile_name, state)
    print(f"[inbox] sync done: {len(new_proposals)} new proposals")

    return {
        "ok": True,
        "messages_seen": len(new_messages),
        "after_prefilter": len(survivors),
        "classified": len(classified),
        "ignored_count": sum(1 for c in classified if c["action_type"] == "ignore"),
        "new_proposals": len(new_proposals),
    }
