import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import requests

from src.jobs.clients.workday import parse_workday_url, probe_workday
from src.profile_loader import CONFIG_DIR, PROFILES_DIR, _atomic_write_json
from src.schemas import validate_companies

SEED_PATH = CONFIG_DIR / "seed_companies.json"

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
LEVER_API = "https://api.lever.co/v0/postings/{slug}?limit=1"
ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

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


def validate_ashby_slug(slug: str) -> dict | None:
    """Hit Ashby API for a slug. Returns company dict or None if invalid.

    Ashby's public board endpoint returns 200 with an empty `jobs` array for
    valid orgs that happen to have no openings, so we accept any 200 with the
    expected JSON shape (a `jobs` key) as proof of a valid slug.
    """
    url = ASHBY_API.format(slug=slug)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None

    if not isinstance(data, dict) or "jobs" not in data:
        return None

    return {
        "name": slug.title(),
        "ats": "ashby",
        "slug": slug,
        "careers_url": f"https://jobs.ashbyhq.com/{slug}",
        "added": date.today().isoformat(),
    }


def validate_workday_url(url: str) -> dict | None:
    """Validate a Workday careers URL. Returns company dict (with shard/site/
    locale fields) or None if the URL doesn't match a known Workday pattern
    or the cxs endpoint isn't reachable.

    Unlike the slug-based ATSes, Workday needs the data-center shard and the
    named career site in addition to the tenant — the regex parser pulls all
    three out of the URL up front.
    """
    parsed = parse_workday_url(url)
    if not parsed:
        return None
    if probe_workday(parsed["tenant"], parsed["shard"], parsed["site"]) is None:
        return None
    return {
        "name": parsed["tenant"].title(),
        "ats": "workday",
        "slug": parsed["tenant"],
        "careers_url": url.strip().rstrip("/"),
        "added": date.today().isoformat(),
        "shard": parsed["shard"],
        "site": parsed["site"],
        "locale": parsed["locale"],
    }


def validate_slug(slug: str, ats: str) -> dict | None:
    """Validate a slug against the appropriate ATS API.

    For Workday, the `slug` parameter is actually a careers URL (see
    `validate_workday_url`). The cascade in the companies-add route uses
    `looks_like_workday_url` to route URL-shaped input through here.
    """
    if ats == "greenhouse":
        return validate_greenhouse_slug(slug)
    elif ats == "lever":
        return validate_lever_slug(slug)
    elif ats == "ashby":
        return validate_ashby_slug(slug)
    elif ats == "workday":
        return validate_workday_url(slug)
    return None


def _validate_seed_entry(entry: dict) -> dict | None:
    """Validate one seed-file entry. Handles workday's URL-not-slug shape."""
    if entry["ats"] == "workday":
        return validate_workday_url(entry["url"])
    return validate_slug(entry["slug"], entry["ats"])



def _load_companies(profile_name: str) -> list:
    """Load existing companies.json for a profile, or return empty list."""
    path = PROFILES_DIR / profile_name / "companies.json"
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def _save_companies(profile_name: str, companies: list) -> None:
    """Write companies list to the profile's companies.json after validation.

    Uses the `_atomic_write_json` primitive so the writer works in contexts
    where profile.json may not exist yet (initial company discovery during
    setup, test fixtures).
    """
    _atomic_write_json(
        PROFILES_DIR / profile_name / "companies.json",
        companies,
        validate_companies,
    )


def _existing_slugs(companies: list) -> set:
    """Return set of (ats, slug) tuples from existing companies."""
    return {(c["ats"], c["slug"]) for c in companies}


def discover_companies(
    profile_name: str,
    max_workers: int = 5,
    on_progress=None,
    cancel_check=None,
) -> dict:
    """Run company discovery from seed file. Merges with existing companies.json.

    Validates slugs in parallel for faster discovery.

    `on_progress` (optional): called as `on_progress(added=, skipped=, failed=,
    processed=, total=)` whenever counts change. CLI callers pass nothing and
    rely on the existing print() output; UI callers pass a writer that updates
    a status file.

    `cancel_check` (optional): called between completed futures. If it returns
    True, the executor is shut down and in-progress validations are allowed to
    finish but no new ones are started. Partial results are still saved.

    Returns a summary dict with counts of added, skipped, failed, processed,
    and the final total count in companies.json.
    """
    with open(SEED_PATH) as f:
        seeds = json.load(f)

    existing = _load_companies(profile_name)
    known = _existing_slugs(existing)

    added = 0
    skipped = 0
    failed = 0
    processed = 0
    total = len(seeds)

    def _emit():
        if on_progress is not None:
            on_progress(
                added=added, skipped=skipped, failed=failed,
                processed=processed, total=total,
            )

    # Filter out already-known slugs
    to_validate = []
    for entry in seeds:
        if (entry["ats"], entry["slug"]) in known:
            skipped += 1
            processed += 1
            print(f"  skip: {entry['slug']} ({entry['ats']}) — already in companies.json")
        else:
            to_validate.append(entry)
    _emit()

    # Validate remaining slugs in parallel
    cancelled = False
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_validate_seed_entry, entry): entry
            for entry in to_validate
        }
        for future in as_completed(futures):
            if cancel_check is not None and cancel_check():
                cancelled = True
                # cancel_futures requires Python 3.9+. The currently-running
                # validations still finish (their HTTP call is in flight), but
                # no new ones start.
                pool.shutdown(wait=False, cancel_futures=True)
                break
            entry = futures[future]
            slug, ats = entry["slug"], entry["ats"]
            result = future.result()
            if result:
                existing.append(result)
                known.add((ats, slug))
                added += 1
                print(f"  added: {result['name']} ({ats}/{slug})")
            else:
                failed += 1
                print(f"  failed: {slug} ({ats}) — not found or no jobs")
            processed += 1
            _emit()

    # Save whatever we got, even on cancel — partial discovery still useful.
    _save_companies(profile_name, existing)

    return {
        "added": added,
        "skipped": skipped,
        "failed": failed,
        "processed": processed,
        "total": len(existing),
        "cancelled": cancelled,
    }
