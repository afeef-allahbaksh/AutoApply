import json
import random
import re
import time
from datetime import date
from pathlib import Path

from src.ats.ashby import fill_ashby_application
from src.ats.greenhouse import fill_greenhouse_application
from src.ats.lever import fill_lever_application
from src.profile_loader import PROFILES_DIR, _atomic_write_json, normalize_posting_url
from src.schemas import validate_applications
from src.resume.optimizer import (
    find_cached_resume, optimize_resume,
    _optimization_hash, _slugify, save_tailored_resume, select_projects,
)


def _handle_post_submit_verification(page, verification_handler, progress_callback) -> str:
    """After a Submit click, detect Greenhouse's post-submit email verification
    challenge and pause for the user. Returns the final status string.

    Greenhouse rolls out an 8-character email verification step on some boards:
    after Submit, the page shows `<fieldset id="email-verification">` with a
    legend like "A verification code was sent to ___" and 8 single-char input
    boxes. The application isn't accepted until the code is entered.

    Returns:
      'applied'         — no verification, or verification completed
      'review_pending'  — verification skipped by user
    """
    verification = page.locator('#email-verification')
    if verification.count() == 0:
        return "applied"

    # If no handler wired, we can't pause — record review_pending so the user
    # knows this one didn't actually go through.
    if verification_handler is None:
        print("  Email verification detected but no handler wired — marking review_pending.")
        return "review_pending"

    legend_text = ""
    try:
        legend_text = verification.locator('legend').first.inner_text()
    except Exception:
        pass
    m = re.search(r'sent to (\S+@\S+)', legend_text)
    verify_email = m.group(1).rstrip('.') if m else "the email on the form"

    print(f"  Email verification challenge — code sent to {verify_email}.")
    if progress_callback is not None:
        progress_callback(
            phase="awaiting_verification",
            message=f"Email verification needed — code sent to {verify_email}.",
        )

    verify_action = verification_handler(verify_email)
    if verify_action != "verified":
        print("  Verification skipped — recorded as review_pending.")
        return "review_pending"

    # User says they entered the code. Greenhouse may auto-submit once the 8th
    # digit is filled (fieldset disappears) — or it may need another click.
    time.sleep(2)
    if page.locator('#email-verification').count() > 0:
        submit_btn = page.locator('button:has-text("Submit")')
        if submit_btn.count() > 0:
            submit_btn.first.click()
            time.sleep(3)
    print("  Email verified, application resubmitted.")
    return "applied"


