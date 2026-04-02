import re
import time
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from src.api import create_message

# Selectors
FIRST_NAME = 'input[name="first_name"], #first_name'
LAST_NAME = 'input[name="last_name"], #last_name'
EMAIL = 'input[name="email"], #email'
PHONE = 'input[name="phone"], #phone, input[id*="phone" i]'
PHONE_COUNTRY = 'select[name="phone_country_code"], select[id*="phone" i][id*="country" i]'
LOCATION = 'input[name="location"], #location, input[id*="location" i], input[autocomplete="address-level2"]'
LINKEDIN = 'input[autocomplete="custom-question-linkedin-profile"], input[name*="linkedin" i], input[id*="linkedin" i], input[placeholder*="linkedin" i], input[aria-label*="LinkedIn" i]'
RESUME_UPLOAD = 'input[type="file"][name="resume"]'
RESUME_BUTTON = 'button:has-text("Attach"), button:has-text("Upload"), label:has-text("Attach"), label:has-text("Upload")'
COVER_LETTER_UPLOAD = 'input[type="file"][name="cover_letter"]'
SUBMIT_BUTTON = 'button:has-text("Submit")'
# Custom questions: Greenhouse uses question_ prefix, but some forms use other patterns
CUSTOM_QUESTION = '[id^="question_"], [id^="custom_"], [data-field-type="custom"]'

# Education section selectors (Greenhouse structured fields)
# Greenhouse uses various naming: education[][school_name_id], job_application[education][][school_name_id], etc.
EDU_SCHOOL = 'input[name*="school_name"], input[id*="school" i][type="text"], input[name*="education"][name*="school"]'
EDU_DEGREE = 'select[name*="degree"], select[id*="degree" i], input[id^="degree-"], input[name*="degree"]'
EDU_DISCIPLINE = 'input[name*="discipline"], input[id*="discipline" i], select[name*="discipline"]'
# Greenhouse education date fields — can be <select> or <input type="number"> depending on the form
EDU_START_MONTH = 'select[id^="start-month-"], input[id^="start-month-"], select[name*="start_month"], input[name*="start_month"]'
EDU_START_YEAR = 'select[id^="start-year-"], input[id^="start-year-"], select[name*="start_year"], input[name*="start_year"]'
EDU_END_MONTH = 'select[id^="end-month-"], input[id^="end-month-"], select[name*="end_month"], input[name*="end_month"]'
EDU_END_YEAR = 'select[id^="end-year-"], input[id^="end-year-"], select[name*="end_year"], input[name*="end_year"]'

# EEO / Voluntary Self-Identification selectors
EEO_GENDER = 'select[id*="gender" i], select[name*="gender" i]'
EEO_RACE = 'select[id*="race" i], select[name*="race" i]'
EEO_HISPANIC = 'select[id*="hispanic" i], select[name*="hispanic" i]'
EEO_VETERAN = 'select[id*="veteran" i], select[name*="veteran" i]'
EEO_DISABILITY = 'select[id*="disability" i], select[name*="disability" i]'


def _wait_for_form(page: Page, timeout: int = 15000) -> bool:
    """Wait for the Greenhouse application form to load."""
    try:
        page.wait_for_selector(FIRST_NAME, timeout=timeout)
        return True
    except PlaywrightTimeout:
        return False


def _fill_if_exists(page: Page, selector: str, value: str) -> bool:
    """Fill a field if it exists on the page."""
    try:
        el = page.locator(selector).first
        if el.is_visible(timeout=1000):
            el.fill(value)
            return True
    except (PlaywrightTimeout, Exception):
        pass
    return False


def _upload_if_exists(page: Page, selector: str, file_path: str) -> bool:
    """Upload a file to a file input if it exists."""
    try:
        el = page.locator(selector)
        if el.count() > 0:
            el.set_input_files(file_path)
            return True
    except Exception:
        pass
    return False


def _normalize_words(text: str) -> set[str]:
    """Normalize text into a set of words with punctuation stripped.

    'Bachelor's Degree' -> {'bachelors', 'degree'}
    """
    return {re.sub(r"[^a-z0-9]", "", w) for w in text.lower().split() if w}


