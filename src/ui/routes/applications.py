from datetime import date

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import Response

from src.applicant import _save_applications
from src.profile_loader import Profile

from .. import state
from ..deps import template_context
from ..templates_loader import templates

router = APIRouter()

ALL_STATUSES = [
    "applied", "screen", "technical", "onsite", "offer",
    "rejected", "failed", "review_pending", "skipped",
]


def _load_apps(profile_name: str) -> list:
    try:
        profile = Profile(profile_name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return list(profile.applications)


def _sorted_filtered(apps: list, sort: str, direction: str, status_filter: list[str], search: str) -> list:
    enriched = [{**a, "_idx": i} for i, a in enumerate(apps)]
    if status_filter:
        enriched = [a for a in enriched if a.get("status") in status_filter]
    if search:
        s = search.lower()
        enriched = [a for a in enriched if s in a.get("company", "").lower() or s in a.get("role", "").lower()]

    def key(a):
        if sort == "company":
            return (a.get("company") or "").lower()
        if sort == "role":
            return (a.get("role") or "").lower()
        if sort == "fit":
            return a.get("fit_score") or 0
        if sort == "status":
            return a.get("status") or ""
        if sort == "source":
            return a.get("source") or ""
        return a.get("status_updated_at") or a.get("date") or ""

    enriched.sort(key=key, reverse=(direction == "desc"))
    return enriched


@router.get("/applications")
def applications_page(
    request: Request,
    sort: str = "date",
    dir: str = "desc",
    status: list[str] | None = None,
    q: str = "",
):
    profile_name = state.active_profile()
    apps = _load_apps(profile_name) if profile_name else []
    rows = _sorted_filtered(apps, sort, dir, status or [], q)
    return templates.TemplateResponse(
        request, "applications.html",
        template_context(
            request,
            page_title="Applications",
            rows=rows,
            sort=sort,
            dir=dir,
            status_filter=status or [],
            q=q,
            all_statuses=ALL_STATUSES,
        ),
    )


@router.post("/applications")
def add_manual(
    request: Request,
    company: str = Form(...),
    role: str = Form(...),
    posting_url: str = Form(...),
    date_str: str = Form(..., alias="date"),
    status: str = Form("applied"),
    notes: str = Form(""),
):
    if not all([company.strip(), role.strip(), posting_url.strip(), date_str.strip()]):
        raise HTTPException(status_code=400, detail="company, role, posting_url, date are required")
    if status not in ALL_STATUSES:
        raise HTTPException(status_code=400, detail=f"invalid status: {status}")

    profile_name = state.active_profile()
    lock = state.profile_lock(profile_name)
    with lock:
        apps = _load_apps(profile_name)
        entry = {
            "company": company.strip(),
            "role": role.strip(),
            "posting_url": posting_url.strip(),
            "date": date_str,
            "status": status,
            "status_updated_at": date.today().isoformat(),
            "source": "manual",
        }
        if notes.strip():
            entry["notes"] = notes.strip()
        apps.append(entry)
        _save_applications(profile_name, apps)
        new_idx = len(apps) - 1
    row = {**entry, "_idx": new_idx}
    return templates.TemplateResponse(
        request, "_app_row.html",
        {"request": request, "row": row, "all_statuses": ALL_STATUSES},
    )


@router.patch("/applications/{idx}/status")
def patch_status(request: Request, idx: int, new_status: str = Form(...)):
    if new_status not in ALL_STATUSES:
        raise HTTPException(status_code=400, detail=f"invalid status: {new_status}")
    profile_name = state.active_profile()
    lock = state.profile_lock(profile_name)
    with lock:
        apps = _load_apps(profile_name)
        if not 0 <= idx < len(apps):
            raise HTTPException(status_code=404, detail="application not found")
        apps[idx]["status"] = new_status
        apps[idx]["status_updated_at"] = date.today().isoformat()
        _save_applications(profile_name, apps)
        row = {**apps[idx], "_idx": idx}
    return templates.TemplateResponse(
        request, "_app_row.html",
        {"request": request, "row": row, "all_statuses": ALL_STATUSES},
    )


@router.get("/applications/{idx}/edit")
def edit_form(request: Request, idx: int):
    profile_name = state.active_profile()
    apps = _load_apps(profile_name)
    if not 0 <= idx < len(apps):
        raise HTTPException(status_code=404, detail="application not found")
    row = {**apps[idx], "_idx": idx}
    return templates.TemplateResponse(
        request, "_app_edit.html",
        {"request": request, "row": row, "all_statuses": ALL_STATUSES},
    )


@router.get("/applications/{idx}/row")
def get_row(request: Request, idx: int):
    profile_name = state.active_profile()
    apps = _load_apps(profile_name)
    if not 0 <= idx < len(apps):
        raise HTTPException(status_code=404, detail="application not found")
    row = {**apps[idx], "_idx": idx}
    return templates.TemplateResponse(
        request, "_app_row.html",
        {"request": request, "row": row, "all_statuses": ALL_STATUSES},
    )


@router.patch("/applications/{idx}")
def patch_application(
    request: Request,
    idx: int,
    company: str = Form(...),
    role: str = Form(...),
    posting_url: str = Form(...),
    date_str: str = Form(..., alias="date"),
    status: str = Form(...),
    notes: str = Form(""),
):
    if status not in ALL_STATUSES:
        raise HTTPException(status_code=400, detail=f"invalid status: {status}")
    profile_name = state.active_profile()
    lock = state.profile_lock(profile_name)
    with lock:
        apps = _load_apps(profile_name)
        if not 0 <= idx < len(apps):
            raise HTTPException(status_code=404, detail="application not found")
        existing = apps[idx]
        prev_status = existing.get("status")
        existing.update({
            "company": company.strip(),
            "role": role.strip(),
            "posting_url": posting_url.strip(),
            "date": date_str,
            "status": status,
        })
        if status != prev_status:
            existing["status_updated_at"] = date.today().isoformat()
        if notes.strip():
            existing["notes"] = notes.strip()
        else:
            existing.pop("notes", None)
        _save_applications(profile_name, apps)
        row = {**existing, "_idx": idx}
    return templates.TemplateResponse(
        request, "_app_row.html",
        {"request": request, "row": row, "all_statuses": ALL_STATUSES},
    )


@router.delete("/applications/{idx}")
def delete_application(idx: int):
    profile_name = state.active_profile()
    lock = state.profile_lock(profile_name)
    with lock:
        apps = _load_apps(profile_name)
        if not 0 <= idx < len(apps):
            raise HTTPException(status_code=404, detail="application not found")
        apps.pop(idx)
        _save_applications(profile_name, apps)
    return Response(status_code=200, content="")
