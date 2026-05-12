"""Smoke test for the discover_companies streaming refactor + the worker
wiring in src.ui.routes.companies. Uses a temp profile dir and mocks
validate_slug so no real APIs are hit."""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/Users/afeef/workspace/Projects/AutoApply")
from src.discovery import discover_companies  # noqa: E402
from src.tasks import runner  # noqa: E402

REPO = Path("/Users/afeef/workspace/Projects/AutoApply")
PROFILES_DIR = REPO / "profiles"
TEST_PROFILE = "_test_discover_smoke"
TEST_DIR = PROFILES_DIR / TEST_PROFILE


def assert_eq(actual, expected, msg):
    if actual != expected:
        raise AssertionError(f"{msg}: expected {expected!r}, got {actual!r}")
    print(f"  ok: {msg}")


def fake_validate(slug, ats):
    """Returns a result for slugs prefixed 'valid_', None otherwise."""
    if slug.startswith("valid_"):
        return {
            "name": slug.replace("_", " ").title(),
            "ats": ats,
            "slug": slug,
            "careers_url": f"https://example.com/{slug}",
            "added": "2026-05-10",
        }
    return None


def setup_test_profile_and_seeds():
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    # Start with one already-known company so we can confirm dedup works
    with open(TEST_DIR / "companies.json", "w") as f:
        json.dump([
            {"name": "Already Here", "ats": "greenhouse", "slug": "already_here",
             "careers_url": "https://example.com/already_here", "added": "2026-05-01"},
        ], f, indent=2)
    seed_path = Path(tempfile.mkdtemp()) / "seeds.json"
    with open(seed_path, "w") as f:
        json.dump([
            {"slug": "valid_a", "ats": "greenhouse"},
            {"slug": "valid_b", "ats": "lever"},
            {"slug": "valid_c", "ats": "greenhouse"},
            {"slug": "invalid_x", "ats": "greenhouse"},
            {"slug": "already_here", "ats": "greenhouse"},  # dedup
        ], f)
    return seed_path


def teardown():
    shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_legacy_signature_still_works():
    """CLI calls discover_companies(profile_name) — must not break."""
    seed_path = setup_test_profile_and_seeds()
    with patch("src.discovery.SEED_PATH", seed_path), \
         patch("src.discovery.validate_slug", side_effect=fake_validate):
        summary = discover_companies(TEST_PROFILE)
    assert_eq(summary["added"], 3, "3 valid slugs added")
    assert_eq(summary["skipped"], 1, "1 already-known skipped")
    assert_eq(summary["failed"], 1, "1 invalid failed")
    assert_eq(summary["processed"], 5, "5 processed total")
    assert_eq(summary["cancelled"], False, "not cancelled")
    teardown()


def test_on_progress_callback_fires():
    seed_path = setup_test_profile_and_seeds()
    events = []

    def on_progress(**kw):
        events.append(kw)

    with patch("src.discovery.SEED_PATH", seed_path), \
         patch("src.discovery.validate_slug", side_effect=fake_validate):
        summary = discover_companies(TEST_PROFILE, on_progress=on_progress)

    assert len(events) >= 2, f"expected >=2 progress events, got {len(events)}"
    print(f"  ok: on_progress fired {len(events)} times")
    final = events[-1]
    assert_eq(final["added"], 3, "final on_progress: added=3")
    assert_eq(final["skipped"], 1, "final on_progress: skipped=1")
    assert_eq(final["failed"], 1, "final on_progress: failed=1")
    assert_eq(final["processed"], 5, "final on_progress: processed=5")
    assert_eq(final["total"], 5, "final on_progress: total=5")
    teardown()


def test_cancel_check_aborts_validation():
    """Cancel flag returns True immediately — should short-circuit before any
    new validations start. Skipped (already-known) entries still process."""
    seed_path = setup_test_profile_and_seeds()
    # Make validate_slug slow so cancel has time to fire
    def slow_validate(slug, ats):
        time.sleep(0.2)
        return fake_validate(slug, ats)

    call_count = {"n": 0}

    def cancel_check():
        call_count["n"] += 1
        # Return True after the first as_completed yields
        return call_count["n"] > 0

    with patch("src.discovery.SEED_PATH", seed_path), \
         patch("src.discovery.validate_slug", side_effect=slow_validate):
        summary = discover_companies(TEST_PROFILE, cancel_check=cancel_check)
    # Cancelled flag should be True; some validations may have completed
    assert_eq(summary["cancelled"], True, "cancel_check=True → cancelled flag set")
    # Partial results saved
    with open(TEST_DIR / "companies.json") as f:
        saved = json.load(f)
    assert len(saved) >= 1, "at least the pre-existing entry remains"
    print(f"  ok: partial results saved ({len(saved)} companies)")
    teardown()


def test_runner_integration_via_worker():
    """Simulate the route's worker path: start_task → discover runs → status
    transitions to idle with the summary message."""
    seed_path = setup_test_profile_and_seeds()
    from src.ui.routes.companies import _discover_worker, _discover_status_path, _discover_task_key

    sf = _discover_status_path(TEST_PROFILE)

    with patch("src.discovery.SEED_PATH", seed_path), \
         patch("src.discovery.validate_slug", side_effect=fake_validate):
        started, msg = runner.start_task(
            task_key=_discover_task_key(TEST_PROFILE),
            status_file=sf,
            target=_discover_worker,
            args=(TEST_PROFILE,),
            initial_status={"added": 0, "skipped": 0, "failed": 0,
                            "processed": 0, "total": 0, "message": "Starting…"},
            thread_name=f"discover-{TEST_PROFILE}",
        )
        assert_eq(started, True, "start_task returns True")
        # Wait for completion
        for _ in range(40):
            time.sleep(0.1)
            st = runner.read_status(sf, _discover_task_key(TEST_PROFILE))
            if st.get("state") in ("idle", "error", "cancelled"):
                break
        assert_eq(st["state"], "idle", "worker reached idle state")
        assert_eq(st["added"], 3, "status added=3")
        assert_eq(st["skipped"], 1, "status skipped=1")
        assert_eq(st["failed"], 1, "status failed=1")
        assert "Discovery complete" in st.get("message", ""), \
            f"final message: {st.get('message')}"
        print(f"  ok: final message — {st['message']}")

    teardown()


if __name__ == "__main__":
    tests = [
        test_legacy_signature_still_works,
        test_on_progress_callback_fires,
        test_cancel_check_aborts_validation,
        test_runner_integration_via_worker,
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
