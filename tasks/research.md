# ATS Public API Research: Greenhouse & Lever

Research conducted 2026-03-31 with live API testing. Frozen-in-time snapshot.

> **Scope of this document.** This was the pre-implementation pass for the
> Greenhouse and Lever public APIs that drive `src/discovery.py` and
> `src/job_discovery.py`. Subsequent integration work (IMAP inbox sync,
> Claude/regex email classification, kanban UI, streaming background worker)
> didn't need standalone research docs — the canonical references live in
> the corresponding source files:
>
> - **IMAP** — `src/inbox/auth.py` (app-password credential storage + login
>   verification), `src/inbox/fetch.py` (bulk FETCH with two-phase
>   header/body pull). RFC 3501 is the source of truth for IMAP semantics.
> - **Claude email classification prompt** — `src/inbox/classify.py:PROMPT`
>   documents the prompt design and JSON schema inline.
> - **Free-mode regex classifier** — `src/inbox/keyword_classify.py` has all
>   patterns in priority order at the top of the file.
> - **Application matching algorithm** — `src/inbox/matcher.py`: thread-id
>   → classifier company → sender domain stem → subject prefix → body scan
>   against existing applications.
> - **Sync orchestrator + rate limiting** — `src/inbox/sync.py` for the
>   pipeline, `src/api.py:reserve_input_tokens` for the sliding-window
>   token bucket that keeps the classifier under Anthropic Tier 1 TPM.
>
> Future research that goes beyond reading external docs (e.g. an Ashby or
> Workday ATS pass) should be added as new top-level sections below.

---

## 1. Greenhouse Job Board API

### 1.1 Public Index of Companies

**There is no public index/master list of all companies using Greenhouse.** The endpoint `GET https://boards-api.greenhouse.io/v1/boards` returns 404. You must already know a company's board slug (e.g., `anthropic`, `stripe`) to query their jobs.

**Discovery strategies:**
- Curate slugs manually from known companies
- Scrape career pages for `greenhouse.io` references
- Community-maintained lists (GitHub repos, etc.)
- The slug is typically the company name lowercased (e.g., `anthropic`, `stripe`)
- Invalid slugs return HTTP 404

### 1.2 API Base URL & Endpoints

Base: `https://boards-api.greenhouse.io/v1/boards/{board_token}`

| Endpoint | Description |
|----------|-------------|
| `/jobs` | List all job posts |
| `/jobs/{job_id}` | Single job with full content |
| `/departments` | List departments (with nested jobs) |
| `/departments/{id}` | Single department |
| `/offices` | List offices/locations (with nested jobs) |
| `/offices/{id}` | Single office |
| `/sections` | Prospect post sections |
| `/ ` (root) | Board metadata |
| `/education/degrees` | Degree options |
| `/education/disciplines` | Discipline options |
| `/education/schools` | School options (searchable) |

### 1.3 Query Parameters

**`/jobs` endpoint:**
| Param | Type | Description |
|-------|------|-------------|
| `content` | boolean | When `true`, includes `content` (HTML description), `departments`, and `offices` arrays in each job |

**`/jobs/{id}` endpoint:**
| Param | Type | Description |
|-------|------|-------------|
| `questions` | boolean | Include application questions |
| `pay_transparency` | boolean | Include salary range info |

**`/departments` and `/offices`:**
| Param | Type | Description |
|-------|------|-------------|
| `render_as` | string | `"list"` (default) or `"tree"` for hierarchical view |

**`/education/*`:**
| Param | Type | Description |
|-------|------|-------------|
| `term` | string | Search filter |
| `page` | string | Pagination cursor (100 items/page) |

**Important: There are NO server-side filters for department, location, or keyword on the `/jobs` endpoint.** All jobs are returned in a single response. Filtering must be done client-side. Anthropic returns all 439 jobs in one response with no pagination needed for the jobs list.

### 1.4 Sample Response: `/jobs` (without `content=true`)

