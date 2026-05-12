"""Fill Greenhouse's structured education section from resume data."""
import time

from playwright.sync_api import Page

from .dates import parse_date_parts, try_select_date
from .fields import (
    EDU_DEGREE, EDU_DISCIPLINE, EDU_END_MONTH, EDU_END_YEAR,
    EDU_SCHOOL, EDU_START_MONTH, EDU_START_YEAR,
)
from .selectors import select_option_fuzzy_el


def _find_edu_section(page: Page):
    """Find the education section container on the page.

    Returns a Locator scoped to the education section, or the full page as fallback.
    """
    for sel in [
        '#education_section', '[data-section="education"]',
        'fieldset:has(legend:has-text("Education"))',
        'div:has(> h2:has-text("Education")):not(:has(h2:not(:has-text("Education"))))',
        'div:has(> h3:has-text("Education")):not(:has(h3:not(:has-text("Education"))))',
        'div:has(> label:has-text("School"))',
    ]:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible(timeout=500):
                return loc.first
        except Exception:
            continue
    return page


def fill_education_section(page: Page, resume_data: dict | None) -> list[str]:
    """Fill the Greenhouse structured education section from resume data."""
    if not resume_data or not resume_data.get("education"):
        return []

    filled = []
    section = _find_edu_section(page)

    for i, edu in enumerate(resume_data["education"]):
        # For entries after the first, click "Add another"
        if i > 0:
            try:
                add_btn = page.locator('button:has-text("Add another"), a:has-text("Add another")')
                if add_btn.count() > 0:
                    add_btn.first.click()
                    time.sleep(0.5)
                else:
                    break
            except Exception:
                break

        # School name (with autocomplete handling)
        school = edu.get("institution", "")
        if school:
            try:
                school_inputs = section.locator(EDU_SCHOOL)
                if school_inputs.count() == 0:
                    school_inputs = page.locator('label:has-text("School") + input, label:has-text("School") ~ input')
                el = school_inputs.nth(i) if school_inputs.count() > i else school_inputs.last
                if el.is_visible(timeout=1000):
                    el.fill("")
                    el.type(school, delay=30)
                    time.sleep(0.8)
                    suggestion = page.locator('[role="option"], .autocomplete-suggestions li, .pac-item').first
                    try:
                        if suggestion.is_visible(timeout=800):
                            suggestion.click()
                        else:
                            el.press("ArrowDown")
                            el.press("Enter")
                    except Exception:
                        pass
                    filled.append(f"education_{i}_school")
            except Exception:
                pass

        # Degree — React Select combobox (id="degree--{i}"), <select>, or plain input
        degree = edu.get("degree", "")
        if degree:
            try:
                el = page.locator(f'#degree--{i}')
                if el.count() == 0:
                    el_candidates = section.locator(EDU_DEGREE)
                    if el_candidates.count() == 0:
                        el_candidates = page.locator('label:has-text("Degree") ~ input, label:has-text("Degree") ~ select')
                    el = el_candidates.nth(i) if el_candidates.count() > i else el_candidates.last

                if el.is_visible(timeout=1000):
                    tag = el.evaluate("el => el.tagName.toLowerCase()")
                    role = el.get_attribute("role") or ""

                    if tag == "select":
                        if select_option_fuzzy_el(el, degree):
                            filled.append(f"education_{i}_degree")
                    elif role == "combobox":
                        # React Select — click to focus, clear, type, pick from dropdown.
                        # Scope to `.select__menu` so the intl-tel-input phone country
                        # picker's 240+ ambient role=option elements don't poison the pick.
                        el.click()
                        time.sleep(0.3)
                        el.fill("")
                        el.type(degree, delay=50)
                        time.sleep(1.0)
                        menu = page.locator('.select__menu').first
                        option = (
                            menu.locator('[role="option"]').first
                            if menu.count() > 0 else page.locator('[role="option"]').first
                        )
                        try:
                            if option.is_visible(timeout=1500):
                                option.click()
                            else:
                                el.press("ArrowDown")
                                time.sleep(0.2)
                                el.press("Enter")
                        except Exception:
                            el.press("Enter")
                        filled.append(f"education_{i}_degree")
                    else:
                        el.fill(degree)
                        filled.append(f"education_{i}_degree")
            except Exception:
                pass

        # Discipline
        field = edu.get("field", "")
        if field:
            try:
                disc_els = section.locator(EDU_DISCIPLINE)
                if disc_els.count() == 0:
                    disc_els = page.locator('label:has-text("Discipline") + input, label:has-text("Discipline") ~ input, label:has-text("Discipline") + select, label:has-text("Discipline") ~ select')
                el = disc_els.nth(i) if disc_els.count() > i else disc_els.last
                if el.is_visible(timeout=1000):
                    tag = el.evaluate("el => el.tagName.toLowerCase()")
                    if tag == "select":
                        select_option_fuzzy_el(el, field)
                    else:
                        el.fill(field)
                    filled.append(f"education_{i}_discipline")
            except Exception:
                pass

        # Start date — try by ID first, then section selectors, then label-based fallback
        start = edu.get("start_date", "")
        if start:
            month, year = parse_date_parts(start)
            start_year_el = page.locator(f'#start-year--{i}')
            if start_year_el.count() == 0:
                start_year_el = section.locator(EDU_START_YEAR)
            if start_year_el.count() == 0:
                start_year_el = page.locator('[aria-label="Start date year"]')
            start_month_el = page.locator(f'#start-month--{i}')
            if start_month_el.count() == 0:
                start_month_el = section.locator(EDU_START_MONTH)
            if month:
                try_select_date(start_month_el, 0, month)
            if year:
                if try_select_date(start_year_el, 0, year):
                    filled.append(f"education_{i}_start_date")

        # End date
        end = edu.get("end_date", "")
        if end:
            month, year = parse_date_parts(end)
            end_year_el = page.locator(f'#end-year--{i}')
            if end_year_el.count() == 0:
                end_year_el = section.locator(EDU_END_YEAR)
            if end_year_el.count() == 0:
                end_year_el = page.locator('[aria-label="End date year"]')
            end_month_el = page.locator(f'#end-month--{i}')
            if end_month_el.count() == 0:
                end_month_el = section.locator(EDU_END_MONTH)
            if month:
                try_select_date(end_month_el, 0, month)
            if year:
                if try_select_date(end_year_el, 0, year):
                    filled.append(f"education_{i}_end_date")

    return filled
