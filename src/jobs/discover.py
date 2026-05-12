"""Top-level job-discovery pipeline orchestrator.

Composes the per-ATS clients with the filter + classify modules into the
8-stage pipeline. Streams progress via `on_progress` callback and supports
cooperative cancellation via `cancel_check` — used by the UI's background
task runner.
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.profile_loader import PROFILES_DIR, _atomic_write_json

from .classify import classify_jobs_by_country, classify_jobs_by_level, score_jobs_fit
from .clients.ashby import fetch_ashby_jobs
from .clients.greenhouse import fetch_greenhouse_jobs
from .clients.lever import fetch_lever_jobs
from .filter import deduplicate_jobs, filter_jobs


def fetch_jobs_for_company(company: dict) -> list[dict]:
    """Fetch jobs for a single company entry from companies.json."""
    ats = company["ats"]
    slug = company["slug"]
    if ats == "greenhouse":
        return fetch_greenhouse_jobs(slug)
    elif ats == "lever":
        return fetch_lever_jobs(slug)
    elif ats == "ashby":
        return fetch_ashby_jobs(slug)
    return []


def discover_jobs(profile_name: str, on_progress=None, cancel_check=None) -> list[dict]:
    """Full job discovery pipeline for a profile.

    1. Load companies.json
    2. Fetch jobs from each company (in parallel)
    3. Filter by preferences (keyword + location)
    4. LLM-classify by country
    5. LLM-classify by experience level
    6. Deduplicate against applications.json
    7. LLM-score fit
    8. Save to jobs.json

    `on_progress` (optional): called as `on_progress(phase=..., **counts)` at
    each phase transition and during parallel fetch. Phases:
    `loading_companies`, `fetching`, `filtering`, `classifying_country`,
    `classifying_level`, `dedup`, `scoring_fit`, `saving`.

    `cancel_check` (optional): polled at phase boundaries and between completed
    fetch futures. If it returns True, the pipeline shuts down the executor
    and returns whatever was collected up to that point — but does NOT save
    partial results to jobs.json (a cancelled run leaves jobs.json untouched
    so re-running picks up cleanly).
    """
    def _emit(**kw):
        if on_progress is not None:
            on_progress(**kw)

    def _cancelled() -> bool:
        return cancel_check is not None and cancel_check()

    profile_dir = PROFILES_DIR / profile_name

    _emit(phase="loading_companies", message="Loading companies…")

    companies_path = profile_dir / "companies.json"
    companies = []
    if companies_path.exists() and companies_path.stat().st_size > 0:
        try:
            with open(companies_path) as f:
                companies = json.load(f)
        except json.JSONDecodeError:
            companies = []
    if not companies:
        print("No companies on file — use Discover companies on the Companies page first.")
        _emit(phase="error", message="No companies on file — use Discover companies on the Companies page first.")
        return []

    with open(profile_dir / "profile.json") as f:
        profile = json.load(f)
    preferences = profile["job_preferences"]

    applications_path = profile_dir / "applications.json"
    applications = []
    if applications_path.exists() and applications_path.stat().st_size > 0:
        try:
            with open(applications_path) as f:
                applications = json.load(f)
        except json.JSONDecodeError:
            applications = []

    if _cancelled():
        return []

    all_jobs = []
    companies_total = len(companies)
    companies_processed = 0
    _emit(
        phase="fetching",
        companies_total=companies_total, companies_processed=0,
        jobs_fetched=0,
        message=f"Fetching jobs from {companies_total} companies…",
    )
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            pool.submit(fetch_jobs_for_company, company): company
            for company in companies
        }
        for future in as_completed(futures):
            if _cancelled():
                pool.shutdown(wait=False, cancel_futures=True)
                return []
            company = futures[future]
            jobs = future.result()
            print(f"  fetched: {company['name']} ({company['ats']}/{company['slug']}) — {len(jobs)} jobs")
            all_jobs.extend(jobs)
            companies_processed += 1
            _emit(
                phase="fetching",
                companies_total=companies_total,
                companies_processed=companies_processed,
                jobs_fetched=len(all_jobs),
                message=(
                    f"Fetched {companies_processed}/{companies_total} companies · "
                    f"{len(all_jobs)} jobs so far"
                ),
            )

    if _cancelled():
        return []

    _emit(phase="filtering", message="Filtering by keywords and location…")
    matched = filter_jobs(all_jobs, preferences)
    print(f"\n  {len(matched)} jobs match keywords/location (from {len(all_jobs)} total)")
    _emit(
        phase="filtering",
        jobs_after_filter=len(matched),
        message=f"{len(matched)} jobs match keywords/location (from {len(all_jobs)} total)",
    )

    user_location = profile.get("location", "")
    if user_location and matched and not _cancelled():
        _emit(
            phase="classifying_country",
            message=f"Classifying {len(matched)} jobs by country (Claude, ~$0.005)…",
        )
        before = len(matched)
        print(f"  Classifying {len(matched)} jobs by country (~$0.005)...")
        matched = classify_jobs_by_country(matched, user_location)
        print(f"  {len(matched)} jobs in your country (dropped {before - len(matched)})")
        _emit(
            phase="classifying_country",
            jobs_after_country=len(matched),
            message=f"{len(matched)} jobs in your country (dropped {before - len(matched)})",
        )

    if _cancelled():
        return []

    experience_levels = preferences.get("experience_levels", [])
    if experience_levels and matched:
        _emit(
            phase="classifying_level",
            message=f"Classifying {len(matched)} jobs by experience level (Claude, ~$0.01)…",
        )
        print(f"  Classifying {len(matched)} jobs by experience level (~$0.01)...")
        matched = classify_jobs_by_level(matched, experience_levels)
        print(f"  {len(matched)} jobs match experience level")
        _emit(
            phase="classifying_level",
            jobs_after_level=len(matched),
            message=f"{len(matched)} jobs match experience level",
        )

    if _cancelled():
        return []

    _emit(phase="dedup", message="Deduplicating against application history…")
    new_jobs = deduplicate_jobs(matched, applications)
    print(f"  {len(new_jobs)} new jobs (after dedup)")
    _emit(
        phase="dedup",
        jobs_after_dedup=len(new_jobs),
        message=f"{len(new_jobs)} new jobs (after dedup)",
    )

    resume_path = profile_dir / "resume.json"
    if new_jobs and resume_path.exists() and not _cancelled():
        with open(resume_path) as f:
            resume_data = json.load(f)
        _emit(
            phase="scoring_fit",
            message=f"Scoring {len(new_jobs)} jobs for fit (Claude, ~$0.01)…",
        )
        print(f"  Scoring {len(new_jobs)} jobs for fit (~$0.01)...")
        new_jobs = score_jobs_fit(new_jobs, resume_data, preferences)
        excellent_good = sum(1 for j in new_jobs if j.get("fit_score", 3) >= 4)
        moderate = sum(1 for j in new_jobs if j.get("fit_score", 3) == 3)
        weak_poor = sum(1 for j in new_jobs if j.get("fit_score", 3) < 3)
        print(f"  Fit scores: {excellent_good} excellent/good, {moderate} moderate, {weak_poor} weak/poor")
        new_jobs.sort(key=lambda j: (j.get("fit_score", 3), j.get("relevance_score", 0)), reverse=True)
        _emit(
            phase="scoring_fit",
            jobs_scored=len(new_jobs),
            fit_excellent_good=excellent_good,
            fit_moderate=moderate,
            fit_weak_poor=weak_poor,
            message=(
                f"Fit scores: {excellent_good} excellent/good · "
                f"{moderate} moderate · {weak_poor} weak/poor"
            ),
        )

    # Save to jobs.json (cancelled runs exit before this point so jobs.json is untouched)
    _emit(phase="saving", message="Saving jobs.json…")
    _atomic_write_json(profile_dir / "jobs.json", new_jobs)

    return new_jobs
