"""Smoke test for the optimize migration. Mocks every Claude/PDF call so the
test is hermetic and fast."""
import json
import shutil
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.tasks import runner  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PROFILES_DIR = REPO / "profiles"
TEST_PROFILE = "_test_optimize"
TEST_DIR = PROFILES_DIR / TEST_PROFILE


def assert_eq(actual, expected, msg):
    if actual != expected:
        raise AssertionError(f"{msg}: expected {expected!r}, got {actual!r}")
    print(f"  ok: {msg}")


def setup():
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    (TEST_DIR / "jobs.json").write_text(json.dumps([
        {"id": "j1", "title": "Software Engineer", "company": "Acme",
         "location": "Remote", "departments": ["eng"],
         "posting_url": "https://example.com/acme/swe",
         "ats": "greenhouse", "slug": "acme",
         "content": "We're looking for a SWE who knows Python and TypeScript"},
        {"id": "j2", "title": "Backend Engineer", "company": "Beta",
         "location": "NYC", "departments": ["eng"],
         "posting_url": "https://example.com/beta/be",
         "ats": "lever", "slug": "beta",
         "content": "Backend role focused on Go services"},
    ]))
    (TEST_DIR / "resume.json").write_text(json.dumps({
        "contact": {"name": "Test User", "email": "t@e.com"},
        "section_order": ["contact"],
    }))


def teardown():
    shutil.rmtree(TEST_DIR, ignore_errors=True)
    # Clean any test-only PDF/JSON paths
    Path("/tmp/test_optimize.pdf").unlink(missing_ok=True)
    Path("/tmp/test_optimize.json").unlink(missing_ok=True)


def stubs():
    """All the mocks needed for a hermetic optimize run."""
    return [
        patch("src.ui.routes.optimize.select_projects",
              return_value={"projects": [], "reasoning": [], "had_pool": False}),
        patch("src.ui.routes.optimize.find_cached_resume", return_value=None),
        patch("src.ui.routes.optimize.optimize_resume",
              return_value={"contact": {"name": "Test User", "email": "t@e.com"},
                            "section_order": ["contact"]}),
        patch("src.ui.routes.optimize.save_tailored_resume",
              return_value={"json": "/tmp/test_optimize.json", "pdf": "/tmp/test_optimize.pdf"}),
        patch("src.ui.routes.optimize.validate_resume", return_value=None),
        patch("src.ui.routes.optimize.diff_resumes",
              return_value="canned diff:\nSKILLS\n  + Added: python\n"),
    ]


def with_stubs(fn):
    def wrapped():
        ctx = stubs()
        for p in ctx:
            p.start()
        try:
            fn()
        finally:
            for p in ctx:
                p.stop()
    return wrapped


@with_stubs
def test_worker_happy_path():
    setup()
    try:
        from src.ui.routes.optimize import (
            _optimize_status_path,
            _optimize_task_key,
            _optimize_worker,
            _read_optimize_status,
        )
        sf = _optimize_status_path(TEST_PROFILE)

        # Use start_task so the thread is registered (so read_status doesn't
        # flag interrupted via the stuck-state check).
        started, msg = runner.start_task(
            task_key=_optimize_task_key(TEST_PROFILE),
            status_file=sf,
            target=_optimize_worker,
            args=(TEST_PROFILE, 0),
            initial_status={
                "phase": "starting", "job_idx": 0,
                "company": "Acme", "role": "Software Engineer",
                "message": "Starting…",
                "diff": None, "json_path": None, "pdf_path": None,
            },
            thread_name="optimize-test-0",
        )
        assert_eq(started, True, "start_task returns True")

        # Wait for completion
        for _ in range(60):
            time.sleep(0.1)
            st = runner.read_status(sf, _optimize_task_key(TEST_PROFILE))
            if st.get("state") in ("idle", "error", "cancelled"):
                break

        assert_eq(st["state"], "idle", "terminal state idle")
        assert_eq(st["phase"], "complete", "final phase=complete")
        assert_eq(st["job_idx"], 0, "job_idx preserved")
        assert "canned diff" in st["diff"], f"diff captured: {st['diff'][:60]}"
        print(f"  ok: diff in status ({len(st['diff'])} chars)")
        assert_eq(st["pdf_path"], "/tmp/test_optimize.pdf", "pdf_path stored")
    finally:
        teardown()


