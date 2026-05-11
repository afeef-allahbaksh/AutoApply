"""Shared helpers used by both single-job and batch apply.

Single and batch apply share one task key + status file (`apply:{profile}` /
`apply_status.json`) because they're mutually exclusive — only one Playwright
session runs per profile. This module owns the path/key conventions, dedup
partitioning, and the `completed_results` append helper.
"""
from pathlib import Path

from src.applicant import _is_already_applied
from src.profile_loader import PROFILES_DIR
from src.tasks import runner

from ...pipeline import load_jobs


def _apply_status_path(profile_name: str) -> Path:
    return PROFILES_DIR / profile_name / "apply_status.json"


def _apply_task_key(profile_name: str) -> str:
    return f"apply:{profile_name}"


_load_jobs = load_jobs  # local alias — kept stable for callers/tests


def _partition_selected(
    selected_indices: list[int],
    jobs: list,
    applications: list,
) -> tuple[list[dict], list[dict]]:
    """Split selected indices into (to_apply, already_applied) using composite-key
    dedup against applications.json. Each entry carries idx + company + role so
    the UI's completed_results table can render skips before the browser opens."""
    to_apply: list[dict] = []
    already_applied: list[dict] = []
    for idx in selected_indices:
        if not 0 <= idx < len(jobs):
            continue
        job = jobs[idx]
        meta = {
            "idx": idx,
            "company": job.get("company", ""),
            "role": job.get("title", ""),
        }
        if _is_already_applied(applications, job["company"], job["title"], job["posting_url"]):
            already_applied.append({**meta, "status": "skipped_duplicate"})
        else:
            to_apply.append({**meta, "job": job})
    return to_apply, already_applied


def _append_completed(sf: Path, entry: dict) -> None:
    """Append one result to `completed_results` in the status file. The runner's
    `write_status` is read-modify-write so we read+append+write here."""
    current = runner.read_status_raw(sf)
    completed = list(current.get("completed_results") or [])
    completed.append(entry)
    runner.write_status(sf, completed_results=completed)
