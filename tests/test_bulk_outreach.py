"""Smoke test for bulk-import + generate-all + batch_generate_outreach.
Mocks Claude to avoid real API calls."""
import json
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, "/Users/afeef/workspace/Projects/AutoApply")

REPO = Path("/Users/afeef/workspace/Projects/AutoApply")
PROFILES_DIR = REPO / "profiles"
TEST_PROFILE = "_test_bulk_outreach"
TEST_DIR = PROFILES_DIR / TEST_PROFILE


def assert_eq(actual, expected, msg):
    if actual != expected:
        raise AssertionError(f"{msg}: expected {expected!r}, got {actual!r}")
    print(f"  ok: {msg}")


def setup():
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    (TEST_DIR / "profile.json").write_text(json.dumps({
        "name": "Test", "email": "t@e.com", "phone": "1", "location": "X",
        "job_preferences": {"roles": ["A"], "experience_levels": ["B"], "locations": ["C"]},
        "settings": {"auto_submit": False},
    }))


def cleanup():
    shutil.rmtree(TEST_DIR, ignore_errors=True)


def setenv():
    os.environ["AUTOAPPLY_PROFILE"] = TEST_PROFILE


def test_csv_parser_basic_comma():
    from src.outreach import parse_csv_rows as _parse_csv_rows
    rows, errs = _parse_csv_rows("Jane,jane@x.com,EM,Acme,acme.com,met at conf\nJohn,,PM,Stripe,stripe.com,")
    assert_eq(len(rows), 2, "two rows parsed")
    assert_eq(rows[0]["name"], "Jane", "name col 0")
    assert_eq(rows[0]["email"], "jane@x.com", "email col 1")
    assert_eq(rows[1]["email"], "", "missing email tolerated")
    assert_eq(errs, [], "no errors")


def test_csv_parser_tab_separated():
    from src.outreach import parse_csv_rows as _parse_csv_rows
    text = "Jane\tjane@x.com\tEM\tAcme\tacme.com\tnotes\nJohn\t\tPM\tStripe\t\t"
    rows, errs = _parse_csv_rows(text)
    assert_eq(len(rows), 2, "tab-separated works")
    assert_eq(rows[0]["company"], "Acme", "company from tab cols")
    assert_eq(rows[1]["domain"], "", "empty domain in tab row")


def test_csv_parser_with_header_row():
    from src.outreach import parse_csv_rows as _parse_csv_rows
    text = "name,company,email,title\nJane,Acme,jane@acme.com,EM\nJohn,Stripe,,PM"
    rows, errs = _parse_csv_rows(text)
    assert_eq(len(rows), 2, "header row stripped, 2 data rows")
    assert_eq(rows[0]["email"], "jane@acme.com", "email column resolves under header")
    assert_eq(rows[0]["title"], "EM", "title resolves")
    assert_eq(rows[1]["company"], "Stripe", "second row company")


def test_csv_parser_header_aliases():
    from src.outreach import parse_csv_rows as _parse_csv_rows
    text = "contact_name,contact_email,company_domain,company,context_notes\nJane,j@x.com,acme.com,Acme,met conf"
    rows, errs = _parse_csv_rows(text)
    assert_eq(len(rows), 1, "aliased header parsed")
    assert_eq(rows[0]["name"], "Jane", "contact_name → name")
    assert_eq(rows[0]["domain"], "acme.com", "company_domain → domain")
    assert_eq(rows[0]["context"], "met conf", "context_notes → context")


def test_csv_parser_missing_required_fields_skipped():
    from src.outreach import parse_csv_rows as _parse_csv_rows
    text = "Jane,jane@x.com,EM,Acme\n,j@y.com,PM,Stripe\nJohn,,PM,"
    rows, errs = _parse_csv_rows(text)
    assert_eq(len(rows), 1, "only row with name + company kept (1 of 3)")
    assert_eq(rows[0]["name"], "Jane", "valid row preserved")
    assert len(errs) == 2, f"two errors raised, got {len(errs)}"
    print(f"  ok: invalid rows produced errors: {errs}")


def test_csv_parser_empty_input():
    from src.outreach import parse_csv_rows as _parse_csv_rows
    rows, errs = _parse_csv_rows("")
    assert_eq(rows, [], "empty → no rows")
    assert errs, "empty → error message"
    rows, errs = _parse_csv_rows("   \n  \n")
    assert_eq(rows, [], "whitespace-only → no rows")


