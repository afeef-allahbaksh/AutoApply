"""LLM-driven job filtering: country, experience level, and fit scoring.

Each function makes a single batched Claude call regardless of job count, so
cost scales with N (input tokens) rather than per-job. All three return jobs
in the same shape they came in (filtered + decorated).
"""
import json

from src.api import create_message, strip_code_fences

from .snippet import extract_job_snippet


def classify_jobs_by_level(jobs: list[dict], experience_levels: list[str]) -> list[dict]:
    """Use a single LLM call to filter out jobs that don't match the user's experience level.

    Sends all titles in one batch — costs ~$0.01 regardless of count.
    Returns only the jobs that match the target experience level.
    """
    if not jobs or not experience_levels:
        return jobs

    entries = []
    for i, job in enumerate(jobs):
        snippet = extract_job_snippet(job.get("content", ""))
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

    raw = strip_code_fences(message.content[0].text)

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

    raw = strip_code_fences(message.content[0].text)

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

    entries = []
    for i, job in enumerate(jobs):
        snippet = extract_job_snippet(job.get("content", ""))
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

    raw = strip_code_fences(message.content[0].text)

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
