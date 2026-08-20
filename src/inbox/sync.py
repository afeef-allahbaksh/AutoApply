"""Sync orchestrator — fetch -> prefilter -> classify -> match -> write proposals.

Long syncs run on a background thread so the UI returns immediately and can
poll for progress. Each chunk's proposals are persisted as soon as they're
matched, so the kanban fills in progressively rather than waiting for the
entire batch to finish.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.inbox import auth, classify, fetch, keyword_classify, matcher
from src.inbox.fetch import _chunked
from src.profile_loader import Profile
from src.schemas import validate_inbox_state, validate_proposals
from src.tasks import runner

INITIAL_LOOKBACK_DAYS = 365
DEEP_LOOKBACK_DAYS = 1825
# Per-run budgets, counted in messages that actually reach the classifier.
# Already-processed and prefiltered messages cost a header fetch, not an API
# call, so re-scanning a covered range is cheap and doesn't burn budget.
DEFAULT_MAX_NEW = 1000
DEEP_MAX_NEW = 5000
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


def _task_key(profile_name: str) -> str:
    return f"inbox-sync:{profile_name}"


def read_sync_status(profile_name: str) -> dict:
    """Status with stale-detection. If the file says running but no live worker
    thread exists in this process (e.g. uvicorn restarted mid-sync), surface
    that as `interrupted` so the UI can stop polling."""
    return runner.read_status(
        status_path(profile_name),
        _task_key(profile_name),
        interrupted_message="Sync interrupted (worker stopped). Click Sync to retry.",
    )


def _write_sync_status(profile_name: str, **fields) -> None:
    """Thin wrapper so progress-update sites in `_run_sync_streaming` keep their
    original call signature."""
    runner.write_status(status_path(profile_name), **fields)


def request_cancel(profile_name: str) -> bool:
    """Flip the cancel flag. The worker checks this at every chunk boundary
    and exits cleanly (without committing state, so a re-sync resumes)."""
    return runner.request_cancel(status_path(profile_name))


def _empty_state() -> dict:
    return {
        "last_sync_at": "",
        "uidvalidity": 0,
        "horizon_uid": 0,
        "covered_low_uid": 0,
        "covered_high_uid": 0,
        "coverage_classifier": "llm",
        "processed_message_ids": [],
    }


def load_state(profile_name: str) -> dict:
    """Read state, filling in v2 coverage fields.

    A v1 state (timestamp watermark, no UID fields) migrates to empty coverage:
    the first v2 sync then walks the whole lookback window newest-first, and the
    retained `processed_message_ids` make the already-classified part of that
    walk cost header fetches only. That is what heals a window a v1 truncation
    had silently skipped.
    """
    p = state_path(profile_name)
    state = _empty_state()
    if not p.exists():
        return state
    with open(p) as f:
        state.update(json.load(f))
    return state


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


def start_background_sync(profile_name: str, deep: bool = False, classifier: str = "llm") -> tuple[bool, str]:
    """Spawn a daemon thread that runs the sync to completion.

    `classifier`: "llm" (Claude — paid, accurate) or "keyword" (regex — free,
    catches common patterns). Returns (started, message).
    """
    if classifier not in ("llm", "keyword"):
        return False, f"Unknown classifier: {classifier}"
    started, msg = runner.start_task(
        task_key=_task_key(profile_name),
        status_file=status_path(profile_name),
        target=_sync_worker,
        args=(profile_name, deep, classifier),
        initial_status={
            "deep": deep,
            "classifier": classifier,
            "messages_seen": 0,
            "after_prefilter": 0,
            "classified": 0,
            "new_proposals": 0,
            "message": "Connecting to inbox…",
        },
        thread_name=f"inbox-sync-{profile_name}",
        already_running_msg="Sync already in progress for this profile.",
    )
    return started, ("Sync started." if started else msg)


def _sync_worker(profile_name: str, deep: bool, classifier: str = "llm") -> None:
    """Background thread target — runs the pipeline through the shared runner so
    error traps and terminal-state normalization (idle / cancelled / error) are
    consistent across task kinds."""
    runner.run_with_terminal_status(
        status_path(profile_name),
        # The pipeline returns its own completion line — it's the only thing that
        # knows whether any backlog is left, and "Sync complete." would be a lie.
        work=lambda: _run_sync_streaming(profile_name, deep, classifier),
        idle_message="Sync complete.",
        cancelled_message="Sync cancelled. Re-click Sync to resume.",
        log_prefix="[inbox] sync",
    )


# Server-side IMAP filter for free mode — match anything in our keyword set
# at the inbox level so we don't pay to fetch + parse 5000 messages we'd
# discard locally anyway. False-negative risk is the same as the keyword
# classifier (which is what's deciding things in free mode); LLM mode skips
# this filter so Claude still gets ambiguous messages.
_FREE_MODE_SENDER_TERMS = [
    f'FROM "{d}"' for d in (
        "greenhouse.io", "lever.co", "ashbyhq", "workable",
        "myworkdayjobs", "smartrecruiters", "bamboohr",
        "breezy.hr", "jazzhr", "recruitee", "icims", "jobvite",
    )
]
_FREE_MODE_SUBJECT_TERMS = [
    f'SUBJECT "{kw}"' for kw in (
        "applying", "application", "interview", "interest",
        "screen", "offer", "unfortunately", "next steps",
        "moving forward", "thank you", "thanks for",
        "take-home", "take home", "onsite", "final round",
    )
]


def _imap_or_chain(terms: list[str]) -> str:
    """Right-fold a list of IMAP search keys into a chain of binary ORs.
    [a, b, c, d] -> 'OR (a) OR (b) OR (c) (d)' which the IMAP parser reads as
    OR(a, OR(b, OR(c, d)))."""
    if not terms:
        return ""
    if len(terms) == 1:
        return f"({terms[0]})"
    return f"OR ({terms[0]}) {_imap_or_chain(terms[1:])}"


def _free_mode_search_filter() -> str:
    return _imap_or_chain(_FREE_MODE_SENDER_TERMS + _FREE_MODE_SUBJECT_TERMS)


def _classify_chunk(chunk: list[dict], classifier: str, profile_name: str) -> list[dict]:
    """Dispatch to the requested classifier. Same return shape regardless."""
    if classifier == "keyword":
        from src.discovery import _load_companies
        try:
            known = _load_companies(profile_name)
        except (OSError, ValueError):
            # Missing or malformed companies.json — fall back to empty list so
            # the classifier still runs; company extraction degrades to
            # sender-domain only.
            known = []
        return keyword_classify.classify_messages(chunk, known)
    return classify.classify_messages(chunk)


class _Progress:
    """Running totals for one sync run, shared across both passes."""

    def __init__(self) -> None:
        self.examined = 0
        self.classified = 0
        self.proposals = 0
        self.spent = 0          # messages sent to the classifier (the budget unit)
        self.cancelled = False


def _select_page(page: list[dict], processed_ids: set, remaining: int) -> tuple[list[dict], list[dict], int | None]:
    """Split one newest-first header page into (to_classify, examined, floor_uid).

    Walks UIDs downward and stops the moment the budget is spent, so `floor_uid`
    is the exact UID down to which this page was examined — that, not the page
    boundary, is what coverage advances to. `None` means the budget was already
    gone and nothing here was looked at.
    """
    to_classify: list[dict] = []
    examined: list[dict] = []
    floor: int | None = None
    for msg in page:
        if len(to_classify) >= remaining:
            break
        floor = msg["uid"]
        examined.append(msg)
        if msg["id"] in processed_ids:
            continue
        if not _prefilter(msg):
            continue
        to_classify.append(msg)
    return to_classify, examined, floor


def _walk_uids(
    profile_name: str,
    mailbox: "fetch.Mailbox",
    uids_desc: list[int],
    state: dict,
    processed_ids: set,
    budget: int,
    classifier: str,
    progress: _Progress,
    lower_only: bool,
    prev_low: int = 0,
    prev_high: int = 0,
) -> None:
    """Process UIDs newest-first, committing coverage after every whole page.

    `lower_only` distinguishes the backlog pass (extends the covered block
    downward) from the head pass (also raises `covered_high_uid`, and merges
    with the previous block when the walk reaches it).

    Coverage is committed per page rather than per run so a cancelled sync keeps
    everything it finished — the old all-or-nothing commit threw away an entire
    deep sync on cancel.
    """
    status_file = status_path(profile_name)
    for page in mailbox.iter_header_pages(uids_desc):
        if runner.is_cancel_requested(status_file):
            progress.cancelled = True
            return
        if not page:
            continue

        to_classify, examined, floor = _select_page(page, processed_ids, budget - progress.spent)
        if floor is None:
            return  # budget spent — stop before examining anything in this page

        if to_classify:
            _write_sync_status(
                profile_name,
                message=f"Fetching bodies for {len(to_classify)} candidates…",
            )
            mailbox.populate_bodies(to_classify)
            for chunk in _chunked(to_classify, CLASSIFY_CHUNK_SIZE):
                # Cooperative cancel — checked between chunks so we don't abort
                # mid-API-call. Bailing here leaves this page uncommitted, so the
                # next sync redoes it rather than skipping it.
                if runner.is_cancel_requested(status_file):
                    progress.cancelled = True
                    return
                _classify_and_propose(profile_name, chunk, classifier, progress)

        # Whole page done: only now does it count as covered.
        processed_ids.update(m["id"] for m in examined)
        progress.examined += len(examined)
        progress.spent += len(to_classify)
        _advance_coverage(state, page_high=page[0]["uid"], floor=floor, lower_only=lower_only,
                          prev_low=prev_low, prev_high=prev_high)
        _save_coverage(profile_name, state, processed_ids)

        if progress.spent >= budget:
            return


def _advance_coverage(
    state: dict, page_high: int, floor: int, lower_only: bool,
    prev_low: int = 0, prev_high: int = 0,
) -> None:
    """Extend the covered block to include everything down to `floor`.

    The block must stay contiguous, which is the whole invariant. A head walk
    descends page by page, so its low-water mark is simply how far it got; the
    merge test is against the block as it stood *before this run* (`prev_*`) —
    testing against the running block instead would be satisfied by the walk's
    own previous page and freeze coverage at the first page's floor.

    Reaching the previous block (`floor <= prev_high + 1`) merges with it and
    keeps its low-water mark; stopping short leaves it behind as backlog to be
    re-walked later (cheap — its message ids dedup at header stage). Either way
    no UID is recorded as covered without having been classified.
    """
    if lower_only:
        state["covered_low_uid"] = floor
        return
    state["covered_high_uid"] = max(state.get("covered_high_uid") or 0, page_high)
    if prev_low and prev_high and floor <= prev_high + 1:
        state["covered_low_uid"] = min(prev_low, floor)
    else:
        state["covered_low_uid"] = floor


def _classify_and_propose(profile_name: str, chunk: list[dict], classifier: str, progress: _Progress) -> None:
    chunk_classified = _classify_chunk(chunk, classifier, profile_name)
    chunk_new = _propose_for_chunk(profile_name, chunk, chunk_classified)
    if chunk_new:
        with _proposal_lock(profile_name):
            latest = load_proposals(profile_name)
            latest_ids = {p["id"] for p in latest}
            additions = [p for p in chunk_new if p["id"] not in latest_ids]
            if additions:
                save_proposals(profile_name, latest + additions)
        progress.proposals += len(chunk_new)
    progress.classified += len(chunk)
    _write_sync_status(
        profile_name,
        classified=progress.classified,
        new_proposals=progress.proposals,
        message=f"Classified {progress.classified} · {progress.proposals} proposals so far",
    )


def _run_sync_streaming(profile_name: str, deep: bool, classifier: str = "llm") -> str:
    """The actual pipeline. Writes proposals incrementally; updates sync_status.

    Two passes over one connection: newest mail first (so an interview invite
    that landed an hour ago is never queued behind a year of backlog), then the
    remaining budget spent walking backwards into whatever history isn't covered
    yet. Returns the message the completion banner should show.
    """
    creds = auth.load_credentials(profile_name)
    if not creds:
        raise RuntimeError("Inbox not connected. Connect on the Settings page first.")

    state = load_state(profile_name)
    processed_ids = set(state.get("processed_message_ids", []))
    lookback_days = DEEP_LOOKBACK_DAYS if deep else INITIAL_LOOKBACK_DAYS
    budget = DEEP_MAX_NEW if deep else DEFAULT_MAX_NEW
    progress = _Progress()

    _write_sync_status(profile_name, message="Connecting to inbox…")
    with fetch.Mailbox(creds) as mailbox:
        uidvalidity, uidnext = mailbox.status()
        if state.get("uidvalidity") and state["uidvalidity"] != uidvalidity:
            # Server re-issued the UID space; every stored UID is meaningless.
            print(f"[inbox] UIDVALIDITY changed ({state['uidvalidity']} -> {uidvalidity}); resetting coverage")
            state.update(covered_low_uid=0, covered_high_uid=0, horizon_uid=0)
        state["uidvalidity"] = uidvalidity

        # Free mode filters server-side, so its walk never even sees messages
        # outside the keyword set. Marking those UIDs covered would hide them
        # from a later paid sync, so an upgrade to the LLM classifier resets
        # coverage and re-walks. Skipped messages were never fetched, so they
        # aren't in `processed_message_ids` and do get classified the second time.
        covered_by = state.get("coverage_classifier") or classifier
        if classifier == "llm" and covered_by == "keyword":
            print("[inbox] classifier upgraded keyword -> llm; resetting coverage for a full re-walk")
            state.update(covered_low_uid=0, covered_high_uid=0)
            covered_by = "llm"
        state["coverage_classifier"] = "keyword" if "keyword" in (covered_by, classifier) else "llm"
        search_filter = _free_mode_search_filter() if classifier == "keyword" else ""

        since_dt = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        wanted_horizon = fetch.resolve_horizon_uid(mailbox, since_dt, uidnext)
        horizon = state.get("horizon_uid") or 0
        # A deep sync widens the horizon; a normal sync must never narrow it, or
        # backlog below the new horizon would become unreachable.
        state["horizon_uid"] = horizon = min(horizon, wanted_horizon) if horizon else wanted_horizon

        print(f"[inbox] sync starting (deep={deep}, classifier={classifier}, budget={budget}, "
              f"horizon_uid={horizon}, covered={state['covered_low_uid']}-{state['covered_high_uid']})")

        # --- Head pass: everything newer than the covered block ---
        head_from = max((state.get("covered_high_uid") or 0) + 1, horizon)
        _write_sync_status(profile_name, message="Checking for new mail…")
        # `UID n:*` always returns the highest UID even when it is below n
        # (RFC 3501 range semantics), so filter rather than trust the server.
        head = [u for u in mailbox.search_uids(f"UID {head_from}:* {search_filter}".strip()) if u >= head_from]
        print(f"[inbox] head pass: {len(head)} uids from {head_from}")
        if head:
            _walk_uids(profile_name, mailbox, sorted(head, reverse=True), state,
                       processed_ids, budget, classifier, progress, lower_only=False,
                       prev_low=state.get("covered_low_uid") or 0,
                       prev_high=state.get("covered_high_uid") or 0)

        # --- Backlog pass: whatever history the covered block hasn't reached ---
        if not progress.cancelled and progress.spent < budget:
            low = state.get("covered_low_uid") or 0
            if low > horizon:
                backlog = [u for u in mailbox.search_uids(f"UID {horizon}:{low - 1} {search_filter}".strip()) if u < low]
                print(f"[inbox] backlog pass: {len(backlog)} uids in {horizon}-{low - 1}")
                if backlog:
                    _write_sync_status(profile_name, message=f"Scanning {len(backlog)} older messages…")
                    _walk_uids(profile_name, mailbox, sorted(backlog, reverse=True), state,
                               processed_ids, budget, classifier, progress, lower_only=True)

        remaining = _backlog_remaining(mailbox, state)

    state["last_sync_at"] = datetime.now(timezone.utc).isoformat()
    _save_coverage(profile_name, state, processed_ids)
    _write_sync_status(
        profile_name,
        messages_seen=progress.examined,
        after_prefilter=progress.spent,
        classified=progress.classified,
        new_proposals=progress.proposals,
        backlog_remaining=remaining,
    )
    print(f"[inbox] sync done: {progress.proposals} new proposals, "
          f"{progress.examined} examined, {remaining} backlog (cancelled={progress.cancelled})")

    if progress.cancelled:
        return ""
    if remaining:
        # Never report a clean finish over a window we knowingly didn't reach —
        # that false all-clear is exactly how a missed offer email goes unnoticed.
        return f"Caught up on new mail · {remaining} older messages still to scan — click Sync again."
    return f"Sync complete. {progress.proposals} new proposals." if progress.proposals else "Sync complete."


def _backlog_remaining(mailbox: "fetch.Mailbox", state: dict) -> int:
    """Exact count of messages below the covered block, so the UI can state the
    gap instead of implying there isn't one."""
    low = state.get("covered_low_uid") or 0
    horizon = state.get("horizon_uid") or 0
    if not low or low <= horizon:
        return 0
    return len([u for u in mailbox.search_uids(f"UID {horizon}:{low - 1}") if u < low])


