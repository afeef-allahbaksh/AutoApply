"""Smoke test for cold outreach v2.1 — covers CRUD on outreach.json, Claude
stub for draft generation, Gmail URL construction, status transitions.
Mocks src.cold_email.generate_outreach so no real Claude calls."""
import json
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent
PROFILES_DIR = REPO / "profiles"
TEST_PROFILE = "_test_cold_email"
TEST_DIR = PROFILES_DIR / TEST_PROFILE


def assert_eq(actual, expected, msg):
    if actual != expected:
        raise AssertionError(f"{msg}: expected {expected!r}, got {actual!r}")
    print(f"  ok: {msg}")


def setup():
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    (TEST_DIR / "profile.json").write_text(json.dumps({
        "name": "Test User", "email": "t@e.com", "phone": "555",
        "location": "Remote",
        "job_preferences": {"roles": ["SWE"], "experience_levels": ["Junior"], "locations": ["Remote"]},
        "settings": {"auto_submit": False},
    }))
    (TEST_DIR / "resume.json").write_text(json.dumps({
        "contact": {"name": "Test User"},
        "section_order": ["contact"],
        "experience": [{"title": "SWE", "company": "Acme", "bullets": ["Shipped X with metric Y"]}],
        "projects": [],
    }))


def cleanup():
    shutil.rmtree(TEST_DIR, ignore_errors=True)


def setenv():
    os.environ["AUTOAPPLY_PROFILE"] = TEST_PROFILE


def test_routes_registered():
    from src.ui.app import app
    paths = sorted({r.path for r in app.routes if hasattr(r, "path")})
    for p in ["/cold-email", "/cold-email/{rid}/generate", "/cold-email/{rid}/edit",
              "/cold-email/{rid}/status", "/cold-email/{rid}/delete"]:
        assert p in paths, f"missing route: {p}"
        print(f"  ok: {p} registered")


def test_create_and_persist():
    setup()
    try:
        from fastapi.testclient import TestClient

        from src.ui.app import app
        setenv()
        client = TestClient(app)
        r = client.post("/cold-email", data={
            "company": "Anthropic", "contact_name": "Dario A.",
            "contact_email": "dario@anthropic.com",
            "contact_title": "CEO",
            "context_notes": "Built Claude.",
        })
        assert_eq(r.status_code, 200, "POST /cold-email returns 200 (partial render)")
        with open(TEST_DIR / "outreach.json") as f:
            data = json.load(f)
        assert_eq(len(data), 1, "1 record persisted")
        rec = data[0]
        assert_eq(rec["company"], "Anthropic", "company saved")
        assert_eq(rec["status"], "draft", "default status is draft")
        assert rec["id"] and len(rec["id"]) == 12, f"id is 12-char uuid: {rec['id']}"
        print(f"  ok: id generated ({rec['id']})")
        assert_eq(rec["draft_subject"], "", "no subject yet (not generated)")
    finally:
        cleanup()


def test_generate_populates_subject_and_body():
    setup()
    try:
        from fastapi.testclient import TestClient

        from src.outreach import load_outreach as _load_outreach
        from src.ui.app import app
        setenv()
        client = TestClient(app)
        client.post("/cold-email", data={
            "company": "Stripe", "contact_name": "Patrick C.",
            "contact_email": "patrick@stripe.com",
            "contact_title": "Co-founder",
            "context_notes": "Built Stripe.",
        })
        rec = _load_outreach(TEST_PROFILE)[0]
        with patch("src.ui.routes.cold_email.generate_outreach",
                   return_value={"subject": "Curious about Stripe", "body": "Hi Patrick,\nMock body.\n— Test"}) as m:
            r2 = client.post(f"/cold-email/{rec['id']}/generate")
            assert_eq(r2.status_code, 200, "generate returns 200")
            m.assert_called_once()
            # Check that the generator was called with the right inputs
            call_kwargs = m.call_args.kwargs
            assert_eq(call_kwargs["company"], "Stripe", "company passed to generator")
            assert_eq(call_kwargs["contact_name"], "Patrick C.", "contact_name passed")
            assert_eq(call_kwargs["context_notes"], "Built Stripe.", "context_notes passed")
            assert call_kwargs.get("resume_data") is not None, "resume_data loaded and passed"
            print("  ok: generator inputs threaded correctly")
        # Confirm draft saved to disk
        records = _load_outreach(TEST_PROFILE)
        assert_eq(records[0]["draft_subject"], "Curious about Stripe", "subject persisted")
        assert "Mock body" in records[0]["draft_body"], "body persisted"
    finally:
        cleanup()


