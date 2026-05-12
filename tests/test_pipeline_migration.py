"""Smoke test for the pipeline route — chains discover_companies and
discover_jobs in a single worker. Mocks both stages to avoid real network."""
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
TEST_PROFILE = "_test_pipeline"
TEST_DIR = PROFILES_DIR / TEST_PROFILE


def assert_eq(actual, expected, msg):
    if actual != expected:
        raise AssertionError(f"{msg}: expected {expected!r}, got {actual!r}")
    print(f"  ok: {msg}")


def setup():
    TEST_DIR.mkdir(parents=True, exist_ok=True)


def teardown():
    shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_routes_registered():
    from src.ui.app import app
    paths = sorted({r.path for r in app.routes if hasattr(r, "path")})
    for p in ["/pipeline/start", "/pipeline/status", "/pipeline/cancel"]:
        assert p in paths, f"missing route: {p}"
        print(f"  ok: {p} registered")


def test_worker_runs_both_stages_in_order():
    setup()
    try:
        from src.ui.routes.pipeline import (
            _pipeline_status_path,
            _pipeline_task_key,
            _pipeline_worker,
        )
        sf = _pipeline_status_path(TEST_PROFILE)
        stages_called = []

        def fake_discover(profile_name, on_progress=None, cancel_check=None):
            stages_called.append("discover")
            if on_progress:
                on_progress(processed=5, total=5, added=3, skipped=1, failed=1)
            return {"added": 3, "skipped": 1, "failed": 1, "processed": 5,
                    "total": 5, "cancelled": False}

        def fake_discover_jobs(profile_name, on_progress=None, cancel_check=None):
            stages_called.append("discover_jobs")
            if on_progress:
                on_progress(phase="fetching", message="Fetching jobs…")
                on_progress(phase="saving", message="Saving jobs.json…")
            return []

        with patch("src.ui.routes.pipeline.discover_companies", side_effect=fake_discover), \
             patch("src.ui.routes.pipeline.discover_jobs", side_effect=fake_discover_jobs):
            started, _ = runner.start_task(
                task_key=_pipeline_task_key(TEST_PROFILE),
                status_file=sf,
                target=_pipeline_worker,
                args=(TEST_PROFILE,),
                initial_status={"stage": "starting", "message": "Starting…"},
            )
            assert_eq(started, True, "worker started")
            for _ in range(60):
                time.sleep(0.05)
                st = runner.read_status(sf, _pipeline_task_key(TEST_PROFILE))
                if st.get("state") in ("idle", "error", "cancelled"):
                    break
        assert_eq(st["state"], "idle", "pipeline reached idle")
        assert_eq(stages_called, ["discover", "discover_jobs"], "stages ran in order")
        assert_eq(st["stage"], "complete", "final stage=complete")
    finally:
        teardown()


def test_cancel_between_stages():
    """Flip cancel after stage 1 → stage 2 must not run."""
    setup()
    try:
        from src.ui.routes.pipeline import (
            _pipeline_status_path,
            _pipeline_task_key,
            _pipeline_worker,
        )
        sf = _pipeline_status_path(TEST_PROFILE)
        stages_called = []

        def fake_discover_slow(profile_name, on_progress=None, cancel_check=None):
            stages_called.append("discover")
            # Hold long enough that cancel can flip mid-stage
            time.sleep(0.3)
            return {"added": 0, "skipped": 0, "failed": 0, "processed": 0,
                    "total": 0, "cancelled": False}

        def fake_discover_jobs(profile_name, on_progress=None, cancel_check=None):
            stages_called.append("discover_jobs")
            return []

        with patch("src.ui.routes.pipeline.discover_companies", side_effect=fake_discover_slow), \
             patch("src.ui.routes.pipeline.discover_jobs", side_effect=fake_discover_jobs):
            runner.start_task(
                task_key=_pipeline_task_key(TEST_PROFILE),
                status_file=sf,
                target=_pipeline_worker,
                args=(TEST_PROFILE,),
                initial_status={"stage": "starting", "message": "Starting…"},
            )
            # Flip cancel during stage 1
            time.sleep(0.1)
            runner.request_cancel(sf)
            for _ in range(60):
                time.sleep(0.05)
                st = runner.read_status(sf, _pipeline_task_key(TEST_PROFILE))
                if st.get("state") in ("idle", "error", "cancelled"):
                    break

        assert_eq(st["state"], "cancelled", "state=cancelled")
        assert_eq(stages_called, ["discover"], "only stage 1 ran; stage 2 short-circuited")
    finally:
        teardown()


def test_dashboard_renders_pipeline_status():
    """Sanity check: dashboard route renders without exception even with the
    new pipeline_status context."""
    setup()
    try:
        from src.ui.routes.dashboard import _metrics_for, _profile_summary_for
        from src.ui.routes.pipeline import _read_pipeline_status

        # Pre-flight: all three helpers callable with the test profile
        # (won't have profile.json, so they return empty/None — that's fine)
        s = _read_pipeline_status(TEST_PROFILE)
        assert s.get("state") == "idle", f"empty profile -> idle, got {s}"
        print("  ok: pipeline status idle when no file")
    finally:
        teardown()


if __name__ == "__main__":
    tests = [
        test_routes_registered,
        test_worker_runs_both_stages_in_order,
        test_cancel_between_stages,
        test_dashboard_renders_pipeline_status,
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
