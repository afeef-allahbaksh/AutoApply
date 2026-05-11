"""Greenhouse public board API client.

Endpoint: `boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true`. No auth.
"""
import requests

from ._http import HEADERS, TIMEOUT

GREENHOUSE_JOBS_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


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
