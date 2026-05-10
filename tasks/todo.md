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

## Future (v2+)
- [ ] Ashby ATS support
- [ ] Crunchbase API for richer company discovery
- [ ] Workday support
- [ ] Cold email composer + tracker (sidebar slot reserved)
- [ ] Stable application IDs instead of list indices (avoids two-tab drag race)
- [ ] Background daemon mode for periodic inbox sync without dashboard open
- [ ] Multi-account inbox (currently single IMAP account per profile)
