"""Top-level Ashby orchestrator — navigates to the apply URL, fills every
recognized standard field, then dispatches to the custom-question handler."""
import time
from pathlib import Path

from playwright.sync_api import Page

from .custom_questions import handle_custom_questions
from .fields import (
    EMAIL, LINKEDIN, LOCATION, NAME, PHONE, RESUME_UPLOAD,
)
from .selectors import fill_if_exists, upload_if_exists, wait_for_form


def fill_ashby_application(
    page: Page,
    job_url: str,
    profile_data: dict,
    responses: dict,
    resume_path: str,
    job_content: str = "",
    resume_data: dict | None = None,
    company: str = "",
    role: str = "",
) -> dict:
    """Navigate to an Ashby job and fill out the application form.

    Returns a status dict: {success, fields_filled, custom_answers, error}.
    """
    result = {
        "success": False,
        "fields_filled": [],
        "custom_answers": [],
        "error": None,
    }

    try:
        # Ashby's posting page often has an "Apply" or "Apply for this job" CTA
        # that opens the form. Some boards inline the form on the same page.
        page.goto(job_url, wait_until="networkidle", timeout=30000)
        time.sleep(2)

        apply_btn = page.locator(
            'a:has-text("Apply for this Job"), '
            'a:has-text("Apply for this job"), '
            'button:has-text("Apply"), '
            'a:has-text("Apply")'
        )
        if apply_btn.count() > 0:
            apply_btn.first.click()
            time.sleep(2)

        if not wait_for_form(page):
            result["error"] = "Application form did not load"
            return result

        # Standard fields — Ashby uses a single full-name field, not separate
        # first/last like Greenhouse.
        field_map = [
            (NAME, profile_data.get("name", ""), "name"),
            (EMAIL, profile_data.get("email", ""), "email"),
            (PHONE, profile_data.get("phone", ""), "phone"),
            (LOCATION, profile_data.get("location", ""), "location"),
        ]

        linkedin = profile_data.get("linkedin", "")
        if linkedin:
            if not linkedin.startswith("http"):
                linkedin = f"https://linkedin.com/in/{linkedin}"
            field_map.append((LINKEDIN, linkedin, "linkedin"))

        # Track filled element ids so the custom-question handler skips them.
        # Same pattern as the greenhouse orchestrator after Phase 38.
        filled_ids: set[str] = set()
        for selector, value, name in field_map:
            if not value or not fill_if_exists(page, selector, value):
                continue
            result["fields_filled"].append(name)
            try:
                elid = page.locator(selector).first.get_attribute("id") or ""
                if elid:
                    filled_ids.add(elid)
            except Exception:
                pass

        # Upload resume
        if resume_path and Path(resume_path).exists():
            if upload_if_exists(page, RESUME_UPLOAD, resume_path):
                result["fields_filled"].append("resume")

        # Custom questions
        result["custom_answers"] = handle_custom_questions(
            page, responses, job_content, profile_data,
            resume_data=resume_data, skip_ids=filled_ids,
        )

        result["success"] = True

    except Exception as e:
        result["error"] = str(e)

    return result
