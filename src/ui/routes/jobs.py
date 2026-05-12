import json
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from src.applicant import _save_applications
from src.jobs import discover_jobs
from src.profile_loader import PROFILES_DIR, Profile
from src.tasks import runner

from .. import state
from ..deps import template_context
from ..pipeline import load_jobs
from ..templates_loader import templates

router = APIRouter()


def _load_jobs(profile_name: str) -> list:
    """Compat wrapper — keeps the route's existing 404-on-missing-profile
    semantics distinct from the lenient shared `load_jobs` which returns []."""
    if profile_name:
        try:
            Profile(profile_name)  # surfaces FileNotFoundError → 404
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
    return load_jobs(profile_name)


def _filtered(jobs: list, min_fit: int, company: str, ats: str) -> list:
    enriched = [{**j, "_idx": i} for i, j in enumerate(jobs)]
    if min_fit > 0:
        enriched = [j for j in enriched if (j.get("fit_score") or 0) >= min_fit]
    if company:
        c = company.lower()
        enriched = [j for j in enriched if c in (j.get("company") or "").lower()]
    if ats:
        enriched = [j for j in enriched if j.get("ats") == ats]
    return enriched


def _any_fit_scored(jobs: list) -> bool:
    """True if at least one job in jobs.json has a fit_score. Used to decide
    whether the default min_fit filter would silently hide everything."""
    return any(j.get("fit_score") is not None for j in jobs)


def _discover_jobs_status_path(profile_name: str) -> Path:
    return PROFILES_DIR / profile_name / "discover_jobs_status.json"


def _discover_jobs_task_key(profile_name: str) -> str:
    return f"discover-jobs:{profile_name}"


def _read_discover_jobs_status(profile_name: str) -> dict:
    if not profile_name:
        return {"state": "idle"}
    return runner.read_status(
        _discover_jobs_status_path(profile_name),
        _discover_jobs_task_key(profile_name),
        interrupted_message="Job discovery interrupted (worker stopped). Click Refresh to retry.",
    )


def _render_jobs_main(
    request: Request,
    profile_name: str,
    *,
    min_fit: int = 3,
    company: str = "",
    ats: str = "",
    discover_msg: str = "",
    discover_err: str = "",
) -> HTMLResponse:
    """Render the #jobs-content partial — banner + table. Used by every
    discover-related mutating route AND by the 4s polling endpoint."""
    jobs = _load_jobs(profile_name) if profile_name else []
    rows = _filtered(jobs, min_fit, company, ats)
    discover_status = _read_discover_jobs_status(profile_name)
    return templates.TemplateResponse(
        request, "_jobs_main.html",
        {
            "request": request,
            "rows": rows,
            "total": len(jobs),
            "discover_status": discover_status,
            "discover_running": discover_status.get("state") == "running",
            "discover_msg": discover_msg,
            "discover_err": discover_err,
        },
    )


@router.get("/jobs")
def jobs_page(
    request: Request,
    min_fit: int | None = None,
    company: str = "",
    ats: str = "",
):
    profile_name = state.active_profile()
    jobs = _load_jobs(profile_name) if profile_name else []
    # When the user hasn't explicitly set min_fit, auto-relax to 0 if no jobs
    # have been scored (no resume → no fit scores → default of 3 would hide
    # everything). Otherwise the historical default is 3.
    fit_scored = _any_fit_scored(jobs)
    if min_fit is None:
        min_fit = 3 if fit_scored else 0
    rows = _filtered(jobs, min_fit, company, ats)
    discover_status = _read_discover_jobs_status(profile_name)
    return templates.TemplateResponse(
        request, "jobs.html",
        template_context(
            request,
            page_title="Jobs",
            rows=rows,
            min_fit=min_fit,
            company=company,
            ats=ats,
            total=len(jobs),
            fit_scored=fit_scored,
            discover_status=discover_status,
            discover_running=discover_status.get("state") == "running",
            discover_msg="",
            discover_err="",
        ),
    )


def _discover_jobs_worker(profile_name: str) -> None:
    """Background worker — streams progress from `discover_jobs` to the status file."""
    sf = _discover_jobs_status_path(profile_name)

    def work():
        def on_progress(**kw):
            # All fields pass through; the template reads phase/message/counters.
            runner.write_status(sf, **kw)

        def cancel_check():
            return runner.is_cancel_requested(sf)

        discover_jobs(
            profile_name,
            on_progress=on_progress,
            cancel_check=cancel_check,
        )

    runner.run_with_terminal_status(
        sf,
        work=work,
        idle_message="Job discovery complete.",
        cancelled_message="Job discovery cancelled. jobs.json unchanged.",
        log_prefix="[discover-jobs]",
    )


