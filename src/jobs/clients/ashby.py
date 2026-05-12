"""Ashby public board API client.

Endpoint: `api.ashbyhq.com/posting-api/job-board/{slug}`. No auth. Returns
the full posting list including descriptions and locations. Setting
`includeCompensation=true` adds salary range info when the org has it
configured (cheap to request even if absent).
"""
import requests

from ._http import HEADERS, TIMEOUT

ASHBY_JOBS_API = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"


def fetch_ashby_jobs(slug: str) -> list[dict]:
    """Fetch all listed jobs from an Ashby board. Returns normalized job dicts.

    Same shape as `fetch_greenhouse_jobs` / `fetch_lever_jobs` so downstream
    filter / classify / fit-score code doesn't have to special-case.
    """
    url = ASHBY_JOBS_API.format(slug=slug)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return []

    jobs = []
    for raw in data.get("jobs", []):
        # Ashby has both isListed (visible on the board) and a hidden flag;
        # only emit listed ones.
        if raw.get("isListed") is False:
            continue

        location = raw.get("locationName", "") or ""
        if raw.get("isRemote"):
            location = f"{location} · Remote".strip(" ·") if location else "Remote"

        # Build a departments list from the org's own categorization fields.
        # Ashby exposes departmentName + teamName separately; we merge.
        department = raw.get("departmentName", "")
        team = raw.get("teamName", "")
        departments = [d for d in (department, team) if d]

        # Prefer descriptionPlain (already stripped) over the HTML description.
        content = raw.get("descriptionPlain") or raw.get("description", "") or ""

        jobs.append({
            "id": str(raw.get("id", "")),
            "title": raw.get("title", ""),
            "company": slug.title(),
            "location": location,
            "departments": departments,
            "posting_url": raw.get("jobUrl") or raw.get("applyUrl") or "",
            "ats": "ashby",
            "slug": slug,
            "content": content,
        })
    return jobs
