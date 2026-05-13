"""Tests for the Workday integration — URL parsing, validation, fetching.

All HTTP is mocked. No live API calls. The integration test against a real
tenant lives in /tmp manually (see Phase 43 verification notes).
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from src.discovery import validate_slug, validate_workday_url
from src.jobs.clients.workday import (
    fetch_workday_jobs,
    looks_like_workday_url,
    parse_workday_url,
    probe_workday,
)


def assert_eq(actual, expected, msg):
    if actual != expected:
        raise AssertionError(f"{msg}: expected {expected!r}, got {actual!r}")
    print(f"  ok: {msg}")


# ---------- parse_workday_url ----------

def test_parse_url_primary_form_with_locale():
    parsed = parse_workday_url("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite")
    assert_eq(parsed["tenant"], "nvidia", "tenant")
    assert_eq(parsed["shard"], "wd5", "shard")
    assert_eq(parsed["site"], "NVIDIAExternalCareerSite", "site")
    assert_eq(parsed["locale"], "en-US", "locale")


def test_parse_url_primary_form_no_locale():
    parsed = parse_workday_url("https://acme.wd1.myworkdayjobs.com/AcmeCareers")
    assert_eq(parsed["tenant"], "acme", "tenant")
    assert_eq(parsed["shard"], "wd1", "shard")
    assert_eq(parsed["site"], "AcmeCareers", "site")
    assert_eq(parsed["locale"], "en-US", "locale defaults to en-US")


def test_parse_url_trailing_slash_ok():
    parsed = parse_workday_url("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/")
    assert parsed is not None, "trailing slash should still parse"
    assert_eq(parsed["site"], "NVIDIAExternalCareerSite", "site (with trailing slash)")


def test_parse_url_alt_form():
    parsed = parse_workday_url("https://jobs.myworkdaysite.com/recruiting/acme/AcmeCareers")
    assert_eq(parsed["tenant"], "acme", "alt-form tenant")
    assert_eq(parsed["site"], "AcmeCareers", "alt-form site")
    assert_eq(parsed["shard"], "wd1", "alt-form shard sentinel")


def test_parse_url_rejects_non_workday():
    for url in [
        "https://boards.greenhouse.io/anthropic",
        "https://jobs.lever.co/spotify",
        "https://jobs.ashbyhq.com/notion",
        "not a url",
        "",
        "https://example.com/random/path",
        "https://nvidia.myworkdayjobs.com/NVIDIA",  # missing shard
    ]:
        assert_eq(parse_workday_url(url), None, f"non-workday URL rejected: {url[:40]}")


def test_parse_url_strips_whitespace():
    parsed = parse_workday_url("  https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIA  ")
    assert parsed is not None
    assert_eq(parsed["tenant"], "nvidia", "tenant after strip")


# ---------- looks_like_workday_url ----------

def test_looks_like_workday_url():
    assert_eq(looks_like_workday_url("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIA"), True, "real workday URL")
    assert_eq(looks_like_workday_url("anthropic"), False, "bare slug rejected")
    assert_eq(looks_like_workday_url("https://boards.greenhouse.io/anthropic"), False, "greenhouse URL rejected")


# ---------- probe_workday ----------

def test_probe_workday_happy_path():
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"jobPostings": [], "total": 1234}
    with patch("src.jobs.clients.workday.requests.post", return_value=fake) as post:
        result = probe_workday("nvidia", "wd5", "NVIDIAExternalCareerSite")
        assert_eq(result, {"total": 1234}, "probe returns total")
        # Verify the URL + body we sent
        call_url = post.call_args[0][0]
        assert_eq(
            call_url,
            "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs",
            "cxs URL constructed correctly",
        )


def test_probe_workday_returns_none_on_http_error():
    fake = MagicMock()
    fake.raise_for_status.side_effect = requests.HTTPError("400 Bad Request")
    with patch("src.jobs.clients.workday.requests.post", return_value=fake):
        result = probe_workday("badtenant", "wd99", "FakeCareers")
        assert_eq(result, None, "HTTP error returns None")


def test_probe_workday_returns_none_on_bad_shape():
    fake = MagicMock()
    fake.json.return_value = {"unexpected": "shape"}
    with patch("src.jobs.clients.workday.requests.post", return_value=fake):
        result = probe_workday("acme", "wd1", "Site")
        assert_eq(result, None, "missing jobPostings key returns None")


# ---------- validate_workday_url (in discovery.py) ----------

def test_validate_workday_url_happy_path():
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"jobPostings": [], "total": 500}
    with patch("src.jobs.clients.workday.requests.post", return_value=fake):
        result = validate_workday_url("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite")
        assert result is not None, "should validate"
        assert_eq(result["ats"], "workday", "ats")
        assert_eq(result["slug"], "nvidia", "slug = tenant")
        assert_eq(result["shard"], "wd5", "shard stored")
        assert_eq(result["site"], "NVIDIAExternalCareerSite", "site stored")
        assert_eq(result["locale"], "en-US", "locale stored")


def test_validate_workday_url_returns_none_for_non_workday():
    result = validate_workday_url("not-a-workday-url")
    assert_eq(result, None, "non-Workday input rejected without API call")


def test_validate_workday_url_returns_none_on_dead_tenant():
    fake = MagicMock()
    fake.raise_for_status.side_effect = requests.HTTPError("404")
    with patch("src.jobs.clients.workday.requests.post", return_value=fake):
        result = validate_workday_url("https://ghost.wd99.myworkdayjobs.com/Site")
        assert_eq(result, None, "404 on probe returns None")


# ---------- validate_slug dispatch ----------

def test_validate_slug_dispatches_workday():
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"jobPostings": [], "total": 1}
    with patch("src.jobs.clients.workday.requests.post", return_value=fake):
        result = validate_slug(
            "https://acme.wd1.myworkdayjobs.com/en-US/AcmeCareers",
            "workday",
        )
        assert result is not None, "workday dispatch works"
        assert_eq(result["ats"], "workday", "ats=workday")


# ---------- fetch_workday_jobs ----------

def _make_response(jobs, total):
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"jobPostings": jobs, "total": total}
    return fake


def test_fetch_workday_jobs_single_page():
    company = {
        "name": "NVIDIA",
        "ats": "workday",
        "slug": "nvidia",
        "shard": "wd5",
        "site": "NVIDIAExternalCareerSite",
        "locale": "en-US",
    }
    postings = [
        {
            "title": "Senior Software Engineer",
            "externalPath": "/job/USA-Santa-Clara/Senior-SWE_R-1",
            "locationsText": "USA - California - Santa Clara",
            "bulletFields": ["JR1234567"],
        },
        {
            "title": "Quantitative Research Analyst",
            "externalPath": "/job/USA-NYC/Quant-Research_R-2",
            "locationsText": "USA - New York - New York",
            "bulletFields": ["JR7654321"],
        },
    ]
    with patch(
        "src.jobs.clients.workday.requests.post",
        return_value=_make_response(postings, 2),
    ):
        jobs = fetch_workday_jobs(company)

    assert_eq(len(jobs), 2, "two jobs returned")
    assert_eq(jobs[0]["title"], "Senior Software Engineer", "first title")
    assert_eq(jobs[0]["company"], "NVIDIA", "company name carried through")
    assert_eq(jobs[0]["ats"], "workday", "ats=workday")
    assert_eq(jobs[0]["id"], "JR1234567", "id from bulletFields[0]")
    assert_eq(
        jobs[0]["posting_url"],
        "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/USA-Santa-Clara/Senior-SWE_R-1",
        "posting_url built from careers base + externalPath",
    )
    assert_eq(jobs[0]["content"], "", "content is empty in v1")


def test_fetch_workday_jobs_paginates():
    """If total > page size, fetch_workday_jobs makes multiple calls."""
    company = {
        "name": "BigCo",
        "ats": "workday",
        "slug": "bigco",
        "shard": "wd1",
        "site": "BigCoCareers",
        "locale": "en-US",
    }
    page_size = 20
    total = 35

    page1 = [{"title": f"Job {i}", "externalPath": f"/job/{i}", "locationsText": "Remote"} for i in range(page_size)]
    page2 = [{"title": f"Job {i}", "externalPath": f"/job/{i}", "locationsText": "Remote"} for i in range(page_size, total)]

    responses = [_make_response(page1, total), _make_response(page2, total)]
    with patch("src.jobs.clients.workday.requests.post", side_effect=responses) as post:
        jobs = fetch_workday_jobs(company)
        assert_eq(len(jobs), total, "all 35 jobs fetched across 2 pages")
        assert_eq(post.call_count, 2, "two API calls for 2-page pagination")
        # Verify offset incremented
        first_body = post.call_args_list[0].kwargs["json"]
        second_body = post.call_args_list[1].kwargs["json"]
        assert_eq(first_body["offset"], 0, "first call offset=0")
        assert_eq(second_body["offset"], 20, "second call offset=20")


def test_fetch_workday_jobs_handles_zero_total_on_subsequent_pages():
    """Regression: Workday returns the real total only on page 1; subsequent
    pages return total=0. Pagination must latch the first total instead of
    bailing when offset >= 0 on page 2. Pre-fix this only returned 40 jobs
    for tenants with thousands of openings (NVIDIA: 2000 → 40)."""
    company = {
        "name": "NVIDIA",
        "ats": "workday",
        "slug": "nvidia",
        "shard": "wd5",
        "site": "NVIDIAExternalCareerSite",
        "locale": "en-US",
    }
    real_total = 50  # 3 pages of 20 each: page sizes 20 + 20 + 10

    p1 = [{"title": f"J{i}", "externalPath": f"/job/{i}", "locationsText": "Remote"} for i in range(20)]
    p2 = [{"title": f"J{i}", "externalPath": f"/job/{i}", "locationsText": "Remote"} for i in range(20, 40)]
    p3 = [{"title": f"J{i}", "externalPath": f"/job/{i}", "locationsText": "Remote"} for i in range(40, 50)]

    # Page 1 reports the real total; subsequent pages report total=0 (Workday's bug).
    responses = [
        _make_response(p1, real_total),
        _make_response(p2, 0),
        _make_response(p3, 0),
    ]
    with patch("src.jobs.clients.workday.requests.post", side_effect=responses):
        jobs = fetch_workday_jobs(company)
        assert_eq(len(jobs), real_total, "latched-total: all 50 jobs fetched despite page-2 reporting total=0")


def test_fetch_workday_jobs_empty_response():
    company = {"slug": "nope", "shard": "wd1", "site": "Site"}
    with patch(
        "src.jobs.clients.workday.requests.post",
        return_value=_make_response([], 0),
    ):
        jobs = fetch_workday_jobs(company)
        assert_eq(jobs, [], "empty postings returns empty list")


def test_fetch_workday_jobs_handles_http_error():
    company = {"slug": "broken", "shard": "wd1", "site": "Site"}
    fake = MagicMock()
    fake.raise_for_status.side_effect = requests.HTTPError("500")
    with patch("src.jobs.clients.workday.requests.post", return_value=fake):
        jobs = fetch_workday_jobs(company)
        assert_eq(jobs, [], "HTTP error returns empty list")


def test_fetch_workday_jobs_missing_required_fields():
    """Without shard or site, the fetcher returns [] without hitting the network."""
    bad_company = {"slug": "nvidia"}  # no shard, no site
    with patch("src.jobs.clients.workday.requests.post") as post:
        jobs = fetch_workday_jobs(bad_company)
        assert_eq(jobs, [], "missing shard/site returns empty")
        assert_eq(post.call_count, 0, "no HTTP call made when shard/site missing")


if __name__ == "__main__":
    for fn in [
        test_parse_url_primary_form_with_locale,
        test_parse_url_primary_form_no_locale,
        test_parse_url_trailing_slash_ok,
        test_parse_url_alt_form,
        test_parse_url_rejects_non_workday,
        test_parse_url_strips_whitespace,
        test_looks_like_workday_url,
        test_probe_workday_happy_path,
        test_probe_workday_returns_none_on_http_error,
        test_probe_workday_returns_none_on_bad_shape,
        test_validate_workday_url_happy_path,
        test_validate_workday_url_returns_none_for_non_workday,
        test_validate_workday_url_returns_none_on_dead_tenant,
        test_validate_slug_dispatches_workday,
        test_fetch_workday_jobs_single_page,
        test_fetch_workday_jobs_paginates,
        test_fetch_workday_jobs_handles_zero_total_on_subsequent_pages,
        test_fetch_workday_jobs_empty_response,
        test_fetch_workday_jobs_handles_http_error,
        test_fetch_workday_jobs_missing_required_fields,
    ]:
        print(f"\n{fn.__name__}")
        fn()
    print("\nall tests passed")
