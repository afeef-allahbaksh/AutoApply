from fastapi import APIRouter, HTTPException, Request

from src.profile_loader import Profile

from .. import state
from ..deps import template_context
from ..templates_loader import templates

router = APIRouter()

ACTIVE_STATUSES = {"applied", "screen", "technical", "onsite", "offer", "rejected"}
PIPELINE_STATUSES = {"screen", "technical", "onsite"}
RESPONSE_STATUSES = {"screen", "technical", "onsite", "offer"}


def _metrics_for(profile_name: str) -> dict:
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


@router.get("/")
def dashboard(request: Request):
    profile_name = state.active_profile()
    metrics = _metrics_for(profile_name) if profile_name else {
        "total_applied": 0, "in_pipeline": 0, "offers": 0, "rejections": 0,
        "response_rate": 0.0, "responses_count": 0, "recent": [],
    }
    return templates.TemplateResponse(
        request, "dashboard.html",
        template_context(request, page_title="Dashboard", metrics=metrics),
    )


@router.get("/_metrics")
def metrics_partial(request: Request):
    profile_name = state.active_profile()
    metrics = _metrics_for(profile_name) if profile_name else {
        "total_applied": 0, "in_pipeline": 0, "offers": 0, "rejections": 0,
        "response_rate": 0.0, "responses_count": 0, "recent": [],
    }
    return templates.TemplateResponse(
        request, "_metrics.html",
        {"request": request, "metrics": metrics},
    )
