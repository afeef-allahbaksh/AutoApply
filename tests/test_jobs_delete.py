"""Smoke test for the per-row delete on /jobs — POST /jobs/{idx}/delete
removes the job from jobs.json without mutating applications.json."""
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent
PROFILES_DIR = REPO / "profiles"

PROFILE_SLUG = "_test_jobs_delete"


def _setup_profile():
    pdir = PROFILES_DIR / PROFILE_SLUG
    shutil.rmtree(pdir, ignore_errors=True)
    pdir.mkdir(parents=True, exist_ok=True)
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
    (pdir / "responses.json").write_text(json.dumps({"placeholder": "x"}))
    (pdir / "companies.json").write_text(json.dumps([
        {"name": "Alpha Co", "slug": "alpha", "ats": "greenhouse",
         "careers_url": "https://boards.greenhouse.io/alpha", "added": "2025-01-01"},
    ]))
    (pdir / "jobs.json").write_text(json.dumps([
        {"company": "Alpha Co", "title": "SWE I",   "ats": "greenhouse",
         "posting_url": "https://x/1", "content": "", "location": "Remote"},
        {"company": "Alpha Co", "title": "SWE II",  "ats": "greenhouse",
         "posting_url": "https://x/2", "content": "", "location": "Remote"},
        {"company": "Alpha Co", "title": "SWE III", "ats": "greenhouse",
         "posting_url": "https://x/3", "content": "", "location": "Remote"},
    ]))
    (pdir / "applications.json").write_text(json.dumps([
        {"company": "Alpha Co", "role": "SWE II", "posting_url": "https://x/2",
         "status": "applied", "date": "2025-01-05",
         "status_updated_at": "2025-01-05T00:00:00Z", "source": "autoapply"},
    ]))


def _teardown_profile():
    shutil.rmtree(PROFILES_DIR / PROFILE_SLUG, ignore_errors=True)


def _client():
    os.environ["AUTOAPPLY_PROFILE"] = PROFILE_SLUG
    from fastapi.testclient import TestClient

    from src.ui.app import app
    return TestClient(app)


def _read_json(name: str):
    return json.loads((PROFILES_DIR / PROFILE_SLUG / name).read_text())


def test_delete_job_removes_only_indexed_row():
    _setup_profile()
    try:
        resp = _client().post("/jobs/1/delete")
        assert resp.status_code == 200, resp.text
        jobs = _read_json("jobs.json")
        titles = [j["title"] for j in jobs]
        assert titles == ["SWE I", "SWE III"], titles
    finally:
        _teardown_profile()


def test_delete_job_returns_empty_body_for_hx_swap_delete():
    """hx-swap='delete' on the client side removes the row when the server
    returns 200 — the body itself is ignored. We send an empty body."""
    _setup_profile()
    try:
        resp = _client().post("/jobs/0/delete")
        assert resp.status_code == 200
        assert resp.text == ""
    finally:
        _teardown_profile()


def test_delete_job_leaves_applications_intact():
    """Deleting a job does NOT delete the matching application entry."""
    _setup_profile()
    try:
        _client().post("/jobs/1/delete")  # the job an application references
        apps = _read_json("applications.json")
        assert len(apps) == 1
        assert apps[0]["role"] == "SWE II"
    finally:
        _teardown_profile()


def test_delete_job_out_of_range_returns_404():
    _setup_profile()
    try:
        resp = _client().post("/jobs/99/delete")
        assert resp.status_code == 404, resp.text
        jobs = _read_json("jobs.json")
        assert len(jobs) == 3
    finally:
        _teardown_profile()


def test_delete_job_negative_index_returns_404():
    """Don't let Python-style negative indexing wrap around to delete from
    the end — `-1` should be out-of-range, not pop(-1)."""
    _setup_profile()
    try:
        resp = _client().post("/jobs/-1/delete")
        # FastAPI parses the path param as int; -1 < 0 → our range check fires.
        assert resp.status_code in (404, 422), resp.text
        jobs = _read_json("jobs.json")
        assert len(jobs) == 3
    finally:
        _teardown_profile()


def test_jobs_route_registered():
    """Sanity check that the new route is wired into the FastAPI app."""
    from src.ui.app import app
    paths = {r.path for r in app.routes if hasattr(r, "methods")}
    assert "/jobs/{idx}/delete" in paths


if __name__ == "__main__":
    import inspect
    fns = [(n, f) for n, f in globals().items() if n.startswith("test_") and inspect.isfunction(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  ✓ {name}")
        except AssertionError as e:
            print(f"  ✗ {name}: {e}")
            failed += 1
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
