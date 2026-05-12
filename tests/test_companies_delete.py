"""Smoke test for the Companies delete route — verifies the row × button
backed by POST /companies/{slug}/delete removes the company without
mutating applications.json or jobs.json."""
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "/Users/afeef/workspace/Projects/AutoApply")

REPO = Path("/Users/afeef/workspace/Projects/AutoApply")
PROFILES_DIR = REPO / "profiles"

PROFILE_SLUG = "_test_companies_delete"


def _setup_profile():
    """Seed a hermetic _test_companies_delete profile with 3 companies and a
    couple of application/job entries to verify the delete is non-destructive."""
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
        {"name": "Beta Inc", "slug": "beta", "ats": "lever",
         "careers_url": "https://jobs.lever.co/beta", "added": "2025-01-02"},
        {"name": "Gamma Ltd", "slug": "gamma", "ats": "ashby",
         "careers_url": "https://jobs.ashbyhq.com/gamma", "added": "2025-01-03"},
    ]))
    (pdir / "applications.json").write_text(json.dumps([
        {"company": "Beta Inc", "role": "SWE", "posting_url": "https://x/1",
         "status": "applied", "date": "2025-01-05",
         "status_updated_at": "2025-01-05T00:00:00Z", "source": "autoapply"},
    ]))
    (pdir / "jobs.json").write_text(json.dumps([
        {"company": "Beta Inc", "title": "SWE", "ats": "lever",
         "posting_url": "https://x/1", "content": ""},
    ]))


def _teardown_profile():
    shutil.rmtree(PROFILES_DIR / PROFILE_SLUG, ignore_errors=True)


def _client():
    """Activate the test profile and return a TestClient bound to the FastAPI app."""
    os.environ["AUTOAPPLY_PROFILE"] = PROFILE_SLUG
    from fastapi.testclient import TestClient
    from src.ui.app import app
    return TestClient(app)


def _read_json(name: str):
    return json.loads((PROFILES_DIR / PROFILE_SLUG / name).read_text())


def test_delete_company_removes_only_that_slug():
    """POST /companies/beta/delete removes Beta Inc, leaves Alpha and Gamma."""
    _setup_profile()
    try:
        resp = _client().post(f"/companies/beta/delete")
        assert resp.status_code == 200, resp.text
        companies = _read_json("companies.json")
        slugs = [c["slug"] for c in companies]
        assert slugs == ["alpha", "gamma"], slugs
    finally:
        _teardown_profile()


def test_delete_company_leaves_applications_intact():
    """Deleting a company does NOT delete that company's applications.
    History is preserved (composite-key dedup keeps them independent)."""
    _setup_profile()
    try:
        _client().post(f"/companies/beta/delete")
        apps = _read_json("applications.json")
        assert len(apps) == 1
        assert apps[0]["company"] == "Beta Inc"
    finally:
        _teardown_profile()


def test_delete_company_leaves_jobs_intact():
    """Deleting a company does NOT prune jobs.json. The next Refresh on the
    Jobs page re-fetches and naturally drops orphaned rows."""
    _setup_profile()
    try:
        _client().post(f"/companies/beta/delete")
        jobs = _read_json("jobs.json")
        assert len(jobs) == 1
        assert jobs[0]["company"] == "Beta Inc"
    finally:
        _teardown_profile()


def test_delete_unknown_slug_returns_404():
    _setup_profile()
    try:
        resp = _client().post(f"/companies/nonexistent/delete")
        assert resp.status_code == 404, resp.text
        # And nothing was mutated
        companies = _read_json("companies.json")
        assert len(companies) == 3
    finally:
        _teardown_profile()


def test_delete_lowercases_slug():
    """Slug is URL-path-sourced; route lowercases it so /BETA/delete works
    the same as /beta/delete (matches add_company's lowercasing)."""
    _setup_profile()
    try:
        resp = _client().post(f"/companies/BETA/delete")
        assert resp.status_code == 200, resp.text
        slugs = [c["slug"] for c in _read_json("companies.json")]
        assert "beta" not in slugs
    finally:
        _teardown_profile()


def test_delete_response_partial_renders_remaining_companies():
    """Response body is the #companies-content partial — surviving rows
    appear with their slug; the deleted row's slug is absent. ('Beta Inc' as
    a name appears in the success flash banner, so we check the slug column,
    not the company-name string.)"""
    _setup_profile()
    try:
        resp = _client().post(f"/companies/beta/delete")
        body = resp.text
        assert ">alpha<" in body, "alpha slug cell should be present"
        assert ">gamma<" in body, "gamma slug cell should be present"
        assert ">beta<" not in body, "beta slug cell should be removed"
        assert "Removed Beta Inc." in body, "flash banner should confirm deletion"
    finally:
        _teardown_profile()


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