def _save_progress(profile_name: str, job: dict, fields_filled: list, custom_answers: list) -> str:
    """Save progress state for a partially filled application."""
    progress_dir = PROFILES_DIR / profile_name / "progress"
    progress_dir.mkdir(exist_ok=True)

    state = {
        "job": job,
        "fields_filled": fields_filled,
        "custom_answers": custom_answers,
        "date": date.today().isoformat(),
    }

    filename = f"{job.get('company', 'unknown')}_{job.get('title', 'unknown')}".replace(" ", "_").lower()[:60]
    path = progress_dir / f"{filename}.json"
    with open(path, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")
    return str(path)


def _save_applications(profile_name: str, applications: list) -> None:
    """Write applications list to disk after validation.

    Module-level helper kept because several call sites import it directly
    (jobs.track_job, cold-email route, _process_job, test mocks). Uses the
    `_atomic_write_json` primitive instead of going through Profile, so the
    helper works in contexts where profile.json may not exist yet (test
    fixtures, partial profile setup).
    """
    _atomic_write_json(
        PROFILES_DIR / profile_name / "applications.json",
        applications,
        validate_applications,
    )


def _is_already_applied(applications: list, company: str, role: str, posting_url: str) -> bool:
    """Check composite key dedup, normalizing URLs to ignore tracking params."""
    target = normalize_posting_url(posting_url)
    return any(
        a["company"] == company and a["role"] == role
        and normalize_posting_url(a["posting_url"]) == target
        for a in applications
    )


def _take_screenshot(page, profile_name: str, company: str, role: str) -> str:
    """Take a screenshot of the filled form for review."""
    screenshots_dir = PROFILES_DIR / profile_name / "screenshots"
    screenshots_dir.mkdir(exist_ok=True)
    filename = f"{company}_{role}_{date.today().isoformat()}.png".replace(" ", "_").lower()[:80]
    path = screenshots_dir / filename
    page.screenshot(path=str(path), full_page=True)
    return str(path)


# ----------------------------------------------------------------------------
# Per-stage helpers used by `_process_job`. Each does one thing and is small
# enough to unit-test by mocking its inputs (Playwright page, Profile, etc.).
# The orchestrator at the bottom composes them top-to-bottom.
# ----------------------------------------------------------------------------


def _prepare_tailored_resume(
    profile,
    job: dict,
    resume_data: dict | None,
    project_selections: list | None,
    name_slug: str,
    resumes_dir: Path,
    i: int,
    progress_callback=None,
) -> str:
    """Find an existing tailored resume PDF, or generate one via Claude.

    Strategy:
      1. Look for `{name_slug}_{company}*.pdf` in `resumes_dir`
      2. Fall back to `{company}_{role}*.pdf` (legacy naming)
      3. If neither exists and we have base resume data, run project selection
         + optimization + render to PDF (cache-aware via `find_cached_resume`)

    Returns the PDF path string, or `""` if no base resume is available
    (caller submits with whatever resume the form auto-uploads, if any).
    """
    company = job["company"]
    role = job["title"]
    if progress_callback is not None:
        progress_callback(phase="tailoring_resume", message="Checking for tailored resume…",
                          company=company, role=role)

    resume_path = ""
    if resumes_dir.exists():
        matching = sorted(resumes_dir.glob(f"{name_slug}_{_slugify(company)}*.pdf"), reverse=True)
        if not matching:
            matching = sorted(resumes_dir.glob(f"{_slugify(company)}_{_slugify(role)}*.pdf"), reverse=True)
        if matching:
            resume_path = str(matching[0])
            print(f"  Using tailored resume: {matching[0].name}")

    if resume_path or not resume_data:
        return resume_path

    try:
        job_content = job.get("content", role)
        # Use batched project selection if available, else single call.
        if project_selections and project_selections[i]["had_pool"]:
            selection = project_selections[i]
        else:
            selection = select_projects(resume_data, job_content)

        if selection["had_pool"]:
            print(f"  Selected projects: {', '.join(p['name'] for p in selection['projects'])}")
            tailored_base = {**resume_data, "projects": selection["projects"]}
        else:
            tailored_base = resume_data

        cached = find_cached_resume(profile.profile_name, tailored_base, job_content, company)
        if cached:
            print(f"  Using cached resume: {Path(cached['pdf']).name}")
            return cached["pdf"]

        print("  Generating tailored resume...")
        optimized = optimize_resume(tailored_base, job_content)
        opt_hash = _optimization_hash(tailored_base, job_content)
        paths = save_tailored_resume(
            profile.profile_name, optimized, company, role, optimization_hash=opt_hash,
        )
        print(f"  Saved: {Path(paths['pdf']).name}")
        return paths["pdf"]
    except Exception as e:
        # Failure here isn't fatal — submit with whatever default the form has.
        print(f"  Warning: Could not generate tailored resume: {e}")
        return ""


def _fill_with_captcha_retry(
    page,
    job: dict,
    profile,
    resume_data: dict | None,
    resume_path: str,
    captcha_handler,
    progress_callback=None,
) -> dict:
    """Fill the ATS form. On first failure, scan for CAPTCHA indicators; if
    present, pause for the user via `captcha_handler` and retry the fill once.

    Returns the final `fill_result` dict from the ATS handler. For unsupported
    ATSes, returns a synthetic `{"success": False, "_unsupported": True}` so
    the caller can record `skipped` without crashing.
    """
    ats = job.get("ats", "")
    fill_kwargs = dict(
        page=page, job_url=job["posting_url"], profile_data=profile.data,
        responses=profile.responses, resume_path=resume_path,
        job_content=job.get("content", ""), resume_data=resume_data,
        company=job["company"], role=job["title"],
    )

    if progress_callback is not None:
        progress_callback(phase="filling_form", message=f"Filling {ats} application form…")

    if ats == "greenhouse":
        fill_fn = fill_greenhouse_application
    elif ats == "lever":
        fill_fn = fill_lever_application
    elif ats == "ashby":
        fill_fn = fill_ashby_application
    else:
        print(f"  Skipped: unsupported ATS '{ats}'")
        return {"success": False, "error": f"unsupported ATS '{ats}'", "_unsupported": True}

    fill_result = fill_fn(**fill_kwargs)
    if fill_result["success"]:
        return fill_result

    error_msg = fill_result.get("error") or "Unknown error"
    print(f"  Failed: {error_msg}")
    fail_screenshot = _take_screenshot(
        page, profile.profile_name, job["company"], f"{job['title']}_FAILED",
    )
    print(f"  Failure screenshot: {fail_screenshot}")

    page_text = page.content().lower()
    if not any(kw in page_text for kw in ("captcha", "recaptcha", "hcaptcha")):
        return fill_result  # not a CAPTCHA failure — return as-is

    print("  CAPTCHA detected! Pausing for manual intervention.")
    if progress_callback is not None:
        progress_callback(phase="captcha_pause",
                          message="CAPTCHA detected — solve in the browser, then continue.")
    if captcha_handler() != "continue":
        print("  Skipping CAPTCHA'd application.")
        return fill_result

    # Retry once on the same page (the user just solved the CAPTCHA)
    return fill_fn(**fill_kwargs)


def _record_failure_entry(
    profile_name: str,
    job: dict,
    fill_result: dict,
    applications: list,
) -> None:
    """Save the partial-fill progress checkpoint + append a failed entry to
    `applications.json`. Used when CAPTCHA retry didn't succeed."""
    progress_path = _save_progress(
        profile_name, job,
        fill_result.get("fields_filled", []),
        fill_result.get("custom_answers", []),
    )
    print(f"  Progress saved: {progress_path}")

    fail_entry = {
        "company": job["company"],
        "role": job["title"],
        "posting_url": job["posting_url"],
        "date": date.today().isoformat(),
        "status": "failed",
        "ats": job.get("ats", ""),
        "error": fill_result.get("error") or "Unknown error",
        "status_updated_at": date.today().isoformat(),
        "source": "autoapply",
    }
    if job.get("fit_score") is not None:
        fail_entry["fit_score"] = job["fit_score"]
        fail_entry["fit_rationale"] = job.get("fit_rationale", "")
    applications.append(fail_entry)
    _save_applications(profile_name, applications)


def _decide_submit_action(
    page,
    job: dict,
    auto_submit: bool,
    dry_run: bool,
    submit_handler,
    verification_handler,
    progress_callback=None,
) -> tuple[str, bool]:
    """Decide what to do after the form is filled: dry-run, auto-submit, or
    prompt the user. Execute the chosen action, handle post-submit verification,
    and return `(status, quit_loop)` where `quit_loop` is True if the user
    asked to break out of the batch.

    Status values:
      - 'dry_run'        — form filled, never clicked submit (dry_run=True)
      - 'applied'        — submit clicked + verified (or no verification)
      - 'skipped'        — user clicked skip on the modal
      - 'failed'         — submit button not found on the page
      - 'review_pending' — user clicked quit OR verification was skipped
    """
    def _progress(**kw):
        if progress_callback is not None:
            progress_callback(**kw)

    if dry_run:
        print("  [DRY RUN] Form filled — not submitting")
        _progress(phase="dry_run_complete",
                  message="Dry run complete — form filled, not submitted.")
        return "dry_run", False

    def _click_submit_and_verify() -> str:
        """Click Submit, sleep, run post-submit verification. Returns status."""
        submit_btn = page.locator('button:has-text("Submit")')
        if submit_btn.count() == 0:
            print("  Warning: Submit button not found")
            return "failed"
        submit_btn.first.click()
        time.sleep(3)
        print("  Submitted!")
        return _handle_post_submit_verification(page, verification_handler, progress_callback)

    if auto_submit:
        _progress(phase="submitting", message="Auto-submitting (auto_submit=on)…")
        return _click_submit_and_verify(), False

    # Review-and-submit path
    print("  Paused for review (auto_submit is off)")
    print("  Review the screenshot and the form in the browser.")
    _progress(phase="awaiting_submit", message="Form filled. Submit, skip, or quit?")
    action = submit_handler()

    if action == "submit":
        _progress(phase="submitting", message="Clicking submit…")
        return _click_submit_and_verify(), False
    if action == "quit":
        print("  Quitting apply loop.")
        return "review_pending", True
    # "skip"
    print("  Skipped by user.")
    return "skipped", False


def _record_success_entry(
    profile_name: str,
    job: dict,
    resume_path: str,
    status: str,
    applications: list,
) -> None:
    """Append the final application entry (applied / skipped / review_pending)
    to `applications.json` after a non-failure outcome."""
    app_entry = {
        "company": job["company"],
        "role": job["title"],
        "posting_url": job["posting_url"],
        "date": date.today().isoformat(),
        "status": status,
        "ats": job.get("ats", ""),
        "status_updated_at": date.today().isoformat(),
        "source": "autoapply",
    }
    if resume_path:
        app_entry["tailored_resume_path"] = resume_path
    if job.get("fit_score") is not None:
        app_entry["fit_score"] = job["fit_score"]
        app_entry["fit_rationale"] = job.get("fit_rationale", "")
    applications.append(app_entry)
    _save_applications(profile_name, applications)


def _process_job(
    page, context, job, profile, resume_data, project_selections,
    applications, results, name_slug, resumes_dir,
    auto_submit, dry_run, rate_limit, i, total_jobs,
    *,
    captcha_handler,
    submit_handler,
    verification_handler=None,
    progress_callback=None,
) -> bool:
    """Apply to one job. Returns True if the user asked to quit the apply loop.

    Composes 5 stage helpers top-to-bottom: prepare resume → fill (with CAPTCHA
    retry) → either record failure and exit, or screenshot + decide submit
    action → record outcome → inter-job sleep.

    `captcha_handler` / `submit_handler` are required; the UI wires them to
    `src/tasks/prompt.py` so the worker blocks on a modal:
      - captcha_handler() → "continue" | "skip"
      - submit_handler()  → "submit" | "skip" | "quit"
      - verification_handler(email) → "verified" | "skip"  (optional)

    `progress_callback`: keyword-args callable invoked at each phase boundary
    so the UI status file streams progress. Does not replace `print()`.
    """
    def _progress(**kwargs):
        if progress_callback is not None:
            progress_callback(**kwargs)

    company = job["company"]
    role = job["title"]

    # Stage 1: resume
    resume_path = _prepare_tailored_resume(
        profile, job, resume_data, project_selections,
        name_slug, resumes_dir, i, progress_callback=progress_callback,
    )

    # Stage 2: form fill (with CAPTCHA retry inside)
    fill_result = _fill_with_captcha_retry(
        page, job, profile, resume_data, resume_path,
        captcha_handler, progress_callback=progress_callback,
    )

    # Stage 2b: handle terminal failures (unsupported ATS or post-CAPTCHA fail)
    if fill_result.get("_unsupported"):
        results.append({"company": company, "role": role, "status": "skipped"})
        return False
    if not fill_result["success"]:
        _record_failure_entry(profile.profile_name, job, fill_result, applications)
        results.append({"company": company, "role": role, "status": "failed"})
        return False

    print(f"  Filled: {', '.join(fill_result['fields_filled'])}")
    if fill_result["custom_answers"]:
        print(f"  Custom Qs answered: {len(fill_result['custom_answers'])}")

    # Stage 3: screenshot
    screenshot = _take_screenshot(page, profile.profile_name, company, role)
    print(f"  Screenshot: {screenshot}")
    _progress(phase="screenshot_ready", message="Form filled. Review the screenshot.",
              screenshot_path=screenshot)

    # Stage 4: submit decision (dry-run / auto / prompt)
    status, quit_loop = _decide_submit_action(
        page, job, auto_submit, dry_run,
        submit_handler, verification_handler,
        progress_callback=progress_callback,
    )

    # Stage 5: record outcome (dry-run skips the applications.json write)
    if status != "dry_run":
        _record_success_entry(profile.profile_name, job, resume_path, status, applications)
    results.append({"company": company, "role": role, "status": status})
    _progress(phase="complete", message=f"Done · status={status}",
              result_status=status, screenshot_path=screenshot)

    if quit_loop:
        return True

    # Inter-job rate limit (skipped for last job in batch)
    if i < total_jobs - 1:
        jitter = random.uniform(0.5, 1.5)
        delay = rate_limit * jitter
        print(f"  Waiting {delay:.0f}s before next application...")
        time.sleep(delay)

    return False
