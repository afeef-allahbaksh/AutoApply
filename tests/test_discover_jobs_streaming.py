"""Smoke test for the discover_jobs streaming refactor + the worker wiring in
src.ui.routes.jobs. Uses a temp profile dir under profiles/_test_*/ and stubs
out every network/LLM call so the test is hermetic."""
import json
import shutil
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/Users/afeef/workspace/Projects/AutoApply")
from src.tasks import runner  # noqa: E402

REPO = Path("/Users/afeef/workspace/Projects/AutoApply")
PROFILES_DIR = REPO / "profiles"
TEST_PROFILE = "_test_discover_jobs"
TEST_DIR = PROFILES_DIR / TEST_PROFILE


def assert_eq(actual, expected, msg):
    if actual != expected:
        raise AssertionError(f"{msg}: expected {expected!r}, got {actual!r}")
    print(f"  ok: {msg}")


def setup():
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    (TEST_DIR / "companies.json").write_text(json.dumps([
        {"name": "Acme", "ats": "greenhouse", "slug": "acme",
         "careers_url": "https://example.com/acme", "added": "2026-05-10"},
        {"name": "Beta", "ats": "lever", "slug": "beta",
         "careers_url": "https://example.com/beta", "added": "2026-05-10"},
        {"name": "Gamma", "ats": "greenhouse", "slug": "gamma",
         "careers_url": "https://example.com/gamma", "added": "2026-05-10"},
    ]))
    (TEST_DIR / "profile.json").write_text(json.dumps({
        "personal_info": {"name": "Test", "email": "t@e.com"},
        "location": "New York, NY",
        "job_preferences": {
            "roles": ["software engineer", "swe"],
            "locations": ["new york", "remote"],
            "experience_levels": ["junior"],
            "industries": [],
            "salary_min": 0,
        },
        "settings": {"auto_submit": False, "rate_limit_seconds": 5},
    }))
    (TEST_DIR / "applications.json").write_text("[]")
    (TEST_DIR / "resume.json").write_text(json.dumps({
        "contact": {"name": "Test", "email": "t@e.com"},
        "skills": [], "experience": [], "education": [],
    }))


def teardown():
    shutil.rmtree(TEST_DIR, ignore_errors=True)


def fake_fetch_greenhouse(slug):
    return [{
        "id": f"gh-{slug}-1", "title": "Software Engineer", "company": slug.title(),
        "location": "New York, NY", "departments": ["eng"],
        "posting_url": f"https://example.com/{slug}/1",
        "ats": "greenhouse", "slug": slug, "content": "Looking for SWE",
    }]


def fake_fetch_lever(slug):
    return [{
        "id": f"lv-{slug}-1", "title": "SWE", "company": slug.title(),
        "location": "Remote", "departments": ["eng"],
        "posting_url": f"https://example.com/{slug}/2",
        "ats": "lever", "slug": slug, "content": "Junior engineer wanted",
    }]


def fake_classify_country(jobs, user_location): return jobs


def fake_classify_level(jobs, levels): return jobs


def fake_score_fit(jobs, resume_data, preferences=None):
    for j in jobs:
        j["fit_score"] = 4
        j["fit_rationale"] = "stubbed"
    return jobs


def stubbed():
    """Bundle the network/LLM patches into one context manager."""
    return [
        patch("src.jobs.discover.fetch_greenhouse_jobs", fake_fetch_greenhouse),
        patch("src.jobs.discover.fetch_lever_jobs", fake_fetch_lever),
        patch("src.jobs.discover.classify_jobs_by_country", fake_classify_country),
        patch("src.jobs.discover.classify_jobs_by_level", fake_classify_level),
        patch("src.jobs.discover.score_jobs_fit", fake_score_fit),
    ]


def with_stubs(fn):
    """Decorator-ish: run fn with all patches active."""
    def wrapped():
        ctx = stubbed()
        for p in ctx:
            p.start()
        try:
            fn()
        finally:
            for p in ctx:
                p.stop()
    return wrapped


@with_stubs
def test_legacy_signature():
    setup()
    try:
        from src.jobs import discover_jobs
        jobs = discover_jobs(TEST_PROFILE)
        # Three companies, one job each, all match preferences → 3 jobs
        assert len(jobs) == 3, f"expected 3 jobs, got {len(jobs)}"
        print(f"  ok: discover_jobs(profile) returns {len(jobs)} jobs")
        with open(TEST_DIR / "jobs.json") as f:
            saved = json.load(f)
        assert len(saved) == 3, f"jobs.json has {len(saved)} entries"
        print("  ok: jobs.json saved with 3 entries")
    finally:
        teardown()


@with_stubs
def test_on_progress_phases_fire():
    setup()
    try:
        from src.jobs import discover_jobs
        events = []

        def on_progress(**kw):
            events.append(kw)

        discover_jobs(TEST_PROFILE, on_progress=on_progress)

        phases_seen = [e.get("phase") for e in events if "phase" in e]
        print(f"  ok: {len(events)} progress events fired")
        print(f"  phases: {phases_seen}")
        # Required phases for a clean run with all features wired
        for required in ["loading_companies", "fetching", "filtering",
                         "classifying_country", "classifying_level",
                         "dedup", "scoring_fit", "saving"]:
            assert required in phases_seen, f"missing phase: {required}"
        print("  ok: every expected phase emitted")
    finally:
        teardown()


@with_stubs
def test_cancel_aborts_and_does_not_save():
    setup()
    try:
        from src.jobs import discover_jobs
        # Cancel right away — should bail before saving jobs.json
        jobs = discover_jobs(TEST_PROFILE, cancel_check=lambda: True)
        assert_eq(jobs, [], "cancelled run returns empty list")
        # jobs.json should NOT exist (cancel before save)
        assert not (TEST_DIR / "jobs.json").exists(), \
            "jobs.json must not be written on cancel"
        print("  ok: jobs.json untouched after cancel")
    finally:
        teardown()


@with_stubs
def test_runner_integration():
    """Full path: start_task → worker → discover_jobs streams → terminal idle."""
    setup()
    try:
        from src.ui.routes.jobs import (
            _discover_jobs_status_path,
            _discover_jobs_task_key,
            _discover_jobs_worker,
        )
        sf = _discover_jobs_status_path(TEST_PROFILE)
        started, msg = runner.start_task(
            task_key=_discover_jobs_task_key(TEST_PROFILE),
            status_file=sf,
            target=_discover_jobs_worker,
            args=(TEST_PROFILE,),
            initial_status={"message": "Starting…"},
            thread_name=f"discover-jobs-{TEST_PROFILE}",
        )
        assert_eq(started, True, "start_task returns True")
        for _ in range(60):
            time.sleep(0.1)
            st = runner.read_status(sf, _discover_jobs_task_key(TEST_PROFILE))
            if st.get("state") in ("idle", "error", "cancelled"):
                break
        assert_eq(st["state"], "idle", "worker reached idle state")
        # Status file should carry the last phase + counts
        assert st.get("phase") == "saving", f"final phase={st.get('phase')}"
        print(f"  ok: final phase={st['phase']}, message={st.get('message')}")
        # jobs.json was saved
        assert (TEST_DIR / "jobs.json").exists(), "jobs.json must exist after idle"
        with open(TEST_DIR / "jobs.json") as f:
            saved = json.load(f)
        assert_eq(len(saved), 3, "jobs.json has 3 entries after worker run")
    finally:
        teardown()


if __name__ == "__main__":
    tests = [
        test_legacy_signature,
        test_on_progress_phases_fire,
        test_cancel_aborts_and_does_not_save,
        test_runner_integration,
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
