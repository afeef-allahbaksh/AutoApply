"""Lever public board API client.

Endpoint: `api.lever.co/v0/postings/{slug}`. No auth. Lever's content lives in
`descriptionPlain` plus structured `lists` (e.g. "Requirements", "What you'll
do") — we concatenate them for richer context downstream.
"""
import requests

from ..snippet import _HTML_TAG_RE
from ._http import HEADERS, TIMEOUT

LEVER_JOBS_API = "https://api.lever.co/v0/postings/{slug}"


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
