# AutoApply

Apply to dozens of jobs in the time it takes to apply to one — without the slop. AutoApply discovers open roles at companies you care about, scores each one against your resume, tailors the resume per job, fills out the application, and tracks your interview pipeline on a local dashboard.

You stay in control: every submission can pause for your review, every inbox-driven status change goes through a manual approve/dismiss queue, and your personal data lives only on your machine.

## How it works

```
Profile → Discover Companies → Find Open Roles → Score Fit → Select Projects
       → Optimize Resume → Apply → Log → Track on Kanban
```

1. **You import your resume once** (PDF → structured JSON via Claude).
2. **AutoApply finds matching roles** at companies on Greenhouse / Lever using each company's public job board API.
3. **Every job gets a 1-5 fit score** before you spend any time on it. Low-fit jobs are flagged so you can skip them.
4. **Each application gets a tailored resume** — keywords from the JD woven into your bullets, projects swapped in from your project pool, exported as a clean one-page PDF.
5. **Playwright submits the application** for you. Default is "fill it out, pause, let you review" — you click Submit. Set `auto_submit: true` once you trust it.
6. **A local dashboard** tracks everything as a kanban board (`applied → screen → technical → onsite → offer / rejected`) with drag-and-drop between columns.
7. **Optionally**: connect your email so the dashboard auto-detects interview invites, rejections, and offers from your inbox and proposes status updates for you to approve.

## Quick Start

```bash
# Install
pip install -r requirements.txt
python -m playwright install chromium

# System dependency for PDF rendering
# macOS:
brew install pango
# Ubuntu/Debian:
sudo apt install libpango-1.0-0 libpangocairo-1.0-0

# Set your Anthropic API key
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

# Interactive setup — creates your profile, imports your resume
python main.py setup

# Run the full pipeline
python main.py --profile yourname run
```

## Commands

```bash
# Run the whole pipeline end-to-end
python main.py --profile yourname run

# Or run individual stages
python main.py --profile yourname discover         # Find companies
python main.py --profile yourname discover-jobs    # Find matching jobs
python main.py --profile yourname optimize --job 0 # Tailor resume for one job
python main.py --profile yourname apply --job 0    # Apply to one job
python main.py --profile yourname apply --dry-run  # Fill forms without submitting

# Profile management
python main.py --profile yourname status               # Profile summary
python main.py --profile yourname history              # Application history
python main.py --profile yourname add-company          # Add a company by slug
python main.py --profile yourname update-preferences   # Change target roles/locations/salary
python main.py --profile yourname update-responses     # Update EEO answers, visa, etc.
python main.py --profile yourname update-settings      # Toggle auto_submit, change rate limit

# Resume
python main.py --profile yourname import-resume        # Parse a PDF resume
python main.py --profile yourname add-projects         # Add projects from another resume

# Local dashboard (port 8000 by default)
python main.py --profile yourname ui
python main.py --profile yourname ui --port 8765
```

All commands prompt interactively for missing inputs — no manual JSON editing.

## Dashboard

`python main.py --profile yourname ui` launches a local FastAPI dashboard at `http://127.0.0.1:8000`. It's read-only on your data files (mutations go through validated routes) and bound to localhost — single-user, no auth.

What you'll see:

- **Dashboard** — totals at a glance: applied / in pipeline / offers / rejections / response rate, plus your last 10 status changes.
- **Jobs** — a filterable view of `jobs.json` with fit scores. Each row has a Track button that creates a manual application entry from the job.
- **Applications** — a 6-column kanban board you drag cards across to update status. Manual add, inline edit, delete. If you've connected your inbox, proposed changes from new email show up in a review queue above the board.
- **Companies** — list and add by slug (greenhouse/lever auto-detect).
- **Settings** — edit your profile, EEO responses, and the IMAP inbox connection.

The dashboard never submits applications — applying still happens via the CLI `apply` command in your terminal. The dashboard is a *tracker*, not a submitter.

## Inbox sync (optional)

If you want the dashboard to notice when companies email you about interview invites, rejections, or offers, connect an IMAP-supporting inbox in Settings:

