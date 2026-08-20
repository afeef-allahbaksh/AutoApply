# Tests

20 test suites, 171 individual test functions, all hermetic.

## Running

```bash
pip install -r requirements.txt   # pytest is in the test deps
pytest                             # run everything
pytest tests/test_batch_apply.py   # run one suite
pytest -k captcha                  # run tests matching a name pattern
pytest -v                          # verbose — list each test function
```

The repo's `pyproject.toml` points `pytest` at `tests/` and adds the repo root to
`pythonpath`, so `from src.X import Y` works inside tests with no boilerplate.

## What's covered

| Suite | What it exercises |
|---|---|
| `test_apply_migration.py` | Single-job apply worker via prompt channel (submit/skip/quit/CAPTCHA), dedup short-circuit |
| `test_batch_apply.py` | Multi-job batch worker, partition+dedup, quit-breaks-loop, cancel-mid-batch, current_index progression |
| `test_bulk_outreach.py` | CSV/TSV parser variants, bulk-import route, batch-generate via Claude |
| `test_cold_email.py` | Outreach CRUD, generate (Claude-mocked), status transitions, Gmail compose URL |
| `test_discover_jobs_streaming.py` | Job discovery pipeline streaming, per-phase progress, cancel mid-fetch |
| `test_discover_streaming.py` | Company discovery streaming, cancel, progress callbacks |
| `test_email_send.py` | SMTP host derivation, send_email happy path, auth failure, no-inbox state |
| `test_optimize_migration.py` | Per-job resume optimization worker, cache hit, dry-run, status file lifecycle |
| `test_pipeline_migration.py` | Run-pipeline orchestrator chaining discover-companies + discover-jobs |
| `test_profile_delete.py` | Profile deletion route, double-entry confirm guard, path-traversal protection |
| `test_prompt_channel.py` | `src/tasks/prompt.py` round-trip, cancel + timeout, stale-response rejection |
| `test_resume_upload.py` | Resume PDF import (Claude-mocked), project-pool merge with dedup, schema validation |
| `test_setup_wizard.py` | Slug munging, profile creation, role expansion call-through, redirect-on-no-profile |
| `test_task_runner.py` | Background task lifecycle (idle/running/cancelled/error/interrupted), per-profile thread registry |

## Conventions

**Hermetic.** Every test that touches the filesystem uses a `_test_*` profile name
(e.g. `_test_apply`, `_test_batch_apply`) so it can never accidentally read or
write your real `profiles/` data. Tests `shutil.rmtree` their `_test_*` directory
on teardown.

**Standalone-runnable.** Each test file can also be invoked directly:
`python3 tests/test_batch_apply.py`. The trailing `if __name__ == "__main__":`
block runs the same `test_*` functions pytest collects, useful for ad-hoc debugging.

**No network, no Claude calls.** `create_message`, `fill_greenhouse_application`,
SMTP libraries, and IMAP connections are all `unittest.mock.patch`'d. Tests
complete in ~30 seconds total on a laptop with no external services.

**No browser.** Playwright `Page` is replaced with `MagicMock` configured to
satisfy the worker's expected calls (locators, screenshots, `inner_text()`).
The `fresh_page()` helper in `test_apply_migration.py` and `test_batch_apply.py`
shows the pattern.
