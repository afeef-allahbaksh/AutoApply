"""Per-job resume optimization route. Mirrors the CLI `optimize --job N` flow:
select projects → cache check → optimize via Claude → render PDF → save.

One optimize at a time per profile (task key = `optimize:{profile}`); the status
file carries `job_idx` so the per-job page can distinguish running-for-this-job
vs running-for-another-job."""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from src.profile_loader import PROFILES_DIR
from src.resume.diff import diff_resumes
from src.resume.optimizer import (
    _optimization_hash,
    find_cached_resume,
    optimize_resume,
    save_tailored_resume,
    select_projects,
)
from src.schemas import validate_resume
from src.tasks import runner

from .. import state
from ..deps import template_context
from ..pipeline import load_jobs
from ..templates_loader import templates

router = APIRouter()


def _optimize_status_path(profile_name: str) -> Path:
    return PROFILES_DIR / profile_name / "optimize_status.json"


def _optimize_task_key(profile_name: str) -> str:
    return f"optimize:{profile_name}"


_load_jobs = load_jobs  # local alias — keep route code unchanged


def _read_optimize_status(profile_name: str) -> dict:
    if not profile_name:
        return {"state": "idle"}
    return runner.read_status(
        _optimize_status_path(profile_name),
        _optimize_task_key(profile_name),
        interrupted_message="Optimize interrupted (worker stopped). Click Start to retry.",
    )


def _flags(status: dict, job_idx: int) -> dict:
    """Derive page flags from the status dict and the job_idx the page is rendering."""
    is_running = status.get("state") == "running"
    same_job = status.get("job_idx") == job_idx
    has_result = (
        status.get("state") in ("idle", "cancelled", "error", "interrupted")
        and same_job
        and status.get("diff") is not None
    )
    return {
        "running_for_this_job": is_running and same_job,
        "running_for_other_job": is_running and not same_job,
        "result_for_this_job": has_result,
    }


