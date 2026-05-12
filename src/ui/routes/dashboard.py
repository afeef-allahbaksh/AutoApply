import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from src.profile_loader import PROFILES_DIR, Profile

from .. import state
from ..deps import template_context
from ..pipeline import STATUS_BADGE_CLASS
from ..templates_loader import templates

router = APIRouter()

ACTIVE_STATUSES = {"applied", "screen", "technical", "onsite", "offer", "rejected"}
PIPELINE_STATUSES = {"screen", "technical", "onsite"}
RESPONSE_STATUSES = {"screen", "technical", "onsite", "offer"}

EMPTY_METRICS = {
    "total_applied": 0, "in_pipeline": 0, "offers": 0, "rejections": 0,
    "response_rate": 0.0, "responses_count": 0, "recent": [],
}


def _metrics_for(profile_name: str) -> dict:
    if not profile_name:
        return EMPTY_METRICS
    try:
        profile = Profile(profile_name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    apps = list(profile.applications)
    total_applied = sum(1 for a in apps if a.get("status") in ACTIVE_STATUSES)
    in_pipeline = sum(1 for a in apps if a.get("status") in PIPELINE_STATUSES)
    offers = sum(1 for a in apps if a.get("status") == "offer")
    rejections = sum(1 for a in apps if a.get("status") == "rejected")
    responses = sum(1 for a in apps if a.get("status") in RESPONSE_STATUSES)
    response_rate = (responses / total_applied * 100) if total_applied else 0.0

    def _sort_key(a):
        return a.get("status_updated_at") or a.get("date") or ""

    recent = sorted(apps, key=_sort_key, reverse=True)[:10]

    return {
        "total_applied": total_applied,
        "in_pipeline": in_pipeline,
        "offers": offers,
        "rejections": rejections,
        "response_rate": round(response_rate, 1),
        "responses_count": responses,
        "recent": recent,
    }


def _profile_summary_for(profile_name: str) -> dict:
    """Compact profile config readout — parity with the CLI `status` output
    (name, roles, locations, auto-submit, applications count) plus a couple of
    workspace counts that the CLI didn't show but are useful at a glance."""
    if not profile_name:
        return {}
    try:
        profile = Profile(profile_name)
    except FileNotFoundError:
        return {}

    prefs = profile.job_preferences or {}

    def _safe_count(filename: str) -> int:
        path = profile.profile_dir / filename
        if not path.exists() or path.stat().st_size == 0:
            return 0
        try:
            with open(path) as f:
                return len(json.load(f))
        except (json.JSONDecodeError, OSError):
            return 0

    return {
        "name": profile.data.get("name") or profile_name,
        "slug": profile_name,
        "roles": prefs.get("roles", []),
        "locations": prefs.get("locations", []),
        "experience_levels": prefs.get("experience_levels", []),
        "auto_submit": profile.auto_submit,
        "rate_limit_seconds": profile.rate_limit_seconds,
        "applications_count": len(list(profile.applications)),
        "companies_count": _safe_count("companies.json"),
        "jobs_count": _safe_count("jobs.json"),
    }


@router.get("/")
def dashboard(request: Request, setup_msg: str = ""):
    profile_name = state.active_profile()
    # First-visit redirect: no profile exists OR active profile is missing its
    # profile.json on disk. The setup wizard creates one.
    if not profile_name or not (PROFILES_DIR / profile_name / "profile.json").exists():
        return RedirectResponse(url="/setup", status_code=303)
    metrics = _metrics_for(profile_name)
    profile_summary = _profile_summary_for(profile_name)
    from .pipeline import _read_pipeline_status
    pipeline_status = _read_pipeline_status(profile_name)
    resume_missing = not (PROFILES_DIR / profile_name / "resume.json").exists()
    return templates.TemplateResponse(
        request, "dashboard.html",
        template_context(
            request, page_title="Dashboard",
            metrics=metrics,
            profile_summary=profile_summary,
            status_badge_class=STATUS_BADGE_CLASS,
            pipeline_status=pipeline_status,
            pipeline_running=pipeline_status.get("state") == "running",
            pipeline_msg="",
            pipeline_err="",
            resume_missing=resume_missing,
            setup_msg=setup_msg,
        ),
    )


@router.get("/_metrics")
def metrics_partial(request: Request):
    profile_name = state.active_profile()
    metrics = _metrics_for(profile_name)
    profile_summary = _profile_summary_for(profile_name)
    return templates.TemplateResponse(
        request, "_metrics.html",
        {
            "request": request,
            "metrics": metrics,
            "profile_summary": profile_summary,
            "status_badge_class": STATUS_BADGE_CLASS,
        },
    )
