"""Top-level orchestrator — navigates a Greenhouse job posting and fills every
recognized field. Composes the per-section helpers from sibling modules."""
import time
from pathlib import Path

from playwright.sync_api import Page

from .custom_questions import handle_custom_questions
from .demographics import fill_demographics
from .education import fill_education_section
from .fields import (
    CANDIDATE_LOCATION,
    COVER_LETTER_UPLOAD,
    EMAIL,
    FIRST_NAME,
    LAST_NAME,
    LINKEDIN,
    PHONE,
    PHONE_COUNTRY,
    PREFERRED_NAME,
    RESUME_UPLOAD,
)
from .location import fill_location_autocomplete
from .selectors import fill_if_exists, upload_if_exists, wait_for_form


def _split_name(full_name: str) -> tuple[str, str]:
    """Split a full name into first and last name.

    First word is first name, last word is last name. Middle name(s) are
    dropped — only used if the form asks for full name.
    """
    parts = full_name.strip().split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def fill_greenhouse_application(
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
    """Navigate to a Greenhouse job and fill out the application form.

    Returns a status dict: {success, fields_filled, custom_answers, error}
    """
    result = {
        "success": False,
        "fields_filled": [],
        "custom_answers": [],
        "error": None,
    }

    try:
        page.goto(job_url, wait_until="networkidle", timeout=30000)
        time.sleep(2)

        # Look for apply button and click it if present
        apply_btn = page.locator('a:has-text("Apply"), button:has-text("Apply")')
        if apply_btn.count() > 0:
            apply_btn.first.click()
            time.sleep(2)

        if not wait_for_form(page):
            # Try finding a Greenhouse iframe
            for frame in page.frames:
                if "greenhouse" in (frame.url or "").lower():
                    page = frame
                    if wait_for_form(page):
                        break
            else:
                result["error"] = "Application form did not load"
                return result

        first, last = _split_name(profile_data.get("name", ""))

        field_map = [
            (FIRST_NAME, first, "first_name"),
            (LAST_NAME, last, "last_name"),
            (PREFERRED_NAME, first, "preferred_name"),
            (EMAIL, profile_data.get("email", ""), "email"),
            (PHONE, profile_data.get("phone", ""), "phone"),
        ]

        linkedin = profile_data.get("linkedin", "")
        if linkedin:
            if not linkedin.startswith("http"):
                linkedin = f"https://linkedin.com/in/{linkedin}"
            field_map.append((LINKEDIN, linkedin, "linkedin"))

        # Track which element ids got filled by the standard pass so the
        # custom-question handler can skip them. Without this, a field like
        # LinkedIn (`aria-label="LinkedIn Profile"` + `id="question_X"`) gets
        # filled here, then the custom-question handler iterates the same
        # `id^="question_"` element and calls Claude redundantly — wasting
        # API calls and potentially overwriting the correct value.
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

        # Location — try regular input first, then React Select combobox
        location = profile_data.get("location", "")
        if location:
            if fill_location_autocomplete(page, location):
                result["fields_filled"].append("location")
            else:
                try:
                    loc_el = page.locator(CANDIDATE_LOCATION).first
                    if loc_el.is_visible(timeout=1000):
                        role_attr = loc_el.get_attribute("role") or ""
                        if role_attr == "combobox" or loc_el.get_attribute("aria-haspopup"):
                            loc_el.click()
                            time.sleep(0.3)
                            loc_el.fill("")
                            loc_el.type(location, delay=50)
                            time.sleep(1.0)
                            # Scope to .select__menu — see custom_questions.py for
                            # why (intl-tel-input phone picker ambient options).
                            menu = page.locator('.select__menu').first
                            option = (
                                menu.locator('[role="option"]').first
                                if menu.count() > 0 else page.locator('[role="option"]').first
                            )
                            if option.is_visible(timeout=1500):
                                option.click()
                            else:
                                loc_el.press("ArrowDown")
                                time.sleep(0.2)
                                loc_el.press("Enter")
                            result["fields_filled"].append("location")
                        else:
                            loc_el.fill(location)
                            result["fields_filled"].append("location")
                except Exception:
                    pass

        # Phone country code dropdown — select US (+1)
        try:
            country_sel = page.locator(PHONE_COUNTRY)
            if country_sel.count() > 0:
                for val in ["US", "us", "1", "+1"]:
                    try:
                        country_sel.first.select_option(value=val)
                        result["fields_filled"].append("phone_country")
                        break
                    except Exception:
                        continue
        except Exception:
            pass

        # Upload resume — set_input_files works on hidden inputs without clicking
        if resume_path and Path(resume_path).exists():
            resume_selectors = [
                RESUME_UPLOAD,
                'input[type="file"][id*="resume" i]',
                'input[type="file"]',
            ]
            for sel in resume_selectors:
                if upload_if_exists(page, sel, resume_path):
                    result["fields_filled"].append("resume")
                    break

        # Cover letter — generate and fill if the form has a text field, or upload
        cl_text = page.locator('textarea[name="cover_letter_text"], textarea[id*="cover_letter"]')
        cl_file = page.locator(COVER_LETTER_UPLOAD)
        if cl_text.count() > 0 or cl_file.count() > 0:
            if resume_data:
                from src.cover_letter import generate_cover_letter
                cover = generate_cover_letter(profile_data, resume_data, company, role, job_content)
                if cl_text.count() > 0:
                    cl_text.first.fill(cover)
                    result["fields_filled"].append("cover_letter")

        edu_filled = fill_education_section(page, resume_data)
        result["fields_filled"].extend(edu_filled)

        # Demographics first so the custom-question handler skips them.
        demo_filled, demo_ids = fill_demographics(page, responses)
        result["fields_filled"].extend(demo_filled)

        # skip_ids = filled-by-standard-pass ∪ filled-by-demographics so the
        # custom-question handler doesn't re-process either set.
        result["custom_answers"] = handle_custom_questions(
            page, responses, job_content, profile_data,
            resume_data=resume_data, skip_ids=filled_ids | demo_ids,
        )

        result["success"] = True

    except Exception as e:
        result["error"] = str(e)

    return result
