import json
import random
import time
from datetime import date
from pathlib import Path

from src.ats.greenhouse import fill_greenhouse_application
from src.ats.lever import fill_lever_application
from src.profile_loader import PROFILES_DIR, normalize_posting_url
from src.resume.optimizer import (
    find_cached_resume, optimize_resume,
    _optimization_hash, _slugify, save_tailored_resume, select_projects,
)
from src.schemas import validate_applications


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
    """Write applications list to disk after validation."""
    validate_applications(applications)
    path = PROFILES_DIR / profile_name / "applications.json"
    with open(path, "w") as f:
        json.dump(applications, f, indent=2)
        f.write("\n")


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


def _process_job(
    page, context, job, profile, resume_data, project_selections,
    applications, results, name_slug, resumes_dir,
    auto_submit, dry_run, rate_limit, i, total_jobs,
    *,
    captcha_handler,
    submit_handler,
    progress_callback=None,
) -> bool:
    """Process a single job. Returns True if the user wants to quit the apply loop.

    `captcha_handler` / `submit_handler` are required — the UI wires them to
    `src/tasks/prompt.py` so the worker blocks on the modal:
      - captcha_handler() returns "continue" | "skip"
      - submit_handler() returns "submit" | "skip" | "quit"

    `progress_callback`: optional callable invoked at each phase with
    keyword args (phase=..., message=..., **extra) so the UI status file
    can stream progress. Does not replace print() — both fire.
    """
    def _progress(**kwargs):
        if progress_callback is not None:
            progress_callback(**kwargs)

    company = job["company"]
    role = job["title"]
    posting_url = job["posting_url"]
    ats = job.get("ats", "")

    # Find tailored resume PDF — generate one if it doesn't exist
    _progress(phase="tailoring_resume", message="Checking for tailored resume…",
              company=company, role=role)
    resume_path = ""
    if resumes_dir.exists():
        matching = sorted(resumes_dir.glob(f"{name_slug}_{_slugify(company)}*.pdf"), reverse=True)
        if not matching:
            matching = sorted(resumes_dir.glob(f"{_slugify(company)}_{_slugify(role)}*.pdf"), reverse=True)
        if matching:
            resume_path = str(matching[0])
            print(f"  Using tailored resume: {matching[0].name}")

    # Auto-generate tailored resume if none found and base resume exists
    if not resume_path and resume_data:
        try:
            job_content = job.get("content", role)

            # Use batched project selection if available, else fall back to single call
            if project_selections and project_selections[i]["had_pool"]:
                selection = project_selections[i]
            else:
                selection = select_projects(resume_data, job_content)

            if selection["had_pool"]:
                print(f"  Selected projects: {', '.join(p['name'] for p in selection['projects'])}")
                tailored_base = {**resume_data, "projects": selection["projects"]}
            else:
                tailored_base = resume_data

            # Check cache before calling API
            cached = find_cached_resume(profile.profile_name, tailored_base, job_content, company)
            if cached:
                resume_path = cached["pdf"]
                print(f"  Using cached resume: {Path(resume_path).name}")
            else:
                print(f"  Generating tailored resume...")
                optimized = optimize_resume(tailored_base, job_content)
                opt_hash = _optimization_hash(tailored_base, job_content)
                paths = save_tailored_resume(profile.profile_name, optimized, company, role, optimization_hash=opt_hash)
                resume_path = paths["pdf"]
                print(f"  Saved: {Path(resume_path).name}")
        except Exception as e:
            print(f"  Warning: Could not generate tailored resume: {e}")

    # Fill the form
    _progress(phase="filling_form", message=f"Filling {ats} application form…")
    if ats == "greenhouse":
        fill_result = fill_greenhouse_application(
            page=page, job_url=posting_url, profile_data=profile.data,
            responses=profile.responses, resume_path=resume_path,
            job_content=job.get("content", ""), resume_data=resume_data,
            company=company, role=role,
        )
    elif ats == "lever":
        fill_result = fill_lever_application(
            page=page, job_url=posting_url, profile_data=profile.data,
            responses=profile.responses, resume_path=resume_path,
            job_content=job.get("content", ""), resume_data=resume_data,
            company=company, role=role,
        )
    else:
        print(f"  Skipped: unsupported ATS '{ats}'")
        results.append({"company": company, "role": role, "status": "skipped"})
        return False

    if not fill_result["success"]:
        error_msg = fill_result["error"] or "Unknown error"
        print(f"  Failed: {error_msg}")
        fail_screenshot = _take_screenshot(page, profile.profile_name, company, f"{role}_FAILED")
        print(f"  Failure screenshot: {fail_screenshot}")

        # Detect CAPTCHA — pause for user to solve, then retry on current page
        page_text = page.content().lower()
        if "captcha" in page_text or "recaptcha" in page_text or "hcaptcha" in page_text:
            print(f"  CAPTCHA detected! Pausing for manual intervention.")
            print(f"  Solve the CAPTCHA in the browser, then press Enter to retry.")
            _progress(phase="captcha_pause",
                      message="CAPTCHA detected — solve in the browser, then continue.")
            action = captcha_handler()
            if action == "continue":
                fill_fn = fill_greenhouse_application if ats == "greenhouse" else fill_lever_application
                fill_result = fill_fn(
                    page=page, job_url=posting_url, profile_data=profile.data,
                    responses=profile.responses, resume_path=resume_path,
                    job_content=job.get("content", ""), resume_data=resume_data,
                    company=company, role=role,
                )
            else:
                print(f"  Skipping CAPTCHA'd application.")

        if not fill_result["success"]:
            progress_path = _save_progress(
                profile.profile_name, job,
                fill_result.get("fields_filled", []),
                fill_result.get("custom_answers", []),
            )
            print(f"  Progress saved: {progress_path}")

            fail_entry = {
                "company": company, "role": role, "posting_url": posting_url,
                "date": date.today().isoformat(), "status": "failed",
                "ats": ats, "error": fill_result.get("error") or error_msg,
                "status_updated_at": date.today().isoformat(),
                "source": "autoapply",
            }
            if job.get("fit_score") is not None:
                fail_entry["fit_score"] = job["fit_score"]
                fail_entry["fit_rationale"] = job.get("fit_rationale", "")
            applications.append(fail_entry)
            _save_applications(profile.profile_name, applications)
            results.append({"company": company, "role": role, "status": "failed"})
            return False

    print(f"  Filled: {', '.join(fill_result['fields_filled'])}")
    if fill_result["custom_answers"]:
        print(f"  Custom Qs answered: {len(fill_result['custom_answers'])}")

    screenshot = _take_screenshot(page, profile.profile_name, company, role)
    print(f"  Screenshot: {screenshot}")
    _progress(phase="screenshot_ready", message="Form filled. Review the screenshot.",
              screenshot_path=screenshot)

    if dry_run:
        print(f"  [DRY RUN] Form filled — not submitting")
        _progress(phase="dry_run_complete",
                  message="Dry run complete — form filled, not submitted.")
        results.append({"company": company, "role": role, "status": "dry_run"})
        if i < total_jobs - 1:
            jitter = random.uniform(0.5, 1.5)
            delay = rate_limit * jitter
            print(f"  Waiting {delay:.0f}s before next application...")
            time.sleep(delay)
        return False

    if auto_submit:
        _progress(phase="submitting", message="Auto-submitting (auto_submit=on)…")
        submit_btn = page.locator('button:has-text("Submit")')
        if submit_btn.count() > 0:
            submit_btn.first.click()
            time.sleep(3)
            print(f"  Submitted!")
            status = "applied"
        else:
            print(f"  Warning: Submit button not found")
            status = "failed"
    else:
        print(f"  Paused for review (auto_submit is off)")
        print(f"  Review the screenshot and the form in the browser.")
        _progress(phase="awaiting_submit",
                  message="Form filled. Submit, skip, or quit?")
        action = submit_handler()

        if action == "submit":
            _progress(phase="submitting", message="Clicking submit…")
            submit_btn = page.locator('button:has-text("Submit")')
            if submit_btn.count() > 0:
                submit_btn.first.click()
                time.sleep(3)
                print(f"  Submitted!")
                status = "applied"
            else:
                print(f"  Submit button not found")
                status = "failed"
        elif action == "quit":
            print("  Quitting apply loop.")
            status = "review_pending"
            applications.append({
                "company": company, "role": role, "posting_url": posting_url,
                "date": date.today().isoformat(), "status": status,
                "ats": ats, "tailored_resume_path": resume_path,
                "status_updated_at": date.today().isoformat(),
                "source": "autoapply",
            })
            _save_applications(profile.profile_name, applications)
            results.append({"company": company, "role": role, "status": status})
            return True
        else:  # "skip"
            print(f"  Skipped by user.")
            status = "skipped"

    app_entry = {
        "company": company, "role": role, "posting_url": posting_url,
        "date": date.today().isoformat(), "status": status, "ats": ats,
        "status_updated_at": date.today().isoformat(),
        "source": "autoapply",
    }
    if resume_path:
        app_entry["tailored_resume_path"] = resume_path
    if job.get("fit_score") is not None:
        app_entry["fit_score"] = job["fit_score"]
        app_entry["fit_rationale"] = job.get("fit_rationale", "")
    applications.append(app_entry)
    _save_applications(profile.profile_name, applications)
    results.append({"company": company, "role": role, "status": status})
    _progress(phase="complete", message=f"Done · status={status}",
              result_status=status, screenshot_path=screenshot)

    if i < total_jobs - 1:
        jitter = random.uniform(0.5, 1.5)
        delay = rate_limit * jitter
        print(f"  Waiting {delay:.0f}s before next application...")
        time.sleep(delay)

    return False
