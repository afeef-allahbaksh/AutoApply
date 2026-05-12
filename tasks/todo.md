# AutoApply — Build Plan

## Phase 1: Profiles System
- [x] Create project structure (`src/`, `profiles/`, `tasks/`, `config/`)
- [x] Add `profiles/` to `.gitignore`
- [x] Define and validate `profile.json` schema (personal info, job_preferences, settings)
- [x] Define and validate `responses.json` schema (canned ATS answers)
- [x] Define `applications.json` schema (with composite key: company + role + posting URL)
- [x] Build profile loader — reads/validates a profile by name from `profiles/{name}/`
- [x] Build CLI entry point (`main.py --profile <name>`) that loads the active profile
- [x] Create a sample/template profile for testing (not real data — in `config/example_profile/`)

## Phase 2: Company Discovery Agent
- [x] **Research first (subagent):** Verify Greenhouse and Lever public board indexes are actually scrapeable. Document exact URL structure, response format, rate limits, and any gotchas in `tasks/research.md`. Do this before writing any scraper code.
- [x] Research Greenhouse public board index — confirmed: no master index, per-company API at `boards-api.greenhouse.io/v1/boards/{slug}/jobs`, no auth, no server-side filtering
- [x] Research Lever public board index — confirmed: no master index, per-company API at `api.lever.co/v0/postings/{slug}`, no auth, supports server-side filters
- [x] Seed initial company slugs — 11 verified slugs in `config/seed_companies.json`
- [x] Build Greenhouse slug validator — hit API, confirm slug is valid, extract company name
- [x] Build Lever slug validator — hit API, confirm slug is valid, extract company name
- [x] ATS detection by URL signature (`boards.greenhouse.io/X` → Greenhouse, `jobs.lever.co/X` → Lever)
- [x] Define `companies.json` schema (name, ats, slug, careers_url, added date)
- [x] Write discovered companies to `companies.json` (merge with existing, don't overwrite manual entries)
- [x] Support manual additions — users can hand-edit `companies.json` and it won't be clobbered
- [x] Add CLI command: `python main.py --profile {profile} discover`

## Phase 3: Job Discovery
- [x] Build Greenhouse jobs API client (`boards-api.greenhouse.io/v1/boards/{slug}/jobs`)
- [x] Build Lever jobs API client (`api.lever.co/v0/postings/{slug}`)
- [x] Filter jobs by profile preferences (roles, experience levels, locations)
- [x] Keyword matching / relevance scoring against `job_preferences.roles`
- [x] Deduplication — skip jobs already in `applications.json`
- [x] Store discovered jobs in a working file (`jobs.json` or similar) for the next stage
- [x] Add CLI command: `python main.py --profile {profile} discover-jobs`

## Phase 4: Resume Optimizer
- [x] Define `resume.json` schema (dynamic: section_order controls layout, all sections optional except contact)
- [x] Build PDF resume parser — import existing PDF into `resume.json` via Claude API (handles any layout)
- [x] Build PDF renderer — `resume.json` → polished PDF via weasyprint + HTML/CSS template
- [x] Claude API integration — given base `resume.json` + job description, return tailored version
- [x] Prompt engineering — mirror JD language, surface keywords, rewrite bullets, preserve metrics and voice
- [x] Diff view — show what changed between base and tailored resume before proceeding
- [x] Save tailored resume per application (JSON + PDF to `profiles/{name}/resumes/`)
- [x] Add CLI command: `python main.py --profile {profile} optimize --job <index>`

## Phase 5: Playwright ATS Handlers
- [x] Set up Playwright with persistent browser context (cookies, sessions)
- [x] Build Greenhouse form handler — navigate to application page, identify fields, fill them
- [x] Build Lever form handler — same for Lever's application flow
- [x] Map `profile.json` fields to standard ATS fields (name, email, phone, LinkedIn, etc.)
- [x] Map `responses.json` to common dropdown/radio questions (work auth, visa, demographics)
- [x] Claude API for custom free-text questions — use JD + profile context to generate answers
- [x] Resume upload — attach the tailored PDF to the application
- [x] Cover letter generation (if required by the form) — Claude API with JD + profile context

## Phase 6: Auto-Submit Toggle + Rate Limiting
- [x] Implement pause-for-review flow (`auto_submit: false`) — fill form, screenshot, prompt y/n/q
- [x] Implement auto-submit flow (`auto_submit: true`) — fill and submit without pausing
- [x] Rate limiting between submissions — respect `rate_limit_seconds` from profile settings
- [x] Add configurable delay randomization to appear more human (0.5x-1.5x jitter)

## Phase 7: Application History Logging
- [x] Log every application to `applications.json` (company, role, URL, date, status, tailored resume path)
- [x] Deduplication check before applying — composite key (company + role + posting URL)
- [x] Status tracking: `applied`, `failed`, `review_pending`, `skipped`
- [x] Summary command: `python main.py --profile {profile} history`

## Phase 8: Error Recovery
- [x] Save Playwright progress state per application (fields filled, custom answers → `progress/`)
- [x] Retry failed applications — re-run `apply` on failed jobs (dedup skips successful ones)
- [x] Graceful handling of CAPTCHAs — detect, pause, prompt user to solve, then continue
- [x] Timeout handling for slow-loading ATS pages (configurable timeouts on goto + wait_for_selector)
- [x] Screenshot on failure for debugging (saved to `screenshots/`)

## Phase 9: Infrastructure & Optimizations
- [x] Shared Anthropic API client singleton (`src/api.py`) replacing 7 separate instantiations
- [x] Exponential backoff retry (3 attempts) for all Claude API calls
- [x] Parallelized company discovery with `ThreadPoolExecutor(max_workers=5)`
- [x] Parallelized job fetching with `ThreadPoolExecutor(max_workers=5)`
- [x] Consolidated `PROFILES_DIR` — defined once in `profile_loader.py`, imported everywhere
- [x] Fixed inline imports (removed redundant `import re` / `import json` in loops)
- [x] Fixed resume renderer — removed unused location variable in education
- [x] Fixed resume diff — handles new skill categories and unequal bullet counts
- [x] Fixed setup salary parsing — handles commas and floats

## Phase 10: Project Pool & Smart Project Selection
- [x] Added `project_pool` to resume schema (optional array, same structure as `projects`)
- [x] `select_projects()` — Claude picks the N most relevant projects per job from the pool
- [x] Project selection reasoning in diff output (selected/skipped with one-sentence reason per project)
- [x] Setup wizard prompts to add projects from extra resume PDFs
- [x] CLI command `add-projects --pdf <path>` for adding projects after setup
- [x] Pipeline and optimize commands integrate project selection before optimization
- [x] Resume naming: `{name}_{company}.pdf` with applicant lookup fallback to old naming

## Phase 11: Greenhouse Form Handling Fixes
- [x] React Select combobox handling for degree, school, custom questions (click→type→pick option)
- [x] Direct ID targeting for date fields (`#start-year--{i}`, `#end-year--{i}`)
- [x] `_try_select_date` detects `<input>` vs `<select>` and uses `fill()` vs `select_option()`
- [x] Custom question combobox branch (checks `role="combobox"` before `tag in ("input", "textarea")`)
- [x] Optional URL fields (website, portfolio) skipped when user has no data
- [x] Yes/no confirmation questions return only "Yes" or "No"
- [x] CAPTCHA retry fixed — retries on current page instead of re-navigating

## Phase 12: CLI Management Commands (No Manual JSON Editing)
- [x] `add-company` — Add a company by slug with ATS auto-detection
- [x] `update-settings` — Toggle auto_submit, change rate_limit_seconds
- [x] `update-responses` — Re-enter canned ATS answers (EEO, visa, work auth)
- [x] `update-preferences` — Change target roles (with re-expansion), locations, salary, industries

## Phase 13: Job Fit Scoring, ATS Normalization & Keyword Optimization
- [x] ATS Unicode normalization — `normalize_ats_text()` replaces smart typography with ASCII before PDF render
- [x] Recursive `_normalize_resume_data()` walker applied at top of `render_resume_html()`
- [x] Keyword extraction — optimizer prompt restructured into two phases (extract keywords, then optimize)
- [x] Response format `{"keywords": [...], "resume": {...}}` with old-format fallback
- [x] `score_jobs_fit()` — batched LLM call scoring jobs 1-5 against candidate resume
- [x] Fit scoring integrated into `discover_jobs()` after dedup, before save
- [x] Display updates in `main.py` and `pipeline.py` — fit score, rationale, `[!]` warning for low-fit
- [x] Schema update — `fit_score` and `fit_rationale` added to `applications_schema.json`
- [x] Application logging — fit data propagated to both success and failure entries in `applicant.py`

## Phase 14: Caching, Batching & Performance
- [x] Resume optimization caching — sha256 hash of (resume + JD) skips redundant API calls
- [x] `find_cached_resume()` integrated in optimize, apply, and pipeline commands
- [x] `batch_select_projects()` — single LLM call for multi-job project selection
- [x] Batched selection in pipeline.py and applicant.py apply loop
- [x] Dry run mode — `apply --dry-run` fills forms without submitting or logging
- [x] `--dry-run` CLI flag with separate summary output
- [x] Pre-compiled HTML tag regex (`_HTML_TAG_RE`) in job_discovery.py
- [x] CSS cached at module level in resume_renderer.py (read once per process)
- [x] `validate_resume()` moved outside apply loop (validate once, not per-job)
- [x] `_slugify` import moved to top-level in applicant.py
- [x] Lever content enrichment — `lists` and `commitment` fields added to job content
- [x] Jobs sorted by (fit_score, relevance_score) after scoring
- [x] Skip weak-fit jobs prompt in pipeline mode
- [x] Fit score column in history command
- [x] Removed unused imports (os, date from resume_optimizer; Path from job_discovery)

## Phase 15: Local FastAPI Dashboard
- [x] FastAPI skeleton with sidebar layout, profile switcher, and design tokens (`src/ui/`)
- [x] Initial design system shipped — has since iterated through several aesthetic passes (see Phase 22 for current dark theme)
- [x] Dashboard page with metrics and 10s HTMX polling (`/_metrics`)
- [x] Applications kanban with 6 columns (applied / screen / technical / onsite / offer / rejected)
- [x] Drag-and-drop between columns via Sortable.js — optimistic UI with PATCH on drop
- [x] Hover-revealed edit/delete actions on cards; manual add inline form
- [x] Jobs page with filter form, refresh button, per-row Track button (creates a manual application entry — never submits via Playwright)
- [x] Companies page with auto-detect ATS add form
- [x] Settings page editing `profile.json` and `responses.json` with validation, plus optional Claude role expansion
- [x] Cold email v2 placeholder reserved in sidebar
- [x] `python main.py --profile {name} ui [--port 8000]` launcher

## Phase 16: Persistent Browser Session
- [x] `get_browser_context` accepts `storage_state_path` parameter (`src/browser.py`)
- [x] `apply_to_jobs` saves `profiles/{name}/browser_state.json` on completion so 2FA cookies carry across runs

## Phase 17: Application Status Lifecycle Extension
- [x] Extend `applications.json` status enum with interview-pipeline values (`screen`, `technical`, `onsite`, `offer`, `rejected`)
- [x] Add `notes`, `status_updated_at` (re-stamped on every status change), and `source` (`autoapply` | `manual` | `email`) fields
- [x] Add `email_thread_ids` array linking entries to inbox threads
- [x] Update every `applications.append({...})` site in `applicant.py` to stamp `status_updated_at` + `source`

## Phase 18: Inbox Integration via IMAP (Read-Only)
- [x] Per-profile IMAP credential store with login verification on save (`src/inbox/auth.py`)
- [x] Settings page Inbox card — email + app password + server/port + Connect / Disconnect (replaces earlier OAuth flow for distribution-friendliness)
- [x] `imaplib`-backed message fetcher with body extraction and Message-ID threading via References / In-Reply-To headers (`src/inbox/fetch.py`)
- [x] Heuristic prefilter — skip LinkedIn/Indeed/newsletter senders and noise subjects
- [x] Claude classifier — batched (10/call) classification into `new_application` / `status_update` / `ignore`
- [x] Application matcher — thread-id pin first, then fuzzy company + role tokens
- [x] Sync orchestrator with `processed_message_ids` cap (5000) and proposals.json persistence
- [x] Review queue panel above kanban with Apply / Dismiss / View buttons
- [x] Apply for `new_application` creates entry with `source="email"` and links thread id
- [x] Apply for `status_update` moves matched card and appends thread id

## Phase 19: Streaming Sync Pipeline + Performance
- [x] Background daemon thread per profile for non-blocking sync (`start_background_sync` → spawns worker, returns immediately)
- [x] UI polls `/email/sync_status` every 4s with auto-stop when state flips to idle
- [x] Two-phase IMAP fetch — headers via bulk FETCH chunks of 500 (no body cost), bodies only for prefilter survivors via chunks of 50
- [x] Parallel Claude classification via `ThreadPoolExecutor(max_workers=2)`
- [x] Sliding-window input-TPM token bucket (`src/api.py:reserve_input_tokens`) — default 45K/min, env override
- [x] Atomic `sync_status.json` writes via tmp + rename
- [x] Stuck-state detection — `read_sync_status` surfaces `interrupted` when the file says running but no live worker exists in this process
- [x] Cooperative cancel button — flag in status file, worker checks at chunk boundaries
- [x] Body fetch retry with reconnect on IMAP connection drop
- [x] Switch classifier model from Sonnet to Haiku 4.5 (3× higher TPM ceiling, ~4× cheaper, same accuracy on the binary classification task)

## Phase 20: Free Mode Keyword Classifier
- [x] `src/inbox/keyword_classify.py` — pure-regex classifier with same return shape as LLM classifier
- [x] Status detection via priority-ordered patterns (offer > rejected > onsite > technical > screen > applied)
- [x] ATS sender domain heuristic — `*.greenhouse.io`, `*.lever.co`, etc. tagged as "applied" when no keyword fires
- [x] Company extraction cascade: known applications → sender domain (skipping ATS relays) → sender display name
- [x] Role extraction via regex patterns ("for the X position")
- [x] IMAP server-side keyword filter applied in free mode so non-application mail never downloads
- [x] UI: "Free mode" checkbox next to Sync inbox button

## Phase 21: Matcher Hardening + Dedup
- [x] Matcher pulls company candidates from multiple sources: classifier output, sender domain, subject prefix, body scan against existing applications
- [x] ATS relay stems (`greenhouse`, `lever`, `avature`, `myworkdayjobs`, etc.) excluded from sender-domain matching
- [x] `status_update + no_match` upgraded to `new_application` so orphan interview emails surface a "create entry" proposal instead of getting dropped
- [x] `new_application + match/ambiguous` silently skipped — re-confirmations don't create duplicates
- [x] `status_update + match` skipped when proposed status equals existing (no-op guard for re-syncs)

## Phase 22: UI Iteration to Dark Modern Theme
- [x] Multiple design passes — editorial mono → vercel-clean → light gray with depth → dark theme
- [x] Final dark theme: canvas `#0d0e13`, raised `#16171d`, sunken `#07080b`, elevated `#1c1d24`
- [x] Indigo accent `#818cf8` with violet-shifted wordmark gradient
- [x] Smooth motion across all interactive elements (cubic-bezier easing, hover lift, drag rotation)
- [x] Three-tier button hierarchy: `btn-primary` (indigo gradient + glow), `btn-secondary` (indigo-tinted), `btn-ghost` (dark elevated surface)
- [x] `.btn-sm` modifier for compact inline actions
- [x] `header-checkbox` class with `accent-color: var(--accent)` so checkboxes match the indigo palette

## Phase 23: Shared Task Runner + Interactive Prompt Channel (Foundation)
*Survey: `src/inbox/sync.py` already provides status_path / read_sync_status / request_cancel / start_background_sync / thread registry. Pattern is inbox-specific; generalize rather than copy. `src/ui/state.py` has profile_lock + active_profile env var. No existing prompt channel beyond inbox proposals queue.*

- [x] Extract generic background-task module into `src/tasks/runner.py` — status file path, atomic tmp+rename writes, cancel flag, per-profile thread registry, stuck-state detection (`interrupted` when status says running but no live worker)
- [x] Migrate `src/inbox/sync.py` to use the shared runner — preserve existing UI polling cadence, cancel button behavior, and `read_sync_status` semantics
- [x] Build `src/tasks/prompt.py` — interactive prompt channel: worker writes `pending_prompt` (id/question/choices/screenshot_path/extra) to the task's status file via `ask()` and blocks polling for `prompt_response`; UI helpers `get_pending()` + `submit_response()` drive the round trip. Cancel and timeout both clear `pending_prompt` and raise `PromptCancelled` / `PromptTimeout`. 8/8 smoke tests pass
- [ ] Generic "task needs your input" modal partial + FastAPI route — **deferred to Phase 27** when apply becomes the first real consumer (no speculative UI without a working end-to-end demo)
- [x] Update CLAUDE.md — removed "CLI is the apply surface" Key Rule; flipped Project section to UI-first with migration framing; rewrote Background-tasks Architecture note to reference `src/tasks/runner.py`
- [x] Update lessons.md — rewrote lesson #8 to scope it to inbox-driven changes only (the apply-stays-in-CLI part is replaced by the prompt-channel architecture in CLAUDE.md)

## Phase 24: Migrate Read-Only / Compute Commands (Low Risk)
- [x] `discover` (companies) — "Discover companies" button in Companies header → runs as background task via `src/tasks/runner.py` → streams progress to `profiles/{name}/discover_status.json` → UI polls `/companies/discover_status` every 4s; cancel + interrupted detection wired. `discover_companies()` gained optional `on_progress` and `cancel_check` callbacks; CLI signature is backwards-compatible (still called as `discover_companies(args.profile)` in main.py). 4/4 smoke tests pass
- [x] `discover-jobs` background-task migration — `/jobs/refresh` was already calling `discover_jobs()` but synchronously, blocking the HTTP request for 30s–2min. Now it spawns a background task via `runner.start_task`, returns a `#jobs-content` partial with a streaming banner, and polls `/jobs/discover_status` every 4s. Phases reported: loading_companies → fetching (per-company) → filtering → classifying_country → classifying_level → dedup → scoring_fit → saving. Cancel-before-save leaves jobs.json untouched. `discover_jobs()` gained optional `on_progress` / `cancel_check` callbacks; CLI signature backwards-compatible. 4/4 smoke tests pass
- [x] `status` — added a Profile summary card to the dashboard via `_profile_summary_for(profile_name)`. Shows name, target roles, locations, experience level, auto-submit toggle, and workspace counts (companies/jobs/applications). Lives inside the `/_metrics` polling block so settings changes from another tab reflect within 10s. CLI status parity reached (CLI prints 5 fields; dashboard now shows all 5 plus useful workspace counts)
- [x] `history` — added Kanban/Timeline view toggle to `/applications` via `?view=` query param. Timeline is a read-only chronological table sorted by `status_updated_at` desc (falls back to `date`), with the same company/role search filter shared between views. `+ Add manual` button hidden in timeline view; manual edits/drag-and-drop only available in Kanban. Extracted `_enrich_and_filter` + `_sort_key` helpers in `src/ui/pipeline.py` so kanban_groups and the new `timeline_rows` share filtering logic

## Phase 25: Long-Running Compute (Optimize + Pipeline)
- [x] `optimize --job N` — per-row "Optimize" link on Jobs page → dedicated `/jobs/{idx}/optimize` page → background task via `src/tasks/runner.py`. Phases: loading → selecting_projects → cache_check → optimizing → saving → complete. One optimize at a time per profile (task key `optimize:{profile}`); the status file tracks `job_idx` so per-job pages differentiate "running for this job" / "running for another job" / "this job has a result". Diff displayed in a `<pre>` block; tailored PDF served via `/jobs/{idx}/optimize/pdf`. Re-run button works. CLI optimize signature untouched. 5/5 smoke tests pass
- [x] `run` (full pipeline) — Dashboard "Run pipeline" header button → `POST /pipeline/start` chains `discover_companies` then `discover_jobs` in one shared `runner.start_task`. Single status file at `profiles/{name}/pipeline_status.json` with a `stage` field (`discover` / `discover_jobs` / `complete`) so the banner reports current step. Per-job optimize and apply remain individual `/jobs` row actions — the CLI's interactive multi-job pick + batch optimize/apply is deferred (lives in the per-row UI workflow instead). Cancel between stages tested. 4/4 smoke tests pass

## Phase 26: File Upload Commands
- [x] `import-resume` — Settings page "Resume" card with file upload → `POST /settings/resume/import` → `_import_resume_from_bytes` → existing `parse_pdf_to_resume()` → writes `resume.json`. 10MB cap, `.pdf`-only validation. Button label flips to "Re-import (overwrites)" when a resume already exists
- [x] `add-projects` — adjacent file upload on same card → `POST /settings/resume/projects/add` → parses extra PDF and merges its projects into `project_pool` via name-dedup. Skips duplicates; reports added/skipped counts. Button disabled until full resume is imported. Both routes are foreground (parsing takes 10–30s with Claude); helpers `_import_resume_from_bytes` / `_add_projects_from_bytes` are pure-function so they unit-test without mocking `UploadFile`. 7/7 smoke tests pass

## Phase 27: Apply (Uses Prompt Channel from Phase 23)
- [x] `apply` and `apply --dry-run` from UI — `/jobs/{idx}/apply` page with Start button + Dry-run checkbox. Single job at a time (task key `apply:{profile}`, mirrors optimize). Phases reported: loading → dedup_check → opening_browser → tailoring_resume → filling_form → screenshot_ready → awaiting_submit → submitting → complete
- [x] Submit confirmation modal — `submit_handler` callback wired through `src/tasks/prompt.py`. When the worker hits the decision point it calls `prompt.ask(sf, "Form filled. Review…", choices=["submit", "skip", "quit"])` and blocks. UI's `_apply_main.html` renders the modal with buttons that POST to `/jobs/{idx}/apply/prompt`. Quit response writes `status=review_pending` and exits (same as CLI behavior)
- [x] CAPTCHA pause modal — `captcha_handler` callback wired through the same prompt channel. Worker pauses on `prompt.ask(sf, "CAPTCHA detected…", choices=["continue", "skip"])` when fill_result detects "captcha"/"recaptcha"/"hcaptcha" in the page text
- [x] Live screenshot display — `_take_screenshot` path streams into the status file via `progress_callback(screenshot_path=...)`. UI renders `<img src="/jobs/{idx}/apply/screenshot">` whenever the status holds a screenshot
- [x] Dedup short-circuit — duplicate check (composite key, URL-normalized) runs before the browser opens; duplicates produce `result_status="skipped"` and `phase="skipped_duplicate"` without touching Playwright
- [x] Refactored `src/applicant._process_job` to accept optional `captcha_handler`, `submit_handler`, `progress_callback` kwargs. Default `_default_captcha_handler` and `_default_submit_handler` fall back to `input()` so CLI behavior is unchanged. 7/7 smoke tests pass
- [ ] **Multi-job batch apply** — deferred to v2+. UI currently applies one job at a time (matches the optimize pattern and the review-each workflow). The CLI's "apply to N jobs in a loop" mode stays CLI-only for now
- [ ] **Dupe-overwrite confirmation modal** — deferred to v2+. UI just skips duplicates today (same as CLI); explicit overwrite prompt can come later if needed
- [ ] **Rate-limit countdown in UI** — deferred to v2+. Only relevant for multi-job batch mode

## Phase 28: Setup Wizard
- [x] Profile **delete** via Settings → Danger zone (`POST /profile/delete`). Requires typing the profile name into a confirm field (double-entry guard so a stray POST/replay can't nuke the wrong profile) + a JS `confirm()` on submit. Path-traversal guard via `Path.resolve()`. After delete, unsets `AUTOAPPLY_PROFILE` and bounces to `/`, which then either picks another profile or redirects to `/setup` if none remain. 5/5 smoke tests pass
- [x] Single-page `/setup` form (chose this over a multi-step wizard because the CLI's prompts have no meaningful Next/Back and progressive sections cover the same UX in one screen). Sections: Profile identity (slug, name, email, phone, location, optional LinkedIn/GitHub) · Job preferences (roles + optional Claude expansion, levels, locations, optional salary/industries) · Application settings (auto_submit, rate_limit) · EEO responses (6 optional fields)
- [x] First-visit redirect: `GET /` bounces to `/setup` when `state.active_profile()` returns empty OR the active profile's `profile.json` doesn't exist
- [x] Profile slug munging: lowercase, spaces → underscores, strip punctuation (mirrors CLI's `name.replace(" ", "_").lower()`)
- [x] Role expansion via Claude is opt-in via a checkbox (default ON to match CLI behavior)
- [x] Resume import added to setup as an optional section with a `strongly recommended` badge — accepts `.pdf` via the form's new `multipart/form-data` enctype, parses synchronously via the Phase 26 helper (`_import_resume_from_bytes`). Skipping it is allowed but the dashboard then renders a warning banner ("No resume on file. Fit scoring is disabled until you import one…") that links straight to Settings. IMAP setup remains on Settings (already migrated in Phase 18)
- [x] EEO responses are optional in setup; only non-empty values persisted, bad shapes silently skipped (user can fix on Settings)
- [x] 7/7 smoke tests pass (slugify, GET /setup, POST happy path no-expand, POST with expand calls expand_roles, empty slug rejected, blank-after-CSV-split rejected, no-profile redirect)

## Phase 29: Retire CLI Surface
- [x] `main.py` rewritten as a 104-line launcher (down from 519). Flags: `--profile`, `--port`, `--host`, `--no-browser`. Env: `AUTOAPPLY_PROFILE`, `AUTOAPPLY_DEV=1` for uvicorn auto-reload. Auto-opens the dashboard in the user's browser (delayed 1s so uvicorn binds first)
- [x] All `argparse` subcommands removed — there's no positional `command` argument anymore
- [x] **Deleted** `src/setup.py` and `src/pipeline.py` (CLI-only modules)
- [x] **Deleted** `apply_to_jobs` from `src/applicant.py` (~100 lines) + the stdin-backed default handlers (`_default_captcha_handler`, `_default_submit_handler`, ~20 lines). `_process_job` now requires `captcha_handler` and `submit_handler` kwargs so a stray caller can't silently fall back to terminal prompts
- [x] Dead imports purged from `src/applicant.py` (`batch_select_projects`, `get_browser_context`, `Profile`, `validate_resume` no longer needed there)
- [x] README rewritten — quick-start is now just `python main.py`; CLI command table replaced with a dashboard walkthrough; launcher flags documented
- [x] CLAUDE.md updated — Project section flipped to "dashboard is the user surface"; UI module description no longer says "originally tracking-only"; the apply Key Rule rewritten around `_process_job`'s required handler kwargs; "Everything goes through CLI" rule rewritten to "through the dashboard"
- [x] All 6 routes load against the live profile; main.py imports cleanly; `src.setup` and `src.pipeline` confirmed unimportable

## Phase 30: Cold Outreach v2.1 — Manual Compose + Claude Drafts
- [x] `src/cold_email.py` — `generate_outreach(profile, resume, company, contact_name, contact_title, context_notes, job_content)` → `{subject, body}`. Claude-tuned to 3-paragraph / ≤120-word body, scannable subject (≤8 words). JSON-shaped output so future v2.2 bulk path can call it identically. Hard fallback to a hand-written skeleton if Claude returns garbage — user always has something editable
- [x] `src/ui/routes/cold_email.py` rewritten — CRUD routes (`POST /cold-email`, `POST /cold-email/{id}/generate|edit|status|delete`). Per-profile `outreach.json` with `id` (12-char uuid), company, contact name/email/title, context_notes, draft_subject, draft_body, status enum (`draft`/`sent`/`replied`/`no_reply`/`closed`), timestamps, `sent_at` stamped first time status flips to sent
- [x] Gmail compose URL builder (`_gmail_compose_url`) — opens `mail.google.com/mail/?view=cm` prefilled with To/Subject/Body. User clicks Send themselves. **No SMTP in v2.1** (matches the read-only inbox stance for now; SMTP needs explicit opt-in)
- [x] UI: `cold_email.html` + `_cold_email_main.html`. Header-bar `+ New outreach` toggles a create form (company input has a `<datalist>` autocomplete from `companies.json`). Records render as `<details>` cards — collapsed by default, expanded after generate/edit. In-card: contact details editor + subject/body textareas + `Save` / `✨ Generate` / `Open in Gmail ↗` / status dropdown / `Delete`
- [x] Sidebar `v2` tag dropped from the Cold email nav entry (no longer a stub)
- [x] **Direct SMTP send** (v2.5 shipped early — Gmail/IMAP-link UX was friction the user didn't want to live with). New `src/email_send.py` reuses the IMAP credentials stored in `profiles/{name}/imap/credentials.json` (same app password works for Gmail/Outlook/Yahoo SMTP). SMTP host derived from IMAP host (`imap.gmail.com` → `smtp.gmail.com`), port 587 STARTTLS. New `POST /cold-email/{rid}/send` route validates draft fields, calls send_email, flips status to `sent` + stamps `sent_at` on success; leaves record in draft state with inline error on failure. UI primary action is now "Send email" (with JS confirm showing recipient + sender's inbox address); "or open in Gmail" demoted to a small secondary link as a fallback for attachments / different-account sends. If inbox isn't connected, the button reads "Connect inbox to send →" and links to Settings instead. 7/7 smoke tests pass (host derivation, validation, no-inbox path, happy path with STARTTLS, auth failure messaging, route success updates status, route failure preserves state)
- [x] **Job linking wired** — outreach records can now reference a job from `jobs.json` via `linked_job_idx`. Generate route fetches the linked job's content and passes it to the Claude generator (the JD becomes the primary personalization signal). Create + edit forms expose a `<select>` with `<optgroup>`s grouped by company. Invalid `linked_job_idx` values are rejected before save. Linked job label (`Senior SWE at Acme`) shown on the collapsed record card. 7/7 sub-checks pass (grouped lookup, persist as int, generate threads job_content, invalid-idx rejected, edit can clear, edit can switch, card label visible)
- [x] 8/8 smoke tests pass (`/tmp/test_cold_email.py`): route registration, create+persist, generate threads inputs through to Claude stub, manual edit preserves content, status→sent stamps `sent_at` (and preserves it on subsequent transitions), Gmail URL URL-encodes special chars, delete removes from disk, generator fallback fires on bad Claude response

## Phase 31: Code Review Pass (Post-Phase-30 Cleanup)
- [x] **Fixed broken exception tuples** — `src/cold_email.py:117,239` had `except (json.JSONDecodeError, …, Exception)` which collapses to `except Exception:` (the catch-all subsumes the specifics, defeating intent and swallowing bugs). Replaced with narrow specific tuples
- [x] **Extracted `strip_code_fences()`** to `src/api.py` — the inline pattern `if raw.startswith("\`\`\`"): raw = raw.split("\\n", 1)[1]; raw = raw.rsplit("\`\`\`", 1)[0]` was duplicated in **11 sites across 6 files** and silently crashed with IndexError when Claude returned a fence with no newline after the opener. Helper handles every edge case (no trailing fence, only-whitespace inside, ` ```json ` language tags, etc.)
- [x] **Consolidated `_load_jobs`** — was defined identically in 4 route files (`jobs.py`, `optimize.py`, `apply.py`, `cold_email.py`). Moved to `src/ui/pipeline.py` as `load_jobs()` alongside `load_applications()`. Each route keeps a thin `_load_jobs = load_jobs` alias so call sites don't churn
- [x] **Deleted `batch_select_projects`** in `src/resume_optimizer.py` (~95 lines including prompt) — was only called by the retired CLI batch-apply path. Replaced with a 6-line tombstone comment pointing future work at `batch_generate_outreach`'s pattern
- [x] **Improved worker error logging** in `src/tasks/runner.py` — top-of-thread `except Exception` now `traceback.print_exc(file=sys.stderr)` for the full stack; the status file stays truncated to `str(e)[:240]` for the UI banner
- [x] **Documentation sweep** — CLAUDE.md gained sections for the strip_code_fences helper, cold outreach architecture (cold_email + email_send), and shared route helpers. The IMAP-read-only key rule now correctly distinguishes inbound (read-only) from outbound (SMTP via explicit confirmation). README's dashboard walkthrough now lists Cold email as a feature. Stale v2+ "Cold email composer (sidebar slot reserved)" line removed (the feature shipped in Phase 30)
- [x] All 13 test suites pass post-cleanup (81 individual checks). Caught and fixed a Phase-29 test regression — apply-flow tests had been written when `_process_job` had default handlers; now require explicit `captcha_handler` + `submit_handler` kwargs since the CLI fallback was removed

## Phase 32: Multi-Job Batch Apply

*Closes the README/reality gap — the pitch says "Apply to dozens of jobs in the time it takes to apply to one," and now the UI does. Reuses Phase 23/27 infrastructure end to end; `_process_job` was already loop-shaped from the start.*

- [x] **Shared template fragments** — extracted `_apply_prompt.html` (pending-prompt modal) and `_apply_screenshot.html` (live form screenshot) so single and batch render the same modals via Jinja include + variable-set pattern (`post_url` / `hx_target` / `screenshot_url`)
- [x] **`_batch_apply_worker`** in `src/ui/routes/apply.py` — `_partition_selected()` pre-filters duplicates (browser never opens for an all-dupes batch); one Playwright session loops `_process_job` per surviving index; quit/cancel breaks the loop and the cleanup pass marks every un-recorded selection as `not_attempted` (driven off `completed_results.idx` rather than the loop counter, so cancel-before-process and quit-after-process behave correctly)
- [x] **`/apply/batch/*` routes** — start, page, status partial (2s polling), cancel, prompt, screenshot. Task key `apply:{profile}` shared with single-job apply; status file gains `mode` (`"single"` / `"batch"`), `selected_indices`, `current_index`, `completed_results`. Single-job page's `_flags()` treats any batch as "running for other" so prompt modals only surface in one place
- [x] **Templates** — `_batch_apply_main.html` (progress bar, completed-results table, shared prompt + screenshot includes, idle-state copy) + `apply_batch.html` page wrapper. Single-job "running_for_other_job" banner reworked to point at `/apply/batch` when the running task is a batch
- [x] **Jobs page selection UI** — checkbox column in `_job_row.html`, `<form id="batch-apply-form">` wrapping `_jobs_table.html`, header-row "Apply to selected (N)" primary button with Dry-run toggle, JS in the partial wires select-all + count + enabled-state and re-runs on every HTMX swap
- [x] **Smoke tests** in `/tmp/test_batch_apply.py` — 7/7 passing: routes registered, partition splits dedup correctly, batch iterates in selection order with `current_index` increment, quit short-circuits + remaining marked not_attempted, cancel mid-batch preserves processed results, all-dupes never opens browser, single-job regression. Helpers `wait_for_new_pending(previous_id)` + `wait_for_completed_count(n)` plus a teardown that cancels stragglers handle the daemon-thread cross-test bleeding (`write_status` recreates `parent` with mkdir, so workers can resurrect rmtree'd paths)
- [x] **TestClient render check** — 5/5 endpoints render cleanly (GET /jobs has the checkbox UI, GET /apply/batch shows idle state, POST /apply/batch/start with no selection surfaces the error, single-job page unchanged)
- [x] **Regression check** — existing `/tmp/test_apply_migration.py` still 7/7 (schema `mode` addition didn't break single-job apply)
- [x] **Docs** — CLAUDE.md apply key rule extended to cover both modes + shared task key + dedup pre-filter behavior; README's Jobs section mentions batch apply

### Out of scope (deferred)
- Per-job browser restart on hang/crash — recovery path is cancel + retry with un-applied subset selected
- Stable application IDs — list indices work; batch worker reads jobs once at task start so a concurrent `jobs.json` reorder can't shuffle the run
- Bulk `batch_select_projects` — `_process_job` calls `select_projects` per job today; per-job cost not yet a concern

### Followups
- A user opening `/jobs/{idx}/apply` for a job currently being processed inside a batch sees the "batch running" banner — correct, since prompts live on /apply/batch. The job-row "Apply" link still appears on /jobs even while a batch runs; clicking it lands on the per-job page which then shows the banner. Could disable the per-row Apply button while a batch is active, but it's not actively confusing — leaving as-is

## Phase 33: Modularization — split god-modules into packages

*Three of the largest files (`ats_greenhouse.py` 1096, `apply.py` 711, `job_discovery.py` 635) doing too many things. Split into packages with `__init__.py` re-exports — every external import still works. Python idiom here is module folders, not classes — pure-ish functions over a Playwright `page` or HTTP request, not stateful objects.*

### Result

- Largest src/ file is now 564 (`ui/routes/cold_email.py`, candidate for Phase 34)
- No file over 600 lines (was 1096, 711, 635)
- 14/14 smoke test suites green (+ patch paths updated for moved modules)

### Splits shipped

- [x] **`ats_greenhouse.py` → `src/ats/greenhouse/`** (1096 lines → 9 modules):
  - `selectors.py` — `_wait_for_form`, `_fill_if_exists`, `_upload_if_exists`, `_normalize_words`, `_fuzzy_match_options`, `_select_option_fuzzy[_el]`
  - `dates.py` — `_parse_date_parts`, `_try_select_date`
  - `location.py` — `_fill_location_autocomplete`
  - `education.py` — `_find_edu_section`, `_fill_education_section`
  - `demographics.py` — `_is_decline_option`, `_demographic_key_for_label`, `_select_decline_in_native`, `_fill_combobox_with_decline`, `_fill_demographics`
  - `custom_questions.py` — `_split_name`, `_build_applicant_context`, `_answer_custom_question`, `_answer_select_question`, `_get_question_text`, `_handle_custom_questions`
  - `application.py` — `fill_greenhouse_application` (the orchestrator)
  - `__init__.py` re-exports `fill_greenhouse_application`. `build_applicant_context` lifted to `src/ats/applicant_context.py` (shared with Lever) — cleaner than a per-package re-export since it's genuinely cross-ATS
  - Moved `src/ats_lever.py` → `src/ats/lever.py` for consistency; updated `src/applicant.py` imports
- [x] **`src/ui/routes/apply.py` → `src/ui/routes/apply/`** (711 lines → 4 modules):
  - `shared.py` — path/key helpers (`_apply_status_path`, `_apply_task_key`), `_flags()`, `_partition_selected`, `_append_completed`, `_load_jobs` alias
  - `single.py` — `_apply_worker`, 6 single-job routes (start, page, status partial, cancel, prompt, screenshot)
  - `batch.py` — `_batch_apply_worker`, 6 batch routes
  - `__init__.py` combines both routers via `include_router`, re-exports `_apply_worker`, `_batch_apply_worker`, `_partition_selected`, etc. so test imports keep working. Caveat documented: tests must patch `get_browser_context` at the submodule level (`single.py` / `batch.py`), not the package level — `mock.patch` rewrites a name in *one* module, not all imports of it
- [x] **`job_discovery.py` → `src/jobs/`** (635 lines → 7 modules):
  - `greenhouse.py` — `fetch_greenhouse_jobs`, Greenhouse-specific snippet extraction
  - `lever.py` — `fetch_lever_jobs`
  - `filter.py` — `score_job`, `_matches_any`, `_matches_location`, `filter_jobs`, `deduplicate_jobs`, `_extract_job_snippet`
  - `classify.py` — `classify_jobs_by_level`, `classify_jobs_by_country`, `score_jobs_fit`
  - `discover.py` — `discover_jobs`, `fetch_jobs_for_company` (orchestrator)
  - Also lifted shared HTTP defaults into `clients/_http.py`. `clients/` is a sub-subpackage with `greenhouse.py` + `lever.py`. `__init__.py` re-exports `discover_jobs` + both fetchers
- [x] Ran all 14 `/tmp/test_*.py` suites — green. One needed patch-path updates (`test_discover_jobs_streaming.py`) — patches now target `src.jobs.discover.fetch_greenhouse_jobs` etc.
- [x] CLAUDE.md: added Architecture-note bullets for ATS package layout, Job discovery package, Apply route package. Fixed stale `apply_to_jobs` reference (Phase 29 deleted it). Updated `strip_code_fences` consumers list (`job_discovery` → `jobs/classify`)

### Out of scope (Phase 34)
- `src/ui/routes/cold_email.py` (564 lines) — needs business logic extracted to `src/outreach.py`. Touches the Send/Generate flows the user actively uses; better as a dedicated phase with browser-level verification.
- `src/ui/routes/settings.py` (448 lines) — borderline; the page is one form so there's no clean per-concern split. Defer until / unless it grows further.

## Phase 34: Outreach package + resume grouping

*Two related "lift related files into a feature directory" splits. Outreach was the biggest god-route remaining (564 → 421); consolidating it with `src/cold_email.py` + `src/email_send.py` brought three scattered files into one `src/outreach/` feature directory. Resume grouping (4 top-level `resume_*.py` → `src/resume/`) was pure tidiness.*

### Result

- `src/ui/routes/cold_email.py`: 564 → 421 lines (thin route handler)
- Top file in `src/` is now 452 (`inbox/sync.py`, deliberately not split — coherent and already in a package)
- All 14 /tmp test suites green (102 individual checks)

### Splits shipped

- [x] **`src/outreach/`** — feature package consolidating three previously-scattered locations:
  - `generator.py` ← `src/cold_email.py` (`generate_outreach` + `batch_generate_outreach`, unchanged)
  - `sender.py` ← `src/email_send.py` (SMTP via inbox app password, unchanged)
  - `store.py` — extracted `outreach_path` / `load_outreach` / `save_outreach` / `now_iso` + `STATUSES` / `STATUS_BADGE` constants from the route file. Dropped underscore prefixes since they're now package-public API
  - `csv_import.py` — extracted `parse_csv_rows` from the route file (~75 lines)
  - `gmail.py` — extracted `gmail_compose_url` + `clean_domain` from the route file
  - `__init__.py` re-exports the public surface
- [x] **`src/ui/routes/cold_email.py`** — slimmed to a thin route handler. All business logic now lives in `src.outreach`; route file is form-handling + lock acquisition + template rendering + HTTP-shaped errors only
- [x] **`src/resume/`** — clean rename of the 4 top-level resume modules into a package; updated 5 call sites (applicant.py, ui/routes/{optimize,settings,apply/single,apply/batch}.py). One internal cross-import (`optimizer.py → renderer.py`) updated to new path
- [x] **Test patches** — updated 3 test files (`test_cold_email.py`, `test_bulk_outreach.py`, `test_email_send.py`) to point at new module paths. Tests that directly imported underscore-prefixed helpers from the route file (`_load_outreach`, `_gmail_compose_url`, `_parse_csv_rows`) now import the renamed public versions via `from src.outreach import load_outreach as _load_outreach` (aliased to keep test internal naming stable)
- [x] **Verification** — 14/14 test suites green, 65 routes register, smoke-imports clean
- [x] **CLAUDE.md** — Cold outreach + Resume Architecture notes rewritten for the new package layouts

### Out of scope
- `src/ui/routes/settings.py` (448 lines, multi-concern but single form) — no clean per-concern split
- `src/applicant.py` `_process_job` decomposition — function refactor, different exercise (Phase 35 candidate)
- `src/inbox/sync.py` (452, in package already, coherent)

## Phase 35: Data layer cleanup

*The #1 architectural issue flagged in the original review: 55 direct `json.dump` sites across 9 modules with no central writer. Mid-write crash = corrupted user data. Schema validation happened at one writer (`_save_applications`) but was bypassed everywhere else. This phase consolidates all top-level profile-file writes into atomic+validated `_atomic_write_json` calls.*

### Result
- All 14 test suites green (102 checks).
- Every top-level profile-file write now: validates against jsonschema (where one exists), writes to a `.tmp` sibling, then `os.replace`s into place. Mid-write crash leaves previous file intact.
- Caught a real test-fixture bug: `test_resume_upload` had stubs with invalid `section_order` ("contact" not in schema enum) that previously got written without complaint. The data layer now rejects them, and the test stubs were corrected.

### Scope (in)

Top-level profile files (`profiles/{name}/*.json`):
- `profile.json`, `responses.json`, `applications.json`, `companies.json`, `jobs.json`, `resume.json`, `outreach.json`

### Scope (out — already handled or different lifecycle)

- Status files under `tasks/runner.py` (already atomic tmp+rename)
- `imap/{credentials,state,proposals,sync_status}.json` subdirectory (inbox lifecycle, separate ownership)
- Resume optimization cache sidecars (`resume_cache/{hash}.json`) — content-addressed, not user state

### Shipped

- [x] Added `_atomic_write_json(path, data, validator=None)` helper in `src/profile_loader.py`
- [x] Added `Profile.save_*()` instance methods (used by `setup.py` after `Profile.create`)
- [x] Added `Profile.create(name, data)` classmethod for first-time profile creation
- [x] Migrated 9 writer sites:
  - `applicant._save_applications` — shim now uses `_atomic_write_json` directly (works in test fixtures without profile.json)
  - `discovery._save_companies` — same pattern
  - `jobs/discover.discover_jobs` — direct `_atomic_write_json`
  - `outreach/store.save_outreach` — direct `_atomic_write_json` (no validator — UI-driven schema)
  - `ui/routes/setup.py` — `Profile.create` for new + `profile.save_responses` for responses
  - `ui/routes/settings.py` x4 — `_atomic_write_json` direct (resume import, project add, profile, responses)
- [x] All 14 test suites green; fixed 1 test-fixture regression (test_resume_upload had invalid `section_order` stub that bypassed validation pre-Phase-35)
- [x] CLAUDE.md updated with Architecture note describing the data layer + the deliberate lock-at-route-layer / atomic-at-data-layer separation

### Design decisions

- **Lock acquisition stays at the route layer** (where read-modify-write sequences live), `save_*()` is unlocked. Two separate concerns: locks serialize cross-thread RMW, atomic writes give filesystem crash safety. Mixing them risks recursive-lock deadlocks (current locks are `threading.Lock`, not `RLock`).
- **Profile instance is a write-through cache**: after `profile.save_applications(...)`, `profile.applications` reflects the new state. Callers don't have to re-instantiate.
- **No validate_jobs / validate_outreach yet** — those schemas don't exist in `schemas.py`. `save_jobs` and `save_outreach` write without validation today; adding validators later is a follow-up.

### Out of scope (Phase 36+)

- `_process_job` decomposition (300 lines, 16 params, accretes complexity)
- Move `/tmp/test_*.py` → `tests/` (CI-discoverable)
- Combobox `.select__menu` scoping in 4 other sites (demographics + education x2 + application.py)
- `except Exception:` triage in `ats/greenhouse/` modules

## Phase 36: `_process_job` decomposition

*`src/applicant.py:_process_job` accreted to 240 lines / 17 params. Every recent fix landed inside it: Phase 27 added captcha+submit handlers, Phase 32 added i/total_jobs, this week added verification_handler + a `_handle_post_submit_verification` helper. The function was reachable but hostile to extending — splitting unlocks unit testability and makes the next bug fix tractable.*

### Result
- `_process_job` orchestrator: **87 lines** (was 230+)
- 5 stage helpers, each 27-71 lines with one clear job
- All 14 test suites still green (102 checks)
- Each stage takes its own args explicitly — no shared mutable context object, signatures stay honest

### Shipped — split into 5 focused helpers + orchestrator

| Function | Responsibility | Returns |
|---|---|---|
| `_prepare_tailored_resume` | Find cached PDF or generate one via Claude (uses `find_cached_resume`, `select_projects`, `optimize_resume`, `save_tailored_resume`) | PDF path string (or `""` if no base resume) |
| `_fill_with_captcha_retry` | ATS dispatch (`fill_greenhouse_application` / `fill_lever_application`) + CAPTCHA detection + one retry on CAPTCHA | `fill_result` dict |
| `_record_failure_entry` | Save progress checkpoint + append failed entry to `applications.json` | None (writes) |
| `_decide_submit_action` | Dry-run / auto-submit / prompt-and-submit decision; click; call `_handle_post_submit_verification`; return outcome | `(status: str, quit_loop: bool)` |
| `_record_success_entry` | Append successful/skipped/review_pending entry to `applications.json` | None (writes) |
| `_process_job` (orchestrator) | Compose the above. ~40 lines. | `bool` (quit signal) |

### Why not split into a new package?

`applicant.py` is ~300 lines total. Even after adding 4 new helpers, it stays around 350. The functions are tightly coupled (all about "apply to one job") — splitting into a package would force argument-passing across module boundaries for state that's naturally co-located. Defer the package split until the file actually approaches 600+.

### Out of scope

- Dataclass / context object for the shared args (handlers, progress callback, job). Each helper takes its own args explicitly — keeps signatures honest, easier to test.
- Refactoring `_handle_post_submit_verification` itself — it's already well-factored from earlier in the session.

## Phase 37: tests → `tests/` directory with pytest discovery

*All 14 test suites lived in `/tmp/test_*.py` (per the early lesson — separate from real profile data). The lesson was right about not mutating real user data, but the cure made tests invisible to CI / linting / any new contributor. Phase 37 moved them into `tests/` with `pytest` discovery; the `_test_*` profile convention is the real protection.*

### Result
- `tests/` directory in repo with 14 test files (93 pytest-discoverable test functions)
- `pyproject.toml` wires `pytest` to discover from `tests/` with `pythonpath = ["."]`
- `pytest` from repo root runs everything in ~30s; each file also runnable as `python3 tests/test_X.py` for ad-hoc debugging
- `tests/README.md` documents the conventions (hermetic, `_test_*` profiles, no network/no Claude/no browser)
- README adds a "Tests" section; CLAUDE.md adds a Tests architecture note; lesson #7 reframed to emphasize the `_test_*` profile pattern over file location
- `pytest>=8.0.0` added to `requirements.txt`

### Shipped

- [ ] Add `pyproject.toml` (pytest + pythonpath config) — minimal, just enough to wire pytest discovery to the repo root
- [ ] Create `tests/conftest.py` with shared fixtures: `tmp_profile_name` (per-test unique name), `tmp_profile_dir` (auto-rmtree), `fresh_page` (the Playwright MagicMock pattern), `wait_for_*` helpers
- [ ] Move all 14 `/tmp/test_*.py` → `tests/test_*.py`
- [ ] Strip the `sys.path.insert(0, "/Users/afeef/...")` boilerplate (pythonpath in pyproject.toml replaces it)
- [ ] Use the conftest fixtures instead of local setup/teardown — cleaner test bodies
- [ ] Add a `tests/README.md` explaining the fixture pattern (no real profile data ever touched, all tests use `_test_*` profile names that get cleaned up)
- [ ] README.md: add a "Running the test suite" section (`pip install pytest && pytest`)
- [ ] CLAUDE.md: update lesson #7 framing — the protection is the `tmp_profile` fixture pattern, not the directory location
- [ ] Verify all 14 suites still pass under `pytest`

### Why this matters for portfolio

A reviewer evaluating the project will check: where are the tests? Today the answer is "in /tmp, you can't see them." That looks like there are no tests. After Phase 37, `pytest` finds 14 suites + ~110 checks in a clean `tests/` directory — that's a real signal.

## Phase 38: Defense-in-depth bundle

*Two small changes that prevent recurrence of bugs we already hit. The `[role="option"]` page-wide query problem (which broke the Mercury visa dropdown) existed in 4 other places — applied the same `.select__menu` scoping pattern preemptively. The custom-question handler was re-processing fields the standard handler already filled (LinkedIn got Claude-answered redundantly on Mercury) — fixed by passing a `filled_ids` set to skip them.*

### Result
- 4 vulnerable `[role="option"]` sites scoped to `.select__menu`: `demographics.py` (2 queries in `_fill_combobox_with_decline`), `education.py:106` (degree React Select), `application.py:110` (candidate-location combobox)
- `application.py` standard fields loop now tracks filled element ids; passes `filled_ids | demo_ids` as `skip_ids` to `handle_custom_questions` so the second pass doesn't redundantly Claude-call already-handled fields
- All 93 tests still green

### Scope (in)

1. **`.select__menu` scoping** at 4 sites that use the same vulnerable pattern as the visa-dropdown bug:
   - `demographics.py:69, 92` — `_fill_combobox_with_decline` (collect options + click matched)
   - `education.py:106` — degree React Select option click
   - `application.py:110` — candidate-location combobox option click

2. **Skip already-filled fields** — orchestrator passes `filled_ids` (the union of standard-field ids + demographic ids) into `handle_custom_questions`; handler short-circuits on match. Saves redundant Claude calls + prevents the LinkedIn double-processing pattern.

### Scope (out — narrowed from original ambition)

- **`except Exception:` triage in `ats/greenhouse/`** (37 sites). After audit: most are intentional "try this selector strategy, fall back to next" patterns where any error means "this strategy didn't work, try the next one." Narrowing them site-by-site requires knowing which specific Playwright errors are expected at each call — high risk of regression for modest gain. Defer.
- `education.py:68` and `location.py:29` use `[role="option"]` for **Google Places autocomplete** (the `.pac-item` widget), not React Select. Different DOM lifecycle — no `.select__menu` to scope to. Leave alone unless we see a concrete bug.
- Schema validators for `jobs.json` / `outreach.json` — over-engineering until we have a multi-writer scenario (today: single producer each, schema enforced by code structure).

## Phase 39: Ashby ATS support

*Added a third ATS. Mirrors the Greenhouse + Lever pattern end-to-end: per-ATS API client in `src/jobs/clients/`, per-ATS form-fill package in `src/ats/`, dispatch via `applicant._fill_with_captcha_retry`. Opens up application paths for Notion, Linear, Ramp, OpenAI, Perplexity, etc. — companies on Ashby that weren't reachable before.*

### Result
- `src/jobs/clients/ashby.py` fetches from the public Ashby board API; verified live against Linear's slug
- `src/ats/ashby/` package: `fields.py`, `selectors.py`, `custom_questions.py`, `application.py` — mirrors greenhouse layout
- `_fill_with_captcha_retry` dispatches `ashby → fill_ashby_application` alongside greenhouse/lever
- 8 Ashby seed companies added to `config/seed_companies.json`
- UI dropdowns + auto-detect cascade include ashby
- All 93 tests still green
- CLAUDE.md + README ATS support table updated; "Greenhouse + Lever only" key rule lifted

### Scope (in)

- **Job discovery**: `src/jobs/clients/ashby.py` — fetches from `api.ashbyhq.com/posting-api/job-board/{slug}` (public, no auth). Normalizes to the same job-dict shape as greenhouse/lever
- **Company discovery**: `validate_ashby_slug` in `src/discovery.py`, wired into the existing `validate_slug` dispatch. UI auto-detect cascade tries greenhouse → lever → ashby in order
- **Form filling**: `src/ats/ashby/` package mirroring greenhouse layout — `fields.py`, `selectors.py`, `custom_questions.py`, `application.py`. Targets Ashby's React Hook Form based forms (form fields use `_systemfield_*` name convention)
- **Dispatch**: `src/applicant.py:_fill_with_captcha_retry` gets an `elif ats == "ashby"` branch
- **UI**: `_companies_main.html` + `jobs.html` ATS dropdowns add ashby option
- **Seed**: a few well-known Ashby companies added to `config/seed_companies.json` (Linear, Notion, Ramp, Vercel — note: some companies move between ATSes, so validation will skip invalid slugs)

### Scope (out)

- **Selector polish** — Ashby's form HTML varies less than Greenhouse's, but real-form iteration is still needed to handle company-specific customizations. Initial implementation covers the common case; expect to iterate when actually submitting through Ashby boards
- **Workday** — explicitly deferred. Workday's auth flows are heavier (account creation gate, multi-page wizards) and inconsistent per-company

## Phase 40: Companies — remove button (queued)

*Real product gap surfaced during Phase 39 testing: there's no way to remove a company without manually editing `companies.json`. Three legitimate reasons to delete: recover from typos / wrong auto-detect, curate the list to targets-only, drop companies that migrated off the supported ATSes.*

### Plan

- Per-row hover-revealed × button in `_companies_main.html`, mirroring the application-card delete pattern
- JS `confirm("Remove {company}?")` to prevent fat-finger deletes
- New `POST /companies/{slug}/delete` route: lock, filter list, `_atomic_write_json`, 303 redirect to `/companies?removed={name}`
- Does NOT delete associated applications (composite-key dedup keeps them independent — history stays intact)
- Does NOT remove jobs.json entries (next Refresh re-fetches and naturally drops them)
- 1 new pytest case

## Future (v2+)
- [ ] Crunchbase / Apollo / Hunter.io for real company + contact discovery (current "Discover companies" only validates a curated seed list)
- [ ] Workday support
- [ ] Inbox classifier extension to auto-detect cold-outreach replies (v2.3 from the cold-outreach memory)
- [ ] Stable application IDs instead of list indices (avoids two-tab drag race)
- [ ] Background daemon mode for periodic inbox sync without dashboard open
- [ ] Multi-account inbox (currently single IMAP account per profile)
- [ ] Deployment shift — currently local-first / single-user / no auth; see `project_deployment_target.md` memory for the move-to-Vercel-or-similar blockers
