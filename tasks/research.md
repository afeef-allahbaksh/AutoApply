# ATS Public API Research: Greenhouse & Lever

Research conducted 2026-03-31 with live API testing.

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
