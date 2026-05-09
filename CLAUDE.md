# CLAUDE.md — AutoApply

## Project
Fully automated job application pipeline. Seven stages in sequence:
Profile → Discover Companies → Find Open Roles → Score Fit → Select Projects → Optimize Resume → Apply → Log

Python backend, Playwright for browser automation, Claude API (via shared singleton client with retry) for resume optimization, project selection, and custom question answering. CLI is the primary surface; a local FastAPI dashboard (`python main.py --profile X ui`) provides tracking — NOT applying — for interview pipeline state.

## Architecture notes
- **Shared API client** — All Claude API calls go through `src/api.py` (`create_message()`) which provides a singleton `anthropic.Anthropic()` client with exponential backoff retry (3 attempts)
- **Parallel I/O** — Company discovery and job fetching use `ThreadPoolExecutor(max_workers=5)` for concurrent HTTP requests
- **Project pool** — Resume can have a `project_pool` array with all available projects. The optimizer picks the best N (matching current project count) per job to maintain one-page format
- **ATS form handling** — Greenhouse uses React Select comboboxes (click→type→pick option pattern) for degree, school, and some custom questions. Date fields use `input[type="number"]` with direct ID targeting (`#start-year--{i}`)
- **Resume naming** — Tailored resumes saved as `{name}_{company}.pdf` (e.g., `john_doe_anthropic.pdf`)
- **ATS normalization** — `resume_renderer.py` normalizes smart typography (curly quotes, em dashes, ellipses, zero-width chars) to plain ASCII before PDF rendering so ATS parsers can read keywords
- **Keyword extraction** — Resume optimizer extracts 10-15 JD keywords in a first pass, then weaves them into the resume. Response format: `{"keywords": [...], "resume": {...}}`
- **Job fit scoring** — After dedup, `score_jobs_fit()` uses a batched LLM call to rate each job 1-5 against the candidate's resume. Scores/rationales stored in `jobs.json` and propagated to `applications.json`
- **Optimization caching** — `save_tailored_resume()` stores a sha256 hash of (resume + JD). `find_cached_resume()` checks hash before calling the API, skipping redundant optimization calls
- **Batch project selection** — `batch_select_projects()` selects projects for multiple jobs in one LLM call. Used in pipeline and apply loops; `select_projects()` still used for single-job optimize command
- **Dry run mode** — `apply --dry-run` fills forms and takes screenshots but never submits or logs to applications.json
- **ATS detection** — companies.json stores detected ATS per company. Never re-detect on apply — trust what's in companies.json
- **Persistent browser state** — `apply_to_jobs` threads `profiles/{name}/browser_state.json` into Playwright via `storage_state`. Cookies + localStorage carry across runs, so previously-completed 2FA does not re-prompt
- **Application status lifecycle** — `applications.json` entries carry `status` (extended enum: applied, screen, technical, onsite, offer, rejected, failed, review_pending, skipped), `status_updated_at` (re-stamped on every change), `source` (`autoapply` | `manual` | `email`), and optional `email_thread_ids` (Gmail thread ids that informed the entry, used by the inbox matcher for thread-id pinning on subsequent emails)
- **UI module** — `src/ui/` is a FastAPI dashboard for tracking, not for applying. It reads `Profile`, calls `_save_applications`, `discover_jobs`, `_save_companies`, `validate_profile`, `validate_responses`, `expand_roles`, and `validate_slug` — no rewrites of business logic. Routes live under `src/ui/routes/`. State lives in `src/ui/state.py` (active profile env-var + per-profile `threading.Lock` registry; mutating routes acquire the lock, apply uses non-blocking acquire and returns 409 if held — kept available for future direct-apply use). Templates use HTMX for partial swaps and Sortable.js for kanban drag-and-drop. Design tokens (editorial mono — Fraunces+Inter, off-white canvas, hairline rules, forest-green accent, no gradients/glass) live in `src/ui/templates/base.html`
- **Inbox integration** — `src/inbox/` is a read-only Gmail scraper. Pipeline: `fetch.list_messages_since(creds, since_dt)` → `sync._prefilter` (drops LinkedIn/Indeed/etc.) → `classify.classify_messages` (batched Claude call, returns `new_application` / `status_update` / `ignore` plus company, role, status, confidence) → `matcher.match_application` (thread-id wins; fuzzy company; narrow by role tokens). Results land as proposals in `profiles/{name}/gmail/proposals.json`; `state.json` tracks `last_sync_at` and `processed_message_ids` so re-syncs skip already-seen messages. Proposals are reviewed manually — the user clicks Apply/Dismiss in the Applications-page review queue. **Never auto-apply silently.** Apply mutates `applications.json` via `_save_applications` (so schema validation runs), tagging entries with `source="email"` and appending the thread id to `email_thread_ids`. OAuth credentials live in `profiles/{name}/gmail/credentials.json` (user-uploaded GCP OAuth client, Desktop app type) and `token.json` (refresh token, written after first consent)

## Planning
- Enter plan mode for any non-trivial task (3+ steps or architectural decisions)
- Write plans to `tasks/todo.md` with checkable items before coding
- Check in with user before implementing new plans
- Stop and re-plan if things go sideways

## Execution
- Prioritize simplicity — make changes as small as possible
- No temporary fixes — find root causes (senior developer standards)
- For non-trivial changes, pause and ask if there's a more elegant solution
- Skip elegance checks for simple, obvious fixes
- Everything user-facing goes through the CLI — no manual file editing required

## Verification
- Never mark tasks complete without proving they work
- Run the code, check logs, demonstrate correct behavior
- Ask: "Would a staff engineer approve this PR?"

## Bug Fixing
- When given a bug report: just fix it with logs or failing behavior, no hand-holding needed

## Self-Improvement
- After user corrections: update `tasks/lessons.md` with the pattern and prevention rules
- Review `tasks/lessons.md` at the start of each session
- Keep tasks/lessons.md under 30 items — when it exceeds 30, consolidate related lessons into higher-level principles before adding new ones

## Subagents
- Use for research, exploration, and parallel analysis to keep main context clean
- One focused task per subagent

## Key Rules
- `profiles/` is gitignored — never commit personal data
- Follow the priority build order in `project-context.md` exactly
- Greenhouse + Lever only in v1 — no Ashby, no Workday, no Crunchbase
- CLI is the apply surface; the FastAPI dashboard is the tracking surface — do NOT add Playwright apply triggers to the UI
- Everything should be configurable through CLI commands and the UI Settings page — users should never need to manually edit JSON files
- All commands that need file paths (import-resume, add-projects) prompt interactively if --pdf is omitted
- Resume stored as structured JSON, rendered to PDF — never edit PDFs directly
- Deduplication by composite key (company + role + posting URL)
- Rate limit Playwright submissions — respect `rate_limit_seconds`
- The dashboard is bound to 127.0.0.1 with no auth — single-user, single-machine assumption. Do not add network exposure or auth without an explicit ask
- Gmail integration is read-only by design (`gmail.readonly` scope). Do not request additional Gmail scopes (send, modify, settings) without an explicit ask
- Email-driven application changes always go through the review queue. Do not add silent auto-apply paths without an explicit ask, even for high-confidence matches
