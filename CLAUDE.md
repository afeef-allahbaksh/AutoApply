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
- **UI module** — `src/ui/` is a FastAPI dashboard for tracking, not for applying. It reads `Profile`, calls `_save_applications`, `discover_jobs`, `_save_companies`, `validate_profile`, `validate_responses`, `expand_roles`, and `validate_slug` — no rewrites of business logic. Routes live under `src/ui/routes/`. State lives in `src/ui/state.py` (active profile env-var + per-profile `threading.Lock` registry; mutating routes acquire the lock, apply uses non-blocking acquire and returns 409 if held — kept available for future direct-apply use). Templates use HTMX for partial swaps and Sortable.js for kanban drag-and-drop. Design tokens (dark theme — deep canvas `#0d0e13`, raised `#16171d`, sunken `#07080b`, elevated `#1c1d24`; Geist Sans + Geist Mono; indigo accent `#818cf8` with violet-shifted wordmark gradient; smooth motion via cubic-bezier easing) live in `src/ui/templates/base.html`. Three-tier button hierarchy: `btn-primary` (indigo gradient + glow on hover), `btn-secondary` (indigo-tinted, less heavy), `btn-ghost` (dark elevated surface). `.btn-sm` modifier for compact inline actions
- **Inbox integration** — `src/inbox/` is a read-only IMAP scraper running as a background daemon thread per profile, streaming proposals to disk as it works. Pipeline: `fetch.list_message_headers_since` (cheap header-only bulk FETCH, ~500 UIDs/round-trip) → `sync._prefilter` (drops LinkedIn/Indeed/etc.) → `fetch.populate_bodies` (full RFC822 bulk FETCH on prefilter survivors only, ~50 UIDs/round-trip, with reconnect-on-failure retry) → `_classify_chunk` dispatches to `classify.classify_messages` (Haiku 4.5, batched, parallelism=2, sliding-window token-bucket throttle) OR `keyword_classify.classify_messages` (regex-only, no API calls — "free mode" for users without credits). Each chunk's classifier output flows to `matcher.match_application` which consults multiple signals in order: stored `email_thread_ids`, classifier-extracted company, sender domain stem (skipping ATS relays), subject prefix `Company - …`, and body scan against existing application company names. Proposals stream into `profiles/{name}/imap/proposals.json` per chunk; `state.json` tracks `last_sync_at` and `processed_message_ids` (capped at 10K) so re-syncs skip already-seen messages; `sync_status.json` carries live progress for UI polling. Three dedup guards: (a) message-id dedup at fetch time, (b) `new_application + match` proposals silently skipped (re-confirmations don't create duplicate entries), (c) `status_update` proposals that would be no-ops skipped. One fallback: `status_update + no_match` upgrades to `new_application` with the proposed status, so an orphan interview invite for an externally-applied job surfaces as "create new entry at screen" rather than getting dropped. Proposals are reviewed manually — **never auto-apply silently.** Apply mutates `applications.json` via `_save_applications` (schema validation runs), tagging entries with `source="email"` and appending the thread id to `email_thread_ids`. Credentials at `profiles/{name}/imap/credentials.json` as `{email, password, server, port}`
- **Background sync worker** — `inbox.sync.start_background_sync(profile_name, deep, classifier)` writes the initial `state="running"` status synchronously, then spawns a daemon thread keyed by profile in `_active_threads`. The thread runs `_sync_worker` which calls `_run_sync_streaming`, catching all exceptions and writing a terminal status (`idle` / `cancelled` / `error`) on exit. Status file writes are atomic (tmp + rename). UI polls `/email/sync_status` every 4s — the polling element renders without an `hx-trigger` when status is idle, so polling stops naturally. `read_sync_status` sanity-checks `state="running"` against `_active_threads` in this process; mismatch surfaces as `interrupted` so a uvicorn restart doesn't leave a zombie "running" badge. Cancel is cooperative: `request_cancel` flips a flag in `sync_status.json`, the worker checks at chunk boundaries (~6-10s latency)
- **Rate limiting** — `src.api.reserve_input_tokens(n)` is a sliding-window token bucket shared across threads. Calls block until the past-60s window has room for `n` more tokens. Default budget 45K input TPM (override with `AUTOAPPLY_INPUT_TPM_BUDGET`). Used by `classify_batch` to throttle the classifier under Anthropic Tier 1 limits without 429-and-retry-storm

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
- Inbox integration is read-only by design — `imaplib` only ever calls `select(readonly=True)` and never SMTP / `STORE` / `EXPUNGE`. Do not add write paths without an explicit ask
- Email-driven application changes always go through the review queue. Do not add silent auto-apply paths without an explicit ask, even for high-confidence matches