```json
{
  "jobs": [
    {
      "absolute_url": "https://job-boards.greenhouse.io/anthropic/jobs/5101832008",
      "data_compliance": [
        {
          "type": "gdpr",
          "requires_consent": false,
          "requires_processing_consent": false,
          "requires_retention_consent": false,
          "retention_period": null,
          "demographic_data_consent_applies": false
        }
      ],
      "internal_job_id": 4418838008,
      "location": {
        "name": "New York City, NY; San Francisco, CA"
      },
      "metadata": [
        {
          "id": 4036944008,
          "name": "Location Type",
          "value": null,
          "value_type": "single_select"
        }
      ],
      "id": 5101832008,
      "updated_at": "2026-03-31T09:45:47-04:00",
      "requisition_id": "260366",
      "title": "Account Executive, Academic Medical Centers",
      "company_name": "Anthropic",
      "first_published": "2026-01-30T08:57:16-05:00",
      "language": "en"
    }
  ],
  "meta": {
    "total": 439
  }
}
```

### 1.5 Additional Fields with `content=true`

When `?content=true` is added, each job gains these extra fields:

```json
{
  "content": "<p>HTML job description...</p>",
  "departments": [
    {
      "id": 4002062008,
      "name": "Sales",
      "child_ids": [],
      "parent_id": null
    }
  ],
  "offices": [
    {
      "id": 4001218008,
      "name": "San Francisco, CA",
      "location": "San Francisco, California, United States",
      "child_ids": [],
      "parent_id": null
    }
  ]
}
```

### 1.6 Single Job Endpoint: `/jobs/{id}`

Returns the same fields as `content=true` listing but for one job. Fields:
`absolute_url`, `data_compliance`, `internal_job_id`, `location`, `metadata`, `id`, `updated_at`, `requisition_id`, `title`, `company_name`, `first_published`, `language`, `content`, `departments`, `offices`

### 1.7 Authentication & Rate Limits

- **GET requests: No authentication required.** Fully public.
- **POST requests (submitting applications): Require HTTP Basic Auth** with a Base64-encoded API key.
- **Rate limits: Not documented.** No rate limit headers observed in responses. Headers show CloudFront CDN caching (`cache-control: max-age=0, private, must-revalidate`). Empirically, rapid sequential requests succeed without throttling, but aggressive scraping will likely trigger CloudFront protections.
- Responses include `x-farm-id`, `x-request-id`, and `x-runtime` headers.

### 1.8 Pagination

- The `/jobs` endpoint returns ALL jobs in a single response (no server-side pagination for the jobs list). Tested: Anthropic returns 439 jobs in one call.
- The `per_page` and `page` params exist but the API still returns all jobs regardless.
- The `meta.total` field appears when pagination params are sent.
- Education endpoints use cursor-based pagination with 100 items per page.

---

## 2. Lever Postings API

### 2.1 Public Index of Companies

**There is no public index/master list of all companies using Lever.** You must know a company's slug. Invalid slugs return an empty array `[]` (HTTP 200), not 404.

**Discovery strategies:** Same as Greenhouse -- curate manually, scrape career pages for `lever.co` references, etc.

### 2.2 API Base URL & Endpoints

Base: `https://api.lever.co/v0/postings/{company_slug}`

| Endpoint | Description |
|----------|-------------|
| `/` | List all postings (returns JSON array) |
| `/{posting_id}` | Single posting |

The URL structure is simpler than Greenhouse. The slug is typically the company name lowercased (e.g., `spotify`).

### 2.3 Query Parameters

| Param | Type | Description | Tested |
|-------|------|-------------|--------|
| `limit` | integer | Max results to return | Works |
| `skip` | integer | Offset for pagination | Works |
| `department` | string | Filter by department (exact match from `categories.department`) | Works |
| `team` | string | Filter by team (exact match from `categories.team`) | Works (empty if no exact match) |
| `location` | string | Filter by location (exact match from `categories.location`) | Works |
| `commitment` | string | Filter by commitment type (e.g., `Permanent`, `Intern`) | Works |
| `mode` | string | Set to `json` for JSON (default behavior already returns JSON) | Works |

**Important: Filters require EXACT string matches** against the `categories` object values. For example, `?location=New York` won't match `"New York, NY"`. You need the exact string `?location=New%20York,%20NY`.

