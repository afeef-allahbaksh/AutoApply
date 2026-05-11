"""Pre-LLM filtering: keyword scoring, location matching, dedup.

These run before any Claude calls — cheap, deterministic, do most of the work."""
from src.profile_loader import normalize_posting_url


NO_PREFERENCE = {"any", "no preference", "anywhere", "all"}


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
    Returns True for all jobs if locations contains 'Any' or 'No preference'.
    """
    if any(loc.lower() in NO_PREFERENCE for loc in preferred_locations):
        return True
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

        if not _matches_any(title, roles):
            continue

        job_loc = job["location"]
        if not _matches_location(job_loc, locations):
            continue

        job["relevance_score"] = score_job(job, preferences)
        matched.append(job)

    matched.sort(key=lambda j: j["relevance_score"], reverse=True)
    return matched


def deduplicate_jobs(jobs: list[dict], applications: list[dict]) -> list[dict]:
    """Remove jobs the user has already applied to.

    Uses composite key (company, title, normalized_posting_url) so URLs with
    tracking params don't bypass dedup.
    """
    applied = {
        (a["company"], a["role"], normalize_posting_url(a["posting_url"]))
        for a in applications
    }
    return [
        j for j in jobs
        if (j["company"], j["title"], normalize_posting_url(j["posting_url"])) not in applied
    ]
