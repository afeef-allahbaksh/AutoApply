"""Smoke test for the setup wizard route — exercises slug munging, validation,
profile + responses persistence, and the first-visit dashboard redirect."""
import json
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/Users/afeef/workspace/Projects/AutoApply")

REPO = Path("/Users/afeef/workspace/Projects/AutoApply")
PROFILES_DIR = REPO / "profiles"


def assert_eq(actual, expected, msg):
    if actual != expected:
        raise AssertionError(f"{msg}: expected {expected!r}, got {actual!r}")
    print(f"  ok: {msg}")


def cleanup(slug):
    shutil.rmtree(PROFILES_DIR / slug, ignore_errors=True)


def test_slugify():
    from src.ui.routes.setup import _slugify_profile_name
    assert_eq(_slugify_profile_name("Jane Smith"), "jane_smith", "spaces → underscores, lowercase")
    assert_eq(_slugify_profile_name(" Jane O'Brien "), "jane_obrien", "strips punctuation")
    assert_eq(_slugify_profile_name("test-123"), "test123", "strips hyphens but keeps digits")
    assert_eq(_slugify_profile_name(""), "", "empty → empty")
    assert_eq(_slugify_profile_name("___"), "___", "underscores preserved")


def test_post_creates_profile_no_expand():
    slug = "_test_setup_basic"
    cleanup(slug)
    try:
        from fastapi.testclient import TestClient
        from src.ui.app import app
        client = TestClient(app)
        # Skip the LLM call by not setting expand
        resp = client.post("/setup", data={
            "profile_name": slug,
            "name": "Test User", "email": "t@e.com", "phone": "555-0100",
            "location": "Remote",
            "roles": "SWE, Backend Engineer",
            "experience_levels": "Junior",
            "locations": "Remote",
            "auto_submit": "",
            "rate_limit_seconds": "45",
            "work_authorization": "Yes", "visa_sponsorship": "No",
            "gender": "", "ethnicity": "", "veteran_status": "", "disability": "",
        }, follow_redirects=False)
        assert_eq(resp.status_code, 303, "POST returns 303 redirect")
        assert_eq(resp.headers["location"], "/", "redirects to /")
        # Files written
        with open(PROFILES_DIR / slug / "profile.json") as f:
            data = json.load(f)
        assert_eq(data["name"], "Test User", "name persisted")
        assert_eq(data["job_preferences"]["roles"], ["SWE", "Backend Engineer"],
                  "roles NOT expanded (no expand flag)")
        assert_eq(data["settings"]["auto_submit"], False, "auto_submit unchecked → False")
        assert_eq(data["settings"]["rate_limit_seconds"], 45, "rate_limit parsed")
        with open(PROFILES_DIR / slug / "responses.json") as f:
            r = json.load(f)
        assert_eq(r, {"work_authorization": "Yes", "visa_sponsorship": "No"},
                  "only non-empty responses persisted")
    finally:
        cleanup(slug)


def test_post_with_expand_calls_role_expander():
    slug = "_test_setup_expand"
    cleanup(slug)
    try:
        from fastapi.testclient import TestClient
        from src.ui.app import app
        client = TestClient(app)
        with patch("src.ui.routes.setup.expand_roles",
                   return_value=["SWE", "Software Engineer", "SDE I", "Junior SWE"]) as exp:
            resp = client.post("/setup", data={
                "profile_name": slug,
                "name": "Test", "email": "t@e.com", "phone": "555",
                "location": "Remote",
                "roles": "SWE",
                "expand": "1",
                "experience_levels": "Junior",
                "locations": "Remote",
                "auto_submit": "1",
            }, follow_redirects=False)
            assert_eq(resp.status_code, 303, "POST → 303")
            exp.assert_called_once()
        with open(PROFILES_DIR / slug / "profile.json") as f:
            data = json.load(f)
        assert_eq(len(data["job_preferences"]["roles"]), 4, "4 expanded roles")
        assert_eq(data["settings"]["auto_submit"], True, "auto_submit=1 → True")
    finally:
        cleanup(slug)


def test_post_empty_slug_rejected():
    from fastapi.testclient import TestClient
    from src.ui.app import app
    client = TestClient(app)
    resp = client.post("/setup", data={
        "profile_name": "   ",  # only whitespace → slug empty
        "name": "T", "email": "t@e.com", "phone": "1", "location": "X",
        "roles": "A", "experience_levels": "B", "locations": "C",
    }, follow_redirects=False)
    assert_eq(resp.status_code, 303, "redirect on empty slug")
    assert "err=" in resp.headers["location"], "err in redirect URL"
    print(f"  ok: empty slug error: {resp.headers['location']}")


def test_post_blank_after_csv_split_rejected():
    """Comma-only or whitespace-only values pass FastAPI's Form() check but
    produce an empty list after _split_csv — my route should redirect with
    an error rather than write an invalid profile."""
    from fastapi.testclient import TestClient
    from src.ui.app import app
    client = TestClient(app)
    resp = client.post("/setup", data={
        "profile_name": "_test_blank",
        "name": "T", "email": "t@e.com", "phone": "1", "location": "X",
        "roles": "A",
        "experience_levels": "   ,  ,  ",  # whitespace-only after CSV split
        "locations": "C",
    }, follow_redirects=False)
    assert_eq(resp.status_code, 303, "blank-after-CSV-split → 303 redirect")
    loc = resp.headers["location"]
    assert "required" in loc.lower(), f"err mentions required: {loc}"
    print(f"  ok: blank CSV rejected: {loc}")
    # And no profile dir should have been created
    assert not (PROFILES_DIR / "_test_blank" / "profile.json").exists(), \
        "profile.json should NOT exist after rejection"
    cleanup("_test_blank")


def test_dashboard_redirects_when_no_profile():
    """If active profile env var is unset AND profiles/ has no real profiles,
    GET / should bounce to /setup."""
    from fastapi.testclient import TestClient
    from src.ui.app import app
    client = TestClient(app)

    # Simulate "no profile" by pointing AUTOAPPLY_PROFILE at a nonexistent one
    saved = os.environ.get("AUTOAPPLY_PROFILE")
    os.environ["AUTOAPPLY_PROFILE"] = "_definitely_does_not_exist"
    try:
        resp = client.get("/", follow_redirects=False)
        assert_eq(resp.status_code, 303, "no profile → 303 redirect")
        assert_eq(resp.headers["location"], "/setup", "redirects to /setup")
    finally:
        if saved is None:
            del os.environ["AUTOAPPLY_PROFILE"]
        else:
            os.environ["AUTOAPPLY_PROFILE"] = saved


def test_get_setup_renders():
    from fastapi.testclient import TestClient
    from src.ui.app import app
    client = TestClient(app)
    resp = client.get("/setup")
    assert_eq(resp.status_code, 200, "GET /setup returns 200")
    assert "Set up your profile" in resp.text, "page renders heading"
    print("  ok: GET /setup renders")


if __name__ == "__main__":
    tests = [
        test_slugify,
        test_get_setup_renders,
        test_post_creates_profile_no_expand,
        test_post_with_expand_calls_role_expander,
        test_post_empty_slug_rejected,
        test_post_blank_after_csv_split_rejected,
        test_dashboard_redirects_when_no_profile,
    ]

    failed = 0
    for t in tests:
        print(f"\n[{t.__name__}]")
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"  FAIL: {type(e).__name__}: {e}")

    print(f"\n{'=' * 40}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(0 if failed == 0 else 1)