@router.post("/jobs/refresh")
def refresh_jobs(
    request: Request,
    min_fit: int = Form(3),
    company: str = Form(""),
    ats: str = Form(""),
):
    """Start a background discover-jobs task. Returns the #jobs-content
    partial with the running banner; HTMX swaps it in and the banner then
    polls /jobs/discover_status every 4s until the task finishes."""
    profile_name = state.active_profile()
    if not profile_name:
        return _render_jobs_main(
            request, profile_name,
            min_fit=min_fit, company=company, ats=ats,
            discover_err="No active profile.",
        )
    started, msg = runner.start_task(
        task_key=_discover_jobs_task_key(profile_name),
        status_file=_discover_jobs_status_path(profile_name),
        target=_discover_jobs_worker,
        args=(profile_name,),
        initial_status={
            "phase": "starting",
            "companies_total": 0,
            "companies_processed": 0,
            "jobs_fetched": 0,
            "message": "Starting job discovery…",
        },
        thread_name=f"discover-jobs-{profile_name}",
        already_running_msg="Job discovery already in progress for this profile.",
    )
    if not started:
        return _render_jobs_main(
            request, profile_name,
            min_fit=min_fit, company=company, ats=ats,
            discover_err=msg,
        )
    return _render_jobs_main(
        request, profile_name,
        min_fit=min_fit, company=company, ats=ats,
        discover_msg="Job discovery started in the background.",
    )


@router.get("/jobs/discover_status")
def discover_jobs_status(
    request: Request,
    min_fit: int = 3,
    company: str = "",
    ats: str = "",
):
    """Polled while job discovery is running. The polling element includes the
    filter form values via `hx-include`, so the table re-renders with the
    user's current filter even mid-discovery."""
    profile_name = state.active_profile()
    return _render_jobs_main(
        request, profile_name,
        min_fit=min_fit, company=company, ats=ats,
    )


@router.post("/jobs/discover_cancel")
def discover_jobs_cancel(
    request: Request,
    min_fit: int = Form(3),
    company: str = Form(""),
    ats: str = Form(""),
):
    profile_name = state.active_profile()
    flipped = runner.request_cancel(_discover_jobs_status_path(profile_name))
    msg = (
        "Cancel requested — discovery will stop at the next phase boundary."
        if flipped else "No discovery running."
    )
    return _render_jobs_main(
        request, profile_name,
        min_fit=min_fit, company=company, ats=ats,
        discover_msg=msg,
    )


@router.post("/jobs/{idx}/track")
def track_job(request: Request, idx: int, status: str = Form("applied")):
    """Create a manual application entry from this job — for tracking only.

    Does not run Playwright or submit the application; the user is expected to apply
    elsewhere (LinkedIn, referrals, the company portal) and use this to log the result.
    """
    profile_name = state.active_profile()
    lock = state.profile_lock(profile_name)
    with lock:
        try:
            profile = Profile(profile_name)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

        jobs = _load_jobs(profile_name)
        if not 0 <= idx < len(jobs):
            raise HTTPException(status_code=404, detail=f"job index {idx} out of range")
        job = jobs[idx]

        applications = list(profile.applications)
        entry = {
            "company": job.get("company", ""),
            "role": job.get("title", ""),
            "posting_url": job.get("posting_url", ""),
            "date": date.today().isoformat(),
            "status": status,
            "status_updated_at": date.today().isoformat(),
            "source": "manual",
        }
        if job.get("ats"):
            entry["ats"] = job["ats"]
        if job.get("fit_score") is not None:
            entry["fit_score"] = job["fit_score"]
            entry["fit_rationale"] = job.get("fit_rationale", "")
        applications.append(entry)
        _save_applications(profile_name, applications)

    row = {**jobs[idx], "_idx": idx}
    return templates.TemplateResponse(
        request, "_job_row.html",
        {"request": request, "row": row, "tracked_now": True, "tracked_status": status},
    )


@router.post("/jobs/{idx}/delete")
def delete_job(request: Request, idx: int):
    """Remove a job from jobs.json by index. Applications referencing the job
    are untouched — composite-key dedup keeps them independent. Discover may
    re-add the job on the next run if it's still on the underlying ATS board."""
    profile_name = state.active_profile()
    if not profile_name:
        raise HTTPException(status_code=400, detail="No active profile.")
    lock = state.profile_lock(profile_name)
    with lock:
        try:
            profile = Profile(profile_name)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        jobs = list(_load_jobs(profile_name))
        if not 0 <= idx < len(jobs):
            raise HTTPException(status_code=404, detail=f"job index {idx} out of range")
        jobs.pop(idx)
        profile.save_jobs(jobs)
    # hx-swap="delete" on the client removes the row in-place. Empty body.
    return HTMLResponse("")
