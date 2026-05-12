"""Unit tests for the rewritten Ashby ATS handler.

Covers pure-function logic that's safe to test without a live browser:
  - graduation date format coercion
  - EEO answer normalization (user "Prefer not to say" → form "Decline...")
  - label-keyword routing to profile/resume sources

Widget shape detection + Playwright-driven filling is verified by the
hand browser test (see lesson #17) — the locator-chain logic is hostile
to MagicMock and the real signal comes from a live form anyway.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ats.ashby.application import (
    _format_grad_date, _is_sponsorship_question, _normalize_eeo_answer,
    _pick_no_sponsorship_option, _route_label, _source_value,
)
from src.ats.ashby.fields import (
    LABEL_ROUTES, SYSTEMFIELD_EEOC_GENDER, SYSTEMFIELD_EEOC_VETERAN,
)


# ---------- _format_grad_date ----------

def test_format_grad_date_month_year_text():
    assert _format_grad_date("June 2026") == "06/15/2026"
    assert _format_grad_date("Jun 2026") == "06/15/2026"
    assert _format_grad_date("DECEMBER 2024") == "12/15/2024"


def test_format_grad_date_iso_yyyy_mm():
    assert _format_grad_date("2026-06") == "06/15/2026"
    assert _format_grad_date("2024-12") == "12/15/2024"


def test_format_grad_date_mm_yyyy():
    assert _format_grad_date("06/2026") == "06/15/2026"
    assert _format_grad_date("6/2026") == "06/15/2026"


def test_format_grad_date_mm_dd_yyyy_passthrough():
    assert _format_grad_date("06/15/2026") == "06/15/2026"
    assert _format_grad_date("6/5/2026") == "06/05/2026"


def test_format_grad_date_empty_and_garbage():
    assert _format_grad_date("") == ""
    assert _format_grad_date("sometime next year") == ""
    assert _format_grad_date("TBD") == ""


# ---------- _normalize_eeo_answer ----------

def test_eeo_normalize_exact_match():
    opts = ["Male", "Female", "Decline to self-identify"]
    assert _normalize_eeo_answer("Male", opts, SYSTEMFIELD_EEOC_GENDER) == "Male"


def test_eeo_normalize_prefer_not_to_say_to_decline():
    opts = ["Male", "Female", "Decline to self-identify"]
    assert _normalize_eeo_answer("Prefer not to say", opts, SYSTEMFIELD_EEOC_GENDER) == "Decline to self-identify"


def test_eeo_normalize_veteran_decline():
    opts = [
        "I identify as one or more of the classifications of protected veteran listed above",
        "I am not a protected veteran",
        "I decline to self-identify for protected veteran status",
    ]
    assert "decline" in _normalize_eeo_answer("Prefer not to say", opts, SYSTEMFIELD_EEOC_VETERAN).lower()


def test_eeo_normalize_substring_match():
    """User says 'I am not a veteran', form option is 'I am not a protected veteran'.
    Substring match catches it."""
    opts = [
        "I identify as one or more of the classifications of protected veteran listed above",
        "I am not a protected veteran",
        "I decline to self-identify for protected veteran status",
    ]
    result = _normalize_eeo_answer("I am not a veteran", opts, SYSTEMFIELD_EEOC_VETERAN)
    assert "not a protected veteran" in result


def test_eeo_normalize_empty_answer_falls_back():
    """No user answer → use the field's default decline label."""
    opts = ["Male", "Female", "Decline to self-identify"]
    assert _normalize_eeo_answer("", opts, SYSTEMFIELD_EEOC_GENDER) == "Decline to self-identify"


# ---------- _route_label ----------

def test_route_phone_to_profile():
    assert _route_label("Phone") == ("profile", "phone")
    assert _route_label("Phone Number") == ("profile", "phone")


def test_route_linkedin_to_profile():
    assert _route_label("LinkedIn Profile") == ("profile", "linkedin")
    assert _route_label("LinkedIn URL") == ("profile", "linkedin")


def test_route_github_to_profile():
    assert _route_label("Github Link") == ("profile", "github")
    assert _route_label("GitHub") == ("profile", "github")


def test_route_school_to_resume():
    assert _route_label("School") == ("resume_edu", "institution")
    assert _route_label("University attended") == ("resume_edu", "institution")


def test_route_graduation_date_to_resume():
    """Order matters in LABEL_ROUTES — 'graduation date' must win over 'date'.
    Critical because both 'school' and 'graduation date' could match the same
    fuzzy substring search."""
    assert _route_label("Graduation Date") == ("resume_edu", "end_date")
    assert _route_label("Expected Graduation") == ("resume_edu", "end_date")