def _fuzzy_match_options(el, target: str) -> bool:
    """Core fuzzy matching logic for a select element.

    Tries exact match, then substring, then normalized word-overlap.
    """
    # Exact match by label
    try:
        el.select_option(label=target)
        return True
    except Exception:
        pass

    # Gather all option texts
    options = el.locator("option").all()
    option_texts = [(opt.inner_text().strip(), opt.get_attribute("value")) for opt in options]

    target_lower = target.lower()

    # Substring match (either direction)
    for text, value in option_texts:
        text_lower = text.lower()
        if target_lower in text_lower or text_lower in target_lower:
            el.select_option(label=text)
            return True

    # Word-overlap with punctuation stripped (bachelor's -> bachelors matches bachelors)
    target_words = _normalize_words(target)
    best_score, best_text = 0, None
    for text, value in option_texts:
        if not text or text.lower().startswith("select") or text == "---":
            continue
        option_words = _normalize_words(text)
        overlap = len(target_words & option_words)
        if overlap > best_score:
            best_score = overlap
            best_text = text

    if best_text and best_score >= 1:
        el.select_option(label=best_text)
        return True

    return False


def _select_option_fuzzy(page: Page, selector: str, target: str) -> bool:
    """Select a dropdown option using fuzzy text matching."""
    try:
        el = page.locator(selector).first
        if not el.is_visible(timeout=1000):
            return False
        return _fuzzy_match_options(el, target)
    except Exception:
        pass
    return False


def _select_option_fuzzy_el(el, target: str) -> bool:
    """Like _select_option_fuzzy but takes a Playwright Locator directly."""
    try:
        if not el.is_visible(timeout=1000):
            return False
        return _fuzzy_match_options(el, target)
    except Exception:
        pass
    return False


def _parse_date_parts(date_str: str) -> tuple[str | None, str | None]:
    """Extract month and year from date strings like 'June 2026' or 'Expected Graduation: June 2026'."""
    MONTH_MAP = {
        "january": "1", "february": "2", "march": "3", "april": "4",
        "may": "5", "june": "6", "july": "7", "august": "8",
        "september": "9", "october": "10", "november": "11", "december": "12",
    }

    match = re.search(
        r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})',
        date_str, re.IGNORECASE,
    )
    if match:
        return MONTH_MAP[match.group(1).lower()], match.group(2)

    match = re.search(r'(\d{1,2})[/-](\d{4})', date_str)
    if match:
        return str(int(match.group(1))), match.group(2)

    match = re.search(r'(\d{4})', date_str)
    if match:
        return None, match.group(1)

    return None, None


def _fill_location_autocomplete(page: Page, location: str) -> bool:
    """Fill location field and handle Google Places autocomplete dropdown."""
    try:
        el = page.locator(LOCATION).first
        if not el.is_visible(timeout=1000):
            return False

        el.fill("")
        el.type(location, delay=50)
        time.sleep(1.0)

        autocomplete_selectors = [
            '.pac-item',
            '.pac-container .pac-item',
            '[role="option"]',
            '.autocomplete-suggestions li',
        ]

        for sel in autocomplete_selectors:
            try:
                suggestion = page.locator(sel).first
                if suggestion.is_visible(timeout=500):
                    suggestion.click()
                    return True
            except Exception:
                continue

        # Fallback: ArrowDown + Enter
        el.press("ArrowDown")
        time.sleep(0.3)
        el.press("Enter")
        return True

    except Exception:
        return _fill_if_exists(page, LOCATION, location)


def _try_select_date(locator, index: int, value: str) -> bool:
    """Try setting a date value in a dropdown or input field."""
    try:
        el = locator.nth(index) if locator.count() > index else locator.last
        if not el.is_visible(timeout=500):
            return False

        tag = el.evaluate("el => el.tagName.toLowerCase()")

        # For <input> elements (type="number" or type="text"), just fill the value
        if tag == "input":
            el.fill(value)
            return True

        # For <select> elements, try by value, then label, then zero-padded
        try:
            el.select_option(value=value)
            return True
        except Exception:
            pass
        try:
            el.select_option(label=value)
            return True
        except Exception:
            pass
        if len(value) == 1:
            try:
                el.select_option(value=f"0{value}")
                return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def _find_edu_section(page: Page):
    """Find the education section container on the page.

    Returns a Locator scoped to the education section, or the full page as fallback.
    """
    # Try common Greenhouse education section containers
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


