import json
import time
from pathlib import Path

import requests

PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"

GREENHOUSE_JOBS_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
LEVER_JOBS_API = "https://api.lever.co/v0/postings/{slug}"

HEADERS = {"User-Agent": "AutoApply/1.0"}
TIMEOUT = 15


def fetch_greenhouse_jobs(slug: str) -> list[dict]:
    """Fetch all jobs from a Greenhouse board. Returns normalized job dicts."""
    url = GREENHOUSE_JOBS_API.format(slug=slug)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return []

    jobs = []
    for raw in data.get("jobs", []):
        departments = [d["name"] for d in raw.get("departments", [])]
        jobs.append({
            "id": str(raw["id"]),
            "title": raw["title"],
            "company": raw.get("company_name", slug.title()),
            "location": raw.get("location", {}).get("name", ""),
            "departments": departments,
            "posting_url": raw["absolute_url"],
            "ats": "greenhouse",
            "slug": slug,
            "content": raw.get("content", ""),
        })
    return jobs


def _matches_any(text: str, keywords: list[str]) -> bool:
    """Check if any keyword appears as a substring in the text (case-insensitive)."""
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def score_job(job: dict, preferences: dict) -> float:
    """Score a job's relevance to preferences. Higher = better match.

    Scoring:
    - Exact title match to a role keyword: +10
    - Partial title match (keyword is substring): +5
    - Experience level keyword in title: +3
    - Experience level keyword in content: +1
    - "Remote" location match: +2
    """
    score = 0.0
    title_lower = job["title"].lower()
    content_lower = job.get("content", "").lower()

    roles = preferences.get("roles", [])
    for role in roles:
        role_lower = role.lower()
        if title_lower == role_lower:
            score += 10
        elif role_lower in title_lower:
            score += 5

    experience_levels = preferences.get("experience_levels", [])
    for level in experience_levels:
        level_lower = level.lower()
        if level_lower in title_lower:
            score += 3
        elif level_lower in content_lower:
            score += 1

    if "remote" in job.get("location", "").lower():
        score += 2

    return score


def _matches_location(job_location: str, preferred_locations: list[str]) -> bool:
    """Check if the job location matches any preferred location.

    Handles cases like 'Remote', 'New York, NY', 'San Francisco, CA; New York, NY'.
    """
    if not job_location:
        return False
    job_lower = job_location.lower()
    for loc in preferred_locations:
        if loc.lower() in job_lower:
            return True
    return False


def filter_jobs(jobs: list[dict], preferences: dict) -> list[dict]:
    """Filter jobs against profile preferences.

    A job passes if:
    1. Title matches at least one role keyword, AND
    2. Location matches at least one preferred location (or job is Remote)
    """
    roles = preferences.get("roles", [])
    locations = preferences.get("locations", [])

    matched = []
    for job in jobs:
        title = job["title"]

        # Title must match at least one role keyword
        if not _matches_any(title, roles):
            continue

        # Location check
        job_loc = job["location"]
        if not _matches_location(job_loc, locations):
            continue

        job["relevance_score"] = score_job(job, preferences)
        matched.append(job)

    matched.sort(key=lambda j: j["relevance_score"], reverse=True)
    return matched


def deduplicate_jobs(jobs: list[dict], applications: list[dict]) -> list[dict]:
    """Remove jobs the user has already applied to.

    Uses composite key: (company, title, posting_url) matched against
    (company, role, posting_url) in applications.json.
    """
    applied = {
        (a["company"], a["role"], a["posting_url"])
        for a in applications
    }
    return [
        j for j in jobs
        if (j["company"], j["title"], j["posting_url"]) not in applied
    ]


def fetch_jobs_for_company(company: dict) -> list[dict]:
    """Fetch jobs for a single company entry from companies.json."""
    ats = company["ats"]
    slug = company["slug"]
    if ats == "greenhouse":
        return fetch_greenhouse_jobs(slug)
    elif ats == "lever":
        return fetch_lever_jobs(slug)
    return []


def discover_jobs(profile_name: str, delay: float = 1.0) -> list[dict]:
    """Full job discovery pipeline for a profile.

    1. Load companies.json
    2. Fetch jobs from each company
    3. Filter by preferences
    4. Deduplicate against applications.json
    5. Save to jobs.json
    6. Return matched jobs
    """
    profile_dir = PROFILES_DIR / profile_name

    # Load companies
    companies_path = profile_dir / "companies.json"
    if not companies_path.exists():
        print("No companies.json found. Run 'discover' first.")
        return []
    with open(companies_path) as f:
        companies = json.load(f)

    # Load profile preferences
    with open(profile_dir / "profile.json") as f:
        profile = json.load(f)
    preferences = profile["job_preferences"]

    # Load applications for dedup
    applications_path = profile_dir / "applications.json"
    applications = []
    if applications_path.exists():
        with open(applications_path) as f:
            applications = json.load(f)

    all_jobs = []
    for company in companies:
        print(f"  fetching: {company['name']} ({company['ats']}/{company['slug']})...")
        jobs = fetch_jobs_for_company(company)
        print(f"    found {len(jobs)} total jobs")
        all_jobs.extend(jobs)
        time.sleep(delay)

    # Filter by preferences
    matched = filter_jobs(all_jobs, preferences)
    print(f"\n  {len(matched)} jobs match preferences (from {len(all_jobs)} total)")

    # Deduplicate
    new_jobs = deduplicate_jobs(matched, applications)
    print(f"  {len(new_jobs)} new jobs (after dedup)")

    # Save to jobs.json
    jobs_path = profile_dir / "jobs.json"
    with open(jobs_path, "w") as f:
        json.dump(new_jobs, f, indent=2)
        f.write("\n")

    return new_jobs


def fetch_lever_jobs(slug: str) -> list[dict]:
    """Fetch all jobs from a Lever board. Returns normalized job dicts."""
    url = LEVER_JOBS_API.format(slug=slug)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return []

    if not isinstance(data, list):
        return []

    jobs = []
    for raw in data:
        categories = raw.get("categories", {})
        jobs.append({
            "id": raw["id"],
            "title": raw.get("text", ""),
            "company": slug.title(),
            "location": categories.get("location", ""),
            "departments": [categories["department"]] if categories.get("department") else [],
            "posting_url": raw.get("hostedUrl", ""),
            "ats": "lever",
            "slug": slug,
            "content": raw.get("descriptionPlain", ""),
        })
    return jobs
