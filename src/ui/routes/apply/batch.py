"""Batch apply — `/apply/batch` and its 5 supporting routes.

Shares the single-job task key + status file so the two modes are mutually
exclusive (one Playwright session per profile). Pre-filters dedup'd jobs up
front so the browser never opens for an all-dupes selection.
"""
import json
from pathlib import Path

from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from src.applicant import _process_job
from src.browser import get_browser_context
from src.profile_loader import Profile
from src.resume.optimizer import _slugify
from src.schemas import validate_resume
from src.tasks import prompt, runner

from ... import state
from ...deps import template_context
from ...templates_loader import templates
from .shared import (
    _append_completed, _apply_status_path, _apply_task_key,
    _load_jobs, _partition_selected,
)

router = APIRouter()


def _read_batch_status(profile_name: str) -> dict:
    if not profile_name:
        return {"state": "idle"}
    return runner.read_status(
        _apply_status_path(profile_name),
        _apply_task_key(profile_name),
        interrupted_message="Batch apply interrupted (worker stopped). Re-start to retry.",
    )


def _batch_flags(status: dict) -> dict:
    """Flags consumed by _batch_apply_main.html."""
    is_batch_running = (
        status.get("state") == "running" and status.get("mode") == "batch"
    )
    has_batch_result = (
        status.get("state") in ("idle", "cancelled", "error", "interrupted")
        and status.get("mode") == "batch"
        and status.get("phase") == "complete"
    )
    pending = status.get("pending_prompt") if is_batch_running else None
    return {
        "is_batch_running": is_batch_running,
        "has_batch_result": has_batch_result,
        "pending_prompt": pending,
    }


def _render_batch_main(
    request: Request,
    profile_name: str,
    *,
    msg: str = "",
    err: str = "",
) -> HTMLResponse:
    status = _read_batch_status(profile_name)
    return templates.TemplateResponse(
        request, "_batch_apply_main.html",
        {
            "request": request,
            "status": status,
            "msg": msg,
            "err": err,
            **_batch_flags(status),
        },
    )