def _fill_education_section(page: Page, resume_data: dict | None) -> list[str]:
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
                    # Fallback: find by label text
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
                # Try direct ID first (Greenhouse React Select pattern)
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
                        if _select_option_fuzzy_el(el, degree):
                            filled.append(f"education_{i}_degree")
                    elif role == "combobox":
                        # React Select — click to focus, clear, type, pick from dropdown
                        el.click()
                        time.sleep(0.3)
                        el.fill("")
                        el.type(degree, delay=50)
                        time.sleep(1.0)
                        option = page.locator('[role="option"]').first
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
                        _select_option_fuzzy_el(el, field)
                    else:
                        el.fill(field)
                    filled.append(f"education_{i}_discipline")
            except Exception:
                pass

        # Start date — try by ID first, then section selectors, then label-based fallback
        start = edu.get("start_date", "")
        if start:
            month, year = _parse_date_parts(start)
            start_year_el = page.locator(f'#start-year--{i}')
            if start_year_el.count() == 0:
                start_year_el = section.locator(EDU_START_YEAR)
            if start_year_el.count() == 0:
                start_year_el = page.locator('[aria-label="Start date year"]')
            start_month_el = page.locator(f'#start-month--{i}')
            if start_month_el.count() == 0:
                start_month_el = section.locator(EDU_START_MONTH)
            if month:
                _try_select_date(start_month_el, 0, month)
            if year:
                if _try_select_date(start_year_el, 0, year):
                    filled.append(f"education_{i}_start_date")

        # End date
        end = edu.get("end_date", "")
        if end:
            month, year = _parse_date_parts(end)
            end_year_el = page.locator(f'#end-year--{i}')
            if end_year_el.count() == 0:
                end_year_el = section.locator(EDU_END_YEAR)
            if end_year_el.count() == 0:
                end_year_el = page.locator('[aria-label="End date year"]')
            end_month_el = page.locator(f'#end-month--{i}')
            if end_month_el.count() == 0:
                end_month_el = section.locator(EDU_END_MONTH)
            if month:
                _try_select_date(end_month_el, 0, month)
            if year:
                if _try_select_date(end_year_el, 0, year):
                    filled.append(f"education_{i}_end_date")

    return filled


def _fill_eeo_section(page: Page, responses: dict) -> list[str]:
    """Fill the Voluntary Self-Identification / EEO section using canned responses only."""
    filled = []

    eeo_fields = [
        (EEO_GENDER, "gender", responses.get("gender", "")),
        (EEO_RACE, "ethnicity", responses.get("ethnicity", "")),
        (EEO_HISPANIC, "hispanic", responses.get("ethnicity", "")),
        (EEO_VETERAN, "veteran_status", responses.get("veteran_status", "")),
        (EEO_DISABILITY, "disability", responses.get("disability", "")),
    ]

    for selector, name, value in eeo_fields:
        if not value:
            continue
        if _select_option_fuzzy(page, selector, value):
            filled.append(f"eeo_{name}")

    return filled


