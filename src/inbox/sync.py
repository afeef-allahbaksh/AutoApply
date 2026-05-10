"""Sync orchestrator — fetch -> prefilter -> classify -> match -> write proposals.

Long syncs run on a background thread so the UI returns immediately and can
poll for progress. Each chunk's proposals are persisted as soon as they're
matched, so the kanban fills in progressively rather than waiting for the
entire batch to finish.
"""
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.inbox import auth, classify, fetch, matcher
from src.inbox.fetch import _chunked
from src.profile_loader import Profile
from src.schemas import validate_inbox_state, validate_proposals

INITIAL_LOOKBACK_DAYS = 365
DEFAULT_MAX_MESSAGES = 1000
DEEP_LOOKBACK_DAYS = 1825
DEEP_MAX_MESSAGES = 5000
MIN_CONFIDENCE = 0.6
# Messages per pipeline chunk — drives how often the UI sees a progress update
# and how often new proposals appear on the kanban. ~2 batches per chunk at
# default BATCH_SIZE=15, ~6-10s per chunk.
CLASSIFY_CHUNK_SIZE = 30
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


def status_path(profile_name: str) -> Path:
    return auth.imap_dir(profile_name) / "sync_status.json"


def _read_status_raw(profile_name: str) -> dict:
    """Internal: read status file as-is, no liveness check. Used by writers."""
    p = status_path(profile_name)
    if not p.exists():
        return {}
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def read_sync_status(profile_name: str) -> dict:
    """Public: status with stale-detection. If the file says running but no
    live worker thread exists in this process (e.g. uvicorn restarted mid-sync),
    surface that as `interrupted` so the UI can stop polling."""
    data = _read_status_raw(profile_name)
    if not data:
        return {"state": "idle"}
    if data.get("state") == "running":
        with _thread_registry_lock:
            t = _active_threads.get(profile_name)
            if t is None or not t.is_alive():
                return {
                    **data,
                    "state": "interrupted",
                    "message": "Sync interrupted (worker stopped). Click Sync to retry.",
                }
    return data


