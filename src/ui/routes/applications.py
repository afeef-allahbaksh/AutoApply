import re
from datetime import date

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from src.applicant import _save_applications

from .. import state
from ..deps import template_context
from ..pipeline import (
    ALL_STATUSES,
    COLUMN_HEADERS,
    KANBAN_COLUMNS,
    PIPELINE,
    STATUS_BADGE_CLASS,
    kanban_groups,
    load_applications,
    timeline_rows,
)
from ..templates_loader import templates

# YYYY-MM-DD — schema requires this format on every entry.
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

router = APIRouter()


def _render_kanban(request: Request, profile_name: str) -> HTMLResponse:
    """Render only the #kanban-grid partial — used by mutation handlers (drag,
    edit, delete, manual add). The page-level search filter is intentionally
    dropped on re-render so a moved card always appears in its new column."""
    apps = load_applications(profile_name) if profile_name else []
    columns, closed = kanban_groups(apps)
    return templates.TemplateResponse(
        request, "_app_kanban.html",
        {
            "request": request,
            "columns": columns,
            "kanban_columns": KANBAN_COLUMNS,
            "column_headers": COLUMN_HEADERS,
            "closed": closed,
            "all_statuses": ALL_STATUSES,
            "pipeline": PIPELINE,
            "status_badge_class": STATUS_BADGE_CLASS,
        },
    )


@router.get("/applications")
def applications_page(request: Request, q: str = "", view: str = "kanban"):
    from src.inbox import auth as inbox_auth
    from src.inbox import sync as inbox_sync

    if view not in ("kanban", "timeline"):
        view = "kanban"

    profile_name = state.active_profile()
    apps = load_applications(profile_name) if profile_name else []
    columns, closed = kanban_groups(apps, q)
    rows = timeline_rows(apps, q) if view == "timeline" else []
    proposals = inbox_sync.enrich_proposals(profile_name) if profile_name else []
    inbox_connected = inbox_auth.is_connected(profile_name) if profile_name else False
    return templates.TemplateResponse(
        request, "applications.html",
        template_context(
            request,
            page_title="Applications",
            view=view,
            columns=columns,
            kanban_columns=KANBAN_COLUMNS,
            column_headers=COLUMN_HEADERS,
            closed=closed,
            rows=rows,
            q=q,
            all_statuses=ALL_STATUSES,
            pipeline=PIPELINE,
            status_badge_class=STATUS_BADGE_CLASS,
            proposals=proposals,
            inbox_connected=inbox_connected,
            sync_msg="",
            sync_err="",
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
    if not _DATE_PATTERN.match(date_str):
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
    if status not in ALL_STATUSES:
        raise HTTPException(status_code=400, detail=f"invalid status: {status}")

    profile_name = state.active_profile()
    lock = state.profile_lock(profile_name)
    with lock:
        apps = load_applications(profile_name)
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
        apps = load_applications(profile_name)
        if not 0 <= idx < len(apps):
            raise HTTPException(status_code=404, detail="application not found")
        apps[idx]["status"] = new_status
        apps[idx]["status_updated_at"] = date.today().isoformat()
        _save_applications(profile_name, apps)
    return _render_kanban(request, profile_name)


@router.get("/applications/{idx}/edit")
def edit_form(request: Request, idx: int):
    profile_name = state.active_profile()
    apps = load_applications(profile_name)
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
    apps = load_applications(profile_name)
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
    if not _DATE_PATTERN.match(date_str):
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
    profile_name = state.active_profile()
    lock = state.profile_lock(profile_name)
    with lock:
        apps = load_applications(profile_name)
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
        apps = load_applications(profile_name)
        if not 0 <= idx < len(apps):
            raise HTTPException(status_code=404, detail="application not found")
        apps.pop(idx)
        _save_applications(profile_name, apps)
    return _render_kanban(request, profile_name)
