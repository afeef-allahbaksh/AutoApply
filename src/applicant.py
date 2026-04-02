import json
import random
import time
from datetime import date
from pathlib import Path

from src.ats_greenhouse import fill_greenhouse_application
from src.ats_lever import fill_lever_application
from src.browser import get_browser_context
from src.profile_loader import PROFILES_DIR, Profile
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
    """Check composite key dedup."""
    return any(
        a["company"] == company and a["role"] == role and a["posting_url"] == posting_url
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


def apply_to_jobs(
    profile: Profile,
    jobs: list[dict],
    resume_data: dict | None = None,
    headless: bool = False,
) -> list[dict]:
    """Apply to a list of jobs using the appropriate ATS handler.

    Respects auto_submit and rate_limit_seconds from profile settings.
    Returns list of application result dicts.
    """
    auto_submit = profile.auto_submit
    rate_limit = profile.rate_limit_seconds
    applications = list(profile.applications)
    results = []

    pw, browser, context = get_browser_context(headless=headless)
    page = context.new_page()

    try:
        for i, job in enumerate(jobs):
            company = job["company"]
            role = job["title"]
            posting_url = job["posting_url"]
            ats = job.get("ats", "")

            print(f"\n[{i + 1}/{len(jobs)}] {company} — {role}")

            # Dedup check
            if _is_already_applied(applications, company, role, posting_url):
                print(f"  Skipped: already applied")
                results.append({"company": company, "role": role, "status": "skipped"})
                continue

            # Find tailored resume PDF if it exists
            resume_path = ""
            resumes_dir = profile.profile_dir / "resumes"
            if resumes_dir.exists():
                # Look for most recent matching resume
                from src.resume_optimizer import _slugify
                # Match by name_company or company_role (supports both naming conventions)
                name_slug = _slugify(profile.data.get("name", ""))
                matching = sorted(resumes_dir.glob(f"{name_slug}_{_slugify(company)}*.pdf"), reverse=True)
                if not matching:
                    # Fallback: old naming convention (company_role_date)
                    matching = sorted(resumes_dir.glob(f"{_slugify(company)}_{_slugify(role)}*.pdf"), reverse=True)
                if matching:
                    resume_path = str(matching[0])
                    print(f"  Using tailored resume: {matching[0].name}")

            # Fill the form
            if ats == "greenhouse":
                fill_result = fill_greenhouse_application(
                    page=page,
                    job_url=posting_url,
                    profile_data=profile.data,
                    responses=profile.responses,
                    resume_path=resume_path,
                    job_content=job.get("content", ""),
                    resume_data=resume_data,
                    company=company,
                    role=role,
                )
            elif ats == "lever":
                fill_result = fill_lever_application(
                    page=page,
                    job_url=posting_url,
                    profile_data=profile.data,
                    responses=profile.responses,
                    resume_path=resume_path,
                    job_content=job.get("content", ""),
                    resume_data=resume_data,
                    company=company,
                    role=role,
                )
            else:
                print(f"  Skipped: unsupported ATS '{ats}'")
                results.append({"company": company, "role": role, "status": "skipped"})
                continue

            if not fill_result["success"]:
                error_msg = fill_result["error"] or "Unknown error"
                print(f"  Failed: {error_msg}")
                # Screenshot on failure for debugging
                fail_screenshot = _take_screenshot(page, profile.profile_name, company, f"{role}_FAILED")
                print(f"  Failure screenshot: {fail_screenshot}")

                # Detect CAPTCHA — pause for user to solve, then retry on current page
                page_text = page.content().lower()
                if "captcha" in page_text or "recaptcha" in page_text or "hcaptcha" in page_text:
                    print(f"  CAPTCHA detected! Pausing for manual intervention.")
                    print(f"  Solve the CAPTCHA in the browser, then press Enter to retry.")
                    try:
                        input("  Press Enter after solving CAPTCHA (or Ctrl+C to skip)...")
                        # Retry form fill on the current page (don't re-navigate)
                        fill_fn = fill_greenhouse_application if ats == "greenhouse" else fill_lever_application
                        fill_result = fill_fn(
                            page=page,
                            job_url=posting_url,
                            profile_data=profile.data,
                            responses=profile.responses,
                            resume_path=resume_path,
                            job_content=job.get("content", ""),
                            resume_data=resume_data,
                            company=company,
                            role=role,
                        )
                    except (EOFError, KeyboardInterrupt):
                        print(f"  Skipping CAPTCHA'd application.")

                if not fill_result["success"]:
                    # Save progress for retry
                    progress_path = _save_progress(
                        profile.profile_name, job,
                        fill_result.get("fields_filled", []),
                        fill_result.get("custom_answers", []),
                    )
                    print(f"  Progress saved: {progress_path}")

                    applications.append({
                        "company": company,
                        "role": role,
                        "posting_url": posting_url,
                        "date": date.today().isoformat(),
                        "status": "failed",
                        "ats": ats,
                        "error": fill_result.get("error") or error_msg,
                    })
                    _save_applications(profile.profile_name, applications)
                    results.append({"company": company, "role": role, "status": "failed"})
                    continue

            print(f"  Filled: {', '.join(fill_result['fields_filled'])}")
            if fill_result["custom_answers"]:
                print(f"  Custom Qs answered: {len(fill_result['custom_answers'])}")

            # Screenshot for review
            screenshot = _take_screenshot(page, profile.profile_name, company, role)
            print(f"  Screenshot: {screenshot}")

            if auto_submit:
                # Submit the form
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
                try:
                    response = input("  Submit? (y/n/q): ").strip().lower()
                except EOFError:
                    response = "n"

                if response == "y":
                    submit_btn = page.locator('button:has-text("Submit")')
                    if submit_btn.count() > 0:
                        submit_btn.first.click()
                        time.sleep(3)
                        print(f"  Submitted!")
                        status = "applied"
                    else:
                        print(f"  Submit button not found")
                        status = "failed"
                elif response == "q":
                    print("  Quitting apply loop.")
                    status = "review_pending"
                    applications.append({
                        "company": company,
                        "role": role,
                        "posting_url": posting_url,
                        "date": date.today().isoformat(),
                        "status": status,
                        "ats": ats,
                        "tailored_resume_path": resume_path,
                    })
                    _save_applications(profile.profile_name, applications)
                    results.append({"company": company, "role": role, "status": status})
                    break
                else:
                    print(f"  Skipped by user.")
                    status = "skipped"

            # Log application
            app_entry = {
                "company": company,
                "role": role,
                "posting_url": posting_url,
                "date": date.today().isoformat(),
                "status": status,
                "ats": ats,
            }
            if resume_path:
                app_entry["tailored_resume_path"] = resume_path
            applications.append(app_entry)
            _save_applications(profile.profile_name, applications)
            results.append({"company": company, "role": role, "status": status})

            # Rate limiting with randomization
            if i < len(jobs) - 1:
                jitter = random.uniform(0.5, 1.5)
                delay = rate_limit * jitter
                print(f"  Waiting {delay:.0f}s before next application...")
                time.sleep(delay)

    finally:
        context.close()
        browser.close()
        pw.stop()

    return results
