"""Run-pipeline route — chains the read-only discovery stages (companies → jobs)
in one background task. Per-job optimize and apply remain individual user
actions on /jobs (deferred to v2+ as multi-job batch UX)."""
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.discovery import discover_companies
from src.jobs import discover_jobs
from src.profile_loader import PROFILES_DIR
from src.tasks import runner

from .. import state
from ..templates_loader import templates

router = APIRouter()


def _pipeline_status_path(profile_name: str) -> Path:
    return PROFILES_DIR / profile_name / "pipeline_status.json"


def _pipeline_task_key(profile_name: str) -> str:
    return f"pipeline:{profile_name}"


def _read_pipeline_status(profile_name: str) -> dict:
    if not profile_name:
        return {"state": "idle"}
    return runner.read_status(
        _pipeline_status_path(profile_name),
        _pipeline_task_key(profile_name),
        interrupted_message="Pipeline interrupted (worker stopped). Re-run to retry.",
    )


def _render_pipeline_main(
    request: Request,
    profile_name: str,
    *,
    msg: str = "",
    err: str = "",
) -> HTMLResponse:
    status = _read_pipeline_status(profile_name)
    return templates.TemplateResponse(
        request, "_pipeline_main.html",
        {
            "request": request,
            "pipeline_status": status,
            "pipeline_running": status.get("state") == "running",
            "pipeline_msg": msg,
            "pipeline_err": err,
        },
    )


def _pipeline_worker(profile_name: str) -> None:
    """Sequentially run discover_companies, then discover_jobs. Each stage
    pumps its on_progress dict into the shared status file with a `stage`
    field so the dashboard banner can show which step is running."""
    sf = _pipeline_status_path(profile_name)

    def work():
        def cancel_check():
            return runner.is_cancel_requested(sf)

        # Stage 1: companies
        runner.write_status(
            sf, stage="discover", phase="starting",
            message="Step 1/2: Discovering companies from seed list…",
        )

        def discover_progress(**kw):
            # Re-prefix with stage so the template can render unified progress
            runner.write_status(sf, stage="discover", **kw)
            kw_total = kw.get("total")
            kw_processed = kw.get("processed")
            if kw_total and kw_processed is not None:
                runner.write_status(
                    sf,
                    message=(
                        f"Step 1/2: {kw_processed}/{kw_total} companies · "
                        f"{kw.get('added', 0)} added · {kw.get('skipped', 0)} skipped · "
                        f"{kw.get('failed', 0)} failed"
                    ),
                )

        discover_companies(profile_name, on_progress=discover_progress, cancel_check=cancel_check)
        if cancel_check():
            return

        # Stage 2: jobs
        runner.write_status(
            sf, stage="discover_jobs", phase="starting",
            message="Step 2/2: Discovering jobs and scoring fit…",
        )

        def jobs_progress(**kw):
            runner.write_status(sf, stage="discover_jobs", **kw)
            # discover_jobs's own message field already has good per-phase text;
            # let it pass through but prefix with "Step 2/2:" so the user knows
            # which stage they're in.
            inner = kw.get("message")
            if inner:
                runner.write_status(sf, message=f"Step 2/2: {inner}")

        discover_jobs(profile_name, on_progress=jobs_progress, cancel_check=cancel_check)
        if cancel_check():
            return

        runner.write_status(
            sf, stage="complete",
            message="Pipeline complete. Review the Jobs page to optimize / apply per role.",
        )

    runner.run_with_terminal_status(
        sf, work=work,
        idle_message="Pipeline complete. Go to /jobs to review matches.",
        cancelled_message="Pipeline cancelled. Partial results saved.",
        log_prefix="[pipeline]",
    )


@router.post("/pipeline/start")
def start_pipeline(request: Request):
    profile_name = state.active_profile()
    if not profile_name:
        return _render_pipeline_main(request, profile_name, err="No active profile.")
    started, msg = runner.start_task(
        task_key=_pipeline_task_key(profile_name),
        status_file=_pipeline_status_path(profile_name),
        target=_pipeline_worker,
        args=(profile_name,),
        initial_status={
            "stage": "starting",
            "phase": "starting",
            "message": "Starting pipeline…",
        },
        thread_name=f"pipeline-{profile_name}",
        already_running_msg="Pipeline already in progress for this profile.",
    )
    if not started:
        return _render_pipeline_main(request, profile_name, err=msg)
    return _render_pipeline_main(request, profile_name, msg="Pipeline started.")


@router.get("/pipeline/status")
def pipeline_status_partial(request: Request):
    profile_name = state.active_profile()
    return _render_pipeline_main(request, profile_name)


@router.post("/pipeline/cancel")
def pipeline_cancel(request: Request):
    profile_name = state.active_profile()
    flipped = runner.request_cancel(_pipeline_status_path(profile_name))
    msg = (
        "Cancel requested — pipeline will stop at the next stage boundary."
        if flipped else "No pipeline running."
    )
    return _render_pipeline_main(request, profile_name, msg=msg)
