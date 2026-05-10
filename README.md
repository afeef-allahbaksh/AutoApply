# AutoApply

A fully automated job application pipeline that discovers relevant job postings, tailors your resume per role, and submits applications through ATS platforms — driven from the command line, with a local FastAPI dashboard for tracking interview pipeline progress.

## How It Works

```
Profile -> Discover Companies -> Find Open Roles -> Score Fit -> Select Projects -> Optimize Resume -> Apply -> Log
```

1. **Company Discovery** — Validates company slugs against Greenhouse and Lever public APIs in parallel, builds a targetable registry
2. **Job Discovery** — Queries each company's job board in parallel, filters by LLM-expanded role keywords and location preferences, scores by relevance, deduplicates against application history
3. **Job Fit Scoring** — Batched LLM call scores each job 1-5 against your resume. Low-fit jobs are flagged, and you can skip them in pipeline mode
4. **Project Selection** — If your resume has a project pool, Claude picks the most relevant projects for each specific role (batched for multi-job runs, maintains your one-page format)
5. **Resume Optimization** — Extracts 10-15 keywords from the JD, then tailors bullet points to incorporate them. ATS-safe Unicode normalization ensures parsers can read your resume. Results are cached to skip redundant API calls on re-runs
6. **Auto-Apply** — Playwright navigates to each application page, fills standard fields (including React Select comboboxes), uploads your tailored resume, answers custom questions using canned responses or Claude API, pauses for review or auto-submits
7. **History & Logging** — Every application is logged with status, fit scores, screenshots, and deduplication

## Architecture

```
main.py                         # CLI entry point (setup, run, individual commands)
src/
  api.py                        # Shared Anthropic client singleton + retry logic
  setup.py                      # Interactive profile creation (with project pool)
  pipeline.py                   # Full pipeline orchestrator
  role_expander.py              # LLM-powered role keyword expansion
  profile_loader.py             # Profile loading + validation
  schemas.py                    # JSON schema validators
  discovery.py                  # Company discovery (parallelized, Greenhouse/Lever API)
  job_discovery.py              # Job fetching, filtering, fit scoring, dedup (parallelized)
  resume_parser.py              # PDF -> structured JSON via Claude API
  resume_optimizer.py           # Resume tailoring, keyword extraction, project selection, caching
  resume_renderer.py            # JSON -> PDF via WeasyPrint (ATS-safe Unicode normalization)
  resume_diff.py                # Human-readable resume change diffs (with project selection reasoning)
  cover_letter.py               # Cover letter generation
  browser.py                    # Playwright browser management
  ats_greenhouse.py             # Greenhouse form handler (React Select + standard inputs)
  ats_lever.py                  # Lever form handler
  applicant.py                  # Application submission orchestrator (persists Playwright storage_state per profile)
  inbox/                        # Inbox integration via IMAP — classify and propose updates
    auth.py                     # IMAP credentials store + login verifier (Gmail-default, any IMAP host)
    fetch.py                    # imaplib wrapper with body extraction and Message-ID threading
    classify.py                 # Batched Claude classifier (new_application | status_update | ignore)
    matcher.py                  # Match classified email to existing applications.json entry
    sync.py                     # Orchestrator: prefilter -> classify -> match -> proposals
  ui/                           # Local FastAPI dashboard (tracking, not applying)
    app.py                      # FastAPI() instance, mounts routers
    state.py                    # Active profile + per-profile threading.Lock registry
    deps.py                     # get_profile dependency, template_globals helper
    templates_loader.py         # Shared Jinja2Templates instance
    routes/                     # dashboard, jobs, applications, companies, settings, cold_email, profile, email
    templates/                  # base.html (design tokens), page templates, kanban + proposal partials
config/
  *_schema.json                 # JSON schemas (profile, resume with project_pool, etc.)
  seed_companies.json           # Verified company slugs (Greenhouse/Lever)
  example_profile/              # Template profile with fake data
  resume_template/resume.css    # PDF rendering stylesheet
profiles/                       # User data (gitignored)
```

## Multi-User Profiles

Each user gets an isolated directory under `profiles/` containing all personal data. The `profiles/` directory is gitignored — nothing personal ever touches GitHub.

```
profiles/
  yourname/
    profile.json          # Personal info, job preferences, settings
    resume.json           # Structured resume (with optional project_pool)
    responses.json        # Canned answers to common ATS questions
    companies.json        # Discovered companies
    jobs.json             # Current matching job listings
    applications.json     # Full application history
    resumes/              # Tailored PDFs (named {name}_{company}.pdf)
    screenshots/          # Form screenshots for review
    progress/             # Saved state for failed applications (retry support)
    browser_state.json    # Persisted Playwright cookies + localStorage (skips 2FA on re-runs)
    imap/                 # (optional) IMAP credentials + sync state for inbox integration
      credentials.json    # {email, password, server, port} — created by Settings page
      state.json          # last_sync_at + processed_message_ids
      proposals.json      # Pending review-queue items
```