### 2.4 Sample Response

The API returns a **JSON array** (not wrapped in an object like Greenhouse):

```json
[
  {
    "id": "1ff4a4e3-897c-4eab-9ee2-aa7d1d07a9d6",
    "text": "Account Executive - Backstage",
    "country": "CA",
    "workplaceType": "hybrid",
    "categories": {
      "commitment": "Permanent",
      "department": "Operations and Business Support",
      "location": "Toronto",
      "team": "Platform",
      "allLocations": ["Toronto"]
    },
    "createdAt": 1773335421350,
    "description": "<div>HTML job description...</div>",
    "descriptionPlain": "Plain text job description...",
    "descriptionBody": "<div>HTML description body...</div>",
    "descriptionBodyPlain": "Plain text description body...",
    "additional": "<div>HTML additional info (compensation, EEO, etc.)...</div>",
    "additionalPlain": "Plain text additional info...",
    "lists": [
      {
        "text": "What You'll Do",
        "content": "<li>Responsibility 1</li><li>Responsibility 2</li>"
      },
      {
        "text": "Who You Are",
        "content": "<li>Requirement 1</li><li>Requirement 2</li>"
      },
      {
        "text": "Where You'll Be",
        "content": "<li>Location info</li>"
      }
    ],
    "opening": "",
    "openingPlain": "",
    "hostedUrl": "https://jobs.lever.co/spotify/1ff4a4e3-897c-4eab-9ee2-aa7d1d07a9d6",
    "applyUrl": "https://jobs.lever.co/spotify/1ff4a4e3-897c-4eab-9ee2-aa7d1d07a9d6/apply"
  }
]
```

### 2.5 Key Field Reference

| Field | Type | Description |
|-------|------|-------------|
| `id` | string (UUID) | Unique posting identifier |
| `text` | string | Job title |
| `country` | string | 2-letter country code |
| `workplaceType` | string | `"hybrid"`, `"remote"`, `"onsite"` etc. |
| `categories.commitment` | string | Employment type (Permanent, Intern, etc.) |
| `categories.department` | string | Department name |
| `categories.location` | string | Primary location |
| `categories.team` | string | Team name |
| `categories.allLocations` | string[] | All locations for this posting |
| `createdAt` | integer | Unix timestamp in milliseconds |
| `description` | string | HTML job description |
| `descriptionPlain` | string | Plain text description |
| `descriptionBody` | string | HTML description body (HTML entities encoded) |
| `descriptionBodyPlain` | string | Plain text description body |
| `additional` | string | HTML additional info (compensation, EEO, etc.) |
| `additionalPlain` | string | Plain text additional info |
| `lists` | array | Structured sections (responsibilities, requirements, etc.) |
| `lists[].text` | string | Section heading |
| `lists[].content` | string | HTML list items |
| `opening` | string | Opening statement HTML |
| `openingPlain` | string | Opening statement plain text |
| `hostedUrl` | string | Public job posting URL |
| `applyUrl` | string | Direct application URL |

### 2.6 Authentication & Rate Limits

- **No authentication required.** Fully public API.
- **Rate limits: Not documented.** No rate limit headers observed. Lever uses standard HTTP caching with ETag headers.
- Invalid/nonexistent slugs return `200 OK` with an empty array `[]`.

### 2.7 Pagination

- Uses `skip` and `limit` query parameters.
- Default behavior returns ALL postings (Spotify returns 166 in one call).
- No `meta` or `total` field -- you must check if the returned array length equals your `limit` to know if more pages exist.

---

## 3. Comparison & Implementation Notes

### 3.1 Key Differences

