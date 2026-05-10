"""Email-driven application updates — sync inbox, review and apply proposals."""
from datetime import date

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from src.applicant import _save_applications
from src.inbox import sync as inbox_sync

from .. import state
from ..pipeline import (
    ALL_STATUSES,
    COLUMN_HEADERS,
    KANBAN_COLUMNS,
    PIPELINE,
    STATUS_BADGE_CLASS,
    kanban_groups,
    load_applications,
)
from ..templates_loader import templates

router = APIRouter()


def _render_main(request: Request, profile_name: str, sync_msg: str = "", sync_err: str = "") -> HTMLResponse:
    """Render the #applications-content block — proposals panel plus kanban."""
    apps = load_applications(profile_name) if profile_name else []
    columns, closed = kanban_groups(apps)
    proposals = inbox_sync.enrich_proposals(profile_name) if profile_name else []
    sync_status = inbox_sync.read_sync_status(profile_name) if profile_name else {"state": "idle"}
    return templates.TemplateResponse(
        request, "_applications_main.html",
        {
            "request": request,
            "columns": columns,
            "kanban_columns": KANBAN_COLUMNS,
            "column_headers": COLUMN_HEADERS,
            "closed": closed,
            "all_statuses": ALL_STATUSES,
            "pipeline": PIPELINE,
            "status_badge_class": STATUS_BADGE_CLASS,
            "proposals": proposals,
            "sync_status": sync_status,
            "sync_running": sync_status.get("state") == "running",
            "sync_msg": sync_msg,
            "sync_err": sync_err,
        },
    )


@router.post("/email/sync")
def sync_inbox(request: Request, deep: str = Form("")):
    """Spawn a background sync and return immediately.

    The kanban polls /email/sync_status every 4s while a sync is running and
    sees proposals appear as the worker writes them.
    """
    profile_name = state.active_profile()
    started, message = inbox_sync.start_background_sync(profile_name, deep=bool(deep))
    if not started:
        return _render_main(request, profile_name, sync_err=message)
    mode = "Full history sync" if deep else "Sync"
    return _render_main(request, profile_name, sync_msg=f"{mode} started in the background.")


@router.get("/email/sync_status")
def sync_status_partial(request: Request):
    """Polled by the in-progress UI. Returns the same #applications-content block
    every time; when status flips to idle the wrapper renders without an
    hx-trigger, so polling stops naturally."""
    profile_name = state.active_profile()
    return _render_main(request, profile_name)


@router.post("/email/proposals/{proposal_id}/apply")
def apply_proposal(request: Request, proposal_id: str, target_idx: str = Form("")):
    profile_name = state.active_profile()
    lock = state.profile_lock(profile_name)
    with lock:
        proposals = inbox_sync.load_proposals(profile_name)
        prop = next((p for p in proposals if p["id"] == proposal_id), None)
        if not prop:
            return _render_main(request, profile_name, sync_err="Proposal not found (already handled?).")

        apps = load_applications(profile_name)
        today = date.today().isoformat()
        thread_id = prop.get("thread_id") or ""

        if prop["action_type"] == "new_application":
            entry = {
                "company": prop.get("company") or "Unknown",
                "role": prop.get("role") or "Unknown",
                "posting_url": prop.get("message_url") or "https://mail.google.com",
                "date": (prop.get("message_received_at") or today)[:10],
                "status": prop["proposed_status"],
                "status_updated_at": today,
                "source": "email",
            }
            if thread_id:
                entry["email_thread_ids"] = [thread_id]
            apps.append(entry)
        else:
            if target_idx.strip():
                try:
                    idx = int(target_idx)
                except ValueError:
                    return _render_main(request, profile_name, sync_err="Bad target index.")
            elif prop.get("target_idx") is not None:
                idx = prop["target_idx"]
            elif prop.get("candidates"):
                idx = prop["candidates"][0]
            else:
                return _render_main(request, profile_name, sync_err="No target application for this update.")
            if not 0 <= idx < len(apps):
                return _render_main(request, profile_name, sync_err=f"Application index {idx} no longer exists.")
            apps[idx]["status"] = prop["proposed_status"]
            apps[idx]["status_updated_at"] = today
            if thread_id:
                threads = list(apps[idx].get("email_thread_ids") or [])
                if thread_id not in threads:
                    threads.append(thread_id)
                    apps[idx]["email_thread_ids"] = threads

        _save_applications(profile_name, apps)
        inbox_sync.save_proposals(profile_name, [p for p in proposals if p["id"] != proposal_id])

    return _render_main(request, profile_name, sync_msg="Applied.")


@router.post("/email/proposals/{proposal_id}/dismiss")
def dismiss_proposal(request: Request, proposal_id: str):
    profile_name = state.active_profile()
    lock = state.profile_lock(profile_name)
    with lock:
        inbox_sync.remove_proposal(profile_name, proposal_id)
    return _render_main(request, profile_name, sync_msg="Dismissed.")
