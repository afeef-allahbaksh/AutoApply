import json
import re
import time
from datetime import date
from pathlib import Path

import requests

from src.schemas import validate_companies

SEED_PATH = Path(__file__).resolve().parent.parent / "config" / "seed_companies.json"
PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
LEVER_API = "https://api.lever.co/v0/postings/{slug}?limit=1"

HEADERS = {"User-Agent": "AutoApply/1.0"}
TIMEOUT = 10


def validate_greenhouse_slug(slug: str) -> dict | None:
    """Hit Greenhouse API for a slug. Returns company dict or None if invalid."""
    url = GREENHOUSE_API.format(slug=slug)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None

    jobs = data.get("jobs", [])
    if not jobs:
        return None

    company_name = jobs[0].get("company_name", slug.title())
    return {
        "name": company_name,
        "ats": "greenhouse",
        "slug": slug,
        "careers_url": f"https://boards.greenhouse.io/{slug}",
        "added": date.today().isoformat(),
    }


def validate_lever_slug(slug: str) -> dict | None:
    """Hit Lever API for a slug. Returns company dict or None if invalid."""
    url = LEVER_API.format(slug=slug)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None

    if not isinstance(data, list) or len(data) == 0:
        return None

    return {
        "name": slug.title(),
        "ats": "lever",
        "slug": slug,
        "careers_url": f"https://jobs.lever.co/{slug}",
        "added": date.today().isoformat(),
    }


def validate_slug(slug: str, ats: str) -> dict | None:
    """Validate a slug against the appropriate ATS API."""
    if ats == "greenhouse":
        return validate_greenhouse_slug(slug)
    elif ats == "lever":
        return validate_lever_slug(slug)
    return None


ATS_PATTERNS = [
    (re.compile(r"boards\.greenhouse\.io/([a-zA-Z0-9_-]+)"), "greenhouse"),
    (re.compile(r"job-boards\.greenhouse\.io/([a-zA-Z0-9_-]+)"), "greenhouse"),
    (re.compile(r"jobs\.lever\.co/([a-zA-Z0-9_-]+)"), "lever"),
    (re.compile(r"jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)"), "ashby"),
]


def detect_ats_from_url(url: str) -> tuple[str, str] | None:
    """Detect ATS type and company slug from a careers URL.

    Returns (ats, slug) tuple or None if no match.
    """
    for pattern, ats in ATS_PATTERNS:
        match = pattern.search(url)
        if match:
            slug = match.group(1)
            # Ignore path segments that aren't slugs
            if slug.lower() in ("jobs", "embed", "api"):
                continue
            return (ats, slug)
    return None


def _load_companies(profile_name: str) -> list:
    """Load existing companies.json for a profile, or return empty list."""
    path = PROFILES_DIR / profile_name / "companies.json"
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def _save_companies(profile_name: str, companies: list) -> None:
    """Write companies list to the profile's companies.json after validation."""
    validate_companies(companies)
    path = PROFILES_DIR / profile_name / "companies.json"
    with open(path, "w") as f:
        json.dump(companies, f, indent=2)
        f.write("\n")


def _existing_slugs(companies: list) -> set:
    """Return set of (ats, slug) tuples from existing companies."""
    return {(c["ats"], c["slug"]) for c in companies}


def discover_companies(profile_name: str, delay: float = 1.0) -> dict:
    """Run company discovery from seed file. Merges with existing companies.json.

    Returns a summary dict with counts of added, skipped, failed slugs.
    """
    with open(SEED_PATH) as f:
        seeds = json.load(f)

    existing = _load_companies(profile_name)
    known = _existing_slugs(existing)

    added = 0
    skipped = 0
    failed = 0

    for entry in seeds:
        slug = entry["slug"]
        ats = entry["ats"]

        if (ats, slug) in known:
            skipped += 1
            print(f"  skip: {slug} ({ats}) — already in companies.json")
            continue

        result = validate_slug(slug, ats)
        if result:
            existing.append(result)
            known.add((ats, slug))
            added += 1
            print(f"  added: {result['name']} ({ats}/{slug})")
        else:
            failed += 1
            print(f"  failed: {slug} ({ats}) — not found or no jobs")

        time.sleep(delay)

    _save_companies(profile_name, existing)

    return {"added": added, "skipped": skipped, "failed": failed, "total": len(existing)}
