# AutoApply

A fully automated job application pipeline that discovers relevant job postings, tailors your resume per role, and submits applications through ATS platforms — all from the command line.

## How It Works

```
Profile -> Discover Companies -> Find Open Roles -> Select Projects -> Optimize Resume -> Apply -> Log
```

1. **Company Discovery** — Validates company slugs against Greenhouse and Lever public APIs in parallel, builds a targetable registry
2. **Job Discovery** — Queries each company's job board in parallel, filters by LLM-expanded role keywords and location preferences, scores by relevance, deduplicates against application history
3. **Project Selection** — If your resume has a project pool, Claude picks the most relevant projects for each specific role (maintains your one-page format)
4. **Resume Optimization** — Takes your structured resume + a job description, rewrites bullet points to mirror JD language, reorders skills by relevance, preserves all metrics and facts, renders a tailored PDF
5. **Auto-Apply** — Playwright navigates to each application page, fills standard fields (including React Select comboboxes), uploads your tailored resume, answers custom questions using canned responses or Claude API, pauses for review or auto-submits
6. **History & Logging** — Every application is logged with status tracking, screenshots, and deduplication

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
  job_discovery.py              # Job fetching, filtering, scoring, dedup (parallelized)
  resume_parser.py              # PDF -> structured JSON via Claude API
  resume_optimizer.py           # Resume tailoring + project selection per job
  resume_renderer.py            # JSON -> PDF via WeasyPrint
  resume_diff.py                # Human-readable resume change diffs (with project selection reasoning)
  cover_letter.py               # Cover letter generation
  browser.py                    # Playwright browser management
  ats_greenhouse.py             # Greenhouse form handler (React Select + standard inputs)
  ats_lever.py                  # Lever form handler
  applicant.py                  # Application submission orchestrator
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
python main.py --profile yourname history                           # View application history

# Profile management
python main.py --profile yourname update-settings     # Toggle auto_submit, change rate limit
python main.py --profile yourname update-preferences  # Change target roles, locations, salary
python main.py --profile yourname update-responses    # Update canned ATS answers (EEO, visa, etc.)
```

## Resume Optimization

Resumes are stored as structured JSON — not as PDFs. This makes them programmatically editable and diffable. The optimizer:

- **Selects the best projects** from your project pool for each specific role (if you have one)
- Rewrites bullet points to mirror the job description's language
- Reorders skills to surface the most relevant ones first
- Adds plausible skills from the JD that the candidate likely has
- Preserves all metrics, company names, dates, and factual claims
- Shows a diff of every change before saving (including project selection reasoning)
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
6. Logs the result to `applications.json`

With `auto_submit: true`:
- Same flow but submits automatically and moves to the next job
- Rate-limited with configurable delay + randomized jitter

## Cost

Designed to minimize API spend — LLM calls only happen where they add real value.

| Action | Cost | When |
|--------|------|------|
| Setup (profile creation) | $0 | Interactive CLI prompts |
| Role keyword expansion | ~$0.005 | One-time during setup |
| Resume import (PDF parse) | ~$0.01 | One-time per resume |
| Company/job discovery | $0 | Public APIs, no auth |
| Project selection | ~$0.005/job | Only when project pool exists |
| Resume optimization | ~$0.02/job | Per application |
| Custom question answering | ~$0.005/question | Only when no canned response matches |
| Cover letter generation | ~$0.01/letter | Only when form requires it |

Applying to 10 jobs with resume optimization costs roughly $0.20-0.35 total.

## Tech Stack

- **Python** — core language
- **Claude API** (Anthropic) — resume optimization, PDF parsing, custom question answering, cover letter generation
- **Playwright** — browser automation for form filling
- **WeasyPrint** — HTML/CSS to PDF rendering
- **Greenhouse & Lever APIs** — job discovery (public, no auth)
