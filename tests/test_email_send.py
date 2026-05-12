"""Smoke test for SMTP send wiring. Mocks smtplib so no real email goes out."""
import json
import os
import shutil
import smtplib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent
PROFILES_DIR = REPO / "profiles"
TEST_PROFILE = "_test_email_send"
TEST_DIR = PROFILES_DIR / TEST_PROFILE


def assert_eq(actual, expected, msg):
    if actual != expected:
        raise AssertionError(f"{msg}: expected {expected!r}, got {actual!r}")
    print(f"  ok: {msg}")


def setup_with_inbox():
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    (TEST_DIR / "profile.json").write_text(json.dumps({
        "name": "T", "email": "t@e.com", "phone": "1", "location": "X",
        "job_preferences": {"roles": ["A"], "experience_levels": ["B"], "locations": ["C"]},
        "settings": {"auto_submit": False},
    }))
    (TEST_DIR / "imap").mkdir(exist_ok=True)
    (TEST_DIR / "imap" / "credentials.json").write_text(json.dumps({
        "email": "sender@example.com",
        "password": "app-pw-1234",
        "server": "imap.gmail.com",
        "port": 993,
    }))


def setup_no_inbox():
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    (TEST_DIR / "profile.json").write_text(json.dumps({
        "name": "T", "email": "t@e.com", "phone": "1", "location": "X",
        "job_preferences": {"roles": ["A"], "experience_levels": ["B"], "locations": ["C"]},
        "settings": {"auto_submit": False},
    }))


def cleanup():
    shutil.rmtree(TEST_DIR, ignore_errors=True)


def setenv():
    os.environ["AUTOAPPLY_PROFILE"] = TEST_PROFILE


def test_smtp_host_derivation():
    from src.outreach.sender import _derive_smtp_host
    assert_eq(_derive_smtp_host("imap.gmail.com"), "smtp.gmail.com", "Gmail")
    assert_eq(_derive_smtp_host("imap-mail.outlook.com"), "imap-mail.outlook.com", "non-conforming → unchanged")
    assert_eq(_derive_smtp_host("imap.fastmail.com"), "smtp.fastmail.com", "Fastmail")
    assert_eq(_derive_smtp_host("imaps.example.com"), "smtp.example.com", "imaps prefix handled")
    assert_eq(_derive_smtp_host("127.0.0.1"), "127.0.0.1", "literal host unchanged")


def test_send_validation():
    setup_with_inbox()
    try:
        from src.outreach.sender import send_email
        ok, err = send_email(TEST_PROFILE, "", "Subject", "Body")
        assert_eq(ok, False, "empty to → rejected")
        assert "recipient" in err.lower(), f"err mentions recipient: {err}"
        ok, err = send_email(TEST_PROFILE, "x@y.com", "", "Body")
        assert_eq(ok, False, "empty subject → rejected")
        ok, err = send_email(TEST_PROFILE, "x@y.com", "Subject", "")
        assert_eq(ok, False, "empty body → rejected")
        print("  ok: blank field rejections all have helpful messages")
    finally:
        cleanup()


def test_send_no_inbox():
    setup_no_inbox()
    try:
        from src.outreach.sender import send_email
        ok, err = send_email(TEST_PROFILE, "x@y.com", "S", "B")
        assert_eq(ok, False, "no creds → rejected")
        assert "settings" in err.lower(), f"err points to Settings: {err}"
        print(f"  ok: no-inbox error: {err}")
    finally:
        cleanup()


def test_send_happy_path():
    setup_with_inbox()
    try:
        from src.outreach.sender import send_email
        mock_smtp = MagicMock()
        mock_smtp.__enter__.return_value = mock_smtp
        mock_smtp.__exit__.return_value = False
        with patch("src.outreach.sender.smtplib.SMTP", return_value=mock_smtp) as smtp_class:
            ok, err = send_email(TEST_PROFILE, "to@example.com", "Hi", "Body line 1\nBody line 2")
            assert_eq(ok, True, "happy path → ok=True")
            assert_eq(err, "", "no error on success")
            # smtp.gmail.com:587 derived from imap.gmail.com
            args, kwargs = smtp_class.call_args
            assert_eq(args[0], "smtp.gmail.com", "SMTP host derived correctly")
            assert_eq(args[1], 587, "STARTTLS port 587 used")
            mock_smtp.starttls.assert_called_once()
            mock_smtp.login.assert_called_once_with("sender@example.com", "app-pw-1234")
            mock_smtp.send_message.assert_called_once()
            # Check the EmailMessage we built
            sent_msg = mock_smtp.send_message.call_args.args[0]
            assert_eq(sent_msg["From"], "sender@example.com", "From: sender")
            assert_eq(sent_msg["To"], "to@example.com", "To: recipient")
            assert_eq(sent_msg["Subject"], "Hi", "Subject preserved")
            assert "Body line 1" in sent_msg.get_content(), "body included"
            print("  ok: SMTP send produced correct EmailMessage")
    finally:
        cleanup()