def _write_sync_status(profile_name: str, **fields) -> None:
    """Atomic read-modify-write. Tmp file + rename so a mid-write crash leaves
    the previous status intact rather than a half-written JSON."""
    current = _read_status_raw(profile_name)
    current.update(fields)
    p = status_path(profile_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(current, f, indent=2)
        f.write("\n")
    tmp.replace(p)


def request_cancel(profile_name: str) -> bool:
    """Flip the cancel flag. The worker checks this at every chunk boundary
    and exits cleanly (without committing state, so a re-sync resumes)."""
    if _read_status_raw(profile_name).get("state") != "running":
        return False
    _write_sync_status(profile_name, cancel_requested=True, message="Cancelling…")
    return True


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


def _proposal_lock(profile_name: str):
    """Per-profile lock around proposals.json so the worker thread and UI
    apply/dismiss handlers don't clobber each other's writes."""
    from src.ui.state import profile_lock
    return profile_lock(profile_name)


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


_active_threads: dict[str, threading.Thread] = {}
_thread_registry_lock = threading.Lock()


def start_background_sync(profile_name: str, deep: bool = False) -> tuple[bool, str]:
    """Spawn a daemon thread that runs the sync to completion.

    Returns (started, message). started=False means a sync is already running
    for this profile; the existing one continues.
    """
    with _thread_registry_lock:
        existing = _active_threads.get(profile_name)
        if existing is not None and existing.is_alive():
            return False, "Sync already in progress for this profile."
        # Drop dead Thread references so the dict doesn't accumulate over weeks.
        if existing is not None:
            _active_threads.pop(profile_name, None)
        # Write the initial running status synchronously *before* starting the
        # thread. Without this, the response that just spawned the worker can
        # render before the worker has written its first status update — the
        # banner wouldn't appear until the next poll.
        _write_sync_status(
            profile_name,
            state="running",
            deep=deep,
            started_at=datetime.now(timezone.utc).isoformat(),
            completed_at=None,
            messages_seen=0,
            after_prefilter=0,
            classified=0,
            new_proposals=0,
            error=None,
            cancel_requested=False,
            message="Connecting to inbox…",
        )
        t = threading.Thread(
            target=_sync_worker,
            args=(profile_name, deep),
            daemon=True,
            name=f"inbox-sync-{profile_name}",
        )
        _active_threads[profile_name] = t
        t.start()
    return True, "Sync started."


def _sync_worker(profile_name: str, deep: bool) -> None:
    """Outer wrapper for the background thread — runs the pipeline, traps errors,
    normalizes terminal state (idle / cancelled / error)."""
    try:
        _run_sync_streaming(profile_name, deep)
        # Pipeline returned cleanly — check if it was a cancel-induced exit.
        final = _read_status_raw(profile_name)
        if final.get("cancel_requested"):
            _write_sync_status(
                profile_name,
                state="cancelled",
                completed_at=datetime.now(timezone.utc).isoformat(),
                message="Sync cancelled. Re-click Sync to resume.",
                cancel_requested=False,
            )
        else:
            _write_sync_status(
                profile_name,
                state="idle",
                completed_at=datetime.now(timezone.utc).isoformat(),
                message="Sync complete.",
            )
    except Exception as e:  # noqa: BLE001 — top-of-thread catch-all is intentional
        _write_sync_status(
            profile_name,
            state="error",
            completed_at=datetime.now(timezone.utc).isoformat(),
            error=str(e)[:240],
            cancel_requested=False,
        )
        print(f"[inbox] sync worker crashed: {e}")


def _run_sync_streaming(profile_name: str, deep: bool) -> None:
    """The actual pipeline. Writes proposals incrementally; updates sync_status."""
    creds = auth.load_credentials(profile_name)
    if not creds:
        raise RuntimeError("Inbox not connected. Connect on the Settings page first.")

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
    _write_sync_status(profile_name, message="Fetching headers from IMAP…")
    headers = fetch.list_message_headers_since(creds, since_dt, max_results=max_messages)
    print(f"[inbox] fetched {len(headers)} headers")

    new_messages = [m for m in headers if m["id"] not in processed_ids]
    survivors = [m for m in new_messages if _prefilter(m)]
    print(f"[inbox] {len(new_messages)} new, {len(survivors)} survived prefilter")
    _write_sync_status(
        profile_name,
        messages_seen=len(new_messages),
        after_prefilter=len(survivors),
        message=f"Fetching bodies for {len(survivors)} candidates…",
    )

    if not survivors:
        # No bodies to fetch, nothing to classify. Still bump state so the next
        # sync starts from now.
        _commit_state(profile_name, processed_ids, new_messages)
        return

    fetch.populate_bodies(creds, survivors)
    print(f"[inbox] bodies fetched; classifying in chunks of {CLASSIFY_CHUNK_SIZE}…")
    _write_sync_status(profile_name, message="Classifying with Claude…")

    classified_total = 0
    new_proposals_total = 0
    cancelled = False

    for chunk in _chunked(survivors, CLASSIFY_CHUNK_SIZE):
        # Cooperative cancel — checked between chunks so we don't abort
        # mid-API-call. ~6-10s max latency before the worker exits.
        if _read_status_raw(profile_name).get("cancel_requested"):
            cancelled = True
            print(f"[inbox] cancel requested at {classified_total}/{len(survivors)} classified")
            break

        chunk_classified = classify.classify_messages(chunk)
        chunk_new = _propose_for_chunk(profile_name, chunk, chunk_classified)

        if chunk_new:
            with _proposal_lock(profile_name):
                latest = load_proposals(profile_name)
                latest_ids = {p["id"] for p in latest}
                additions = [p for p in chunk_new if p["id"] not in latest_ids]
                if additions:
                    save_proposals(profile_name, latest + additions)
            new_proposals_total += len(chunk_new)

        classified_total += len(chunk)
        _write_sync_status(
            profile_name,
            classified=classified_total,
            new_proposals=new_proposals_total,
            message=(
                f"Classified {classified_total}/{len(survivors)} · "
                f"{new_proposals_total} proposals so far"
            ),
        )

    # Only commit state on clean completion. On cancel, leave processed_message_ids
    # untouched so a re-sync picks up where we left off (proposed messages skip
    # via the in-loop existing_ids check).
    if not cancelled:
        _commit_state(profile_name, processed_ids, new_messages)
    print(f"[inbox] sync done: {new_proposals_total} new proposals (cancelled={cancelled})")


def _propose_for_chunk(profile_name: str, chunk: list[dict], classified: list[dict]) -> list[dict]:
    """Match each classified message to an application and build proposals.

    Re-loads applications fresh per chunk so updates the user makes during the
    sync (applying/dismissing proposals, manual adds) feed into matching.
    """
    profile = Profile(profile_name)
    applications = list(profile.applications)
    existing_ids = {p["id"] for p in load_proposals(profile_name)}
    out = []
    for msg, c in zip(chunk, classified):
        if c["action_type"] == "ignore":
            continue
        if c["confidence"] < MIN_CONFIDENCE:
            continue
        if msg["id"] in existing_ids:
            continue
        match = matcher.match_application(c, applications, thread_id=msg.get("thread_id", ""))
        if c["action_type"] == "status_update" and match["resolution"] == "no_match":
            continue
        out.append(_build_proposal(msg, c, match))
        existing_ids.add(msg["id"])
    return out


def _commit_state(profile_name: str, processed_ids: set, new_messages: list[dict]) -> None:
    state = load_state(profile_name)
    state["last_sync_at"] = datetime.now(timezone.utc).isoformat()
    combined_ids = list(processed_ids | {m["id"] for m in new_messages})
    state["processed_message_ids"] = combined_ids[-MAX_PROCESSED_IDS:]
    save_state(profile_name, state)
