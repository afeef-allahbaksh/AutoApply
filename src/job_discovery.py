import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from src.api import create_message
from src.profile_loader import PROFILES_DIR, normalize_posting_url

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_YEARS_RE = re.compile(r"\d+\+?\s*(?:years?|yrs?)\b", re.IGNORECASE)
_REQUIREMENTS_HEADERS = re.compile(
    r"(?:qualifications?|requirements?|who you are|what you.?ll need|what we.?re looking for|minimum qualifications?|must have|years of.{0,20}experience)",
    re.IGNORECASE,
)


def _extract_job_snippet(content: str, max_len: int = 500) -> str:
    """Extract the most relevant snippet from a job description.

    Prioritizes requirements/qualifications sections and years-of-experience
    mentions over intro marketing copy.
    """
    import html
    clean = html.unescape(content)
    clean = _HTML_TAG_RE.sub(" ", clean)
    # Collapse whitespace
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        return ""

    # Try to find the requirements section
    match = _REQUIREMENTS_HEADERS.search(clean)
    if match:
        # Start from the requirements header
        section = clean[match.start():match.start() + max_len]
        return section + "..." if len(clean) > match.start() + max_len else section

    # Fallback: find years-of-experience mentions and grab surrounding context
    years_match = _YEARS_RE.search(clean)
    if years_match:
        start = max(0, years_match.start() - 100)
        section = clean[start:start + max_len]
        return section + "..." if len(clean) > start + max_len else section

    # Last resort: first max_len chars
    return clean[:max_len] + ("..." if len(clean) > max_len else "")

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


NO_PREFERENCE = {"any", "no preference", "anywhere", "all"}


def _matches_location(job_location: str, preferred_locations: list[str]) -> bool:
    """Check if the job location matches any preferred location.

    Handles cases like 'Remote', 'New York, NY', 'San Francisco, CA; New York, NY'.
    Returns True for all jobs if locations contains 'Any' or 'No preference'.
    """
    # No preference = match everything
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


