"""Workday public board client.

Workday is the third-largest ATS by volume after Greenhouse + Lever, and the
dominant choice for banks, asset managers, government agencies, and large
enterprise. Unlike GH/Lever/Ashby, there is no single global slug — every
tenant lives at `{tenant}.wd{N}.myworkdayjobs.com`, where `wd{N}` is the
data-center shard the customer was provisioned on (varies per company, not
guessable). A single tenant can host multiple named career sites (external,
campus, internal).

Discovery endpoint (undocumented but stable, no auth):
  POST https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
  Body: {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}

Returns `{jobPostings: [...], total}`. Pagination is via offset.

Job descriptions are NOT included in the listings response — fetching them
would require a second HTTP call per posting (the `/job/{externalPath}`
detail endpoint). For Phase 43 we set `content=""` and let fit scoring fall
back to title-only ranking. A future phase can add lazy detail fetch when
the user actually opens a job.
"""
import re

import requests

from ._http import HEADERS, TIMEOUT

# Primary form: nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite
# Locale segment is optional — Workday treats both /en-US/Site and /Site as valid.
_WORKDAY_URL_RE = re.compile(
    r"^https?://"
    r"(?P<tenant>[a-zA-Z0-9_-]+)\."
    r"(?P<shard>wd\d+)\."
    r"myworkdayjobs\.com"
    r"(?:/(?P<locale>[a-z]{2}-[A-Z]{2}))?"
    r"/(?P<site>[A-Za-z0-9_-]+)"
    r"/?$"
)

# Secondary form (uncommon): jobs.myworkdaysite.com/recruiting/{tenant}/{site}
_WORKDAY_ALT_URL_RE = re.compile(
    r"^https?://jobs\.myworkdaysite\.com/recruiting/"
    r"(?P<tenant>[a-zA-Z0-9_-]+)/"
    r"(?P<site>[A-Za-z0-9_-]+)"
    r"/?$"
)

DEFAULT_LOCALE = "en-US"


def parse_workday_url(url: str) -> dict | None:
    """Extract {tenant, shard, site, locale} from a Workday careers URL.

    Returns None if the URL doesn't match a known Workday pattern. The
    secondary `jobs.myworkdaysite.com` form has no shard — we use "wd1"
    as a sentinel since the cxs endpoint isn't reachable for that variant
    today (alt form is rare and read-only).
    """
    url = url.strip()
    m = _WORKDAY_URL_RE.match(url)
    if m:
        return {
            "tenant": m.group("tenant"),
            "shard": m.group("shard"),
            "site": m.group("site"),
            "locale": m.group("locale") or DEFAULT_LOCALE,
        }
    m = _WORKDAY_ALT_URL_RE.match(url)
    if m:
        return {
            "tenant": m.group("tenant"),
            "shard": "wd1",
            "site": m.group("site"),
            "locale": DEFAULT_LOCALE,
        }
    return None


def looks_like_workday_url(value: str) -> bool:
    """Cheap check used by the auto-detect cascade to route URL-shaped input."""
    return parse_workday_url(value) is not None


def _cxs_url(tenant: str, shard: str, site: str) -> str:
    return f"https://{tenant}.{shard}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"


def _careers_url(tenant: str, shard: str, site: str, locale: str) -> str:
    return f"https://{tenant}.{shard}.myworkdayjobs.com/{locale}/{site}"


def probe_workday(tenant: str, shard: str, site: str) -> dict | None:
    """One-shot probe with limit=1 to confirm the tenant/shard/site combo resolves.

    Returns a dict with `total` job count if valid; None if invalid (4xx, 5xx,
    network error, unexpected JSON shape).
    """
    url = _cxs_url(tenant, shard, site)
    body = {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}
    try:
        resp = requests.post(url, json=body, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None
    if not isinstance(data, dict) or "jobPostings" not in data:
        return None
    return {"total": data.get("total", 0)}


def fetch_workday_jobs(company: dict) -> list[dict]:
    """Fetch every listed job for a Workday tenant.

    Takes a company record (not just a slug) because Workday needs `shard`
    and `site` in addition to the tenant. Returns the same job-dict shape as
    the other clients so downstream filter / classify / fit-score code is
    identical.

    Paginates via offset until `total` is reached or the API returns fewer
    rows than the page limit.
    """
    tenant = company.get("slug") or company.get("tenant")
    shard = company.get("shard")
    site = company.get("site")
    locale = company.get("locale", DEFAULT_LOCALE)
    if not (tenant and shard and site):
        return []

    cxs = _cxs_url(tenant, shard, site)
    careers_base = _careers_url(tenant, shard, site, locale)

    jobs: list[dict] = []
    offset = 0
    page_size = 20  # Workday's cxs endpoint rejects limit > 20 with HTTP 400
    safety_cap = 250  # 250 * 20 = 5000 max, well above the largest known tenant
    universe_total = 0  # Workday only returns a real `total` on the first call;
                        # subsequent pages return total=0. Latch the first one.
    company_name = company.get("name") or tenant.title()

    for _page in range(safety_cap):
        body = {
            "appliedFacets": {},
            "limit": page_size,
            "offset": offset,
            "searchText": "",
        }
        try:
            resp = requests.post(cxs, json=body, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError):
            break

        postings = data.get("jobPostings", [])
        if not postings:
            break

        response_total = data.get("total") or 0
        if response_total and not universe_total:
            universe_total = response_total

        for raw in postings:
            external_path = raw.get("externalPath", "")
            posting_url = f"{careers_base}{external_path}" if external_path else ""
            # `bulletFields` is Workday's catch-all (typically holds the
            # requisition ID); use the first if present as a stable job id,
            # otherwise fall back to the externalPath.
            bullets = raw.get("bulletFields", []) or []
            job_id = bullets[0] if bullets else external_path

            jobs.append({
                "id": str(job_id),
                "title": raw.get("title", ""),
                "company": company_name,
                "location": raw.get("locationsText", ""),
                "departments": [],  # Workday categorization isn't on the listing
                "posting_url": posting_url,
                "ats": "workday",
                "slug": tenant,
                # See module docstring: descriptions require a second HTTP
                # call per posting. Skipping in v1; title-only fit scoring.
                "content": "",
            })

        offset += len(postings)
        # Two terminators: hit the universe size we latched on page 1, OR the
        # API returned fewer than a full page (natural end). Belt-and-suspenders
        # because Workday's pagination signals are inconsistent.
        if universe_total and offset >= universe_total:
            break
        if len(postings) < page_size:
            break

    return jobs
