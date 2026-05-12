"""Smoke test for the resume upload helpers in src.ui.routes.settings.
Tests the file-bytes helper functions directly so we don't have to mock
FastAPI's UploadFile internals."""
import json
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent
PROFILES_DIR = REPO / "profiles"
TEST_PROFILE = "_test_resume_upload"
TEST_DIR = PROFILES_DIR / TEST_PROFILE

PARSED_RESUME = {
    "contact": {"name": "Test User", "email": "t@e.com", "phone": "555-555-5555"},
    "section_order": ["experience", "projects", "skills"],
    "experience": [],
    "projects": [
        {"name": "Alpha", "technologies": "Python", "bullets": ["Did stuff"]},
        {"name": "Beta", "technologies": "Go", "bullets": ["Other stuff"]},
    ],
    "skills": [],
}

EXTRA_PROJECTS_PARSED = {
    "contact": {"name": "Test User", "email": "t@e.com", "phone": "555-555-5555"},
    "projects": [
        {"name": "Beta", "technologies": "Go", "bullets": ["Dup — should be skipped"]},
        {"name": "Gamma", "technologies": "Rust", "bullets": ["New project"]},
        {"name": "Delta", "technologies": "TS", "bullets": ["Another new one"]},
    ],
}


def assert_eq(actual, expected, msg):
    if actual != expected:
        raise AssertionError(f"{msg}: expected {expected!r}, got {actual!r}")
    print(f"  ok: {msg}")


def setup():
    TEST_DIR.mkdir(parents=True, exist_ok=True)


def teardown():
    shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_import_resume_writes_json():
    setup()
    try:
        from src.ui.routes.settings import _import_resume_from_bytes
        with patch("src.ui.routes.settings.parse_pdf_to_resume", return_value=PARSED_RESUME):
            result = _import_resume_from_bytes(TEST_PROFILE, b"fake pdf bytes")
        assert_eq(result, PARSED_RESUME, "returned parsed dict")
        # File written
        path = TEST_DIR / "resume.json"
        assert path.exists(), "resume.json was written"
        with open(path) as f:
            on_disk = json.load(f)
        assert_eq(on_disk, PARSED_RESUME, "resume.json matches parser output")
    finally:
        teardown()


def test_import_resume_empty_rejected():
    setup()
    try:
        from src.ui.routes.settings import _import_resume_from_bytes
        try:
            _import_resume_from_bytes(TEST_PROFILE, b"")
        except ValueError as e:
            print(f"  ok: empty bytes rejected: {e}")
            return
        raise AssertionError("expected ValueError")
    finally:
        teardown()


def test_import_resume_too_large_rejected():
    setup()
    try:
        from src.ui.routes.settings import MAX_PDF_BYTES, _import_resume_from_bytes
        try:
            _import_resume_from_bytes(TEST_PROFILE, b"\x00" * (MAX_PDF_BYTES + 1))
        except ValueError as e:
            print(f"  ok: oversize bytes rejected: {e}")
            return
        raise AssertionError("expected ValueError")
    finally:
        teardown()


def test_add_projects_merges_and_dedupes():
    setup()
    try:
        from src.ui.routes.settings import _add_projects_from_bytes, _import_resume_from_bytes
        # Seed with full resume
        with patch("src.ui.routes.settings.parse_pdf_to_resume", return_value=PARSED_RESUME):
            _import_resume_from_bytes(TEST_PROFILE, b"fake pdf")

        # Add projects PDF: 1 dup (Beta), 2 new (Gamma, Delta)
        with patch("src.ui.routes.settings.parse_pdf_to_resume", return_value=EXTRA_PROJECTS_PARSED):
            result = _add_projects_from_bytes(TEST_PROFILE, b"fake pdf 2")

        assert_eq(result["added"], 2, "added=2 new projects")
        assert_eq(set(result["added_names"]), {"Gamma", "Delta"}, "added Gamma and Delta")
        assert_eq(result["skipped_names"], ["Beta"], "skipped Beta as duplicate")
        # Pool now has original (Alpha, Beta) + Gamma + Delta = 4
        assert_eq(result["total"], 4, "pool has 4 total")

        # Verify on disk
        with open(TEST_DIR / "resume.json") as f:
            data = json.load(f)
        names = [p["name"] for p in data["project_pool"]]
        assert_eq(sorted(names), ["Alpha", "Beta", "Delta", "Gamma"], "project_pool names on disk")
    finally:
        teardown()


def test_add_projects_no_resume_raises():
    setup()
    try:
        from src.ui.routes.settings import _add_projects_from_bytes
        with patch("src.ui.routes.settings.parse_pdf_to_resume", return_value=EXTRA_PROJECTS_PARSED):
            try:
                _add_projects_from_bytes(TEST_PROFILE, b"fake pdf")
            except FileNotFoundError as e:
                print(f"  ok: no resume.json -> FileNotFoundError: {e}")
                return
        raise AssertionError("expected FileNotFoundError")
    finally:
        teardown()


def test_resume_info_helper():
    setup()
    try:
        from src.ui.routes.settings import _import_resume_from_bytes, _resume_info
        # Before import: not exists
        info = _resume_info(TEST_PROFILE)
        assert_eq(info["exists"], False, "no resume.json -> exists=False")

        with patch("src.ui.routes.settings.parse_pdf_to_resume", return_value=PARSED_RESUME):
            _import_resume_from_bytes(TEST_PROFILE, b"fake pdf")
        info = _resume_info(TEST_PROFILE)
        assert_eq(info["exists"], True, "after import -> exists=True")
        assert_eq(info["sections"], ["experience", "projects", "skills"], "sections captured")
        assert_eq(info["project_count"], 2, "project_count=2 (no project_pool yet, falls back to projects)")
        assert_eq(info["project_pool_names"], ["Alpha", "Beta"], "names from projects list")
    finally:
        teardown()


def test_routes_registered():
    from src.ui.app import app
    paths = sorted({r.path for r in app.routes if hasattr(r, "path")})
    for p in ["/settings/resume/import", "/settings/resume/projects/add"]:
        assert p in paths, f"missing route: {p}"
        print(f"  ok: {p} registered")


if __name__ == "__main__":
    tests = [
        test_routes_registered,
        test_import_resume_writes_json,
        test_import_resume_empty_rejected,
        test_import_resume_too_large_rejected,
        test_add_projects_merges_and_dedupes,
        test_add_projects_no_resume_raises,
        test_resume_info_helper,
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