| Aspect | Greenhouse | Lever |
|--------|-----------|-------|
| Base URL | `boards-api.greenhouse.io/v1/boards/{slug}/jobs` | `api.lever.co/v0/postings/{slug}` |
| Response format | `{ "jobs": [...], "meta": {...} }` | `[...]` (bare array) |
| Job ID type | Integer | UUID string |
| Description included by default | No (need `?content=true`) | Yes (always included) |
| Server-side filtering | None | department, team, location, commitment |
| Pagination | Not needed (returns all) | `skip` + `limit` |
| Invalid slug response | 404 | 200 with `[]` |
| Department/location data | Separate endpoints or `?content=true` | Inline in `categories` object |
| Plain text version | Not provided | Both HTML and plain text fields |
| Structured sections | Not available (all in HTML content) | `lists[]` array with heading + content |
| Apply URL | `absolute_url` field | Separate `hostedUrl` and `applyUrl` |
| Workplace type | In `metadata` array (not standardized) | `workplaceType` field |

### 3.2 Company Discovery

Neither platform provides a public directory. Strategies to build a slug list:

1. **Manual curation** -- Start with known companies from tech lists (YC companies, Fortune 500, etc.)
2. **Career page scraping** -- Detect `greenhouse.io` or `lever.co` in career page URLs/iframes
3. **Community lists** -- GitHub repos like `pittcsc/Summer2025-Internships` contain many ATS URLs
4. **DNS/certificate transparency** -- Subdomains of `job-boards.greenhouse.io` or `jobs.lever.co`
5. **Common slug patterns** -- Company name lowercased, sometimes with hyphens (e.g., `data-dog` for Datadog)

### 3.3 Anti-Scraping Measures & ToS Concerns

**Greenhouse:**
- Uses CloudFront CDN -- aggressive scraping may trigger AWS WAF rules
- No robots.txt restrictions observed on the API subdomain
- The API is explicitly designed for public consumption (documented at `developers.greenhouse.io`)
- Terms of Service: The job board API is intended for displaying jobs; bulk harvesting may violate ToS
- Recommendation: Add delays between requests (1-2s), respect cache headers, use `If-None-Match` with ETags

**Lever:**
- Standard web server (no CDN-level protections observed)
- API is explicitly public and documented
- Less aggressive infrastructure than Greenhouse
- Recommendation: Same courtesy delays, rotate user agents if needed

**General guidance:**
- Both APIs are designed to be publicly consumed -- they exist specifically so third parties can display job listings
- Neither requires authentication for reading
- Rate limiting appears to be soft/undocumented rather than hard-enforced
- For production use: implement exponential backoff, cache responses, and avoid hitting the same endpoint more than once per ~5 minutes
- Consider caching job data locally and only refreshing periodically

### 3.4 Recommended Scraping Architecture

```
1. Maintain a list of company slugs per platform (seed + discover)
2. For each slug:
   - Greenhouse: GET /v1/boards/{slug}/jobs?content=true  (single call gets everything)
   - Lever: GET /v0/postings/{slug}  (single call gets everything)
3. Parse and normalize into a common schema
4. Store with timestamps, diff against previous to detect new/removed postings
5. Run on a cron (every 6-12 hours is sufficient for most use cases)
```

### 3.5 Verified Working Slugs (tested 2026-03-31)

**Greenhouse:** `anthropic`, `stripe`
**Lever:** `spotify`
**Not on Lever:** `figma`, `cloudflare`, `netflix` (returned 404 or empty)

---

## 4. Raw API Call Examples

```bash
# Greenhouse - all jobs for a company (lightweight, no descriptions)
curl 'https://boards-api.greenhouse.io/v1/boards/anthropic/jobs'

# Greenhouse - all jobs with full descriptions, departments, offices
curl 'https://boards-api.greenhouse.io/v1/boards/anthropic/jobs?content=true'

# Greenhouse - single job with application questions
curl 'https://boards-api.greenhouse.io/v1/boards/anthropic/jobs/5101832008?questions=true'

# Greenhouse - departments with nested jobs
curl 'https://boards-api.greenhouse.io/v1/boards/anthropic/departments'

# Greenhouse - offices with nested jobs
curl 'https://boards-api.greenhouse.io/v1/boards/anthropic/offices'

# Lever - all postings for a company
curl 'https://api.lever.co/v0/postings/spotify'

# Lever - paginated
curl 'https://api.lever.co/v0/postings/spotify?skip=0&limit=10'

# Lever - filtered by department
curl 'https://api.lever.co/v0/postings/spotify?department=Engineering'

# Lever - filtered by commitment type
curl 'https://api.lever.co/v0/postings/spotify?commitment=Permanent'

# Lever - combined filters
curl 'https://api.lever.co/v0/postings/spotify?department=Engineering&location=New%20York,%20NY&limit=5'
```

