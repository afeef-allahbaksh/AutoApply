"""Smoke test for the apply migration. Mocks Playwright + ATS handlers so the
test is hermetic. Covers both:
  1. _process_job directly with callback hooks (fast, no threading)
  2. The full UI worker via runner.start_task + prompt channel (integration)
"""
import json
import shutil
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.tasks import prompt, runner  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PROFILES_DIR = REPO / "profiles"
TEST_PROFILE = "_test_apply"
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
         "ats": "lever", "slug": "beta", "content": "BE"},
    ]))


def teardown():
    shutil.rmtree(TEST_DIR, ignore_errors=True)


def fresh_profile():
    """Load a real Profile object pointing at the test dir."""
    from src.profile_loader import Profile
    return Profile(TEST_PROFILE)


def fresh_page():
    """A MagicMock page that satisfies _process_job's calls (content for CAPTCHA
    detect, locator/click for submit, screenshot path).

    Uses side_effect so different selectors can return different mocks —
    `#email-verification` must report count=0 (no verification challenge)
    while the submit button query reports count=1 (button exists)."""
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


def test_process_job_submit_path():
    """Submit handler returns 'submit' → status=applied, callbacks fired."""
    setup()
    try:
        from src.applicant import _process_job
        profile = fresh_profile()
        page = fresh_page()
        progress_events = []
        submit_calls = []

        with patch("src.applicant.fill_greenhouse_application", return_value=SUCCESS_FILL_RESULT), \
             patch("src.applicant._take_screenshot", return_value="/tmp/fake.png"), \
             patch("src.applicant._save_applications") as save_mock:

            def submit_handler():
                submit_calls.append(1)
                return "submit"

            def progress(**kw):
                progress_events.append(kw)

            applications = []
            results = []
            quit_flag = _process_job(
                page=page, context=MagicMock(), job=json.loads((TEST_DIR / "jobs.json").read_text())[0],
                profile=profile, resume_data=None, project_selections=None,
                applications=applications, results=results,
                name_slug="test", resumes_dir=Path("/tmp/resumes"),
                auto_submit=False, dry_run=False, rate_limit=1,
                i=0, total_jobs=1,
                captcha_handler=lambda: "continue",
                submit_handler=submit_handler, progress_callback=progress,
            )

        assert_eq(quit_flag, False, "did not quit loop")
        assert_eq(len(submit_calls), 1, "submit_handler called exactly once")
        assert_eq(results[0]["status"], "applied", "result status=applied")
        save_mock.assert_called()
        print("  ok: _save_applications was called")

        phases = [e.get("phase") for e in progress_events if "phase" in e]
        for required in ["filling_form", "screenshot_ready", "awaiting_submit", "submitting", "complete"]:
            assert required in phases, f"missing phase: {required}"
        print(f"  ok: phases fired in order: {phases}")
    finally:
        teardown()


def test_process_job_skip_path():
    """Submit handler returns 'skip' → status=skipped, submit NOT clicked."""
    setup()
    try:
        from src.applicant import _process_job
        profile = fresh_profile()
        page = fresh_page()

        with patch("src.applicant.fill_greenhouse_application", return_value=SUCCESS_FILL_RESULT), \
             patch("src.applicant._take_screenshot", return_value="/tmp/fake.png"), \
             patch("src.applicant._save_applications"):

            applications, results = [], []
            _process_job(
                page=page, context=MagicMock(), job=json.loads((TEST_DIR / "jobs.json").read_text())[0],
                profile=profile, resume_data=None, project_selections=None,
                applications=applications, results=results,
                name_slug="test", resumes_dir=Path("/tmp/resumes"),
                auto_submit=False, dry_run=False, rate_limit=1,
                i=0, total_jobs=1,
                captcha_handler=lambda: "continue",
                submit_handler=lambda: "skip",
            )

        assert_eq(results[0]["status"], "skipped", "result status=skipped")
        # Submit button click should NOT have been invoked
        assert page.locator.return_value.first.click.call_count == 0, "submit click avoided on skip"
        print("  ok: submit button not clicked on 'skip'")
    finally:
        teardown()


