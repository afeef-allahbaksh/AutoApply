"""Smoke test for the profile-delete route."""
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "/Users/afeef/workspace/Projects/AutoApply")

REPO = Path("/Users/afeef/workspace/Projects/AutoApply")
PROFILES_DIR = REPO / "profiles"
TEST_PROFILE = "_test_delete_smoke"
TEST_DIR = PROFILES_DIR / TEST_PROFILE


def assert_eq(actual, expected, msg):
    if actual != expected:
        raise AssertionError(f"{msg}: expected {expected!r}, got {actual!r}")
    print(f"  ok: {msg}")


def make_test_profile():
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    (TEST_DIR / "profile.json").write_text(json.dumps({
        "name": "Test", "email": "t@e.com", "phone": "1", "location": "X",
        "job_preferences": {"roles": ["A"], "experience_levels": ["B"], "locations": ["C"]},
        "settings": {"auto_submit": False},
    }))


def cleanup():
    shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_delete_with_correct_confirm():
    make_test_profile()
    try:
        from fastapi.testclient import TestClient
        from src.ui.app import app
        os.environ["AUTOAPPLY_PROFILE"] = TEST_PROFILE
        client = TestClient(app)
        assert TEST_DIR.exists(), "profile exists before delete"
        resp = client.post("/profile/delete", data={
            "name": TEST_PROFILE, "confirm": TEST_PROFILE,
        }, follow_redirects=False)
        assert_eq(resp.status_code, 303, "delete returns 303")
        assert resp.headers["location"].startswith("/?setup_msg="), \
            f"redirects to / with msg: {resp.headers['location']}"
        assert not TEST_DIR.exists(), "profile dir removed"
        print("  ok: profile directory deleted")
        # Active env var cleared
        assert os.environ.get("AUTOAPPLY_PROFILE") != TEST_PROFILE, \
            "AUTOAPPLY_PROFILE cleared after deleting active profile"
        print("  ok: AUTOAPPLY_PROFILE unset after deleting active profile")
    finally:
        cleanup()
        os.environ.pop("AUTOAPPLY_PROFILE", None)


def test_delete_rejects_wrong_confirm():
    make_test_profile()
    try:
        from fastapi.testclient import TestClient
        from src.ui.app import app
        client = TestClient(app)
        resp = client.post("/profile/delete", data={
            "name": TEST_PROFILE, "confirm": "not-the-name",
        }, follow_redirects=False)
        assert_eq(resp.status_code, 303, "wrong confirm → 303 (with error msg)")
        assert TEST_DIR.exists(), "profile still exists after rejected delete"
        print("  ok: wrong confirm leaves profile intact")
    finally:
        cleanup()


def test_delete_unknown_profile_no_op():
    from fastapi.testclient import TestClient
    from src.ui.app import app
    client = TestClient(app)
    resp = client.post("/profile/delete", data={
        "name": "_definitely_not_here", "confirm": "_definitely_not_here",
    }, follow_redirects=False)
    assert_eq(resp.status_code, 303, "unknown profile → 303 with error")
    loc = resp.headers["location"]
    assert "not%20found" in loc or "not+found" in loc or "not found" in loc, \
        f"err message in location: {loc}"
    print("  ok: unknown profile request returns redirect with error")


def test_delete_path_traversal_rejected():
    """A slug that's actually a path like '../foo' must be rejected. The
    state.list_profiles() check should already short-circuit this, but
    the path-resolve guard is defense-in-depth."""
    from fastapi.testclient import TestClient
    from src.ui.app import app
    client = TestClient(app)
    resp = client.post("/profile/delete", data={
        "name": "../etc", "confirm": "../etc",
    }, follow_redirects=False)
    assert_eq(resp.status_code, 303, "path traversal → 303")
    # Whatever it does, it must not delete anything outside profiles/
    assert PROFILES_DIR.exists(), "profiles dir untouched"
    print("  ok: path traversal slug rejected (and profiles/ intact)")


def test_profile_delete_modal_rendered_in_sidebar():
    """Smoke check the profile-delete modal + sidebar menu item render on
    every page (they live in base.html now, not just /settings)."""
    from fastapi.testclient import TestClient
    from src.ui.app import app
    profiles = sorted(p.name for p in PROFILES_DIR.iterdir() if p.is_dir() and (p / 'profile.json').exists())
    if not profiles:
        print("  skipped: no profiles on disk")
        return
    os.environ["AUTOAPPLY_PROFILE"] = profiles[0]
    try:
        client = TestClient(app)
        # Pick a page that's NOT /settings to confirm the modal moved out.
        resp = client.get("/")
        assert resp.status_code == 200, f"GET / → {resp.status_code}"
        # Sidebar menu trigger
        assert 'data-open-modal="profile-delete-modal"' in resp.text, \
            "sidebar 'Delete this profile' menu item wires to the modal"
        # Modal itself + form contract
        assert 'id="profile-delete-modal"' in resp.text, "delete modal markup present"
        assert "/profile/delete" in resp.text, "delete form action present"
        assert 'data-confirm-name="' + profiles[0] + '"' in resp.text, \
            "type-to-confirm form is wired to the active profile name"
        # And the disclosure should NO LONGER be on /settings (moved to sidebar)
        settings_resp = client.get("/settings")
        assert "danger-disclosure" not in settings_resp.text, \
            "old <details class='danger-disclosure'> removed from /settings"
        print(f"  ok: profile-delete modal renders globally for {profiles[0]}")
    finally:
        os.environ.pop("AUTOAPPLY_PROFILE", None)


if __name__ == "__main__":
    tests = [
        test_delete_with_correct_confirm,
        test_delete_rejects_wrong_confirm,
        test_delete_unknown_profile_no_op,
        test_delete_path_traversal_rejected,
        test_profile_delete_modal_rendered_in_sidebar,
    ]

    failed = 0
    for t in tests:
        print(f"\n[{t.__name__}]")
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"  FAIL: {type(e).__name__}: {e}")
            cleanup()

    print(f"\n{'=' * 40}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(0 if failed == 0 else 1)
