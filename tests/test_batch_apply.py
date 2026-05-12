"""Phase 32 smoke tests — multi-job batch apply.

Mocks Playwright + ATS handlers so tests are hermetic. Covers:
  1. /apply/batch/* routes registered and respond
  2. _partition_selected splits already-applied vs to-apply correctly
  3. Worker iterates selected indices in order, current_index increments
  4. Submit handler returning 'quit' breaks the loop → remaining = not_attempted
  5. Cancel mid-batch → remaining = not_attempted, recorded results preserved
  6. Pre-filter dedup: a fully-already-applied batch never opens the browser
  7. Single-job worker still works (regression for the schema additions)
"""
import json
import shutil
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, "/Users/afeef/workspace/Projects/AutoApply")
from src.tasks import prompt, runner  # noqa: E402

REPO = Path("/Users/afeef/workspace/Projects/AutoApply")
PROFILES_DIR = REPO / "profiles"
TEST_PROFILE = "_test_batch_apply"
TEST_DIR = PROFILES_DIR / TEST_PROFILE


def assert_eq(actual, expected, msg):
    if actual != expected:
        raise AssertionError(f"{msg}: expected {expected!r}, got {actual!r}")
    print(f"  ok: {msg}")


def setup():
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    (TEST_DIR / "profile.json").write_text(json.dumps({
        "name": "Test User",
        "email": "t@e.com",
        "phone": "555-555-5555",
        "location": "Remote",
        "job_preferences": {
            "roles": ["SWE"], "locations": ["Remote"],
            "experience_levels": ["Junior"],
        },
        "settings": {"auto_submit": False, "rate_limit_seconds": 1},
    }))
    (TEST_DIR / "responses.json").write_text(json.dumps({
        "work_authorization": "Yes", "visa_sponsorship": "No",
    }))
    (TEST_DIR / "applications.json").write_text("[]")
    (TEST_DIR / "jobs.json").write_text(json.dumps([
        {"id": "j1", "title": "Software Engineer", "company": "Acme",
         "location": "Remote", "departments": ["eng"],
         "posting_url": "https://example.com/acme/swe",
         "ats": "greenhouse", "slug": "acme", "content": "SWE"},
        {"id": "j2", "title": "Backend Engineer", "company": "Beta",
         "location": "NYC", "departments": ["eng"],
         "posting_url": "https://example.com/beta/be",
         "ats": "greenhouse", "slug": "beta", "content": "BE"},
        {"id": "j3", "title": "Platform Engineer", "company": "Gamma",
         "location": "SF", "departments": ["eng"],
         "posting_url": "https://example.com/gamma/pe",
         "ats": "greenhouse", "slug": "gamma", "content": "PE"},
    ]))


def teardown():
    """Strong teardown: cancel any running worker on this test profile,
    wait for the thread to die, then remove the profile directory.

    `runner.write_status` recreates `parent` with mkdir(exist_ok=True), so a
    daemon worker that survives `shutil.rmtree` will resurrect the path and
    cross-contaminate the next test. We have to actually wait for it to exit.
    """
    try:
        from src.ui.routes.apply import _apply_status_path, _apply_task_key
        sf = _apply_status_path(TEST_PROFILE)
        task_key = _apply_task_key(TEST_PROFILE)
        if sf.exists():
            runner.request_cancel(sf)
        # Join the registered thread (if any) so it can't write after rmtree.
        with runner._thread_registry_lock:
            t = runner._active_threads.get(task_key)
        if t is not None:
            t.join(timeout=3.0)
    except Exception:
        pass
    shutil.rmtree(TEST_DIR, ignore_errors=True)


def wait_for_new_pending(sf, previous_id=None, timeout=4.0):
    """Wait for a pending_prompt whose id != previous_id. Returns None on timeout.

    This guards against the inter-job race: after submitting a response, the
    worker takes up to `poll_interval` (0.5s) to notice and clear. A naive poll
    sees the stale prompt and returns it — but `previous_id` filter ensures we
    only return on a *new* prompt (job N+1) or after the old one was cleared
    and the next one surfaces.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        p = prompt.get_pending(sf)
        if p and p.get("id") != previous_id:
            return p
        time.sleep(0.05)
    return None


def wait_for_completed_count(sf, count, timeout=4.0):
    """Wait until status.completed_results has at least `count` entries.
    Used to synchronize tests on per-job completion (which is what triggers
    the inter-job loop iteration in the worker)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        results = runner.read_status_raw(sf).get("completed_results") or []
        if len(results) >= count:
            return results
        time.sleep(0.05)
    return runner.read_status_raw(sf).get("completed_results") or []