def _save_coverage(profile_name: str, state: dict, processed_ids: set) -> None:
    """Persist coverage + the message-id guard.

    Ids are stored in sorted order and capped from the front. The UID block is
    the authoritative record of what has been handled; this list only guards
    ranges that get re-walked, so a deterministic cap is enough — the previous
    `list(set)[-N:]` evicted arbitrary members because set iteration order isn't
    insertion order.
    """
    state["processed_message_ids"] = sorted(processed_ids)[-MAX_PROCESSED_IDS:]
    save_state(profile_name, state)


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
        match = matcher.match_application(
            c, applications, thread_id=msg.get("thread_id", ""), msg=msg,
        )
        # When the first email seen for a job is already a follow-up (interview
        # invite, rejection, offer) — i.e. you applied externally and skipped
        # the ATS confirmation step — there's no entry to anchor to. Instead of
        # silently dropping, surface it as "create a new entry at this status"
        # so the user can decide. They can dismiss if it's not relevant.
        if c["action_type"] == "status_update" and match["resolution"] == "no_match":
            if not c.get("company"):
                continue
            c = {**c, "action_type": "new_application"}
        # Dedup against the existing kanban: if a `new_application` proposal's
        # company already has an entry (matcher said match/ambiguous), skip it.
        # The message still gets marked processed, so it won't recur on next
        # sync. Without this, a re-confirmation email or a duplicate from a
        # different ATS system would propose creating a second kanban card.
        if c["action_type"] == "new_application" and match["resolution"] in ("match", "ambiguous"):
            target = match.get("target_idx", match.get("candidates"))
            print(f"[inbox] dedup: skipping new_application proposal for "
                  f"{c.get('company') or '?'} (already on kanban — matched entry/entries: {target})")
            continue
        # No-op skip: a status_update that wouldn't actually change the
        # existing entry's status. Common on re-syncs where the same email
        # gets re-classified (e.g. after a cache rotation).
        if c["action_type"] == "status_update" and match["resolution"] == "match":
            tgt = match.get("target_idx")
            if tgt is not None and 0 <= tgt < len(applications):
                if applications[tgt].get("status") == c.get("status"):
                    print(f"[inbox] dedup: skipping no-op status_update for "
                          f"{c.get('company') or '?'} (already at {c.get('status')})")
                    continue
        out.append(_build_proposal(msg, c, match))
        existing_ids.add(msg["id"])
    return out
