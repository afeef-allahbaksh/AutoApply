# CLAUDE.md — AutoApply

## Project
Fully automated job application pipeline. Seven stages in sequence:
Profile → Discover Companies → Find Open Roles → Score Fit → Select Projects → Optimize Resume → Apply → Log

Python backend, Playwright for browser automation, Claude API (via shared singleton client with retry) for resume optimization, project selection, and custom question answering. CLI only for v1.

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
- CLI only in v1 — no React frontend
- Everything should be configurable through CLI commands — users should never need to manually edit JSON files
- All commands that need file paths (import-resume, add-projects) prompt interactively if --pdf is omitted
- Resume stored as structured JSON, rendered to PDF — never edit PDFs directly
- Deduplication by composite key (company + role + posting URL)
- Rate limit Playwright submissions — respect `rate_limit_seconds`
