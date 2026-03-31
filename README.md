# AutoApply

A fully automated job application pipeline that discovers relevant job postings, tailors your resume per role, and submits applications through ATS platforms — all from the command line.

## How It Works

```
Profile -> Discover Companies -> Find Open Roles -> Optimize Resume -> Apply -> Log
```

1. **Company Discovery** — Validates company slugs against Greenhouse and Lever public APIs, builds a targetable registry
2. **Job Discovery** — Queries each company's job board, filters by role/location preferences, scores by relevance, deduplicates against application history
3. **Resume Optimization** — Takes your structured resume + a job description, rewrites bullet points to mirror JD language, reorders skills by relevance, preserves all metrics and facts, renders a tailored PDF
4. **Auto-Apply** — Playwright navigates to each application page, fills standard fields, uploads your tailored resume, answers custom questions using canned responses or Claude API, pauses for review or auto-submits

## Architecture

```
main.py                         # CLI entry point (setup, run, individual commands)
src/
  setup.py                      # Interactive profile creation (no LLM, $0)
  pipeline.py                   # Full pipeline orchestrator
  profile_loader.py             # Profile loading + validation
  schemas.py                    # JSON schema validators
  discovery.py                  # Company discovery (Greenhouse/Lever API)
  job_discovery.py              # Job fetching, filtering, scoring, dedup
  resume_parser.py              # PDF -> structured JSON via Claude API
  resume_optimizer.py           # Resume tailoring per job description
  resume_renderer.py            # JSON -> PDF via WeasyPrint
  resume_diff.py                # Human-readable resume change diffs
  cover_letter.py               # Cover letter generation
  browser.py                    # Playwright browser management
  ats_greenhouse.py             # Greenhouse form handler
  ats_lever.py                  # Lever form handler
  applicant.py                  # Application submission orchestrator
config/
  *_schema.json                 # JSON schemas for all data files
  seed_companies.json           # 11 verified company slugs (Greenhouse/Lever)
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
    resume.json           # Structured resume (parsed from PDF)
    responses.json        # Canned answers to common ATS questions
    companies.json        # Discovered companies
    jobs.json             # Current matching job listings
    applications.json     # Full application history
    resumes/              # Tailored resume PDFs (one per application)
    screenshots/          # Form screenshots for review
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

# Interactive setup — creates your profile, imports your resume
python main.py setup

# Run the full pipeline
python main.py --profile yourname run
```

## Individual Commands

```bash
python main.py --profile yourname status          # View profile summary
python main.py --profile yourname discover        # Discover companies from seed list
python main.py --profile yourname discover-jobs   # Find matching jobs
python main.py --profile yourname import-resume --pdf resume.pdf  # Parse resume PDF
python main.py --profile yourname optimize --job 0               # Tailor resume for job
python main.py --profile yourname apply --job 0                  # Apply to a specific job
python main.py --profile yourname apply                          # Apply to all matched jobs
python main.py --profile yourname history                        # View application history
```

## Resume Optimization

Resumes are stored as structured JSON — not as PDFs. This makes them programmatically editable and diffable. The optimizer:

- Rewrites bullet points to mirror the job description's language
- Reorders skills to surface the most relevant ones first
- Adds plausible skills from the JD that the candidate likely has
- Preserves all metrics, company names, dates, and factual claims
- Shows a diff of every change before saving
- Renders a clean PDF per application via an HTML/CSS template

The resume schema is fully dynamic — different users can have different section layouts (`skills -> experience -> projects -> education` vs `summary -> experience -> education -> skills`), and `section_order` controls what renders and in what order.

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

- **Setup**: $0 (plain CLI prompts, no LLM)
- **Resume import**: ~$0.01 (one-time Claude API call to parse PDF)
- **Resume optimization**: ~$0.02 per job (Claude API rewrites bullets)
- **Custom questions**: ~$0.005 per question (only when no canned response matches)
- **Company/job discovery**: $0 (public APIs, no auth needed)

## Tech Stack

- **Python** — core language
- **Claude API** (Anthropic) — resume optimization, PDF parsing, custom question answering, cover letter generation
- **Playwright** — browser automation for form filling
- **WeasyPrint** — HTML/CSS to PDF rendering
- **Greenhouse & Lever APIs** — job discovery (public, no auth)