def _split_name(full_name: str) -> tuple[str, str]:
    """Split a full name into first and last name.

    First word is first name, last word is last name.
    Middle name(s) are dropped — only used if the form asks for full name.
    """
    parts = full_name.strip().split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def _build_applicant_context(profile_data: dict, resume_data: dict | None, responses: dict) -> str:
    """Build a comprehensive context string from all available applicant data."""
    sections = []

    # Profile info
    sections.append(f"Name: {profile_data.get('name', '')}")
    sections.append(f"Email: {profile_data.get('email', '')}")
    sections.append(f"Phone: {profile_data.get('phone', '')}")
    sections.append(f"Location: {profile_data.get('location', '')}")
    if profile_data.get("linkedin"):
        linkedin = profile_data["linkedin"]
        full = linkedin if linkedin.startswith("http") else f"https://linkedin.com/in/{linkedin}"
        sections.append(f"LinkedIn: {full}")
    if profile_data.get("github"):
        github = profile_data["github"]
        full = github if github.startswith("http") else f"https://github.com/{github}"
        sections.append(f"GitHub: {full}")
    if profile_data.get("website"):
        sections.append(f"Website: {profile_data['website']}")

    # Education from resume
    if resume_data and resume_data.get("education"):
        for edu in resume_data["education"]:
            parts = [f"University: {edu.get('institution', '')}"]
            if edu.get("degree"):
                parts.append(f"Degree: {edu['degree']}")
            if edu.get("field"):
                parts.append(f"Major/Field: {edu['field']}")
            if edu.get("gpa"):
                parts.append(f"GPA: {edu['gpa']}")
            if edu.get("end_date"):
                parts.append(f"Graduation: {edu['end_date']}")
            if edu.get("start_date"):
                parts.append(f"Start: {edu['start_date']}")
            if edu.get("coursework"):
                parts.append(f"Coursework: {', '.join(edu['coursework'])}")
            sections.append("\n".join(parts))

    # Experience from resume
    if resume_data and resume_data.get("experience"):
        exp_lines = []
        for exp in resume_data["experience"]:
            exp_lines.append(f"- {exp['title']} at {exp['company']} ({exp.get('start_date', '')} - {exp.get('end_date', 'Present')})")
        sections.append("Experience:\n" + "\n".join(exp_lines))

    # Skills from resume
    if resume_data and resume_data.get("skills"):
        for skill_group in resume_data["skills"]:
            sections.append(f"{skill_group['category']}: {', '.join(skill_group['items'])}")

    # Canned responses the user has set
    if responses:
        resp_lines = [f"  {k}: {v}" for k, v in responses.items()]
        sections.append("Pre-set responses:\n" + "\n".join(resp_lines))

    return "\n\n".join(sections)