def test_bulk_import_route():
    setup()
    try:
        from fastapi.testclient import TestClient

        from src.outreach import load_outreach as _load_outreach
        from src.ui.app import app
        setenv()
        c = TestClient(app)
        csv = (
            "name,email,title,company,domain,context\n"
            "Jane,jane@acme.com,EM,Acme,acme.com,met at conf\n"
            "John,,PM,Stripe,stripe.com,leads platform\n"
            "Bob,,CTO,Beta,beta.io,"
        )
        r = c.post("/cold-email/bulk-import", data={"csv_text": csv})
        assert_eq(r.status_code, 200, "bulk-import → 200")
        records = _load_outreach(TEST_PROFILE)
        assert_eq(len(records), 3, "3 records created")
        names = sorted(r["contact_name"] for r in records)
        assert_eq(names, ["Bob", "Jane", "John"], "all three names present")
        # Domain normalization happens
        anthropic_or_acme = next(r for r in records if r["contact_name"] == "Jane")
        assert_eq(anthropic_or_acme["company_domain"], "acme.com", "domain normalized")
    finally:
        cleanup()


def test_bulk_import_partial_failure():
    setup()
    try:
        from fastapi.testclient import TestClient

        from src.outreach import load_outreach as _load_outreach
        from src.ui.app import app
        setenv()
        c = TestClient(app)
        # 2 valid + 1 invalid (no name)
        csv = "Jane,,EM,Acme\n,,,NoCompany\nJohn,,PM,Stripe"
        r = c.post("/cold-email/bulk-import", data={"csv_text": csv})
        records = _load_outreach(TEST_PROFILE)
        assert_eq(len(records), 2, "2 valid rows imported, 1 invalid skipped")
        assert "skipped" in r.text.lower(), "skip count surfaced"
    finally:
        cleanup()


def test_batch_generate_outreach_happy_path():
    """batch_generate_outreach returns aligned drafts when Claude responds well."""
    from src.outreach.generator import batch_generate_outreach
    fake_message = MagicMock()
    fake_message.content = [MagicMock(text=json.dumps([
        {"index": 0, "subject": "Hi Jane", "body": "Hi Jane, mock body. — T"},
        {"index": 1, "subject": "Hi John", "body": "Hi John, mock body. — T"},
    ]))]
    with patch("src.outreach.generator.create_message", return_value=fake_message):
        result = batch_generate_outreach(
            profile_data={"name": "Test"},
            resume_data={"experience": [{"title": "SWE", "company": "Acme", "bullets": ["shipped X"]}]},
            targets=[
                {"company": "Acme", "contact_name": "Jane", "contact_title": "EM", "context_notes": "leads team"},
                {"company": "Stripe", "contact_name": "John", "contact_title": "PM", "context_notes": "shipped Y"},
            ],
        )
        assert_eq(len(result), 2, "2 drafts returned")
        assert_eq(result[0]["subject"], "Hi Jane", "draft 0 aligned to target 0")
        assert_eq(result[1]["subject"], "Hi John", "draft 1 aligned to target 1")


def test_batch_generate_falls_back_on_garbage():
    """When Claude returns malformed JSON, the batch falls back to skeletons
    per record (never returns None)."""
    from src.outreach.generator import batch_generate_outreach
    fake_message = MagicMock()
    fake_message.content = [MagicMock(text="not json at all")]
    with patch("src.outreach.generator.create_message", return_value=fake_message):
        result = batch_generate_outreach(
            profile_data={"name": "Test"}, resume_data={},
            targets=[
                {"company": "Foo", "contact_name": "Jane"},
                {"company": "Bar", "contact_name": "John"},
            ],
        )
    assert_eq(len(result), 2, "fallback fills all slots")
    assert result[0]["subject"] and result[0]["body"], "fallback subject + body non-empty"
    assert "Foo" in result[0]["subject"], "fallback subject references company"
    assert "Jane" in result[0]["body"], "fallback body greets contact"


def test_batch_generate_partial_drift():
    """If Claude returns fewer drafts than targets, missing slots fall back
    individually rather than the whole batch failing."""
    from src.outreach.generator import batch_generate_outreach
    fake_message = MagicMock()
    fake_message.content = [MagicMock(text=json.dumps([
        {"index": 0, "subject": "OK", "body": "body 0"},
        # index 1 missing
    ]))]
    with patch("src.outreach.generator.create_message", return_value=fake_message):
        result = batch_generate_outreach(
            profile_data={"name": "T"}, resume_data={},
            targets=[
                {"company": "A", "contact_name": "Jane"},
                {"company": "B", "contact_name": "John"},
            ],
        )
    assert_eq(result[0]["subject"], "OK", "slot 0 from Claude")
    assert "John" in result[1]["body"], "slot 1 fell back individually"
    print("  ok: partial-drift fallback works per-slot")