def _batch_apply_worker(
    profile_name: str,
    selected_indices: list[int],
    dry_run: bool,
) -> None:
    """Background worker — applies to each selected job in one Playwright
    session. Quit and cancel both break the loop and mark the remaining
    indices as `not_attempted`."""
    sf = _apply_status_path(profile_name)

    def work():
        runner.write_status(
            sf, phase="loading", message="Loading jobs and profile…",
        )
        profile = Profile(profile_name)
        jobs = _load_jobs(profile_name)
        applications = list(profile.applications)

        to_apply, already_applied = _partition_selected(selected_indices, jobs, applications)
        for skip in already_applied:
            _append_completed(sf, skip)

        if not to_apply:
            runner.write_status(
                sf, phase="complete",
                message=(
                    f"All {len(already_applied)} selected jobs already applied to — nothing to do."
                    if already_applied else "No valid jobs selected."
                ),
            )
            return

        resume_data = None
        resume_path = profile.profile_dir / "resume.json"
        if resume_path.exists():
            with open(resume_path) as f:
                resume_data = json.load(f)
            validate_resume(resume_data)

        if runner.is_cancel_requested(sf):
            return

        runner.write_status(
            sf, phase="opening_browser",
            message=f"Opening browser for {len(to_apply)} job(s)…",
        )
        state_path_file = profile.profile_dir / "browser_state.json"
        pw, browser, context = get_browser_context(
            headless=False, storage_state_path=state_path_file,
        )
        page = context.new_page()

        try:
            # NB: handlers DO NOT catch PromptCancelled. Letting it propagate
            # lets the loop body distinguish "user clicked skip" (record as
            # skipped) from "cancel raced mid-prompt" (record as not_attempted
            # via the cleanup pass). The same logic applies to all three
            # handlers — a cancel that fires while CAPTCHA/verification is
            # pending should also be recorded as not_attempted, not skipped.
            def captcha_handler():
                return prompt.ask(
                    sf, "CAPTCHA detected — solve it in the browser, then click Continue.",
                    choices=["continue", "skip"],
                    poll_interval=0.5,
                )

            def submit_handler():
                return prompt.ask(
                    sf, "Form filled. Review the screenshot/browser, then choose:",
                    choices=["submit", "skip", "quit"],
                    poll_interval=0.5,
                )

            def verification_handler(email: str) -> str:
                return prompt.ask(
                    sf,
                    f"Email verification needed — Greenhouse sent an 8-character code to {email}. "
                    "Check your inbox, enter the 8 digits into the browser, then click Verified below.",
                    choices=["verified", "skip"],
                    poll_interval=0.5,
                )

            def progress(**kw):
                runner.write_status(sf, **kw)

            name_slug = _slugify(profile.data.get("name", ""))
            resumes_dir = profile.profile_dir / "resumes"
            total = len(to_apply)
            quit_loop = False

            for i, item in enumerate(to_apply):
                if runner.is_cancel_requested(sf):
                    break
                job = item["job"]
                # Re-stamp single-job-shaped fields each iteration so the
                # screenshot route + result-status logic continues to work.
                runner.write_status(
                    sf,
                    current_index=i,
                    job_idx=item["idx"],
                    company=item["company"],
                    role=item["role"],
                    phase="starting_job",
                    message=f"Applying to {item['company']} — {item['role']} ({i + 1} of {total})…",
                    screenshot_path=None,
                    result_status=None,
                )

                per_job_results: list[dict] = []
                record_this_job = True
                job_status = "failed"
                try:
                    quit_loop = _process_job(
                        page, context, job, profile, resume_data, None,
                        applications, per_job_results, name_slug, resumes_dir,
                        False,  # auto_submit always off in UI
                        dry_run,
                        profile.rate_limit_seconds,
                        i, total,
                        captcha_handler=captcha_handler,
                        submit_handler=submit_handler,
                        verification_handler=verification_handler,
                        progress_callback=progress,
                    )
                    job_status = per_job_results[0]["status"] if per_job_results else "failed"
                except prompt.PromptCancelled:
                    # Cancel fired while a prompt was pending. Don't record this
                    # idx — the cleanup pass below marks it as not_attempted,
                    # which is the right signal for "we never got the user's
                    # actual intent on this job" (vs "skipped" = explicit skip).
                    print(f"  [batch] job {item['idx']} cancelled mid-prompt — not_attempted")
                    record_this_job = False
                except Exception as e:  # noqa: BLE001 — one bad job shouldn't kill the batch
                    print(f"  [batch] job {item['idx']} crashed: {e}")
                    import traceback
                    import sys
                    traceback.print_exc(file=sys.stderr)
                    job_status = "failed"

                if record_this_job:
                    _append_completed(sf, {
                        "idx": item["idx"],
                        "company": item["company"],
                        "role": item["role"],
                        "status": job_status,
                    })

                if quit_loop:
                    break
                if runner.is_cancel_requested(sf):
                    break

            # Cancel can break before _process_job runs (top-of-loop check),
            # quit can break after. Drive the not_attempted cleanup off the
            # actual completed_results so we don't have to track whether i
            # was processed — anything in to_apply whose idx isn't recorded
            # is genuinely un-attempted.
            current_results = runner.read_status_raw(sf).get("completed_results") or []
            processed_indices = {r["idx"] for r in current_results}
            for item in to_apply:
                if item["idx"] not in processed_indices:
                    _append_completed(sf, {
                        "idx": item["idx"],
                        "company": item["company"],
                        "role": item["role"],
                        "status": "not_attempted",
                    })

            final = runner.read_status_raw(sf)
            results = final.get("completed_results") or []
            applied = sum(1 for r in results if r["status"] == "applied")
            runner.write_status(
                sf, phase="complete",
                message=f"Batch complete · {applied} applied · {len(results)} processed.",
                result_status="batch_complete",
            )
        finally:
            try:
                context.storage_state(path=str(state_path_file))
            except Exception as e:
                print(f"  Warning: could not save browser state: {e}")
            try:
                context.close()
                browser.close()
                pw.stop()
            except Exception as e:
                print(f"  Warning: error closing browser: {e}")

    runner.run_with_terminal_status(
        sf, work=work,
        idle_message="Batch apply complete.",
        cancelled_message="Batch apply cancelled.",
        log_prefix=f"[apply-batch] {len(selected_indices)} jobs",
    )