def classify_jobs_by_level(jobs: list[dict], experience_levels: list[str]) -> list[dict]:
    """Use a single LLM call to filter out jobs that don't match the user's experience level.

    Sends all titles in one batch — costs ~$0.01 regardless of count.
    Returns only the jobs that match the target experience level.
    """
    if not jobs or not experience_levels:
        return jobs

    # Build entries with title + requirements snippet for context
    entries = []
    for i, job in enumerate(jobs):
        snippet = _extract_job_snippet(job.get("content", ""))
        entries.append(f"{i}: {job['title']} | {snippet}" if snippet else f"{i}: {job['title']}")

    prompt = f"""You are a job level classifier. Given a list of job postings (title + description snippet) and the applicant's target experience level, return ONLY the indices of jobs that match.

Target experience level: {', '.join(experience_levels)}

Jobs (index: title | description snippet):
{chr(10).join(entries)}

Rules:
- Match the job's seniority to the target experience level above
- Use title signals (Junior, Senior, Staff, Lead, I/II/III/IV, Intermediate, Principal, etc.) to determine the job's level
- For AMBIGUOUS titles with no level indicator (e.g. plain "Software Engineer"):
  - INCLUDE them — the fit scorer will handle fine-grained filtering separately
  - Only EXCLUDE if the description makes it VERY clear the level is wrong (e.g. "10+ years" for an entry-level applicant, or "new grad only" for a senior applicant)
- This classifier removes OBVIOUSLY wrong-level jobs — it is NOT a strict filter
- When in doubt, ALWAYS include the job
- Return ONLY a JSON array of matching index numbers, nothing else"""

    message = create_message(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    try:
        matching_indices = set(json.loads(raw))
    except (json.JSONDecodeError, TypeError):
        # If parsing fails, return all jobs rather than losing everything
        return jobs

    return [job for i, job in enumerate(jobs) if i in matching_indices]


def classify_jobs_by_country(jobs: list[dict], user_location: str) -> list[dict]:
    """Use a single LLM call to keep only jobs the user can apply to from their country.

    Sends all job locations in one batch — costs ~$0.005 regardless of count.
    The LLM infers the user's country from their profile location, then keeps jobs
    that are in the same country or globally remote. Drops jobs explicitly bound
    to a different country.
    """
    if not jobs or not user_location:
        return jobs

    entries = [f"{i}: {job.get('location', '')}" for i, job in enumerate(jobs)]

    prompt = f"""You are a job location filter. The applicant lives in: {user_location}

Decide which jobs the applicant can realistically apply to based on country eligibility.

Jobs (index: location string):
{chr(10).join(entries)}

Rules:
- INCLUDE jobs in the applicant's country
- INCLUDE jobs that are globally remote with no country restriction (e.g. "Remote", "Worldwide")
- INCLUDE jobs listing multiple countries if the applicant's country is one of them
- EXCLUDE jobs bound to a different country only (e.g. applicant in US, job is "Remote - Estonia" or "London, UK")
- When the location is ambiguous, INCLUDE the job
- Return ONLY a JSON array of matching index numbers, nothing else"""

    message = create_message(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    try:
        matching_indices = set(json.loads(raw))
    except (json.JSONDecodeError, TypeError):
        return jobs

    return [job for i, job in enumerate(jobs) if i in matching_indices]


def score_jobs_fit(jobs: list[dict], resume_data: dict, preferences: dict | None = None) -> list[dict]:
    """Use a single LLM call to score how well each job fits the candidate's resume.

    Attaches fit_score (1-5) and fit_rationale to each job dict.
    Returns the jobs list with scores added.
    """
    if not jobs:
        return jobs

    # Build candidate summary from resume
    contact = resume_data.get("contact", {})
    skills_list = []
    for group in resume_data.get("skills", []):
        skills_list.extend(group.get("items", []))

    experience_summary = []
    for exp in resume_data.get("experience", []):
        title = exp["title"]
        company = exp["company"]
        start = exp.get("start_date", "")
        end = exp.get("end_date", "Present")
        experience_summary.append(f"{title} at {company} ({start} - {end})")

    education_summary = []
    for edu in resume_data.get("education", []):
        degree = f"{edu['degree']} {edu.get('field', '')}".strip()
        institution = edu["institution"]
        grad = edu.get("end_date", "")
        start = edu.get("start_date", "")
        education_summary.append(f"{degree} — {institution} ({start} - {grad})")

    experience_levels = preferences.get("experience_levels", []) if preferences else []
    level_str = f"Target experience level: {', '.join(experience_levels)}\n" if experience_levels else ""

    candidate = (
        f"Name: {contact.get('name', 'Unknown')}\n"
        f"{level_str}"
        f"Skills: {', '.join(skills_list[:30])}\n"
        f"Experience: {'; '.join(experience_summary)}\n"
        f"Education: {'; '.join(education_summary)}"
    )

    # Build job entries with requirements snippets
    entries = []
    for i, job in enumerate(jobs):
        snippet = _extract_job_snippet(job.get("content", ""))
        entries.append(f"{i}: {job['title']} at {job['company']} | {snippet}")

    prompt = f"""You are a job fit evaluator. Given a candidate's profile and a list of job postings, score how well the candidate fits each job.

Candidate profile:
{candidate}

IMPORTANT context for scoring:
- Look at the candidate's education dates to determine if they are a current student, recent grad, or experienced professional.
- If the candidate is still in school or recently graduated, their experience entries are likely co-ops/internships — do NOT count them as full-time years of professional experience.
- If a job's required experience level exceeds what the candidate actually has (accounting for co-ops vs full-time), penalize the score.
- If the candidate's target experience level is provided, roles that don't match that level should score lower.

Jobs (index: title at company | description snippet):
{chr(10).join(entries)}

Score each job on a 1-5 scale:
5 = Excellent fit — skills match AND experience level is appropriate for the candidate
4 = Good fit — skills match well, experience level is reasonable
3 = Moderate fit — some skill overlap but level may be a stretch or domain mismatch
2 = Weak fit — role is above/below candidate's level or limited skill overlap
1 = Poor fit — wrong level entirely or unrelated domain

Return ONLY a JSON array, no markdown fences:
[{{"index": 0, "score": 4, "rationale": "Strong match..."}}, ...]"""

    message = create_message(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    try:
        scores = json.loads(raw)
        score_map = {s["index"]: s for s in scores}
        for i, job in enumerate(jobs):
            if i in score_map:
                job["fit_score"] = score_map[i]["score"]
                job["fit_rationale"] = score_map[i].get("rationale", "")
            else:
                job["fit_score"] = 3.0
                job["fit_rationale"] = "Scoring unavailable"
    except (json.JSONDecodeError, TypeError, KeyError):
        for job in jobs:
            job["fit_score"] = 3.0
            job["fit_rationale"] = "Scoring unavailable"

    return jobs


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


def fetch_jobs_for_company(company: dict) -> list[dict]:
    """Fetch jobs for a single company entry from companies.json."""
    ats = company["ats"]
    slug = company["slug"]
    if ats == "greenhouse":
        return fetch_greenhouse_jobs(slug)
    elif ats == "lever":
        return fetch_lever_jobs(slug)
    return []


def discover_jobs(profile_name: str) -> list[dict]:
    """Full job discovery pipeline for a profile.

    1. Load companies.json
    2. Fetch jobs from each company (in parallel)
    3. Filter by preferences
    4. Deduplicate against applications.json
    5. Save to jobs.json
    6. Return matched jobs
    """
    profile_dir = PROFILES_DIR / profile_name

    # Load companies
    companies_path = profile_dir / "companies.json"
    companies = []
    if companies_path.exists() and companies_path.stat().st_size > 0:
        try:
            with open(companies_path) as f:
                companies = json.load(f)
        except json.JSONDecodeError:
            companies = []
    if not companies:
        print("No companies found. Run 'discover' first.")
        return []

    # Load profile preferences
    with open(profile_dir / "profile.json") as f:
        profile = json.load(f)
    preferences = profile["job_preferences"]

    # Load applications for dedup
    applications_path = profile_dir / "applications.json"
    applications = []
    if applications_path.exists() and applications_path.stat().st_size > 0:
        try:
            with open(applications_path) as f:
                applications = json.load(f)
        except json.JSONDecodeError:
            applications = []

    all_jobs = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            pool.submit(fetch_jobs_for_company, company): company
            for company in companies
        }
        for future in as_completed(futures):
            company = futures[future]
            jobs = future.result()
            print(f"  fetched: {company['name']} ({company['ats']}/{company['slug']}) — {len(jobs)} jobs")
            all_jobs.extend(jobs)

    # Filter by keyword + location
    matched = filter_jobs(all_jobs, preferences)
    print(f"\n  {len(matched)} jobs match keywords/location (from {len(all_jobs)} total)")

    # Filter out jobs in other countries using LLM
    user_location = profile.get("location", "")
    if user_location and matched:
        print(f"  Classifying {len(matched)} jobs by country (~$0.005)...")
        before = len(matched)
        matched = classify_jobs_by_country(matched, user_location)
        print(f"  {len(matched)} jobs in your country (dropped {before - len(matched)})")

    # Filter by experience level using LLM
    experience_levels = preferences.get("experience_levels", [])
    if experience_levels and matched:
        print(f"  Classifying {len(matched)} jobs by experience level (~$0.01)...")
        matched = classify_jobs_by_level(matched, experience_levels)
        print(f"  {len(matched)} jobs match experience level")

    # Deduplicate
    new_jobs = deduplicate_jobs(matched, applications)
    print(f"  {len(new_jobs)} new jobs (after dedup)")

    # Fit scoring
    resume_path = profile_dir / "resume.json"
    if new_jobs and resume_path.exists():
        with open(resume_path) as f:
            resume_data = json.load(f)
        print(f"  Scoring {len(new_jobs)} jobs for fit (~$0.01)...")
        new_jobs = score_jobs_fit(new_jobs, resume_data, preferences)
        excellent_good = sum(1 for j in new_jobs if j.get("fit_score", 3) >= 4)
        moderate = sum(1 for j in new_jobs if j.get("fit_score", 3) == 3)
        weak_poor = sum(1 for j in new_jobs if j.get("fit_score", 3) < 3)
        print(f"  Fit scores: {excellent_good} excellent/good, {moderate} moderate, {weak_poor} weak/poor")
        new_jobs.sort(key=lambda j: (j.get("fit_score", 3), j.get("relevance_score", 0)), reverse=True)

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

        # Build richer content from multiple Lever fields
        content_parts = [raw.get("descriptionPlain", "")]
        for lst in raw.get("lists", []):
            if lst.get("text"):
                content_parts.append(lst["text"])
            if lst.get("content"):
                # Strip HTML from list content
                content_parts.append(_HTML_TAG_RE.sub(" ", lst["content"]))
        commitment = categories.get("commitment", "")
        if commitment:
            content_parts.append(f"Commitment: {commitment}")

        jobs.append({
            "id": raw["id"],
            "title": raw.get("text", ""),
            "company": slug.title(),
            "location": categories.get("location", ""),
            "departments": [categories["department"]] if categories.get("department") else [],
            "posting_url": raw.get("hostedUrl", ""),
            "ats": "lever",
            "slug": slug,
            "content": "\n\n".join(part for part in content_parts if part),
        })
    return jobs
