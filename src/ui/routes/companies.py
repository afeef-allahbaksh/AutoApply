from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from src.discovery import _load_companies, _save_companies, discover_companies, validate_slug
from src.profile_loader import PROFILES_DIR
from src.tasks import runner

from .. import state
from ..deps import template_context
from ..templates_loader import templates

router = APIRouter()


def _all_companies(profile_name: str) -> list:
    return _load_companies(profile_name)


def _discover_status_path(profile_name: str) -> Path:
    return PROFILES_DIR / profile_name / "discover_status.json"


def _discover_task_key(profile_name: str) -> str:
    return f"discover:{profile_name}"


def _read_discover_status(profile_name: str) -> dict:
    if not profile_name:
        return {"state": "idle"}
    return runner.read_status(
        _discover_status_path(profile_name),
        _discover_task_key(profile_name),
        interrupted_message="Discovery interrupted (worker stopped). Click Discover to retry.",
    )


def _render_companies_main(
    request: Request,
    profile_name: str,
    *,
    error: str = "",
    added: str = "",
    discover_msg: str = "",
    discover_err: str = "",
) -> HTMLResponse:
    """Render the #companies-content partial — used by every mutating route so
    HTMX swaps the inner block, not the whole page."""
    companies = _all_companies(profile_name) if profile_name else []
    discover_status = _read_discover_status(profile_name)
    return templates.TemplateResponse(
        request, "_companies_main.html",
        {
            "request": request,
            "companies": companies,
            "error": error,
            "added": added,
            "discover_status": discover_status,
            "discover_running": discover_status.get("state") == "running",
            "discover_msg": discover_msg,
            "discover_err": discover_err,
        },
    )


@router.get("/companies")
def companies_page(request: Request, error: str = "", added: str = ""):
    profile_name = state.active_profile()
    companies = _all_companies(profile_name) if profile_name else []
    discover_status = _read_discover_status(profile_name)
    return templates.TemplateResponse(
        request, "companies.html",
        template_context(
            request,
            page_title="Companies",
            companies=companies,
            error=error,
            added=added,
            discover_status=discover_status,
            discover_running=discover_status.get("state") == "running",
            discover_msg="",
            discover_err="",
        ),
    )


@router.post("/companies")
def add_company(
    request: Request,
    slug: str = Form(...),
    ats: str = Form("auto"),
):
    slug = slug.strip().lower()
    if not slug:
        raise HTTPException(status_code=400, detail="slug is required")

    profile_name = state.active_profile()
    lock = state.profile_lock(profile_name)

    with lock:
        companies = _all_companies(profile_name)
        existing = {c["slug"] for c in companies}
        if slug in existing:
            return _render_companies_main(
                request, profile_name,
                error=f"'{slug}' is already in the list.",
            )

        if ats == "auto":
            result = validate_slug(slug, "greenhouse")
            detected = "greenhouse"
            if not result:
                result = validate_slug(slug, "lever")
                detected = "lever"
        elif ats in ("greenhouse", "lever"):
            result = validate_slug(slug, ats)
            detected = ats
        else:
            raise HTTPException(status_code=400, detail=f"invalid ats: {ats}")

        if not result:
            target = "Greenhouse or Lever" if ats == "auto" else ats
            return _render_companies_main(
                request, profile_name,
                error=f"Could not find '{slug}' on {target}.",
            )

        companies.append(result)
        _save_companies(profile_name, companies)

    return _render_companies_main(
        request, profile_name,
        added=f"Added {result['name']} ({detected}).",
    )


def _discover_worker(profile_name: str) -> None:
    """Background worker — validates the seed list, streams progress to the
    discover status file."""
    sf = _discover_status_path(profile_name)

    def work():
        def on_progress(*, added, skipped, failed, processed, total):
            runner.write_status(
                sf,
                added=added,
                skipped=skipped,
                failed=failed,
                processed=processed,
                total=total,
                message=(
                    f"Validating slug {processed}/{total} · "
                    f"{added} added · {skipped} skipped · {failed} failed"
                ),
            )

        def cancel_check():
            return runner.is_cancel_requested(sf)

        discover_companies(
            profile_name,
            on_progress=on_progress,
            cancel_check=cancel_check,
        )
        # No final summary write here — run_with_terminal_status will overwrite
        # `message` with the idle_message anyway. The count fields persist
        # across that terminal write, and the template composes the completion
        # badge directly from them.

    runner.run_with_terminal_status(
        sf,
        work=work,
        idle_message="Discovery complete.",
        cancelled_message="Discovery cancelled. Partial results saved.",
        log_prefix="[discover]",
    )


@router.post("/companies/discover")
def start_discover(request: Request):
    profile_name = state.active_profile()
    if not profile_name:
        return _render_companies_main(
            request, profile_name,
            discover_err="No active profile.",
        )
    started, msg = runner.start_task(
        task_key=_discover_task_key(profile_name),
        status_file=_discover_status_path(profile_name),
        target=_discover_worker,
        args=(profile_name,),
        initial_status={
            "added": 0,
            "skipped": 0,
            "failed": 0,
            "processed": 0,
            "total": 0,
            "message": "Starting discovery…",
        },
        thread_name=f"discover-{profile_name}",
        already_running_msg="Discovery already in progress for this profile.",
    )
    if not started:
        return _render_companies_main(request, profile_name, discover_err=msg)
    return _render_companies_main(
        request, profile_name,
        discover_msg="Discovery started in the background.",
    )


@router.get("/companies/discover_status")
def discover_status(request: Request):
    """Polled while a discovery is running. Returns the same #companies-content
    block; when state flips to idle the wrapper drops its hx-trigger and polling
    stops naturally."""
    profile_name = state.active_profile()
    return _render_companies_main(request, profile_name)


@router.post("/companies/discover_cancel")
def discover_cancel(request: Request):
    profile_name = state.active_profile()
    flipped = runner.request_cancel(_discover_status_path(profile_name))
    msg = (
        "Cancel requested — discovery will stop at the next slug boundary."
        if flipped else "No discovery running."
    )
    return _render_companies_main(request, profile_name, discover_msg=msg)