def _answer_custom_question(
    question_text: str,
    job_content: str,
    profile_data: dict,
    resume_data: dict | None = None,
    responses: dict | None = None,
) -> str:
    """Use Claude to answer a custom question with full applicant context."""
    context = _build_applicant_context(profile_data, resume_data, responses or {})

    message = create_message(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": f"""Answer this job application question using the applicant's real data below.

Question: {question_text}

Applicant data:
{context}

Job description:
{job_content[:2000]}

Rules:
- Use EXACT data from the applicant profile — never guess or fabricate URLs, names, dates, GPAs, etc.
- For URL/link fields (website, portfolio, LinkedIn, GitHub), return ONLY the bare URL — no explanation, no text, just the URL
- For yes/no confirmation questions (e.g. "Can you confirm...?", "Are you...?", "Do you...?", "Will you...?"), return ONLY "Yes" or "No"
- For factual questions (university, GPA, graduation date), return just the value
- For open-ended questions, answer in 2-4 sentences using the applicant's real experience
- If the question matches a pre-set response, use that exact value
- NEVER explain that data is missing — if you don't have the answer, return an empty string
- Return ONLY the answer text, no quotes or labels""",
        }],
    )
    return message.content[0].text.strip()


def _answer_select_question(
    question_text: str,
    options: list[str],
    job_content: str,
    profile_data: dict,
    resume_data: dict | None = None,
    responses: dict | None = None,
) -> str:
    """Use Claude to pick the best option from a dropdown for a custom question."""
    context = _build_applicant_context(profile_data, resume_data, responses or {})
    options_str = "\n".join(f"- {opt}" for opt in options)

    message = create_message(
        model="claude-sonnet-4-20250514",
        max_tokens=100,
        messages=[{
            "role": "user",
            "content": f"""Pick the best option for this job application dropdown question.

Question: {question_text}

Available options:
{options_str}

Applicant data:
{context}

Job description (excerpt):
{job_content[:1000]}

Rules:
- Return ONLY the exact text of one of the available options, nothing else
- Pick the option that best matches the applicant's data
- If the question matches a pre-set response, pick the closest matching option
- If unsure, pick the most neutral/safe option""",
        }],
    )
    return message.content[0].text.strip()


def _handle_custom_questions(
    page: Page,
    responses: dict,
    job_content: str,
    profile_data: dict,
    resume_data: dict | None = None,
) -> list[dict]:
    """Find and answer custom questions on the form.

    Checks canned responses first, then falls back to Claude with full context.
    Returns a list of {question, answer, method} dicts for logging.
    """
    answered = []
    questions = page.locator(CUSTOM_QUESTION).all()

    for q in questions:
        try:
            # Get the question label
            q_id = q.get_attribute("id") or ""
            parent = q.locator("..").first

            # Try to find associated label
            label_el = page.locator(f'label[for="{q_id}"]')
            if label_el.count() > 0:
                label_text = label_el.first.inner_text().strip()
            else:
                label_text = parent.inner_text().strip()[:200]

            if not label_text:
                continue

            tag = q.evaluate("el => el.tagName.toLowerCase()")

            # Check canned responses first (exact user-defined answers for demographic questions)
            answer = None
            label_lower = label_text.lower()
            for key, value in responses.items():
                if key.lower() in label_lower:
                    answer = value
                    method = "canned"
                    break

            role = q.get_attribute("role") or ""

            if role == "combobox":
                # React Select — gather options by clicking to open, then pick
                # These behave like selects but are rendered as inputs
                q.click()
                time.sleep(0.5)
                option_els = page.locator('[role="option"]').all()
                option_labels = [o.inner_text().strip() for o in option_els if o.inner_text().strip()]

                if answer is not None and option_labels:
                    answer_lower = answer.lower().strip()
                    if answer not in option_labels:
                        for opt in option_labels:
                            if opt.lower() == answer_lower or opt.lower() in answer_lower or answer_lower in opt.lower():
                                answer = opt
                                break

                if answer is None:
                    if option_labels:
                        answer = _answer_select_question(
                            label_text, option_labels, job_content,
                            profile_data, resume_data=resume_data, responses=responses,
                        )
                    else:
                        answer = _answer_custom_question(
                            label_text, job_content, profile_data,
                            resume_data=resume_data, responses=responses,
                        )
                    method = "claude"

                # Click the matching option
                matched = False
                for opt_el in page.locator('[role="option"]').all():
                    if opt_el.inner_text().strip() == answer:
                        opt_el.click()
                        matched = True
                        break
                if not matched:
                    # Type and select first match
                    q.fill("")
                    q.type(answer, delay=50)
                    time.sleep(0.8)
                    first_opt = page.locator('[role="option"]').first
                    try:
                        if first_opt.is_visible(timeout=1000):
                            first_opt.click()
                        else:
                            q.press("ArrowDown")
                            q.press("Enter")
                    except Exception:
                        q.press("Enter")
                answered.append({"question": label_text[:100], "answer": answer[:100], "method": method})
                # Close dropdown by pressing Escape in case it's still open
                q.press("Escape")

            elif tag in ("input", "textarea") and q.get_attribute("type") != "file":
                is_required = q.get_attribute("aria-required") == "true"
                if answer is None:
                    # Skip optional fields that ask for data the user doesn't have
                    if not is_required:
                        # Check if this is asking for a specific URL/profile the user lacks
                        skip_keywords = ["website", "portfolio", "personal site", "blog"]
                        if any(kw in label_lower for kw in skip_keywords) and not profile_data.get("website"):
                            continue
                    answer = _answer_custom_question(
                        label_text, job_content, profile_data,
                        resume_data=resume_data, responses=responses,
                    )
                    method = "claude"
                q.fill(answer)
                answered.append({"question": label_text[:100], "answer": answer[:100], "method": method})

            elif tag == "select":
                # For selects, gather options so Claude picks from the actual list
                option_els = q.locator("option").all()
                option_labels = [o.inner_text().strip() for o in option_els
                                 if o.inner_text().strip() and not o.inner_text().strip().startswith("Select")]

                if answer is not None and option_labels:
                    # Canned response matched, but for selects we need to pick the closest option
                    # e.g., canned "Yes, I am authorized..." should pick "Yes" from a Yes/No dropdown
                    answer_lower = answer.lower().strip()
                    # Try exact match first
                    if answer not in option_labels:
                        # Try matching the first word (Yes/No) or fuzzy match
                        for opt in option_labels:
                            if opt.lower() == answer_lower or opt.lower() in answer_lower or answer_lower in opt.lower():
                                answer = opt
                                break

                if answer is None:
                    if option_labels:
                        answer = _answer_select_question(
                            label_text, option_labels, job_content,
                            profile_data, resume_data=resume_data, responses=responses,
                        )
                    else:
                        answer = _answer_custom_question(
                            label_text, job_content, profile_data,
                            resume_data=resume_data, responses=responses,
                        )
                    method = "claude"
                try:
                    q.select_option(label=answer)
                    answered.append({"question": label_text[:100], "answer": answer[:100], "method": method})
                except Exception:
                    # Fuzzy fallback
                    if _fuzzy_match_options(q, answer):
                        answered.append({"question": label_text[:100], "answer": answer[:100], "method": method})

        except Exception:
            continue

    return answered


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

        if not _wait_for_form(page):
            # Try finding a Greenhouse iframe
            for frame in page.frames:
                if "greenhouse" in (frame.url or "").lower():
                    page = frame
                    if _wait_for_form(page):
                        break
            else:
                result["error"] = "Application form did not load"
                return result

        # Fill standard fields
        first, last = _split_name(profile_data.get("name", ""))

        field_map = [
            (FIRST_NAME, first, "first_name"),
            (LAST_NAME, last, "last_name"),
            (EMAIL, profile_data.get("email", ""), "email"),
            (PHONE, profile_data.get("phone", ""), "phone"),
        ]

        # LinkedIn
        linkedin = profile_data.get("linkedin", "")
        if linkedin:
            if not linkedin.startswith("http"):
                linkedin = f"https://linkedin.com/in/{linkedin}"
            field_map.append((LINKEDIN, linkedin, "linkedin"))

        for selector, value, name in field_map:
            if value and _fill_if_exists(page, selector, value):
                result["fields_filled"].append(name)

        # Location with autocomplete handling
        location = profile_data.get("location", "")
        if location and _fill_location_autocomplete(page, location):
            result["fields_filled"].append("location")

        # Phone country code dropdown — select US (+1)
        try:
            country_sel = page.locator(PHONE_COUNTRY)
            if country_sel.count() > 0:
                # Try common value formats for US country code
                for val in ["US", "us", "1", "+1"]:
                    try:
                        country_sel.first.select_option(value=val)
                        result["fields_filled"].append("phone_country")
                        break
                    except Exception:
                        continue
        except Exception:
            pass

        # Upload resume — click upload button first if needed, then set file
        if resume_path and Path(resume_path).exists():
            uploaded = False
            # Try clicking an upload/attach button to reveal the file input
            try:
                upload_btn = page.locator(RESUME_BUTTON)
                if upload_btn.count() > 0:
                    upload_btn.first.click()
                    time.sleep(1)
            except Exception:
                pass

            resume_selectors = [
                RESUME_UPLOAD,
                'input[type="file"][id*="resume" i]',
                'input[type="file"]',
            ]
            for sel in resume_selectors:
                if _upload_if_exists(page, sel, resume_path):
                    result["fields_filled"].append("resume")
                    uploaded = True
                    break

            # Last resort: use page.set_input_files on any file input
            if not uploaded:
                try:
                    file_inputs = page.locator('input[type="file"]')
                    if file_inputs.count() > 0:
                        file_inputs.first.set_input_files(resume_path)
                        result["fields_filled"].append("resume")
                except Exception:
                    pass

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

        # Fill education section
        edu_filled = _fill_education_section(page, resume_data)
        result["fields_filled"].extend(edu_filled)

        # Handle custom questions — pass full resume data for intelligent answering
        result["custom_answers"] = _handle_custom_questions(
            page, responses, job_content, profile_data,
            resume_data=resume_data,
        )

        # Fill EEO / voluntary self-identification
        eeo_filled = _fill_eeo_section(page, responses)
        result["fields_filled"].extend(eeo_filled)

        result["success"] = True

    except Exception as e:
        result["error"] = str(e)

    return result