@router.get("/apply/batch")
def batch_apply_page(request: Request, msg: str = "", err: str = ""):
    profile_name = state.active_profile()
    status = _read_batch_status(profile_name)
    return templates.TemplateResponse(
        request, "apply_batch.html",
        template_context(
            request,
            page_title="Batch apply",
            status=status,
            msg=msg,
            err=err,
            **_batch_flags(status),
        ),
    )


@router.post("/apply/batch/start")
def start_batch_apply(
    request: Request,
    job_indices: list[int] = Form(default=[]),
    dry_run: str = Form(""),
):
    """Spawn the batch worker, then redirect to GET /apply/batch (PRG pattern).

    The Jobs-page form is a plain HTML submit (not HTMX), so returning a
    partial here would render naked on the browser. Redirect lands the user
    on the full styled page that polls for progress.

    Shares task key `apply:{profile}` with single-job apply, so either mode
    blocks the other.
    """
    profile_name = state.active_profile()
    if not profile_name:
        return RedirectResponse(url=f"/apply/batch?err={quote('No active profile.')}", status_code=303)
    if not job_indices:
        return RedirectResponse(url=f"/apply/batch?err={quote('No jobs selected.')}", status_code=303)

    is_dry_run = bool(dry_run)
    started, msg = runner.start_task(
        task_key=_apply_task_key(profile_name),
        status_file=_apply_status_path(profile_name),
        target=_batch_apply_worker,
        args=(profile_name, list(job_indices), is_dry_run),
        initial_status={
            "mode": "batch",
            "phase": "starting",
            "selected_indices": list(job_indices),
            "current_index": 0,
            "completed_results": [],
            "dry_run": is_dry_run,
            "message": f"Starting batch apply ({len(job_indices)} job(s))…",
            "screenshot_path": None,
            "result_status": None,
            "pending_prompt": None,
            "prompt_response": None,
        },
        thread_name=f"apply-batch-{profile_name}",
        already_running_msg="Another apply task is already running for this profile.",
    )
    if not started:
        return RedirectResponse(url=f"/apply/batch?err={quote(msg)}", status_code=303)
    mode = "dry run" if is_dry_run else "real submit"
    summary = f"Batch apply started ({mode}, {len(job_indices)} job(s))."
    return RedirectResponse(url=f"/apply/batch?msg={quote(summary)}", status_code=303)


@router.get("/apply/batch/status")
def batch_status_partial(request: Request):
    profile_name = state.active_profile()
    return _render_batch_main(request, profile_name)


@router.post("/apply/batch/cancel")
def batch_cancel(request: Request):
    profile_name = state.active_profile()
    flipped = runner.request_cancel(_apply_status_path(profile_name))
    msg = (
        "Cancel requested — batch will stop after the current job's pending action resolves."
        if flipped else "No batch running."
    )
    return _render_batch_main(request, profile_name, msg=msg)


@router.post("/apply/batch/prompt")
def batch_prompt_response(
    request: Request,
    prompt_id: str = Form(...),
    value: str = Form(...),
):
    profile_name = state.active_profile()
    accepted = prompt.submit_response(
        _apply_status_path(profile_name), prompt_id, value,
    )
    if not accepted:
        return _render_batch_main(
            request, profile_name,
            err="Prompt response rejected (stale or already resolved).",
        )
    return _render_batch_main(
        request, profile_name,
        msg=f"Response sent: {value}",
    )


@router.get("/apply/batch/screenshot")
def batch_screenshot():
    """Serve the current job's latest screenshot during a batch run."""
    profile_name = state.active_profile()
    status = _read_batch_status(profile_name)
    if status.get("mode") != "batch" or not status.get("screenshot_path"):
        raise HTTPException(status_code=404, detail="No screenshot available.")
    p = Path(status["screenshot_path"])
    if not p.exists():
        raise HTTPException(status_code=404, detail="Screenshot file missing on disk.")
    return FileResponse(p, media_type="image/png", filename=p.name)