## Quick Start

```bash
# Install Python dependencies
pip install -r requirements.txt
python -m playwright install chromium

# System dependency (required for PDF rendering)
# macOS:
brew install pango
# Ubuntu/Debian:
sudo apt install libpango-1.0-0 libpangocairo-1.0-0
# Windows:
# Install GTK3 runtime from https://github.com/nickvdp/weasyprint-windows
# or use: choco install gtk-runtime

# Set your API key
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

# Interactive setup — creates your profile, imports your resume, builds project pool
python main.py setup

# Run the full pipeline
python main.py --profile yourname run
```

## Individual Commands

```bash
# Pipeline
python main.py --profile yourname run             # Run the full pipeline
python main.py --profile yourname status          # View profile summary

# Discovery
python main.py --profile yourname discover        # Discover companies from seed list
python main.py --profile yourname add-company     # Add a company by slug (auto-detects ATS)
python main.py --profile yourname discover-jobs   # Find matching jobs

# Resume
python main.py --profile yourname import-resume                     # Parse resume PDF (prompts for path)
python main.py --profile yourname import-resume --pdf resume.pdf    # Parse resume PDF (direct)
python main.py --profile yourname add-projects                      # Add projects from another resume (prompts)
python main.py --profile yourname add-projects --pdf other.pdf      # Add projects from another resume (direct)
python main.py --profile yourname optimize --job 0                  # Tailor resume for job

# Application
python main.py --profile yourname apply --job 0                     # Apply to a specific job
python main.py --profile yourname apply                             # Apply to all matched jobs
python main.py --profile yourname apply --dry-run                   # Fill forms without submitting
python main.py --profile yourname apply --headless                  # Run browser in headless mode
python main.py --profile yourname history                           # View application history

# Profile management
python main.py --profile yourname update-settings     # Toggle auto_submit, change rate limit
python main.py --profile yourname update-preferences  # Change target roles, locations, salary
python main.py --profile yourname update-responses    # Update canned ATS answers (EEO, visa, etc.)

# Local dashboard
python main.py --profile yourname ui                  # Launch dashboard at http://127.0.0.1:8000
python main.py --profile yourname ui --port 8765      # Custom port
```

## Dashboard

The `ui` subcommand launches a single-user, local-only FastAPI dashboard for tracking the interview pipeline. It does not submit applications — applying is still done via the `apply` command. The dashboard surfaces:

- **Dashboard** — Total applied, in-pipeline, offers, rejections, tracked response rate, and the last 10 status updates. Polls every 10s.
- **Jobs** — Browse `jobs.json` with filters for fit score / company / ATS. Per-row "Track" button creates a manual application entry from the job (for logging applications you submitted via LinkedIn, referrals, or other channels).
- **Applications** — A 6-column kanban (`applied → screen → technical → onsite → offer / rejected`) with drag-and-drop between columns via Sortable.js. Inline edit, delete, manual add. `status_updated_at` is re-stamped on every move. Inbox proposals appear in a review queue above the board when sync finds candidate updates.
- **Companies** — Add/list companies with auto-detect ATS.
- **Settings** — Edit `profile.json` (roles, locations, salary, levels, industries, auto_submit, rate_limit_seconds), `responses.json` (EEO answers), and the IMAP inbox card.

Stack: FastAPI + Jinja2 + HTMX + Sortable.js + Tailwind via CDN. Bound to `127.0.0.1`, no auth. Dark theme — deep canvas (`#0d0e13`), indigo accent (`#818cf8`) with violet-shifted wordmark glyph, smooth motion (cubic-bezier easing, hover lift, drag rotation), 3-tier surface elevation (canvas / raised / elevated) via tonal shifts rather than just shadows.

## Inbox integration (optional, IMAP)

Connect any IMAP-supporting mailbox so the dashboard can scrape application status updates and surface them as proposed changes. AutoApply only reads — it never sends or modifies email.

### One-time setup (Gmail)

1. Enable 2FA on your Google account if it isn't already (<https://myaccount.google.com/security>).
2. Generate an **App password** at <https://myaccount.google.com/apppasswords> (any 16-character one).
3. In the dashboard, go to **Settings → Inbox sync (IMAP)**, enter your email + the app password (server / port can be left blank to default to `imap.gmail.com:993`).
4. Click **Connect**. The dashboard runs a real login + logout to verify and refuses bad credentials.
5. The card now reads "Connected as you@gmail.com".

### Setup for other providers