---

## 5. Workday Survey

Pre-implementation pass for Workday as a fourth ATS alongside Greenhouse / Lever / Ashby. Research conducted 2026-05-12. No code written, no accounts created — public-page reads only.

### 5.1 Job-listing URL pattern

Canonical shape: `https://{tenant}.wd{N}.myworkdayjobs.com/[{locale}/]{site}`, where:
- `wd{N}` is the data-center shard the customer was provisioned on (`wd1`, `wd3`, `wd5`, etc.) — varies per company, must be extracted from the careers link, not hardcoded
- `{tenant}` is the company slug
- `{site}` is a named career site (a single tenant can host multiple — e.g. external careers vs. campus recruiting)

Example: `nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite`. A secondary variant exists at `jobs.myworkdaysite.com/recruiting/{tenant}/{site}`. Custom-domain fronting is possible but uncommon.

### 5.2 Public JSON endpoint (the good news)

There is an **undocumented but stable** endpoint at:

```
POST https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
Body: {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
```

Returns `{ jobPostings: [{ title, locationsText, externalPath, postedOn, bulletFields, ... }], total }`. Job detail is a follow-up `GET /wday/cxs/{tenant}/{site}/job/{externalPath}`. No auth, no documented hard rate limit (1–2s delay recommended). A bare GET to the listings URL returns HTTP 400 — that's a body-shape rejection, not a bot block (confirmed live against nvidia.wd5).

Multiple commercial scrapers (Apify, jobo.world, fantastic.jobs) sell this as a feature, suggesting it's reliably reachable. **Effort comparable to Greenhouse / Lever / Ashby.**

### 5.3 Apply-flow page count

Roughly **5–7 discrete pages** per application, each a full page navigation with a Next button (not a single scrolling form):

1. Create / sign-in account
2. Upload resume → autofill
3. My Information (contact + address)
4. My Experience (education + work history)
5. Application Questions (work authorization, salary, role-specific)
6. Voluntary Disclosures / Self-Identify (EEO + disability + veteran, often on separate substeps)
7. Review & Submit

No single-page guest-apply analogue to Greenhouse.

### 5.4 Account creation (the bad news)

**Mandatory and per-tenant.** Confirmed by multiple primary sources (Glassdoor, university applicant guides, jobwizard.ai). Every company makes the applicant create a fresh Workday account with that tenant's password rules — there is no federated candidate identity. Email verification is **conditional** (some tenants require it, some don't); 2FA appears occasionally.

For AutoApply this is the dominant cost driver: every new company means signup → likely email-verify round-trip before the wizard even starts, and credentials need to be stored per (profile, tenant). This breaks AutoApply's "one apply session per job, fully automated" assumption.

### 5.5 Form field conventions

Workday uses `data-automation-id="..."` consistently across its framework — internal e2e-test hook, stable across releases. Standard widgets (Next, Save, file upload, address) reuse the same IDs across tenants because they're framework primitives, not customer markup.

**Custom questions** (the per-tenant section) do not get stable automation IDs — those are identified by label text. Net: generic handler is feasible for the wizard chrome; custom questions need the same Claude-backed label-matching pattern already used for Greenhouse / Ashby.

### 5.6 Anti-bot detection

**Unconfirmed which vendor** Workday fronts career sites with — no primary source named Cloudflare / Akamai / DataDome on `*.myworkdayjobs.com`. Indirect signals:
- The listings API answers an unauthenticated POST cleanly
- Commercial Apify scrapers run at scale against it
- Both argue against aggressive challenge-on-first-request

The harder surface is likely the apply wizard, not the listings. Playwright headless is detectable by default (`navigator.webdriver=true`, JA3 fingerprint); the persistent-context + non-headless pattern AutoApply already uses mitigates the cheap checks. **Assume occasional CAPTCHA at signup/login** — the existing `captcha_handler` prompt pattern would cover it. No verified evidence of hard blocks today.

