from datetime import date

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

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

# Linear interview pipeline. Cards advance left-to-right.
PIPELINE = ["applied", "screen", "technical", "onsite", "offer"]
KANBAN_COLUMNS = PIPELINE + ["rejected"]
CLOSED_STATUSES = ["failed", "review_pending", "skipped"]


def _load_apps(profile_name: str) -> list:
    try:
        profile = Profile(profile_name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return list(profile.applications)


def _next_status(current: str) -> str | None:
    if current in PIPELINE:
        i = PIPELINE.index(current)
        return PIPELINE[i + 1] if i + 1 < len(PIPELINE) else None
    return None


def _prev_status(current: str) -> str | None:
    if current in PIPELINE:
        i = PIPELINE.index(current)
        return PIPELINE[i - 1] if i > 0 else None
    return None


def _kanban_groups(apps: list, search: str) -> tuple[dict, list]:
    enriched = [{**a, "_idx": i} for i, a in enumerate(apps)]
    if search:
        s = search.lower()
        enriched = [a for a in enriched if s in a.get("company", "").lower() or s in a.get("role", "").lower()]

    columns = {col: [] for col in KANBAN_COLUMNS}
    closed = []
    for a in enriched:
        st = a.get("status")
        if st in columns:
            columns[st].append(a)
        else:
            closed.append(a)

    def sort_key(a):
        return a.get("status_updated_at") or a.get("date") or ""

    for col in columns:
        columns[col].sort(key=sort_key, reverse=True)
    closed.sort(key=sort_key, reverse=True)
    return columns, closed


def _render_kanban(request: Request, profile_name: str, search: str = "") -> HTMLResponse:
    apps = _load_apps(profile_name) if profile_name else []
    columns, closed = _kanban_groups(apps, search)
    return templates.TemplateResponse(
        request, "_app_kanban.html",
        {
            "request": request,
            "columns": columns,
            "kanban_columns": KANBAN_COLUMNS,
            "closed": closed,
            "all_statuses": ALL_STATUSES,
            "pipeline": PIPELINE,
        },
    )


@router.get("/applications")
def applications_page(request: Request, q: str = ""):
    profile_name = state.active_profile()
    apps = _load_apps(profile_name) if profile_name else []
    columns, closed = _kanban_groups(apps, q)
    return templates.TemplateResponse(
        request, "applications.html",
        template_context(
            request,
            page_title="Applications",
            columns=columns,
            kanban_columns=KANBAN_COLUMNS,
            closed=closed,
            q=q,
            all_statuses=ALL_STATUSES,
            pipeline=PIPELINE,
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
    return _render_kanban(request, profile_name)


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
    return _render_kanban(request, profile_name)


@router.post("/applications/{idx}/advance")
def advance_status(request: Request, idx: int, direction: str = Form(...)):
    profile_name = state.active_profile()
    lock = state.profile_lock(profile_name)
    with lock:
        apps = _load_apps(profile_name)
        if not 0 <= idx < len(apps):
            raise HTTPException(status_code=404, detail="application not found")
        current = apps[idx].get("status", "applied")
        if direction == "next":
            new_status = _next_status(current)
        elif direction == "prev":
            new_status = _prev_status(current)
        else:
            raise HTTPException(status_code=400, detail="direction must be next|prev")
        if not new_status:
            return _render_kanban(request, profile_name)
        apps[idx]["status"] = new_status
        apps[idx]["status_updated_at"] = date.today().isoformat()
        _save_applications(profile_name, apps)
    return _render_kanban(request, profile_name)


@router.get("/applications/{idx}/edit")
def edit_form(request: Request, idx: int):
    profile_name = state.active_profile()
    apps = _load_apps(profile_name)
    if not 0 <= idx < len(apps):
        raise HTTPException(status_code=404, detail="application not found")
    row = {**apps[idx], "_idx": idx}
    return templates.TemplateResponse(
        request, "_app_edit_card.html",
        {"request": request, "row": row, "all_statuses": ALL_STATUSES},
    )


@router.get("/applications/{idx}/card")
def get_card(request: Request, idx: int):
    profile_name = state.active_profile()
    apps = _load_apps(profile_name)
    if not 0 <= idx < len(apps):
        raise HTTPException(status_code=404, detail="application not found")
    row = {**apps[idx], "_idx": idx}
    return templates.TemplateResponse(
        request, "_app_card.html",
        {"request": request, "row": row, "all_statuses": ALL_STATUSES, "pipeline": PIPELINE},
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
    return _render_kanban(request, profile_name)


@router.delete("/applications/{idx}")
def delete_application(request: Request, idx: int):
    profile_name = state.active_profile()
    lock = state.profile_lock(profile_name)
    with lock:
        apps = _load_apps(profile_name)
        if not 0 <= idx < len(apps):
            raise HTTPException(status_code=404, detail="application not found")
        apps.pop(idx)
        _save_applications(profile_name, apps)
    return _render_kanban(request, profile_name)
