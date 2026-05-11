"""Single-job apply — `/jobs/{idx}/apply` and its 5 supporting routes.

The worker opens one browser, calls `_process_job` for one job, closes the
browser. Every decision boundary (Submit, CAPTCHA) blocks on the prompt
channel and surfaces as a modal interaction in the UI.
"""
import json
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from src.applicant import _is_already_applied, _process_job
from src.browser import get_browser_context
from src.profile_loader import Profile
from src.resume.optimizer import _slugify
from src.schemas import validate_resume
from src.tasks import prompt, runner

from ... import state
from ...deps import template_context
from ...templates_loader import templates
from .shared import _apply_status_path, _apply_task_key, _load_jobs

router = APIRouter()


def _read_apply_status(profile_name: str) -> dict:
    if not profile_name:
        return {"state": "idle"}
    return runner.read_status(
        _apply_status_path(profile_name),
        _apply_task_key(profile_name),
        interrupted_message="Apply interrupted (worker stopped). Click Start to retry.",
    )


def _flags(status: dict, job_idx: int) -> dict:
    """Per-job-page status flags.

    A batch task is always treated as "running for another job" on the
    single-job page, even if its current_index happens to point at this job
    — prompt interactions for batch live on /apply/batch only, so we don't
    surface the modal in two places."""
    is_running = status.get("state") == "running"
    is_batch = status.get("mode") == "batch"
    same_job = status.get("job_idx") == job_idx and not is_batch
    has_result = (
        status.get("state") in ("idle", "cancelled", "error", "interrupted")
        and same_job
        and status.get("result_status") is not None
    )
    pending = status.get("pending_prompt") if is_running and same_job else None
    return {
        "running_for_this_job": is_running and same_job,
        "running_for_other_job": is_running and not same_job,
        "result_for_this_job": has_result,
        "pending_prompt": pending,
    }


def _render_apply_main(
    request: Request,
    profile_name: str,
    job_idx: int,
    *,
    msg: str = "",
    err: str = "",
) -> HTMLResponse:
    jobs = _load_jobs(profile_name) if profile_name else []
    if not 0 <= job_idx < len(jobs):
        raise HTTPException(status_code=404, detail="job not found")
    job = jobs[job_idx]
    status = _read_apply_status(profile_name)
    return templates.TemplateResponse(
        request, "_apply_main.html",
        {
            "request": request,
            "job": job,
            "job_idx": job_idx,
            "status": status,
            "msg": msg,
            "err": err,
            **_flags(status, job_idx),
        },
    )


@router.get("/jobs/{idx}/apply")
def apply_page(request: Request, idx: int):
    profile_name = state.active_profile()
    jobs = _load_jobs(profile_name) if profile_name else []
    if not 0 <= idx < len(jobs):
        raise HTTPException(status_code=404, detail="job not found")
    job = jobs[idx]
    status = _read_apply_status(profile_name)
    return templates.TemplateResponse(
        request, "apply.html",
        template_context(
            request,
            page_title=f"Apply · {job.get('company', '?')}",
            job=job,
            job_idx=idx,
            status=status,
            msg="",
            err="",
            **_flags(status, idx),
        ),
    )


def _apply_worker(profile_name: str, job_idx: int, dry_run: bool) -> None:
    """Background worker — runs the single-job apply flow with prompt-channel
    callbacks. Mirrors `_process_job` from `src.applicant` but blocks on the
    UI modal instead of stdin for CAPTCHA + Submit decisions."""
    sf = _apply_status_path(profile_name)

    def work():
        runner.write_status(
            sf, phase="loading", message="Loading job and profile…",
            screenshot_path=None, result_status=None,
        )
        profile = Profile(profile_name)
        jobs = _load_jobs(profile_name)
        if not 0 <= job_idx < len(jobs):
            raise RuntimeError(f"Job index {job_idx} out of range")
        job = jobs[job_idx]

        # Dedup check up front — same composite key the CLI uses
        applications = list(profile.applications)
        if _is_already_applied(applications, job["company"], job["title"], job["posting_url"]):
            runner.write_status(
                sf, phase="skipped_duplicate",
                message=f"Already applied to {job['company']} — {job['title']}. Skipped.",
                result_status="skipped",
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

        runner.write_status(sf, phase="opening_browser", message="Opening browser…")
        state_path_file = profile.profile_dir / "browser_state.json"
        pw, browser, context = get_browser_context(
            headless=False, storage_state_path=state_path_file,
        )
        page = context.new_page()

        try:
            def captcha_handler():
                try:
                    return prompt.ask(
                        sf, "CAPTCHA detected — solve it in the browser, then click Continue.",
                        choices=["continue", "skip"],
                        poll_interval=0.5,
                    )
                except prompt.PromptCancelled:
                    return "skip"

            def submit_handler():
                try:
                    return prompt.ask(
                        sf, "Form filled. Review the screenshot/browser, then choose:",
                        choices=["submit", "skip", "quit"],
                        poll_interval=0.5,
                    )
                except prompt.PromptCancelled:
                    return "skip"

            def progress(**kw):
                runner.write_status(sf, **kw)

            name_slug = _slugify(profile.data.get("name", ""))
            resumes_dir = profile.profile_dir / "resumes"

            # UI apply always forces auto_submit=False — clicking Apply implies
            # the user wants to review.
            results = []
            _process_job(
                page, context, job, profile, resume_data, None,
                applications, results, name_slug, resumes_dir,
                False,  # auto_submit always off in UI
                dry_run,
                profile.rate_limit_seconds,
                0, 1,  # single-job: i=0, total=1
                captcha_handler=captcha_handler,
                submit_handler=submit_handler,
                progress_callback=progress,
            )
            if results:
                runner.write_status(sf, result_status=results[0]["status"])
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
        idle_message="Apply task complete.",
        cancelled_message="Apply cancelled.",
        log_prefix=f"[apply] job {job_idx}",
    )


