import json
from datetime import date

from fastapi import APIRouter, Form, HTTPException, Request

from src.applicant import _save_applications
from src.job_discovery import discover_jobs
from src.profile_loader import Profile

from .. import state
from ..deps import template_context
from ..templates_loader import templates

router = APIRouter()


def _load_jobs(profile_name: str) -> list:
    try:
        profile = Profile(profile_name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    path = profile.profile_dir / "jobs.json"
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


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


@router.get("/jobs")
def jobs_page(
    request: Request,
    min_fit: int = 3,
    company: str = "",
    ats: str = "",
):
    profile_name = state.active_profile()
    jobs = _load_jobs(profile_name) if profile_name else []
    rows = _filtered(jobs, min_fit, company, ats)
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
        ),
    )


@router.post("/jobs/refresh")
def refresh_jobs(
    request: Request,
    min_fit: int = Form(3),
    company: str = Form(""),
    ats: str = Form(""),
):
    profile_name = state.active_profile()
    lock = state.profile_lock(profile_name)
    with lock:
        discover_jobs(profile_name)
        jobs = _load_jobs(profile_name)
    rows = _filtered(jobs, min_fit, company, ats)
    return templates.TemplateResponse(
        request, "_jobs_table.html",
        {"request": request, "rows": rows, "total": len(jobs)},
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