1. **Gmail**: enable 2FA, generate an app password at <https://myaccount.google.com/apppasswords>, paste it into the dashboard's Settings → Inbox sync (IMAP) card. Server / port default to `imap.gmail.com:993`.
2. **Other providers**: any IMAPS host works — Outlook, ProtonMail Bridge (`127.0.0.1:1143`), university mailboxes. Enter the server explicitly.
3. Click **Connect**. AutoApply does a real login + logout to verify; bad credentials are refused.

After connecting, hit **Sync inbox** on the Applications page. The button kicks off a background scan and proposals stream into a review queue as the classifier finishes them. You decide what to apply.

**Toggles** next to the button:

- **Full history** — look back 5 years on first sync. Use it once to backfill; default incremental syncs cover what's new since the last run.
- **Free mode** — use a local regex classifier instead of the Claude API. Zero cost but lower recall — catches the obvious patterns ("thank you for applying", "phone screen", "unfortunately", etc.) and misses recruiter cold outreach with vague subjects. Useful when you're out of API credits.

Every email-driven change passes through the review queue. Nothing happens silently.

**Note on app passwords**: app password access is full-mailbox (read + send + delete), not the read-only OAuth scope you'd get if AutoApply went through a Google Cloud project. AutoApply only reads. Trade is ~2 minutes of setup vs. 1-3 months of Google Cloud verification.

## Resume optimization

Your resume lives as structured JSON (`profiles/yourname/resume.json`), not a PDF, so it can be edited and diffed programmatically. For each application, the optimizer:

- Picks the best projects from your project pool for that JD
- Extracts keywords from the JD and weaves them into your bullets
- Normalizes smart quotes / em-dashes / zero-width characters so ATS parsers don't choke
- Renders a one-page PDF via WeasyPrint
- Caches the result by `sha256(resume + JD)` — re-running on the same job is free

The optimizer prints a diff of every change before saving so you can see what changed and why.

## ATS support

| Feature | Greenhouse | Lever |
|---|---|---|
| Job discovery | ✓ | ✓ |
| Form filling | ✓ (incl. React Select dropdowns, date pickers) | ✓ |
| Resume upload | ✓ | ✓ |
| Custom questions | Canned responses + Claude fallback | Canned responses + Claude fallback |
| Cover letter | Auto-generated when required | ✓ |
| CAPTCHA | Pauses for manual solve | Pauses for manual solve |

Ashby, Workday, and other ATSes are planned but not in this version.

## Application flow

With `auto_submit: false` (default):
1. Playwright opens a visible browser.
2. Fills your tailored resume into the form.
3. Takes a screenshot for your records.
4. Pauses and asks: `Submit? (y/n/q)`.
5. Logs to `applications.json` with full status tracking.

With `auto_submit: true`: same flow, no pause. Rate-limited between submissions to avoid looking like a bot.

With `--dry-run`: fills forms, takes screenshots, never submits, never logs. Great for testing on a new company before trusting it.

## Cost

AutoApply uses the Claude API for the bits where smart language matters — resume tailoring, custom-question answers, fit scoring, inbox classification. Public APIs (Greenhouse, Lever) and your local IMAP server are free.

A rough estimate for applying to 10 jobs end-to-end: **$0.25 – $0.40 in Claude tokens**. Inbox sync of a year's email runs in single-digit cents. If you're out of credits, the dashboard's **Free mode** keeps inbox sync working with a regex-based classifier.

Set `ANTHROPIC_API_KEY` in `.env` once and you're done.

## Tech stack

- **Python** + **Claude API** (Anthropic) for the language-heavy work
- **Playwright** for form submission
- **WeasyPrint** for PDF rendering
- **FastAPI + HTMX + Sortable.js** for the dashboard
- **IMAP** (stdlib `imaplib`) for read-only inbox access

Implementation details (matcher algorithms, sync pipeline, rate limiting) live in [CLAUDE.md](./CLAUDE.md) if you want to dig into the architecture.

## Your data

Everything personal lives under `profiles/yourname/` and is gitignored. Nothing leaves your machine except API calls to Anthropic (resume tailoring, classification) and read calls to Greenhouse / Lever / your IMAP server.