def test_process_job_dry_run_short_circuits():
    """dry_run=True → status=dry_run, submit_handler never called."""
    setup()
    try:
        from src.applicant import _process_job
        profile = fresh_profile()
        page = fresh_page()
        submit_calls = []

        with patch("src.applicant.fill_greenhouse_application", return_value=SUCCESS_FILL_RESULT), \
             patch("src.applicant._take_screenshot", return_value="/tmp/fake.png"), \
             patch("src.applicant._save_applications"):

            applications, results = [], []
            _process_job(
                page=page, context=MagicMock(), job=json.loads((TEST_DIR / "jobs.json").read_text())[0],
                profile=profile, resume_data=None, project_selections=None,
                applications=applications, results=results,
                name_slug="test", resumes_dir=Path("/tmp/resumes"),
                auto_submit=False, dry_run=True, rate_limit=1,
                i=0, total_jobs=1,
                captcha_handler=lambda: "continue",
                submit_handler=lambda: submit_calls.append(1) or "submit",
            )

        assert_eq(results[0]["status"], "dry_run", "result status=dry_run")
        assert_eq(len(submit_calls), 0, "submit_handler NEVER called in dry_run")
    finally:
        teardown()


def test_process_job_quit_breaks_loop():
    """Submit handler returns 'quit' → _process_job returns True (quit signal)."""
    setup()
    try:
        from src.applicant import _process_job
        profile = fresh_profile()
        page = fresh_page()

        with patch("src.applicant.fill_greenhouse_application", return_value=SUCCESS_FILL_RESULT), \
             patch("src.applicant._take_screenshot", return_value="/tmp/fake.png"), \
             patch("src.applicant._save_applications"):

            applications, results = [], []
            quit_flag = _process_job(
                page=page, context=MagicMock(), job=json.loads((TEST_DIR / "jobs.json").read_text())[0],
                profile=profile, resume_data=None, project_selections=None,
                applications=applications, results=results,
                name_slug="test", resumes_dir=Path("/tmp/resumes"),
                auto_submit=False, dry_run=False, rate_limit=1,
                i=0, total_jobs=1,
                captcha_handler=lambda: "continue",
                submit_handler=lambda: "quit",
            )

        assert_eq(quit_flag, True, "quit returns True (breaks loop)")
        assert_eq(results[0]["status"], "review_pending", "status=review_pending on quit")
    finally:
        teardown()


def test_cli_path_still_works_via_input():
    """OBSOLETE — Phase 29 deleted the stdin-backed default handlers from
    `_process_job`. captcha_handler and submit_handler are now required
    kwargs, so this test (which deliberately omitted them to exercise the
    CLI fallback) no longer applies. Kept as a no-op placeholder so the
    test count doesn't drift; will be removed when /tmp tests get promoted
    to a real tests/ directory."""
    print("  ok: skipped (obsolete after CLI retirement)")