@router.post("/jobs/{idx}/apply/start")
def start_apply(request: Request, idx: int, dry_run: str = Form("")):
    profile_name = state.active_profile()
    jobs = _load_jobs(profile_name) if profile_name else []
    if not 0 <= idx < len(jobs):
        raise HTTPException(status_code=404, detail="job not found")
    job = jobs[idx]
    is_dry_run = bool(dry_run)
    started, msg = runner.start_task(
        task_key=_apply_task_key(profile_name),
        status_file=_apply_status_path(profile_name),
        target=_apply_worker,
        args=(profile_name, idx, is_dry_run),
        initial_status={
            "mode": "single",
            "phase": "starting",
            "job_idx": idx,
            "company": job.get("company", ""),
            "role": job.get("title", ""),
            "dry_run": is_dry_run,
            "message": "Starting apply…",
            "screenshot_path": None,
            "result_status": None,
            "pending_prompt": None,
            "prompt_response": None,
        },
        thread_name=f"apply-{profile_name}-{idx}",
        already_running_msg="Another apply task is already running for this profile.",
    )
    if not started:
        return _render_apply_main(request, profile_name, idx, err=msg)
    mode = "dry run" if is_dry_run else "real submit"
    return _render_apply_main(
        request, profile_name, idx,
        msg=f"Apply started ({mode}).",
    )


@router.get("/jobs/{idx}/apply/status")
def apply_status_partial(request: Request, idx: int):
    profile_name = state.active_profile()
    return _render_apply_main(request, profile_name, idx)


@router.post("/jobs/{idx}/apply/cancel")
def apply_cancel(request: Request, idx: int):
    profile_name = state.active_profile()
    flipped = runner.request_cancel(_apply_status_path(profile_name))
    msg = (
        "Cancel requested — apply will stop at the next phase boundary or pending prompt."
        if flipped else "No apply running."
    )
    return _render_apply_main(request, profile_name, idx, msg=msg)


@router.post("/jobs/{idx}/apply/prompt")
def apply_prompt_response(
    request: Request,
    idx: int,
    prompt_id: str = Form(...),
    value: str = Form(...),
):
    """User clicked a choice on the prompt modal — submit the response so the
    blocked worker unblocks. Returns False (rendered as inline error) if the
    response doesn't match the currently pending prompt (e.g. stale tab)."""
    profile_name = state.active_profile()
    accepted = prompt.submit_response(
        _apply_status_path(profile_name), prompt_id, value,
    )
    if not accepted:
        return _render_apply_main(
            request, profile_name, idx,
            err="Prompt response rejected (stale or already resolved).",
        )
    return _render_apply_main(
        request, profile_name, idx,
        msg=f"Response sent: {value}",
    )


@router.get("/jobs/{idx}/apply/screenshot")
def apply_screenshot(idx: int):
    """Serve the latest screenshot for this job (if any)."""
    profile_name = state.active_profile()
    status = _read_apply_status(profile_name)
    if status.get("job_idx") != idx or not status.get("screenshot_path"):
        raise HTTPException(status_code=404, detail="No screenshot available for this job.")
    p = Path(status["screenshot_path"])
    if not p.exists():
        raise HTTPException(status_code=404, detail="Screenshot file missing on disk.")
    return FileResponse(p, media_type="image/png", filename=p.name)
