"""Tests for the inbox sync coverage model (`src/inbox/sync.py`).

These exist because of a real miss: a job-offer email was never surfaced. The
old sync sliced its search window to the newest N messages, then stamped
`last_sync_at = now`, so everything below the slice became permanently
invisible and the UI still said "Sync complete."

The invariant these tests defend is: **a UID inside [covered_low, covered_high]
has actually been fetched and classified.** Everything else — budgets, backlog
draining, cancel, UIDVALIDITY resets — is only interesting insofar as it can
break that.

IMAP, Claude, and disk are all faked/hermetic; the profile is `_test_inbox_sync`.
"""
import json
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.inbox import fetch, sync  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PROFILES_DIR = REPO / "profiles"
PROFILE = "_test_inbox_sync"

UIDVALIDITY = 4242
CORPUS_SIZE = 60
# The message this whole feature exists to catch, planted deep enough in history
# that it only surfaces after the backlog has been walked.
OFFER_UID = 5


def assert_eq(actual, expected, msg):
    if actual != expected:
        raise AssertionError(f"{msg}: expected {expected!r}, got {actual!r}")
    print(f"  ok: {msg}")


def assert_true(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok: {msg}")


def _subject(uid: int) -> str:
    return "Vanguard - Job Offer" if uid == OFFER_UID else f"Message {uid}"


class FakeMailbox:
    """Stands in for `fetch.Mailbox`, recording every UID whose headers it served
    so tests can prove coverage never outruns what was actually examined."""

    last: "FakeMailbox | None" = None

    def __init__(self, creds, folder="INBOX", uidvalidity=UIDVALIDITY, page_size=5):
        self.uids = list(range(1, CORPUS_SIZE + 1))
        self.uidvalidity = uidvalidity
        self.page_size = page_size
        self.fetched_uids: list[int] = []
        FakeMailbox.last = self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def status(self):
        return self.uidvalidity, max(self.uids) + 1

    def search_uids(self, criteria: str) -> list[int]:
        criteria = criteria.strip()
        if criteria.startswith("SINCE"):
            return sorted(self.uids)
        body = criteria.split(" ", 1)[1] if criteria.startswith("UID ") else criteria
        rng = body.split(" ", 1)[0]
        lo_s, hi_s = rng.split(":")
        lo = int(lo_s)
        if hi_s == "*":
            # RFC 3501: `n:*` yields the highest UID even when it is below n.
            # Emulated on purpose — sync must filter, not trust the server.
            hits = [u for u in self.uids if u >= lo] or [max(self.uids)]
            return sorted(hits)
        hi = int(hi_s)
        return sorted(u for u in self.uids if lo <= u <= hi)

    def iter_header_pages(self, uids_desc, page_size=None):
        size = page_size or self.page_size
        for i in range(0, len(uids_desc), size):
            page = uids_desc[i:i + size]
            self.fetched_uids.extend(page)
            yield [{
                "id": f"msg-{uid}@example.com",
                "uid": uid,
                "thread_id": f"msg-{uid}@example.com",
                "subject": _subject(uid),
                "from": "Sharon <sharon@vanguard.com>",
                "from_email": "sharon@vanguard.com",
                "to": "me@example.com",
                "received_at": "2026-06-25T17:56:16+00:00",
                "snippet": "",
                "body_text": "",
                "labels": [],
            } for uid in page]

    def populate_bodies(self, messages):
        for m in messages:
            m["body_text"] = f"body of {m['subject']}"


def _fake_classify(messages):
    """Only the offer message is actionable; everything else is noise."""
    return [{
        "id": m["id"],
        "action_type": "new_application" if "Job Offer" in m["subject"] else "ignore",
        "company": "Vanguard",
        "role": "Application Engineer",
        "status": "offer",
        "confidence": 0.95,
        "reasoning": "test",
    } for m in messages]


def _setup_profile():
    pdir = PROFILES_DIR / PROFILE
    shutil.rmtree(pdir, ignore_errors=True)
    (pdir / "imap").mkdir(parents=True, exist_ok=True)
    (pdir / "profile.json").write_text(json.dumps({
        "name": "Test User",
        "email": "test@example.com",
        "phone": "555-0100",
        "location": "Test City",
        "job_preferences": {
            "roles": ["Software Engineer"],
            "experience_levels": ["entry"],
            "locations": ["remote"],
        },
        "settings": {"auto_submit": False, "rate_limit_seconds": 5},
    }))
    (pdir / "applications.json").write_text("[]")
    (pdir / "imap" / "credentials.json").write_text(json.dumps({
        "email": "test@example.com", "password": "x",
        "server": "imap.example.com", "port": 993,
    }))


def _teardown():
    shutil.rmtree(PROFILES_DIR / PROFILE, ignore_errors=True)


def _run_sync(budget=10, deep=False, classifier="llm", mailbox_kwargs=None):
    """Run one sync against the fake mailbox. Returns (message, state)."""
    kwargs = mailbox_kwargs or {}
    with patch.object(sync.fetch, "Mailbox", lambda creds: FakeMailbox(creds, **kwargs)), \
         patch.object(sync.classify, "classify_messages", _fake_classify), \
         patch.object(sync, "DEFAULT_MAX_NEW", budget), \
         patch.object(sync, "DEEP_MAX_NEW", budget):
        message = sync._run_sync_streaming(PROFILE, deep=deep, classifier=classifier)
    return message, sync.load_state(PROFILE)


def _assert_coverage_honest(state, mailbox, label):
    """The invariant: nothing is claimed as covered that was never fetched."""
    low, high = state["covered_low_uid"], state["covered_high_uid"]
    fetched = set(mailbox.fetched_uids)
    unexamined = [u for u in range(low, high + 1) if u not in fetched]
    assert_true(not unexamined, f"{label}: no unexamined UID inside [{low}, {high}] (leaks: {unexamined[:5]})")


def test_truncated_sync_reports_backlog():
    """A budget-capped sync must not claim it finished."""
    _setup_profile()
    try:
        msg, state = _run_sync(budget=10)
        assert_true(state["covered_high_uid"] == CORPUS_SIZE, "head pass reaches the newest message")
        assert_true(state["covered_low_uid"] > 1, "budget stopped the walk short of the oldest message")
        assert_true("still to scan" in msg, f"banner names the backlog (got {msg!r})")
        assert_true("Sync complete" not in msg, "truncated run never says 'Sync complete'")
        _assert_coverage_honest(state, FakeMailbox.last, "truncated run")
    finally:
        _teardown()


def test_repeated_syncs_drain_backlog_and_terminate():
    """Each sync must strictly lower covered_low until the window is fully
    covered — the old model deadlocked here, re-pulling the same newest slice."""
    _setup_profile()
    try:
        msg, state = _run_sync(budget=10)
        seen_lows = [state["covered_low_uid"]]
        for _ in range(20):
            if state["covered_low_uid"] <= state["horizon_uid"]:
                break
            msg, state = _run_sync(budget=10)
            assert_true(
                state["covered_low_uid"] < seen_lows[-1],
                f"sync lowers covered_low ({seen_lows[-1]} -> {state['covered_low_uid']})",
            )
            seen_lows.append(state["covered_low_uid"])
        else:
            raise AssertionError(f"backlog never drained; lows={seen_lows}")
        assert_eq(state["covered_low_uid"], 1, "coverage reaches the oldest message in the window")
        assert_eq(state["covered_high_uid"], CORPUS_SIZE, "coverage still spans to the newest")
        assert_true("Sync complete" in msg, f"only a fully-covered window reports completion (got {msg!r})")
    finally:
        _teardown()


def test_message_past_budget_edge_is_eventually_proposed():
    """The actual bug: an actionable email below the first sync's cut-off must
    surface on a later sync rather than being skipped forever."""
    _setup_profile()
    try:
        _, state = _run_sync(budget=10)
        proposals = sync.load_proposals(PROFILE)
        assert_true(
            not any("Job Offer" in p["message_subject"] for p in proposals),
            "offer is below the first sync's cut-off, so not proposed yet",
        )
        for _ in range(20):
            if state["covered_low_uid"] <= OFFER_UID:
                break
            _, state = _run_sync(budget=10)
        proposals = sync.load_proposals(PROFILE)
        offers = [p for p in proposals if "Job Offer" in p["message_subject"]]
        assert_eq(len(offers), 1, "offer surfaces exactly once after the backlog is walked")
        assert_eq(offers[0]["proposed_status"], "offer", "proposal carries the classified status")
    finally:
        _teardown()


def test_settled_sync_is_a_noop():
    """Once the window is covered, syncing again must not re-classify anything."""
    _setup_profile()
    try:
        _, state = _run_sync(budget=CORPUS_SIZE * 2)
        assert_eq(state["covered_low_uid"], 1, "single generous sync covers the window")
        before = len(sync.load_proposals(PROFILE))
        msg, state2 = _run_sync(budget=CORPUS_SIZE * 2)
        assert_eq(state2["covered_low_uid"], 1, "coverage unchanged")
        assert_eq(state2["covered_high_uid"], CORPUS_SIZE, "coverage unchanged")
        assert_eq(len(sync.load_proposals(PROFILE)), before, "no duplicate proposals")
        assert_true("still to scan" not in msg, "settled sync reports no backlog")
    finally:
        _teardown()


def test_uidvalidity_change_resets_coverage():
    """A re-issued UID space makes every stored UID meaningless."""
    _setup_profile()
    try:
        _, state = _run_sync(budget=CORPUS_SIZE * 2)
        assert_eq(state["uidvalidity"], UIDVALIDITY, "uidvalidity recorded")
        _, state2 = _run_sync(budget=5, mailbox_kwargs={"uidvalidity": 9999})
        assert_eq(state2["uidvalidity"], 9999, "new uidvalidity adopted")
        # The re-walk is nearly free (known message ids dedup at header stage),
        # so the tell is that every UID was re-examined rather than trusted.
        assert_true(
            set(FakeMailbox.last.fetched_uids) >= set(range(1, CORPUS_SIZE + 1)),
            "every UID re-examined instead of inheriting the old UID space's coverage",
        )
        _assert_coverage_honest(state2, FakeMailbox.last, "after uidvalidity reset")
    finally:
        _teardown()


def test_free_mode_upgrade_resets_coverage():
    """Free mode filters server-side, so its coverage must not be inherited by a
    paid sync that would have looked at more messages."""
    _setup_profile()
    try:
        _, state = _run_sync(budget=CORPUS_SIZE * 2, classifier="keyword")
        assert_eq(state["coverage_classifier"], "keyword", "coverage tagged with the weak classifier")
        assert_eq(state["covered_low_uid"], 1, "keyword sync covered the window")
        _, state2 = _run_sync(budget=5, classifier="llm")
        assert_eq(state2["coverage_classifier"], "llm", "coverage retagged after upgrade")
        assert_true(
            set(FakeMailbox.last.fetched_uids) >= set(range(1, CORPUS_SIZE + 1)),
            "paid sync re-walks the window instead of inheriting free mode's blind spots",
        )
    finally:
        _teardown()


def test_uid_range_star_quirk_is_filtered():
    """`UID n:*` returns the highest UID even when it is below n. Trusting that
    would re-examine the newest message on every settled sync."""
    _setup_profile()
    try:
        _, state = _run_sync(budget=CORPUS_SIZE * 2)
        head_from = state["covered_high_uid"] + 1
        raw = FakeMailbox.last.search_uids(f"UID {head_from}:*")
        assert_eq(raw, [CORPUS_SIZE], "server returns the newest UID despite the range")
        assert_eq([u for u in raw if u >= head_from], [], "sync's filter drops it")
    finally:
        _teardown()


def test_bulk_fetch_keys_on_uid_not_sequence_number():
    """Bodies are matched to headers by UID. Keying on the leading sequence
    number instead would staple bodies to the wrong messages after an expunge."""
    resp = [
        (b"1 (UID 4242 BODY[HEADER.FIELDS (SUBJECT)] {12}", b"Subject: A\r\n"),
        b")",
        (b"2 (UID 4250 BODY[HEADER.FIELDS (SUBJECT)] {12}", b"Subject: B\r\n"),
        b")",
    ]
    got = [uid for uid, _ in fetch._iter_bulk_fetch(resp)]
    assert_eq(got, [4242, 4250], "UIDs parsed from the FETCH descriptor")


if __name__ == "__main__":
    tests = [
        test_truncated_sync_reports_backlog,
        test_repeated_syncs_drain_backlog_and_terminate,
        test_message_past_budget_edge_is_eventually_proposed,
        test_settled_sync_is_a_noop,
        test_uidvalidity_change_resets_coverage,
        test_free_mode_upgrade_resets_coverage,
        test_uid_range_star_quirk_is_filtered,
        test_bulk_fetch_keys_on_uid_not_sequence_number,
    ]

    failed = 0
    for t in tests:
        print(f"\n[{t.__name__}]")
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"  FAIL: {e}")

    print(f"\n{'=' * 40}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(0 if failed == 0 else 1)
