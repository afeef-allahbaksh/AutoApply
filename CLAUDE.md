# CLAUDE.md — AutoApply

## Project
Fully automated job application pipeline. Four agents in sequence:
Profile → Discover Companies → Find Open Roles → Optimize Resume → Apply → Log

Python backend, Playwright for browser automation, Claude API for resume optimization and custom question answering. CLI only for v1.

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

## Verification
- Never mark tasks complete without proving they work
- Run the code, check logs, demonstrate correct behavior
- Ask: "Would a staff engineer approve this PR?"

## Bug Fixing
- When given a bug report: just fix it with logs or failing behavior, no hand-holding needed

## Self-Improvement
- After user corrections: update `tasks/lessons.md` with the pattern and prevention rules
- Review `tasks/lessons.md` at the start of each session
- Keep `tasks/lessons.md` under 30 items — consolidate when it grows past that

## Subagents
- Use for research, exploration, and parallel analysis to keep main context clean
- One focused task per subagent

## Key Rules
- `profiles/` is gitignored — never commit personal data
- Follow the priority build order in `project-context.md` exactly
- Greenhouse + Lever only in v1 — no Ashby, no Workday, no Crunchbase
- CLI only in v1 — no React frontend
- Resume stored as structured JSON, rendered to PDF — never edit PDFs directly
- Deduplication by composite key (company + role + posting URL)
- Rate limit Playwright submissions — respect `rate_limit_seconds`