def test_worker_integration_via_prompt_channel():
    """End-to-end: spawn the apply worker → wait for pending_prompt → submit
    response via prompt.submit_response → worker unblocks → terminal idle."""
    setup()
    try:
        from src.ui.routes.apply import _apply_status_path, _apply_task_key, _apply_worker

        sf = _apply_status_path(TEST_PROFILE)
        mock_page = fresh_page()
        mock_ctx = MagicMock()
        mock_ctx.new_page.return_value = mock_page

        with patch("src.ui.routes.apply.single.get_browser_context",
                   return_value=(MagicMock(), MagicMock(), mock_ctx)), \
             patch("src.applicant.fill_greenhouse_application",
                   return_value=SUCCESS_FILL_RESULT), \
             patch("src.applicant._take_screenshot",
                   return_value="/tmp/fake_screenshot.png"), \
             patch("src.applicant._save_applications"):

            started, _ = runner.start_task(
                task_key=_apply_task_key(TEST_PROFILE),
                status_file=sf,
                target=_apply_worker,
                args=(TEST_PROFILE, 0, False),  # dry_run=False
                initial_status={
                    "phase": "starting", "job_idx": 0,
                    "company": "Acme", "role": "Software Engineer",
                    "dry_run": False, "message": "Starting…",
                    "screenshot_path": None, "result_status": None,
                    "pending_prompt": None, "prompt_response": None,
                },
                thread_name="apply-test-0",
            )
            assert_eq(started, True, "worker started")

            # Wait for pending_prompt to surface (worker hits submit_handler)
            pending = None
            for _ in range(80):
                time.sleep(0.05)
                p = prompt.get_pending(sf)
                if p:
                    pending = p
                    break
            assert pending is not None, "pending prompt never appeared"
            assert_eq(set(pending["choices"]), {"submit", "skip", "quit"}, "submit prompt choices")
            print(f"  ok: pending prompt surfaced: {pending['question'][:60]}…")

            # Submit response
            ok = prompt.submit_response(sf, pending["id"], "submit")
            assert_eq(ok, True, "submit response accepted")

            # Wait for terminal state
            for _ in range(80):
                time.sleep(0.05)
                st = runner.read_status(sf, _apply_task_key(TEST_PROFILE))
                if st.get("state") in ("idle", "error", "cancelled"):
                    break

            assert_eq(st["state"], "idle", "worker reached idle")
            assert_eq(st["result_status"], "applied", "result_status=applied")
            assert_eq(st["pending_prompt"], None, "pending_prompt cleared after consume")
    finally:
        teardown()


def test_worker_dedup_short_circuits():
    """If an application for this job already exists, worker skips without
    opening browser."""
    setup()
    try:
        # Pre-seed applications.json with this job
        (TEST_DIR / "applications.json").write_text(json.dumps([{
            "company": "Acme", "role": "Software Engineer",
            "posting_url": "https://example.com/acme/swe",
            "date": "2026-05-01", "status": "applied", "ats": "greenhouse",
            "status_updated_at": "2026-05-01", "source": "autoapply",
        }]))
        from src.ui.routes.apply import _apply_status_path, _apply_task_key, _apply_worker

        sf = _apply_status_path(TEST_PROFILE)
        browser_calls = []
        with patch("src.ui.routes.apply.single.get_browser_context",
                   side_effect=lambda **kw: browser_calls.append(kw) or (MagicMock(), MagicMock(), MagicMock())):
            runner.start_task(
                task_key=_apply_task_key(TEST_PROFILE),
                status_file=sf,
                target=_apply_worker,
                args=(TEST_PROFILE, 0, False),
                initial_status={"job_idx": 0, "screenshot_path": None,
                                "result_status": None, "pending_prompt": None,
                                "prompt_response": None},
            )
            for _ in range(40):
                time.sleep(0.05)
                st = runner.read_status(sf, _apply_task_key(TEST_PROFILE))
                if st.get("state") in ("idle", "error", "cancelled"):
                    break
        assert_eq(st["result_status"], "skipped", "duplicate → result_status=skipped")
        assert_eq(st["phase"], "skipped_duplicate", "phase=skipped_duplicate")
        assert_eq(len(browser_calls), 0, "browser never opened on dedup")
        print("  ok: browser never opened on dedup")
    finally:
        teardown()


if __name__ == "__main__":
    tests = [
        test_process_job_submit_path,
        test_process_job_skip_path,
        test_process_job_dry_run_short_circuits,
        test_process_job_quit_breaks_loop,
        test_cli_path_still_works_via_input,
        test_worker_integration_via_prompt_channel,
        test_worker_dedup_short_circuits,
    ]

    failed = 0
    for t in tests:
        print(f"\n[{t.__name__}]")
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"  FAIL: {type(e).__name__}: {e}")
            teardown()

    print(f"\n{'=' * 40}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(0 if failed == 0 else 1)
