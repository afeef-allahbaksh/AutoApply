"""Tests for src.jobs.filter — the pre-LLM keyword/location/internship filter."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.jobs.filter import (
    _is_internship_title,
    _wants_internships,
    filter_jobs,
)


def assert_eq(actual, expected, msg):
    if actual != expected:
        raise AssertionError(f"{msg}: expected {expected!r}, got {actual!r}")
    print(f"  ok: {msg}")


def _job(title, location="Remote"):
    return {
        "title": title,
        "location": location,
        "content": "",
        "company": "Acme",
        "posting_url": f"https://example.com/{title.lower().replace(' ', '-')}",
    }


# ---------- _is_internship_title ----------

def test_internship_regex_matches_intern_titles():
    """The regex catches Intern / Interns / Internship in titles."""
    for title in [
        "Software Engineer Intern",
        "Marketing Intern (Summer 2026)",
        "Engineering Interns",
        "Internship - Data Science",
        "Summer 2026 Internship Program",
        "Business Analyst Intern, Revenue Ops",
    ]:
        assert_eq(_is_internship_title(title), True, f"intern detected: {title}")


def test_internship_regex_does_not_match_intern_substrings():
    """The regex does NOT match Internal / International / Intermediate."""
    for title in [
        "Internal Audit Manager",
        "International Sales Lead",
        "Intermediate Software Engineer",
        "Internalization Engineer",
    ]:
        assert_eq(_is_internship_title(title), False, f"substring rejected: {title}")


# ---------- _wants_internships ----------

def test_wants_internships_returns_false_for_entry_level_new_grad():
    """Mustafa's case: 'entry-level' and 'new grad' do not contain 'intern'."""
    assert_eq(_wants_internships(["entry-level", "new grad"]), False, "entry-level + new-grad")
    assert_eq(_wants_internships([]), False, "empty levels")
    assert_eq(_wants_internships(["mid-level", "senior"]), False, "mid + senior")


def test_wants_internships_returns_true_when_intern_in_levels():
    """Any level containing 'intern' opts the user into internship results."""
    assert_eq(_wants_internships(["intern"]), True, "plain intern")
    assert_eq(_wants_internships(["internship"]), True, "internship variant")
    assert_eq(_wants_internships(["intern", "new grad"]), True, "intern + new-grad")
    assert_eq(_wants_internships(["Summer Intern"]), True, "case insensitive")


# ---------- filter_jobs integration ----------

def test_filter_drops_intern_titles_when_user_wants_entry_level():
    """The core bug fix: 'Business Analyst Intern' is dropped when user wants new-grad/entry-level."""
    jobs = [
        _job("Business Analyst"),                    # keep
        _job("Business Analyst Intern, Marketing"),  # drop (intern)
        _job("Junior Business Analyst"),             # keep
        _job("Insider Risk Analyst - SkillBridge Intern"),  # drop (intern)
    ]
    prefs = {
        "roles": ["Business Analyst", "Risk Analyst"],
        "locations": ["Anywhere"],
        "experience_levels": ["entry-level", "new grad"],
    }
    matched = filter_jobs(jobs, prefs)
    titles = [j["title"] for j in matched]
    assert_eq(set(titles), {"Business Analyst", "Junior Business Analyst"},
              "only non-intern roles survived")


def test_filter_keeps_intern_titles_when_user_wants_internships():
    """If the user explicitly asked for internships, don't drop them."""
    jobs = [
        _job("Business Analyst"),
        _job("Business Analyst Intern, Marketing"),
    ]
    prefs = {
        "roles": ["Business Analyst"],
        "locations": ["Anywhere"],
        "experience_levels": ["intern"],
    }
    matched = filter_jobs(jobs, prefs)
    titles = [j["title"] for j in matched]
    assert_eq(set(titles), {"Business Analyst", "Business Analyst Intern, Marketing"},
              "intern role kept when user wants internships")


def test_filter_does_not_drop_internal_or_international_jobs():
    """Make sure the regex doesn't false-positive on Internal/International."""
    jobs = [
        _job("Internal Audit Analyst"),
        _job("International Risk Analyst"),
    ]
    prefs = {
        "roles": ["Audit Analyst", "Risk Analyst"],
        "locations": ["Anywhere"],
        "experience_levels": ["entry-level"],
    }
    matched = filter_jobs(jobs, prefs)
    assert_eq(len(matched), 2, "Internal/International jobs kept")


def test_filter_still_requires_role_keyword_match():
    """Sanity: the intern drop doesn't bypass the role-keyword filter."""
    jobs = [
        _job("Software Engineer"),  # not in roles, drop
        _job("Business Analyst"),   # in roles, keep
    ]
    prefs = {
        "roles": ["Business Analyst"],
        "locations": ["Anywhere"],
        "experience_levels": ["entry-level"],
    }
    matched = filter_jobs(jobs, prefs)
    titles = [j["title"] for j in matched]
    assert_eq(titles, ["Business Analyst"], "only role-matching titles survive")


if __name__ == "__main__":
    for fn in [
        test_internship_regex_matches_intern_titles,
        test_internship_regex_does_not_match_intern_substrings,
        test_wants_internships_returns_false_for_entry_level_new_grad,
        test_wants_internships_returns_true_when_intern_in_levels,
        test_filter_drops_intern_titles_when_user_wants_entry_level,
        test_filter_keeps_intern_titles_when_user_wants_internships,
        test_filter_does_not_drop_internal_or_international_jobs,
        test_filter_still_requires_role_keyword_match,
    ]:
        print(f"\n{fn.__name__}")
        fn()
    print("\nall tests passed")