Same flow with a different server. Outlook: `outlook.office365.com:993`. ProtonMail: requires Bridge running locally and uses `127.0.0.1:1143`. University mailboxes: ask IT for the IMAP host. As long as the provider speaks IMAPS and accepts password auth, it works.

### Security trade-off vs. OAuth

An app password gives **full mailbox access** (read, send, delete) — not the read-only scope OAuth would grant. AutoApply only reads, but the secret stored in `profiles/{name}/imap/credentials.json` is more powerful than an OAuth refresh token. Treat it like a password. The trade buys you a 2-minute setup vs. registering a Google Cloud project.

### How sync works

Click **Sync inbox** on the **Applications** page. The button kicks off a background worker and returns immediately — the page polls every 4s for progress, and proposals stream into the review queue as the classifier finishes each chunk.

Two toggles next to the button:

- **Full history** — ignore `last_sync_at` and look back 5 years (cap 5000 messages). Use once on initial setup to backfill; the default incremental sync covers everything since the last run.
- **Free mode** — swap the Claude classifier for a regex-based local classifier. Zero API cost, lower recall, but catches the common patterns ("thank you for applying", "phone screen", "unfortunately", "offer letter", etc.). Useful when out of credits or for cheap incremental syncs.

The pipeline per sync:

1. **IMAP fetch** in two phases — headers only first (bulk FETCH on chunks of 500, returns in 2-5s for thousands of messages), then full bodies for prefilter survivors only (bulk FETCH on chunks of 50). In free mode an IMAP-level keyword filter (`OR (FROM "greenhouse.io") (SUBJECT "interview") ...`) further trims the result set server-side so non-application mail never gets downloaded.
2. **Heuristic prefilter** drops obvious noise — LinkedIn / Indeed job alerts, newsletters, "your application was viewed" pings — by sender domain + subject regex.
3. **Classifier** processes survivors in chunks. LLM mode: Haiku 4.5, batches of 15, two workers concurrent, throttled by a sliding-window token bucket (default 45K input TPM, override with `AUTOAPPLY_INPUT_TPM_BUDGET`). Free mode: regex matching on subject + body for status keywords. Both return the same shape (`action_type`, `company`, `role`, `status`, `confidence`).
4. **Matcher** ties classified emails to existing entries through a cascade of signals:
   - Stored `email_thread_ids` on existing entries (Message-ID chain match).
   - Classifier-extracted company name (case-insensitive substring match against `applications.json`).
   - Sender domain stem (e.g. `noreply@bloomberg.com` → "bloomberg"), skipping ATS relays like `greenhouse-mail.io` whose domain doesn't reveal the actual company.
   - Subject prefix pattern ("CompanyName - …").
   - Body scan against company names already on your kanban.
   When multiple existing entries match the same company, role-token overlap disambiguates.
5. **Dedup**: if matcher finds an existing entry for a `new_application` proposal (re-confirmation, duplicate from a different ATS system), the proposal is silently skipped — the message id is still marked processed so it doesn't recur. Same for `status_update` proposals that would be no-ops (proposed status already matches existing).
6. **Fallback for orphan status updates**: if the first email seen for a job is already a follow-up (phone-screen invite, rejection) and there's no existing entry to anchor to, the proposal is upgraded from `status_update` to `new_application` with the proposed status, so you can create a placeholder entry at the right pipeline stage.
7. Proposals stream into the review-queue panel as chunks complete. Each shows the proposed change, source email subject + sender, confidence dot, and three buttons: **Apply**, **Dismiss**, **View** (deep-links to Gmail web).
8. Applied proposals mutate `applications.json` and tag the entry with `source="email"` plus the inbound thread id in `email_thread_ids`.

### Sync controls

- **Cancel** — banner shows a Cancel button while a sync runs. Cooperative cancellation at chunk boundaries (~6-10s latency). Cancelled syncs don't commit `processed_message_ids`, so re-running picks up where you left off (proposals already saved skip via the in-loop existing-id check).
- **Interrupted state** — if uvicorn restarts mid-sync, the status file says "running" but no worker thread exists. The dashboard detects this on the next read and surfaces a yellow "Sync interrupted" banner with a one-click retry. No more zombie "running" badges.
- **Atomic status writes** — status file written via tmp + rename so a mid-write crash leaves the previous state intact rather than a half-written JSON.

Auto-update is never silent — every change goes through the review queue. Already-processed messages are remembered in `imap/state.json` (capped at 10,000 most recent) so subsequent syncs skip them.

## Resume Optimization

Resumes are stored as structured JSON — not as PDFs. This makes them programmatically editable and diffable. The optimizer:

