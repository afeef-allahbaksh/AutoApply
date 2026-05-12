# AutoApply

Apply to dozens of jobs in the time it takes to apply to one — without the slop. AutoApply discovers open roles at companies you care about, scores each one against your resume, tailors the resume per job, fills out the application, and tracks your interview pipeline on a local dashboard.

You stay in control: every submission pauses for your review in the dashboard, every inbox-driven status change goes through a manual approve/dismiss queue, and your personal data lives only on your machine.

## How it works

```
Profile → Discover Companies → Find Open Roles → Score Fit → Select Projects
       → Optimize Resume → Apply → Log → Track on Kanban
```

1. **You import your resume once** (PDF → structured form via Claude).
2. **AutoApply finds matching roles** at companies on Greenhouse / Lever using each company's public job board API.
3. **Every job gets a 1–5 fit score** before you spend any time on it. Low-fit jobs are flagged so you can skip them.
4. **Each application gets a tailored resume** — keywords from the JD woven into your bullets, projects swapped in from your project pool, exported as a clean one-page PDF.
5. **Playwright submits the application** for you, pausing on the dashboard for your review before each submit and any CAPTCHA.
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

# Launch the dashboard — it'll redirect you to the setup wizard
# the first time, then auto-open in your browser
python main.py
```

That's it. The dashboard opens at `http://127.0.0.1:8000`. Everything else happens in the UI: create your profile, import a resume, discover companies and jobs, optimize, apply, and review.

## Launcher flags

```bash
python main.py                          # launch dashboard, auto-open browser
python main.py --profile yourname       # activate a specific profile on launch
python main.py --port 8765              # use a different port
python main.py --no-browser             # skip auto-opening the browser
python main.py --host 0.0.0.0           # bind on the LAN (no auth — be careful)
```

For development, set `AUTOAPPLY_DEV=1` to enable uvicorn auto-reload. Background tasks get killed on file change, so leave it off when applying to real jobs.

## Using the dashboard

What you'll find in the sidebar:

- **Dashboard** — totals at a glance: applied / in pipeline / offers / rejections / response rate, plus your last 10 status changes and a "Run pipeline" button that chains discovery end-to-end.
- **Jobs** — a filterable view of matched jobs with fit scores. Per-row **Optimize** (tailor a resume for that job) and **Apply** (fill the form in a visible browser, pause for your review, submit) buttons. Check multiple rows and hit **Apply to selected** to run a batch in one browser session — the dashboard pauses on each form's review modal so you stay in control.
- **Applications** — a 6-column kanban board you drag cards across to update status. Manual add, inline edit, delete. Toggle between Kanban and Timeline views. If you've connected your inbox, proposed status changes from new email show up in a review queue above the board.
- **Cold email** — draft and send outreach to specific people at companies you care about. Manual or bulk-import contacts, Claude writes a tailored draft per person (the JD becomes the personalization context if you link a job), send directly through your connected inbox or open in Gmail. Bulk-generate drafts for a long list in one batched call. Per-contact email pattern suggestions when you type a name + company domain.
- **Companies** — list and add by slug; "Discover companies" seeds from a curated list.
- **Settings** — edit your profile, EEO responses, IMAP + SMTP inbox connection (one app password covers both), resume import, and project-pool growth. The Danger zone at the bottom deletes the active profile.

## Inbox sync (optional)

If you want the dashboard to notice when companies email you about interview invites, rejections, or offers, connect an IMAP-supporting inbox in Settings:

1. **Gmail**: enable 2FA, generate an app password at <https://myaccount.google.com/apppasswords>, paste it into the dashboard's Settings → Inbox sync (IMAP) card. Server / port default to `imap.gmail.com:993`.
2. **Other providers**: any IMAPS host works — Outlook, ProtonMail Bridge (`127.0.0.1:1143`), university mailboxes. Enter the server explicitly.
3. Click **Connect**. AutoApply does a real login + logout to verify; bad credentials are refused.

After connecting, hit **Sync inbox** on the Applications page. The button kicks off a background scan and proposals stream into a review queue as the classifier finishes them. You decide what to apply.

Two toggles next to the button:

- **Full history** — look back 5 years on first sync. Use it once to backfill; default incremental syncs cover what's new since the last run.
- **Free mode** — use a local regex classifier instead of the Claude API. Zero cost but lower recall — catches the obvious patterns ("thank you for applying", "phone screen", "unfortunately", etc.) and misses recruiter cold outreach with vague subjects. Useful when you're out of API credits.

Every email-driven change passes through the review queue. Nothing happens silently.

**Note on app passwords**: app password access is full-mailbox (read + send + delete), not the read-only OAuth scope you'd get if AutoApply went through a Google Cloud project. AutoApply only reads. Trade is ~2 minutes of setup vs. 1–3 months of Google Cloud verification.

## Resume optimization

Your resume is stored as structured data so it can be edited and diffed programmatically. For each application, the optimizer:

- Picks the best projects from your project pool for that JD
- Extracts keywords from the JD and weaves them into your bullets
- Normalizes smart quotes / em-dashes / zero-width characters so ATS parsers don't choke
- Renders a one-page PDF via WeasyPrint
- Caches the result so re-running on the same job is free

The optimizer prints a diff of every change before saving so you can see what changed and why.

## ATS support

| Feature | Greenhouse | Lever |
|---|---|---|
| Job discovery | ✓ | ✓ |
| Form filling | ✓ (incl. React Select dropdowns, date pickers) | ✓ |
| Resume upload | ✓ | ✓ |
| Custom questions | Canned responses + Claude fallback | Canned responses + Claude fallback |
| Cover letter | Auto-generated when required | ✓ |
| CAPTCHA | Pauses with a modal for manual solve | Pauses with a modal for manual solve |

Ashby, Workday, and other ATSes are planned but not in this version.

## Application flow

When you click **Apply** on a job row:

1. Playwright opens a visible browser.
2. AutoApply fills your tailored resume into the form.
3. Takes a screenshot — it appears on the dashboard.
4. Pauses, showing a **submit / skip / quit** modal on the dashboard page.
5. You click; the action runs.
6. Logs to your applications history with full status tracking.

If you toggle **Dry run** on the apply page, the flow is the same except step 4–5 are skipped — the form is filled and screenshotted, nothing is submitted, no application is logged. Great for testing on a new company before trusting it.

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

Implementation details (matcher algorithms, sync pipeline, rate limiting, task runner, prompt channel) live in [CLAUDE.md](./CLAUDE.md) if you want to dig into the architecture.

## Your data

Everything personal is stored locally and gitignored. Nothing leaves your machine except API calls to Anthropic (resume tailoring, classification) and read calls to Greenhouse / Lever / your IMAP server.

## Tests

```bash
pytest             # runs the full suite — 14 files, 93 tests, ~30s
pytest -v          # verbose, lists each test function
```

Tests live in `tests/` and are hermetic — they use `_test_*` profile names that can never collide with your real `profiles/` data, mock every external service (Claude API, SMTP, IMAP, Playwright), and `shutil.rmtree` themselves on teardown. See [tests/README.md](./tests/README.md) for the conventions.