def _render_optimize_main(
    request: Request,
    profile_name: str,
    job_idx: int,
    *,
    msg: str = "",
    err: str = "",
) -> HTMLResponse:
    """Render the #optimize-content partial — used by start/status/cancel routes."""
    jobs = _load_jobs(profile_name) if profile_name else []
    if not 0 <= job_idx < len(jobs):
        raise HTTPException(status_code=404, detail="job not found")
    job = jobs[job_idx]
    status = _read_optimize_status(profile_name)
    return templates.TemplateResponse(
        request, "_optimize_main.html",
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


@router.get("/jobs/{idx}/optimize")
def optimize_page(request: Request, idx: int):
    profile_name = state.active_profile()
    jobs = _load_jobs(profile_name) if profile_name else []
    if not 0 <= idx < len(jobs):
        raise HTTPException(status_code=404, detail="job not found")
    job = jobs[idx]
    status = _read_optimize_status(profile_name)
    return templates.TemplateResponse(
        request, "optimize.html",
        template_context(
            request,
            page_title=f"Optimize · {job.get('company', '?')}",
            job=job,
            job_idx=idx,
            status=status,
            msg="",
            err="",
            **_flags(status, idx),
        ),
    )


def _optimize_worker(profile_name: str, job_idx: int) -> None:
    """Background worker — runs the optimize pipeline with phase updates to the
    status file. Mirrors the CLI flow exactly (select_projects → cache check →
    optimize → save), so any future change to the CLI's logic only needs to
    update this worker too."""
    sf = _optimize_status_path(profile_name)

    def work():
        runner.write_status(
            sf, phase="loading", message="Loading job and resume…",
            diff=None, json_path=None, pdf_path=None,
        )
        jobs = _load_jobs(profile_name)
        if not 0 <= job_idx < len(jobs):
            raise RuntimeError(f"Job index {job_idx} out of range")
        job = jobs[job_idx]
        job_content = job.get("content", job["title"])

        resume_path = PROFILES_DIR / profile_name / "resume.json"
        if not resume_path.exists():
            raise RuntimeError("No resume on file for this profile. Import one from Settings and try again.")
        with open(resume_path) as f:
            base_resume = json.load(f)
        validate_resume(base_resume)

        if runner.is_cancel_requested(sf):
            return

        # Project selection
        runner.write_status(
            sf, phase="selecting_projects",
            message="Selecting projects from pool (Claude, ~$0.005)…",
        )
        selection = select_projects(base_resume, job_content)
        if selection["had_pool"]:
            tailored_base = {**base_resume, "projects": selection["projects"]}
        else:
            tailored_base = base_resume
            selection = None

        # Cache check
        runner.write_status(sf, phase="cache_check", message="Checking cache…")
        cached = find_cached_resume(profile_name, tailored_base, job_content, job["company"])
        if cached:
            runner.write_status(
                sf, phase="cached",
                message="Tailored resume already cached for this resume + JD combo.",
                diff="(Resume + JD unchanged from a prior run — no re-optimization needed. Open the PDF below to view.)",
                json_path=cached["json"],
                pdf_path=cached["pdf"],
            )
            return

        if runner.is_cancel_requested(sf):
            return

        # Optimize (the blocking LLM call — can't be cancelled mid-call)
        runner.write_status(
            sf, phase="optimizing",
            message="Tailoring resume (Claude, ~$0.02 — typically 10-30s)…",
        )
        optimized = optimize_resume(tailored_base, job_content)

        # Save: PDF render + JSON write
        runner.write_status(sf, phase="saving", message="Rendering PDF and saving…")
        diff_text = diff_resumes(tailored_base, optimized, project_selection=selection)
        opt_hash = _optimization_hash(tailored_base, job_content)
        paths = save_tailored_resume(
            profile_name, optimized, job["company"], job["title"],
            optimization_hash=opt_hash,
        )

        runner.write_status(
            sf, phase="complete",
            message="Resume tailored.",
            diff=diff_text,
            json_path=paths["json"],
            pdf_path=paths["pdf"],
        )

    runner.run_with_terminal_status(
        sf, work=work,
        idle_message="Optimize complete.",
        cancelled_message="Optimize cancelled. No tailored resume saved.",
        log_prefix=f"[optimize] job {job_idx}",
    )


@router.post("/jobs/{idx}/optimize/start")
def start_optimize(request: Request, idx: int):
    profile_name = state.active_profile()
    jobs = _load_jobs(profile_name) if profile_name else []
    if not 0 <= idx < len(jobs):
        raise HTTPException(status_code=404, detail="job not found")
    job = jobs[idx]
    started, msg = runner.start_task(
        task_key=_optimize_task_key(profile_name),
        status_file=_optimize_status_path(profile_name),
        target=_optimize_worker,
        args=(profile_name, idx),
        initial_status={
            "phase": "starting",
            "job_idx": idx,
            "company": job.get("company", ""),
            "role": job.get("title", ""),
            "message": "Starting optimize…",
            "diff": None,
            "json_path": None,
            "pdf_path": None,
        },
        thread_name=f"optimize-{profile_name}-{idx}",
        already_running_msg="Another optimize task is already running for this profile.",
    )
    if not started:
        return _render_optimize_main(request, profile_name, idx, err=msg)
    return _render_optimize_main(request, profile_name, idx, msg="Optimize started.")


@router.get("/jobs/{idx}/optimize/status")
def optimize_status_partial(request: Request, idx: int):
    profile_name = state.active_profile()
    return _render_optimize_main(request, profile_name, idx)


@router.post("/jobs/{idx}/optimize/cancel")
def optimize_cancel(request: Request, idx: int):
    profile_name = state.active_profile()
    flipped = runner.request_cancel(_optimize_status_path(profile_name))
    msg = (
        "Cancel requested — optimize will stop at the next phase boundary."
        if flipped else "No optimize running."
    )
    return _render_optimize_main(request, profile_name, idx, msg=msg)


@router.get("/jobs/{idx}/optimize/pdf")
def optimize_pdf(idx: int):
    """Serve the tailored PDF for this job. Only valid while the status file
    still points at the most recent optimize for this job_idx — re-running
    optimize for another job invalidates this path."""
    profile_name = state.active_profile()
    status = _read_optimize_status(profile_name)
    if status.get("job_idx") != idx or not status.get("pdf_path"):
        raise HTTPException(status_code=404, detail="No PDF available for this job — run optimize first.")
    pdf_path = Path(status["pdf_path"])
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF file missing on disk.")
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=pdf_path.name,
    )