- **Extracts 10-15 keywords** from the job description before optimizing
- **Selects the best projects** from your project pool for each specific role (batched for multi-job runs)
- Rewrites bullet points to incorporate extracted JD keywords naturally
- Reorders skills to surface the most relevant ones first
- Adds plausible skills from the JD that the candidate likely has
- Preserves all metrics, company names, dates, and factual claims
- **ATS-safe normalization** — replaces smart quotes, em dashes, and invisible Unicode with plain ASCII
- Shows a diff of every change before saving (including project selection reasoning)
- **Caches results** — re-running optimize on the same resume+JD combo skips the API call
- Renders a clean PDF per application via an HTML/CSS template

The resume schema is fully dynamic — different users can have different section layouts (`skills -> experience -> projects -> education` vs `summary -> experience -> education -> skills`), and `section_order` controls what renders and in what order.

### Project Pool

If you have multiple versions of your resume with different projects, you can build a project pool:

```bash
# During setup — prompted automatically after resume import
python main.py setup

# During import-resume — prompted after parsing your main resume
python main.py --profile yourname import-resume

# Or add projects from another resume anytime (prompts for path if --pdf omitted)
python main.py --profile yourname add-projects
python main.py --profile yourname add-projects --pdf other_resume.pdf
```

The optimizer picks the N most relevant projects per job (where N = number of projects in your base resume), keeping your resume at one page. The diff view shows which projects were selected or skipped, with a one-sentence reason for each decision.

## ATS Support

| Feature | Greenhouse | Lever |
|---------|-----------|-------|
| Job discovery | `boards-api.greenhouse.io` | `api.lever.co` |
| Form filling | First/last name, email, phone, LinkedIn, location | Full name, email, phone, LinkedIn, GitHub |
| Resume upload | PDF file input | PDF file input |
| Custom questions | Canned responses + Claude API fallback | Canned responses + Claude API fallback |
| Cover letter | Auto-generated if form requires it | Filled in "Additional info" field |
| CAPTCHA handling | Pauses for manual solve | Pauses for manual solve |

## Application Flow

With `auto_submit: false` (default):
1. Playwright opens a visible browser
2. Navigates to the application page
3. Fills all fields and uploads your tailored resume
4. Takes a screenshot for your records
5. Pauses and asks: `Submit? (y/n/q)`
6. Logs the result (with fit score) to `applications.json`

With `auto_submit: true`:
- Same flow but submits automatically and moves to the next job
- Rate-limited with configurable delay + randomized jitter

With `--dry-run`:
- Fills forms and takes screenshots but never submits
- Does not log to `applications.json` — safe for testing new companies

## Cost

Designed to minimize API spend — LLM calls only happen where they add real value.

| Action | Cost | When |
|--------|------|------|
| Setup (profile creation) | $0 | Interactive CLI prompts |
| Role keyword expansion | ~$0.005 | One-time during setup |
| Resume import (PDF parse) | ~$0.01 | One-time per resume |
| Company/job discovery | $0 | Public APIs, no auth |
| Job fit scoring | ~$0.01/batch | One call per discover-jobs run |
| Project selection | ~$0.005/job (single) or ~$0.01/batch | Batched in pipeline/apply, single for optimize |
| Resume optimization | ~$0.02/job | Per application (cached — free on re-run) |
| Custom question answering | ~$0.005/question | Only when no canned response matches |
| Cover letter generation | ~$0.01/letter | Only when form requires it |

Applying to 10 jobs with fit scoring + resume optimization costs roughly $0.25-0.40 total.

## Application Status Lifecycle

Applications support an extended status enum — both the CLI `apply` flow and manual UI entries flow through it:

- `applied` — submitted (default for both CLI and manual)
- `screen` / `technical` / `onsite` — interview pipeline stages
- `offer` / `rejected` — terminal outcomes
- `failed` / `review_pending` / `skipped` — CLI-only edge cases

Each entry also carries `status_updated_at` (re-stamped on every status change) and `source`:

- `autoapply` — created by the CLI Playwright submission flow
- `manual` — created by the dashboard's manual-add form or the Jobs-page Track button
- `email` — created from a Gmail-derived proposal you accepted in the review queue

Email-derived entries also carry `email_thread_ids` linking back to the inbox threads that informed the entry; subsequent emails on the same thread skip ambiguity matching and pin straight to that entry.

## Tech Stack

- **Python** — core language
- **Claude API** (Anthropic) — resume optimization, PDF parsing, custom question answering, cover letter generation
- **Playwright** — browser automation for form filling, with persisted `storage_state` per profile to skip 2FA on re-runs
- **WeasyPrint** — HTML/CSS to PDF rendering
- **FastAPI + Jinja2 + HTMX + Sortable.js** — local dashboard with kanban drag-and-drop
- **IMAP (stdlib `imaplib`)** — read-only inbox scrape for application status updates (works with Gmail, Outlook, any IMAPS host; optional)
- **Greenhouse & Lever APIs** — job discovery (public, no auth)