def test_edit_preserves_content():
    setup()
    try:
        from fastapi.testclient import TestClient

        from src.outreach import load_outreach as _load_outreach
        from src.ui.app import app
        setenv()
        client = TestClient(app)
        client.post("/cold-email", data={
            "company": "Vercel", "contact_name": "Guillermo R.",
            "contact_email": "", "contact_title": "", "context_notes": "",
        })
        rec = _load_outreach(TEST_PROFILE)[0]
        r = client.post(f"/cold-email/{rec['id']}/edit", data={
            "contact_email": "guillermo@vercel.com",
            "contact_title": "CEO",
            "context_notes": "Built Next.js.",
            "draft_subject": "Hand-written subject",
            "draft_body": "Hand-written body.\n— Test",
        })
        assert_eq(r.status_code, 200, "edit returns 200")
        rec2 = _load_outreach(TEST_PROFILE)[0]
        assert_eq(rec2["contact_email"], "guillermo@vercel.com", "email updated")
        assert_eq(rec2["draft_subject"], "Hand-written subject", "manual subject saved")
        assert_eq(rec2["draft_body"], "Hand-written body.\n— Test", "manual body saved")
    finally:
        cleanup()


def test_status_change_stamps_sent_at():
    setup()
    try:
        from fastapi.testclient import TestClient

        from src.outreach import load_outreach as _load_outreach
        from src.ui.app import app
        setenv()
        client = TestClient(app)
        client.post("/cold-email", data={
            "company": "X", "contact_name": "Y",
            "contact_email": "", "contact_title": "", "context_notes": "",
        })
        rec = _load_outreach(TEST_PROFILE)[0]
        assert rec["sent_at"] is None, "sent_at starts None"
        client.post(f"/cold-email/{rec['id']}/status", data={"status": "sent"})
        rec2 = _load_outreach(TEST_PROFILE)[0]
        assert_eq(rec2["status"], "sent", "status flipped to sent")
        assert rec2["sent_at"] is not None, "sent_at stamped"
        print(f"  ok: sent_at = {rec2['sent_at']}")
        # Flipping to replied should NOT overwrite sent_at
        first_sent = rec2["sent_at"]
        client.post(f"/cold-email/{rec['id']}/status", data={"status": "replied"})
        rec3 = _load_outreach(TEST_PROFILE)[0]
        assert_eq(rec3["sent_at"], first_sent, "sent_at preserved on subsequent status changes")
    finally:
        cleanup()


def test_gmail_url_construction():
    from src.outreach import gmail_compose_url as _gmail_compose_url
    url = _gmail_compose_url({
        "contact_email": "jane+test@example.com",
        "draft_subject": "Hi, are you hiring?",
        "draft_body": "Body with\nnewline & ampersand.",
    })
    assert "to=jane%2Btest%40example.com" in url, f"to encoded: {url}"
    assert "su=Hi%2C%20are%20you%20hiring%3F" in url, f"subject encoded: {url}"
    assert "%0A" in url, "newlines encoded"
    assert "%26" in url, "ampersand encoded"
    print(f"  ok: Gmail URL well-formed ({len(url)} chars)")


def test_delete():
    setup()
    try:
        from fastapi.testclient import TestClient

        from src.outreach import load_outreach as _load_outreach
        from src.ui.app import app
        setenv()
        client = TestClient(app)
        client.post("/cold-email", data={
            "company": "Q", "contact_name": "W",
            "contact_email": "", "contact_title": "", "context_notes": "",
        })
        rec = _load_outreach(TEST_PROFILE)[0]
        r = client.post(f"/cold-email/{rec['id']}/delete")
        assert_eq(r.status_code, 200, "delete returns 200")
        assert_eq(len(_load_outreach(TEST_PROFILE)), 0, "record removed from disk")
    finally:
        cleanup()


def test_generator_fallback_on_claude_failure():
    """If Claude returns garbage, generate_outreach falls back to a skeleton
    so the user always has something editable."""
    from unittest.mock import MagicMock

    from src.outreach.generator import generate_outreach
    # Simulate Claude returning non-JSON
    bad_message = MagicMock()
    bad_message.content = [MagicMock(text="not valid json at all")]
    with patch("src.outreach.generator.create_message", return_value=bad_message):
        result = generate_outreach(
            profile_data={"name": "Test User"}, resume_data={},
            company="Foo", contact_name="Bar", contact_title="CTO",
            context_notes="",
        )
        assert "subject" in result and "body" in result, "fallback has both fields"
        assert "Foo" in result["subject"], f"company in fallback subject: {result['subject']}"
        assert "Bar" in result["body"], f"contact name in fallback body: {result['body']}"
        print(f"  ok: fallback subject = {result['subject']!r}")


if __name__ == "__main__":
    tests = [
        test_routes_registered,
        test_create_and_persist,
        test_generate_populates_subject_and_body,
        test_edit_preserves_content,
        test_status_change_stamps_sent_at,
        test_gmail_url_construction,
        test_delete,
        test_generator_fallback_on_claude_failure,
    ]

    failed = 0
    for t in tests:
        print(f"\n[{t.__name__}]")
        try:
            t()
        except Exception as e:
            failed += 1
            import traceback
            print(f"  FAIL: {type(e).__name__}: {e}")
            traceback.print_exc(limit=2)
            cleanup()

    print(f"\n{'=' * 40}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(0 if failed == 0 else 1)