def test_send_auth_failure():
    setup_with_inbox()
    try:
        from src.outreach.sender import send_email
        with patch("src.outreach.sender.smtplib.SMTP", side_effect=smtplib.SMTPAuthenticationError(535, b"bad")):
            ok, err = send_email(TEST_PROFILE, "to@example.com", "S", "B")
            assert_eq(ok, False, "auth fail → ok=False")
            assert "password" in err.lower() or "app password" in err.lower(), \
                f"err mentions app password: {err}"
            print(f"  ok: auth failure surfaces helpful copy: {err[:80]}")
    finally:
        cleanup()


def test_route_send_happy_path():
    setup_with_inbox()
    try:
        # Create an outreach record with a draft, then call /send
        (TEST_DIR / "outreach.json").write_text(json.dumps([{
            "id": "abc123",
            "company": "Acme", "contact_name": "Jane",
            "contact_email": "jane@acme.com",
            "contact_title": "EM",
            "context_notes": "",
            "draft_subject": "Hi about Python",
            "draft_body": "Hi Jane,\n\nQuick chat?\n\n— Test",
            "status": "draft",
            "created_at": "2026-05-11T00:00:00+00:00",
            "updated_at": "2026-05-11T00:00:00+00:00",
            "sent_at": None,
            "linked_application_idx": None, "linked_job_idx": None,
        }]))
        from fastapi.testclient import TestClient

        from src.ui.app import app
        setenv()
        client = TestClient(app)
        mock_smtp = MagicMock()
        mock_smtp.__enter__.return_value = mock_smtp
        mock_smtp.__exit__.return_value = False
        with patch("src.outreach.sender.smtplib.SMTP", return_value=mock_smtp):
            r = client.post("/cold-email/abc123/send")
            assert_eq(r.status_code, 200, "POST /send → 200")
            mock_smtp.send_message.assert_called_once()
        # Record updated on disk
        with open(TEST_DIR / "outreach.json") as f:
            rec = json.load(f)[0]
        assert_eq(rec["status"], "sent", "status flipped to sent")
        assert rec["sent_at"] is not None, "sent_at stamped"
        print(f"  ok: record updated post-send (sent_at={rec['sent_at']})")
    finally:
        cleanup()


def test_route_send_failure_does_not_change_status():
    setup_with_inbox()
    try:
        (TEST_DIR / "outreach.json").write_text(json.dumps([{
            "id": "abc123",
            "company": "Acme", "contact_name": "Jane",
            "contact_email": "jane@acme.com",
            "contact_title": "",
            "context_notes": "",
            "draft_subject": "S", "draft_body": "B",
            "status": "draft",
            "created_at": "2026-05-11T00:00:00+00:00",
            "updated_at": "2026-05-11T00:00:00+00:00",
            "sent_at": None,
            "linked_application_idx": None, "linked_job_idx": None,
        }]))
        from fastapi.testclient import TestClient

        from src.ui.app import app
        setenv()
        client = TestClient(app)
        with patch("src.outreach.sender.smtplib.SMTP", side_effect=smtplib.SMTPAuthenticationError(535, b"bad")):
            r = client.post("/cold-email/abc123/send")
            assert_eq(r.status_code, 200, "POST /send still 200 (with err)")
            # Surface error in response
            assert "password" in r.text.lower() or "auth" in r.text.lower(), \
                "error visible in rendered partial"
        with open(TEST_DIR / "outreach.json") as f:
            rec = json.load(f)[0]
        assert_eq(rec["status"], "draft", "status untouched on failure")
        assert rec["sent_at"] is None, "sent_at not stamped on failure"
        print("  ok: failed send leaves record in draft state")
    finally:
        cleanup()


if __name__ == "__main__":
    tests = [
        test_smtp_host_derivation,
        test_send_validation,
        test_send_no_inbox,
        test_send_happy_path,
        test_send_auth_failure,
        test_route_send_happy_path,
        test_route_send_failure_does_not_change_status,
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