def fresh_page():
    page = MagicMock()
    page.content.return_value = "form filled successfully"  # no CAPTCHA
    submit_locator = MagicMock()
    submit_locator.count.return_value = 1
    no_verification = MagicMock()
    no_verification.count.return_value = 0

    def locator_side_effect(selector):
        if selector == '#email-verification':
            return no_verification
        return submit_locator
    page.locator.side_effect = locator_side_effect
    return page


SUCCESS_FILL_RESULT = {
    "success": True,
    "fields_filled": ["name", "email"],
    "custom_answers": [],
    "error": None,
}


def wait_for_pending(sf, timeout=4.0):
    """Poll until a pending_prompt appears or we time out."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        p = prompt.get_pending(sf)
        if p:
            return p
        time.sleep(0.05)
    return None


def wait_for_state(sf, task_key, states, timeout=4.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = runner.read_status(sf, task_key)
        if st.get("state") in states:
            return st
        time.sleep(0.05)
    return runner.read_status(sf, task_key)


# ---------------------------------------------------------------------------

def test_routes_registered():
    from src.ui.app import app
    paths = {(tuple(sorted(r.methods)), r.path) for r in app.routes if hasattr(r, "methods")}
    expected = [
        (("POST",), "/apply/batch/start"),
        (("GET",), "/apply/batch"),
        (("GET",), "/apply/batch/status"),
        (("POST",), "/apply/batch/cancel"),
        (("POST",), "/apply/batch/prompt"),
        (("GET",), "/apply/batch/screenshot"),
    ]
    for methods, path in expected:
        assert (methods, path) in paths, f"route missing: {methods} {path}"
        print(f"  ok: {methods[0]} {path} registered")


def test_partition_splits_dedup_correctly():
    """_partition_selected separates already-applied from to-apply, drops invalid idx."""
    setup()
    try:
        from src.ui.routes.apply import _partition_selected, _load_jobs
        from src.profile_loader import Profile

        # Pre-seed: job idx 0 (Acme) already applied
        (TEST_DIR / "applications.json").write_text(json.dumps([{
            "company": "Acme", "role": "Software Engineer",
            "posting_url": "https://example.com/acme/swe",
            "date": "2026-05-01", "status": "applied", "ats": "greenhouse",
            "status_updated_at": "2026-05-01", "source": "autoapply",
        }]))

        jobs = _load_jobs(TEST_PROFILE)
        applications = list(Profile(TEST_PROFILE).applications)
        # Select 0 (dupe), 1 (fresh), 99 (invalid), 2 (fresh)
        to_apply, already = _partition_selected([0, 1, 99, 2], jobs, applications)

        assert_eq([x["idx"] for x in to_apply], [1, 2], "to_apply preserves order, skips invalid")
        assert_eq([x["idx"] for x in already], [0], "already_applied contains the dupe")
        assert_eq(already[0]["status"], "skipped_duplicate", "dupe entry tagged skipped_duplicate")
    finally:
        teardown()


def test_batch_iterates_in_order_with_increment():
    """Two-job batch: indices processed in selection order, current_index increments."""
    setup()
    try:
        from src.ui.routes.apply import (
            _batch_apply_worker, _apply_status_path, _apply_task_key,
        )
        sf = _apply_status_path(TEST_PROFILE)

        mock_ctx = MagicMock()
        mock_ctx.new_page.return_value = fresh_page()
        observed_currents = []

        with patch("src.ui.routes.apply.batch.get_browser_context",
                   return_value=(MagicMock(), MagicMock(), mock_ctx)), \
             patch("src.applicant.fill_greenhouse_application",
                   return_value=SUCCESS_FILL_RESULT), \
             patch("src.applicant._take_screenshot",
                   return_value="/tmp/fake_screenshot.png"), \
             patch("src.applicant._save_applications"):

            runner.start_task(
                task_key=_apply_task_key(TEST_PROFILE),
                status_file=sf,
                target=_batch_apply_worker,
                args=(TEST_PROFILE, [0, 2], False),
                initial_status={
                    "mode": "batch", "phase": "starting",
                    "selected_indices": [0, 2], "current_index": 0,
                    "completed_results": [], "dry_run": False,
                    "screenshot_path": None, "result_status": None,
                    "pending_prompt": None, "prompt_response": None,
                },
            )

            # Job 1 prompt → submit
            pending = wait_for_new_pending(sf)
            assert pending is not None, "first job's prompt never appeared"
            observed_currents.append(runner.read_status_raw(sf).get("current_index"))
            first_id = pending["id"]
            prompt.submit_response(sf, first_id, "submit")

            # Job 2 prompt — wait for a DIFFERENT prompt id so we don't
            # snapshot current_index before the worker advanced past job 1.
            pending = wait_for_new_pending(sf, previous_id=first_id, timeout=6.0)
            assert pending is not None, "second job's prompt never appeared"
            observed_currents.append(runner.read_status_raw(sf).get("current_index"))
            prompt.submit_response(sf, pending["id"], "submit")

            final = wait_for_state(sf, _apply_task_key(TEST_PROFILE), ("idle", "error", "cancelled"), timeout=6.0)

        assert_eq(observed_currents, [0, 1], "current_index incremented 0 → 1")
        assert_eq(final["state"], "idle", "batch reached idle")
        results = final.get("completed_results") or []
        assert_eq([r["idx"] for r in results], [0, 2], "results recorded in selection order")
        assert_eq([r["status"] for r in results], ["applied", "applied"], "both applied")
    finally:
        teardown()


def test_quit_breaks_loop_remaining_not_attempted():
    """Submit 'quit' on first job → second marked not_attempted."""
    setup()
    try:
        from src.ui.routes.apply import (
            _batch_apply_worker, _apply_status_path, _apply_task_key,
        )
        sf = _apply_status_path(TEST_PROFILE)

        mock_ctx = MagicMock()
        mock_ctx.new_page.return_value = fresh_page()

        with patch("src.ui.routes.apply.batch.get_browser_context",
                   return_value=(MagicMock(), MagicMock(), mock_ctx)), \
             patch("src.applicant.fill_greenhouse_application",
                   return_value=SUCCESS_FILL_RESULT), \
             patch("src.applicant._take_screenshot",
                   return_value="/tmp/fake.png"), \
             patch("src.applicant._save_applications"):

            runner.start_task(
                task_key=_apply_task_key(TEST_PROFILE),
                status_file=sf,
                target=_batch_apply_worker,
                args=(TEST_PROFILE, [0, 1], False),
                initial_status={
                    "mode": "batch", "selected_indices": [0, 1],
                    "current_index": 0, "completed_results": [],
                    "screenshot_path": None, "result_status": None,
                    "pending_prompt": None, "prompt_response": None,
                },
            )

            pending = wait_for_pending(sf)
            assert pending is not None
            prompt.submit_response(sf, pending["id"], "quit")

            final = wait_for_state(sf, _apply_task_key(TEST_PROFILE), ("idle", "error", "cancelled"))

        results = final.get("completed_results") or []
        assert_eq([r["idx"] for r in results], [0, 1], "both indices recorded")
        assert_eq(results[0]["status"], "review_pending", "first job: review_pending (quit branch)")
        assert_eq(results[1]["status"], "not_attempted", "second job: not_attempted")
    finally:
        teardown()


def test_cancel_mid_batch_marks_remaining_not_attempted():
    """Cancel between jobs → remaining indices marked not_attempted."""
    setup()
    try:
        from src.ui.routes.apply import (
            _batch_apply_worker, _apply_status_path, _apply_task_key,
        )
        sf = _apply_status_path(TEST_PROFILE)

        mock_ctx = MagicMock()
        mock_ctx.new_page.return_value = fresh_page()

        with patch("src.ui.routes.apply.batch.get_browser_context",
                   return_value=(MagicMock(), MagicMock(), mock_ctx)), \
             patch("src.applicant.fill_greenhouse_application",
                   return_value=SUCCESS_FILL_RESULT), \
             patch("src.applicant._take_screenshot",
                   return_value="/tmp/fake.png"), \
             patch("src.applicant._save_applications"):

            runner.start_task(
                task_key=_apply_task_key(TEST_PROFILE),
                status_file=sf,
                target=_batch_apply_worker,
                args=(TEST_PROFILE, [0, 1, 2], False),
                initial_status={
                    "mode": "batch", "selected_indices": [0, 1, 2],
                    "current_index": 0, "completed_results": [],
                    "screenshot_path": None, "result_status": None,
                    "pending_prompt": None, "prompt_response": None,
                },
            )

            # Process job 0
            pending = wait_for_new_pending(sf)
            prompt.submit_response(sf, pending["id"], "submit")

            # Wait for job 0 to fully complete (worker advances past _process_job
            # and appends to completed_results) BEFORE cancelling. Otherwise the
            # cancel race wins and job 0 gets recorded as "skipped" because the
            # PromptCancelled exception fires while prompt.ask is still polling.
            wait_for_completed_count(sf, 1, timeout=4.0)
            runner.request_cancel(sf)

            final = wait_for_state(sf, _apply_task_key(TEST_PROFILE), ("idle", "error", "cancelled"), timeout=6.0)

        results = final.get("completed_results") or []
        statuses = {r["idx"]: r["status"] for r in results}
        assert_eq(statuses.get(0), "applied", "job 0 recorded as applied")
        # Jobs 1 and 2 should be not_attempted (cancel hit before they ran)
        assert_eq(statuses.get(1), "not_attempted", "job 1: not_attempted (cancelled)")
        assert_eq(statuses.get(2), "not_attempted", "job 2: not_attempted (cancelled)")
        assert_eq(final["state"], "cancelled", "task reached cancelled terminal state")
    finally:
        teardown()


def test_all_dupes_short_circuits_no_browser():
    """If every selected idx is already applied, browser never opens."""
    setup()
    try:
        # Pre-seed: jobs 0 + 1 already applied
        (TEST_DIR / "applications.json").write_text(json.dumps([
            {"company": "Acme", "role": "Software Engineer",
             "posting_url": "https://example.com/acme/swe",
             "date": "2026-05-01", "status": "applied", "ats": "greenhouse",
             "status_updated_at": "2026-05-01", "source": "autoapply"},
            {"company": "Beta", "role": "Backend Engineer",
             "posting_url": "https://example.com/beta/be",
             "date": "2026-05-01", "status": "applied", "ats": "greenhouse",
             "status_updated_at": "2026-05-01", "source": "autoapply"},
        ]))
        from src.ui.routes.apply import (
            _batch_apply_worker, _apply_status_path, _apply_task_key,
        )
        sf = _apply_status_path(TEST_PROFILE)

        browser_calls = []
        with patch("src.ui.routes.apply.batch.get_browser_context",
                   side_effect=lambda **kw: browser_calls.append(kw)
                                            or (MagicMock(), MagicMock(), MagicMock())):
            runner.start_task(
                task_key=_apply_task_key(TEST_PROFILE),
                status_file=sf,
                target=_batch_apply_worker,
                args=(TEST_PROFILE, [0, 1], False),
                initial_status={
                    "mode": "batch", "selected_indices": [0, 1],
                    "current_index": 0, "completed_results": [],
                },
            )
            final = wait_for_state(sf, _apply_task_key(TEST_PROFILE), ("idle", "error", "cancelled"))

        assert_eq(len(browser_calls), 0, "browser never opened when all selections are dupes")
        results = final.get("completed_results") or []
        statuses = {r["idx"]: r["status"] for r in results}
        assert_eq(statuses, {0: "skipped_duplicate", 1: "skipped_duplicate"},
                  "both selections recorded as skipped_duplicate")
    finally:
        teardown()


def test_single_job_apply_still_works():
    """Regression: the schema additions (mode field) didn't break single-job apply."""
    setup()
    try:
        from src.ui.routes.apply import (
            _apply_worker, _apply_status_path, _apply_task_key,
        )
        sf = _apply_status_path(TEST_PROFILE)

        mock_ctx = MagicMock()
        mock_ctx.new_page.return_value = fresh_page()

        with patch("src.ui.routes.apply.single.get_browser_context",
                   return_value=(MagicMock(), MagicMock(), mock_ctx)), \
             patch("src.applicant.fill_greenhouse_application",
                   return_value=SUCCESS_FILL_RESULT), \
             patch("src.applicant._take_screenshot",
                   return_value="/tmp/fake.png"), \
             patch("src.applicant._save_applications"):

            runner.start_task(
                task_key=_apply_task_key(TEST_PROFILE),
                status_file=sf,
                target=_apply_worker,
                args=(TEST_PROFILE, 0, False),
                initial_status={
                    "mode": "single", "phase": "starting", "job_idx": 0,
                    "company": "Acme", "role": "Software Engineer",
                    "dry_run": False, "screenshot_path": None,
                    "result_status": None, "pending_prompt": None,
                    "prompt_response": None,
                },
            )
            pending = wait_for_pending(sf)
            assert pending is not None, "single-job prompt never appeared"
            prompt.submit_response(sf, pending["id"], "submit")
            final = wait_for_state(sf, _apply_task_key(TEST_PROFILE), ("idle", "error"))

        assert_eq(final["state"], "idle", "single-job worker still reaches idle")
        assert_eq(final["result_status"], "applied", "single-job result still applied")
        assert_eq(final.get("mode"), "single", "single-job status carries mode='single'")
    finally:
        teardown()


if __name__ == "__main__":
    tests = [
        test_routes_registered,
        test_partition_splits_dedup_correctly,
        test_batch_iterates_in_order_with_increment,
        test_quit_breaks_loop_remaining_not_attempted,
        test_cancel_mid_batch_marks_remaining_not_attempted,
        test_all_dupes_short_circuits_no_browser,
        test_single_job_apply_still_works,
    ]

    failed = 0
    for t in tests:
        print(f"\n[{t.__name__}]")
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"  FAIL: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            teardown()

    print(f"\n{'=' * 40}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(0 if failed == 0 else 1)