def test_generate_all_route_skips_already_drafted():
    setup()
    try:
        from fastapi.testclient import TestClient

        from src.outreach import load_outreach as _load_outreach
        from src.ui.app import app
        setenv()
        # Seed 3 records: 2 ungenerated, 1 already drafted
        (TEST_DIR / "outreach.json").write_text(json.dumps([
            {"id": "rA", "company": "A", "contact_name": "Jane", "contact_email": "",
             "contact_title": "", "context_notes": "", "company_domain": "",
             "draft_subject": "", "draft_body": "",
             "status": "draft", "created_at": "2026-05-11T00:00:00+00:00",
             "updated_at": "2026-05-11T00:00:00+00:00",
             "sent_at": None, "linked_application_idx": None, "linked_job_idx": None},
            {"id": "rB", "company": "B", "contact_name": "John", "contact_email": "",
             "contact_title": "", "context_notes": "", "company_domain": "",
             "draft_subject": "MANUAL", "draft_body": "Hand-written",  # already drafted
             "status": "draft", "created_at": "2026-05-11T00:00:00+00:00",
             "updated_at": "2026-05-11T00:00:00+00:00",
             "sent_at": None, "linked_application_idx": None, "linked_job_idx": None},
            {"id": "rC", "company": "C", "contact_name": "Bob", "contact_email": "",
             "contact_title": "", "context_notes": "", "company_domain": "",
             "draft_subject": "", "draft_body": "",
             "status": "draft", "created_at": "2026-05-11T00:00:00+00:00",
             "updated_at": "2026-05-11T00:00:00+00:00",
             "sent_at": None, "linked_application_idx": None, "linked_job_idx": None},
        ]))
        with patch("src.ui.routes.cold_email.batch_generate_outreach",
                   return_value=[
                       {"subject": "Hi Jane gen", "body": "body Jane"},
                       {"subject": "Hi Bob gen", "body": "body Bob"},
                   ]) as m:
            c = TestClient(app)
            r = c.post("/cold-email/generate-all")
            assert_eq(r.status_code, 200, "generate-all → 200")
            # Should have been called with exactly 2 targets (Jane + Bob, not John)
            targets_passed = m.call_args.args[2]
            assert_eq(len(targets_passed), 2, "only ungenerated rows passed to batch")
            names = sorted(t["contact_name"] for t in targets_passed)
            assert_eq(names, ["Bob", "Jane"], "Jane + Bob queued; John skipped (already drafted)")
        records = _load_outreach(TEST_PROFILE)
        by_id = {r["id"]: r for r in records}
        assert_eq(by_id["rA"]["draft_subject"], "Hi Jane gen", "rA drafted")
        assert_eq(by_id["rB"]["draft_subject"], "MANUAL", "rB manual draft preserved")
        assert_eq(by_id["rC"]["draft_subject"], "Hi Bob gen", "rC drafted")
    finally:
        cleanup()


def test_generate_all_empty_is_noop():
    setup()
    try:
        from fastapi.testclient import TestClient

        from src.ui.app import app
        setenv()
        (TEST_DIR / "outreach.json").write_text("[]")
        c = TestClient(app)
        with patch("src.ui.routes.cold_email.batch_generate_outreach") as m:
            r = c.post("/cold-email/generate-all")
            assert_eq(r.status_code, 200, "200 even with no records")
            m.assert_not_called()
            print("  ok: batch_generate not called when nothing ungenerated")
    finally:
        cleanup()


if __name__ == "__main__":
    tests = [
        test_csv_parser_basic_comma,
        test_csv_parser_tab_separated,
        test_csv_parser_with_header_row,
        test_csv_parser_header_aliases,
        test_csv_parser_missing_required_fields_skipped,
        test_csv_parser_empty_input,
        test_bulk_import_route,
        test_bulk_import_partial_failure,
        test_batch_generate_outreach_happy_path,
        test_batch_generate_falls_back_on_garbage,
        test_batch_generate_partial_drift,
        test_generate_all_route_skips_already_drafted,
        test_generate_all_empty_is_noop,
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