@with_stubs
def test_cached_path_short_circuits():
    setup()
    try:
        from src.ui.routes.optimize import (
            _optimize_status_path,
            _optimize_task_key,
            _optimize_worker,
        )
        sf = _optimize_status_path(TEST_PROFILE)
        # Override the find_cached_resume stub locally to return a cached hit
        with patch("src.ui.routes.optimize.find_cached_resume",
                   return_value={"json": "/tmp/cached.json", "pdf": "/tmp/cached.pdf"}), \
             patch("src.ui.routes.optimize.optimize_resume") as opt_mock:
            started, _ = runner.start_task(
                task_key=_optimize_task_key(TEST_PROFILE),
                status_file=sf,
                target=_optimize_worker,
                args=(TEST_PROFILE, 0),
                initial_status={"phase": "starting", "job_idx": 0, "diff": None,
                                "json_path": None, "pdf_path": None},
            )
            for _ in range(40):
                time.sleep(0.05)
                st = runner.read_status(sf, _optimize_task_key(TEST_PROFILE))
                if st.get("state") in ("idle", "error", "cancelled"):
                    break
            assert_eq(st["state"], "idle", "cached path → terminal idle")
            assert_eq(st["phase"], "cached", "phase=cached when cache hit")
            assert_eq(st["pdf_path"], "/tmp/cached.pdf", "cached pdf path returned")
            assert opt_mock.call_count == 0, "optimize_resume must NOT be called on cache hit"
            print("  ok: optimize_resume skipped on cache hit")
    finally:
        teardown()


@with_stubs
def test_error_path_captured():
    setup()
    try:
        from src.ui.routes.optimize import (
            _optimize_status_path,
            _optimize_task_key,
            _optimize_worker,
        )
        sf = _optimize_status_path(TEST_PROFILE)
        with patch("src.ui.routes.optimize.optimize_resume",
                   side_effect=RuntimeError("Claude blew up")):
            runner.start_task(
                task_key=_optimize_task_key(TEST_PROFILE),
                status_file=sf,
                target=_optimize_worker,
                args=(TEST_PROFILE, 0),
                initial_status={"job_idx": 0, "diff": None},
            )
            for _ in range(40):
                time.sleep(0.05)
                st = runner.read_status(sf, _optimize_task_key(TEST_PROFILE))
                if st.get("state") in ("idle", "error", "cancelled"):
                    break
            assert_eq(st["state"], "error", "exception → error state")
            assert "Claude blew up" in (st.get("error") or ""), f"error captured: {st.get('error')}"
            print(f"  ok: error text captured ({st['error']})")
    finally:
        teardown()


@with_stubs
def test_job_idx_persists_across_running_and_terminal():
    setup()
    try:
        from src.ui.routes.optimize import (
            _optimize_status_path,
            _optimize_task_key,
            _optimize_worker,
        )
        sf = _optimize_status_path(TEST_PROFILE)
        # Start optimize for job 1, not 0
        runner.start_task(
            task_key=_optimize_task_key(TEST_PROFILE),
            status_file=sf,
            target=_optimize_worker,
            args=(TEST_PROFILE, 1),
            initial_status={"job_idx": 1, "company": "Beta", "role": "Backend Engineer",
                            "diff": None, "json_path": None, "pdf_path": None},
        )
        for _ in range(60):
            time.sleep(0.05)
            st = runner.read_status(sf, _optimize_task_key(TEST_PROFILE))
            if st.get("state") in ("idle", "error", "cancelled"):
                break
        assert_eq(st["job_idx"], 1, "job_idx=1 preserved through to terminal state")
        # Now if a hypothetical page renders for job 0, _flags should report not-this-job
        from src.ui.routes.optimize import _flags
        flags = _flags(st, job_idx=0)
        assert_eq(flags["result_for_this_job"], False, "result not surfaced for non-matching job")
        flags_self = _flags(st, job_idx=1)
        assert_eq(flags_self["result_for_this_job"], True, "result surfaced for matching job")
    finally:
        teardown()


def test_routes_registered():
    """Static check — confirm the new routes are wired into the app."""
    from src.ui.app import app
    paths = sorted({r.path for r in app.routes if hasattr(r, "path")})
    required = [
        "/jobs/{idx}/optimize",
        "/jobs/{idx}/optimize/start",
        "/jobs/{idx}/optimize/status",
        "/jobs/{idx}/optimize/cancel",
        "/jobs/{idx}/optimize/pdf",
    ]
    for p in required:
        assert p in paths, f"missing route: {p}"
        print(f"  ok: {p} registered")


if __name__ == "__main__":
    tests = [
        test_routes_registered,
        test_worker_happy_path,
        test_cached_path_short_circuits,
        test_error_path_captured,
        test_job_idx_persists_across_running_and_terminal,
    ]

    failed = 0
    for t in tests:
        print(f"\n[{t.__name__}]")
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"  FAIL: {e}")
            teardown()

    print(f"\n{'=' * 40}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(0 if failed == 0 else 1)