def test_route_unknown_label_returns_none():
    assert _route_label("What's your favorite color?") is None
    assert _route_label("Why do you want to work here?") is None


# ---------- _source_value ----------

def test_source_value_profile_linkedin_normalizes_to_url():
    """Bare username → full URL."""
    profile = {"linkedin": "afeef"}
    assert _source_value("profile", "linkedin", profile, None, {}) == "https://linkedin.com/in/afeef"


def test_source_value_profile_linkedin_preserves_full_url():
    profile = {"linkedin": "https://www.linkedin.com/in/someone-else/"}
    assert _source_value("profile", "linkedin", profile, None, {}) == "https://www.linkedin.com/in/someone-else/"


def test_source_value_github_normalizes_to_url():
    profile = {"github": "afeef-ab"}
    assert _source_value("profile", "github", profile, None, {}) == "https://github.com/afeef-ab"


def test_source_value_resume_edu_graduation_date_formats():
    """end_date in resume is 'June 2026' → MM/DD/YYYY."""
    resume = {"education": [{"institution": "Drexel", "end_date": "June 2026"}]}
    assert _source_value("resume_edu", "end_date", {}, resume, {}) == "06/15/2026"


def test_source_value_resume_edu_institution_passthrough():
    resume = {"education": [{"institution": "Drexel University"}]}
    assert _source_value("resume_edu", "institution", {}, resume, {}) == "Drexel University"


def test_source_value_resume_edu_no_education_returns_empty():
    assert _source_value("resume_edu", "institution", {}, {"education": []}, {}) == ""
    assert _source_value("resume_edu", "institution", {}, None, {}) == ""


def test_source_value_responses_pronouns():
    assert _source_value("responses", "pronouns", {}, None, {"pronouns": "they/them"}) == "they/them"
    assert _source_value("responses", "pronouns", {}, None, {}) == ""


# ---------- LABEL_ROUTES sanity ----------

def test_label_routes_ordering_specific_before_generic():
    """`graduation date` must appear before `school` for keyword matching
    to resolve 'graduation' to end_date and not get confused with edu lookup."""
    keywords_in_order = [keywords for keywords, _, _ in LABEL_ROUTES]
    grad_idx = next(i for i, kw in enumerate(keywords_in_order) if "graduation date" in kw)
    school_idx = next(i for i, kw in enumerate(keywords_in_order) if "school" in kw)
    assert grad_idx < school_idx, "graduation date keywords must come before school keywords"


# ---------- sponsorship routing ----------

def test_is_sponsorship_question_detects_visa():
    assert _is_sponsorship_question("Will you now or at any time require sponsorship for employment visa status (e.g. H1B, OPT)?")
    assert _is_sponsorship_question("Are you in need of sponsorship?")
    assert _is_sponsorship_question("For this specific internship, will you require any of the below sponsorship?")
    assert _is_sponsorship_question("Visa status")


def test_is_sponsorship_question_ignores_unrelated():
    assert not _is_sponsorship_question("How did you hear about this opportunity?")
    assert not _is_sponsorship_question("What pronouns would you like our team to use?")
    assert not _is_sponsorship_question("Why do you want to work at Notion?")


def test_pick_no_sponsorship_option_finds_none():
    """Notion's sponsorship radio group: OPT/H1B/TN/None/Other → pick 'None'."""
    assert _pick_no_sponsorship_option(["OPT", "H1B", "TN", "None", "Other"]) == "None"


def test_pick_no_sponsorship_option_finds_j1_f1_none():
    """First Notion sponsorship question (J1/F1/None/Other) → 'None'."""
    assert _pick_no_sponsorship_option(["J1", "F1", "None", "Other"]) == "None"


def test_pick_no_sponsorship_option_handles_phrasing_variants():
    assert _pick_no_sponsorship_option(["Yes", "No", "Not applicable"]) == "Not applicable"
    assert _pick_no_sponsorship_option(["H1B", "I do not require sponsorship", "Other"]) == "I do not require sponsorship"


def test_pick_no_sponsorship_option_returns_empty_when_no_match():
    """All options need sponsorship → can't short-circuit, return '' so we fall to Claude."""
    assert _pick_no_sponsorship_option(["H1B", "OPT", "TN"]) == ""


if __name__ == "__main__":
    # Standalone runner for ad-hoc debugging (matches the repo convention).
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
