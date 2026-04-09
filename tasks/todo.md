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

## Future (v2+)
- [ ] Ashby ATS support
- [ ] Crunchbase API for richer company discovery
- [ ] React frontend for review UI
- [ ] Workday support
- [ ] Analytics dashboard (application stats, response rates)