### 5.7 Per-tenant variance

- **Page sequence and chrome widgets** — framework-controlled, stable across tenants
- **Page content** — customer-configurable; tenants add/remove sections, reorder Voluntary Disclosures, change required fields, write arbitrary custom questions

Realistic split: ~70% of fields are addressable by stable `data-automation-id` (framework primitives), ~30% need label-matching (customer-authored questions). More variance than Greenhouse but less than fully bespoke ATSes.

### 5.8 Existing prior art

Three relevant OSS projects, all small and unmaintained:

- **ubangura/Workday-Application-Automator** — JS/Puppeteer, ~70 stars. Fills contact/education/demographics; doesn't clearly handle the full multi-page wizard.
- **amgenene/workday_auto** — Python/Selenium, ~12 stars. Closest in spirit to AutoApply's approach: explicit signup-vs-signin branching, iterates pages until complete, uses sentence embeddings for question→answer matching.
- **simonfong6/auto-apply** — multi-ATS (GH/Lever/Workday/Jobvite), ~32 stars. Workday completeness unclear from README.

None widely adopted. The problem is **partially solved but not in a robust, dependency-grade way** — no off-the-shelf library to lean on.

### 5.9 Scope honestly — TL;DR

**5-session phase, leaning toward 6–7 if email verification gets ugly.** Breakdown:

| Sub-phase | Sessions | Notes |
|---|---|---|
| Listings discovery | 1 | `/wday/cxs/.../jobs` endpoint is Greenhouse-equivalent in difficulty. Cheap win, ships value to users on day 1. |
| Account creation + per-tenant credential storage + email-verify loop | 2 | Net-new infrastructure. Schema changes for per-tenant creds. Email-verify handler reuses the existing IMAP reader to pull the verification link. Signup-vs-signin branching. |
| Multi-page wizard orchestrator + 5–7 page handlers | 2 | Doable thanks to stable `data-automation-id`. Voluntary Disclosures alone roughly equals current `demographics.py` in surface. |
| Anti-bot resilience + CAPTCHA at signup | 0.5 | Mostly free by reusing existing CAPTCHA pause infrastructure. |

**The honest case against doing it:** per-tenant account creation is a UX regression, not just an engineering cost. Every new company adds a 30–60s signup detour the user must oversee for email verification.

**Recommendation:** Split into two phases.
- **Phase 43 — Workday listings discovery** (1 session): build `src/jobs/clients/workday.py` so Workday jobs show up in `/jobs` with fit scores. Doesn't unlock apply; gives users immediate visibility into a much larger jobs universe.
- **Phase 44 — Workday apply wizard** (4 sessions): gated behind a beta toggle so the account-creation friction is opt-in. Defer until Phase 43 is shipped and users have asked for it.

Punting Phase 44 entirely is also defensible — Workday is roughly 1.5x the engineering surface of Greenhouse with materially worse UX at the account-creation step. The listings-only path is the safe minimum.

### Sources

- [Workday Scraper API spec (jobo.world)](https://jobo.world/ats/workday)
- [Workday Jobs API (fantastic.jobs)](https://fantastic.jobs/ats/workday)
- [Apify Workday Job Scraper](https://apify.com/shahidirfan/workday-job-scraper)
- [Workday Job Applications Made Simple (JobWizard)](https://www.jobwizard.ai/post/workday-job-applications-made-simple)
- [Glassdoor: per-company Workday accounts](https://www.glassdoor.com/Community/job-hunting-in-tech/why-tf-do-i-have-to-create-a-new-workday-account-for-every-company-i-apply-to-certainly-it-would-be-easier-for-everyone-if-i)
- [UVA External Applicant guide (PDF)](https://hr.virginia.edu/sites/default/files/TALENT%20COE/Workday/REC-Applicant%20Process%20External.pdf)
- [ubangura/Workday-Application-Automator](https://github.com/ubangura/Workday-Application-Automator)
- [amgenene/workday_auto](https://github.com/amgenene/workday_auto)
- [simonfong6/auto-apply](https://github.com/simonfong6/auto-apply)
