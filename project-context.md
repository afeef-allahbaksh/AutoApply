# Project Context

## Who I am
CS senior at Drexel, graduating June 2026. Have production LLM experience — built a classification pipeline processing 40k+ pharmaceutical documents using Claude Sonnet and Amazon Nova Pro on AWS Bedrock, and a RAG system that reduced document lookup from 4-15 minutes to under 30 seconds. Recently built Catchup, an agentic messaging assistant using Claude API with tool use, streaming, and a React/Node.js stack.

## What we're building
A fully automated job application pipeline — one cohesive project with four agents working in sequence:

1. **Company discovery** — Find companies using Greenhouse/Lever, detect their ATS, build a targetable companies.json
2. **Job discovery** — Query those companies' job boards for open roles matching the user's preferences
3. **Resume optimization** — Tailor the resume per job using Claude API, show a diff of changes, render to PDF
4. **Auto-apply** — Fill out and submit applications via Playwright, log everything to application history

## Full pipeline mental model
```
Profile → Discover Companies → Find Open Roles → Optimize Resume → Apply → Log
```

## Profiles system
The app supports multiple users via a `profiles/` directory. Each user has their own subdirectory containing all personal data. The `profiles/` directory is gitignored — no personal info ever hits GitHub.

```
profiles/
├── {user}/
│   ├── profile.json        # personal info, job preferences, settings
│   ├── resume.json         # structured resume (rendered to PDF per application)
│   ├── responses.json      # canned answers to common ATS questions
│   └── applications.json   # history of every application (role, company, date, status, URL)
├── friend/
│   ├── profile.json
│   ├── resume.json
│   ├── responses.json
│   └── applications.json
```

Run the app by passing a profile name:
```bash
python main.py --profile {profile_name}
```

All operations are scoped to the active profile.

## Profile schema (profile.json)
```json
{
  "name": "Your name",
  "email": "xxx@email.com",
  "phone": "+1 (xxx) xxx-xxxx",
  "location": "City, State",
  "linkedin": "linkedin.com/in/yourprofile",
  "github": "github.com/yourusername",
  "job_preferences": {
    "roles": ["Software Engineer", "New Grad SWE", "Junior Software Engineer", "Early Career SWE"],
    "experience_levels": ["entry-level", "new grad", "early career"],
    "locations": ["Remote", "New York", "Philadelphia", "San Francisco"],
    "salary_min": 90000,
    "industries": ["AI", "Tech", "SaaS"]
  },
  "settings": {
    "auto_submit": false,
    "rate_limit_seconds": 30
  }
}
```

### auto_submit flag
- `false` — agent fills out the entire application, pauses for review, user clicks submit. Start here.
- `true` — fully automated end-to-end, logs to applications.json without human input. Enable once you trust the output.

### responses.json
Canned answers to questions that repeat across every application — saves API calls and ensures consistency:
```json
{
  "work_authorization": "Yes, I am authorized to work in the United States",
  "visa_sponsorship": "No",
  "gender": "Prefer not to say",
  "ethnicity": "Prefer not to say"
}
```

## Company discovery agent
Runs first. Builds and maintains companies.json by detecting which ATS each company uses.

**How it works:**
1. Reads preferences from profile.json
2. Scrapes Greenhouse and Lever public board indexes — free, public, already confirms ATS
3. Detects ATS by URL signature:
   - boards.greenhouse.io/companyname → Greenhouse
   - jobs.lever.co/companyname → Lever
   - jobs.ashbyhq.com/companyname → Ashby
4. Writes confirmed entries to companies.json

**companies.json schema:**
```json
[
  {
    "name": "Anthropic",
    "ats": "greenhouse",
    "slug": "anthropic",
    "careers_url": "https://boards.greenhouse.io/anthropic",
    "added": "2024-03-21"
  }
]
```

Users can also manually add companies at any time. Discovery sources in priority order:
1. Greenhouse public board index
2. Lever public board index
3. Crunchbase API (later — requires API key, richer industry/stage filtering)

## Key decisions already made
- No LinkedIn scraping — use Greenhouse/Lever public board indexes instead
- Profiles system for multi-user support — no auth, no database, just directories
- CLI only for v1 — no React frontend yet
- Resume stored as structured JSON, rendered to PDF via template (weasyprint) — not editing PDFs directly. Users import their existing PDF once; system parses it into JSON.
- Greenhouse + Lever only in v1 — Ashby in v2
- Curated companies.json bootstrapped from board indexes — no Crunchbase in v1
- Deduplication by composite key: (company + role + posting URL) in applications.json
- Configurable rate limiting between Playwright submissions (rate_limit_seconds in profile)
- Error recovery: save Playwright progress state so failed applications can be retried
- Workday deprioritized — inconsistent per company, tackle later

## Priority build order
1. Profiles system + profile.json + responses.json schema
2. Company discovery agent — populate companies.json from Greenhouse/Lever board indexes
3. Job discovery — query APIs for open roles at discovered companies, filtered by profile preferences
4. Resume optimizer — JSON → PDF via template, Claude rewrites bullets per JD, diff view of changes
5. Playwright ATS handlers for Greenhouse and Lever
6. Auto-submit toggle + rate limiting
7. Application history logging to applications.json with deduplication
8. Error recovery — retry failed applications from saved state
9. Ashby support (v2)
10. Workday support (later, if at all)
