import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

load_dotenv()

# Selectors
FIRST_NAME = 'input[name="first_name"], #first_name'
LAST_NAME = 'input[name="last_name"], #last_name'
EMAIL = 'input[name="email"], #email'
PHONE = 'input[name="phone"], #phone'
LOCATION = 'input[name="location"], #location'
LINKEDIN = 'input[autocomplete="custom-question-linkedin-profile"], input[name*="linkedin" i]'
RESUME_UPLOAD = 'input[type="file"][name="resume"]'
COVER_LETTER_UPLOAD = 'input[type="file"][name="cover_letter"]'
SUBMIT_BUTTON = 'button:has-text("Submit")'
CUSTOM_QUESTION = '[id^="question_"]'


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


def _split_name(full_name: str) -> tuple[str, str]:
    """Split a full name into first and last name."""
    parts = full_name.strip().split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _answer_custom_question(question_text: str, job_content: str, profile_data: dict) -> str:
    """Use Claude to answer a custom free-text question."""
    client = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": f"""Answer this job application question concisely and professionally.

Question: {question_text}

Context about the applicant:
Name: {profile_data.get('name', '')}
Location: {profile_data.get('location', '')}
Current focus: {', '.join(profile_data.get('job_preferences', {}).get('roles', []))}

Job description context:
{job_content[:2000]}

Rules:
- Answer in 2-4 sentences max unless the question requires more
- Be genuine and specific, not generic
- Draw from the applicant's context where relevant
- Return ONLY the answer text, no quotes or labels""",
        }],
    )
    return message.content[0].text.strip()


def _handle_custom_questions(
    page: Page,
    responses: dict,
    job_content: str,
    profile_data: dict,
) -> list[dict]:
    """Find and answer custom questions on the form.

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

            # Check canned responses first
            answer = None
            label_lower = label_text.lower()
            for key, value in responses.items():
                if key.lower() in label_lower:
                    answer = value
                    method = "canned"
                    break

            if tag in ("input", "textarea") and q.get_attribute("type") != "file":
                if answer is None:
                    answer = _answer_custom_question(label_text, job_content, profile_data)
                    method = "claude"
                q.fill(answer)
                answered.append({"question": label_text[:100], "answer": answer[:100], "method": method})

            elif tag == "select":
                # For native selects, try to match canned response
                if answer:
                    try:
                        q.select_option(label=answer)
                        answered.append({"question": label_text[:100], "answer": answer[:100], "method": method})
                    except Exception:
                        pass

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
            result["error"] = "Application form did not load"
            return result

        # Fill standard fields
        first, last = _split_name(profile_data.get("name", ""))

        field_map = [
            (FIRST_NAME, first, "first_name"),
            (LAST_NAME, last, "last_name"),
            (EMAIL, profile_data.get("email", ""), "email"),
            (PHONE, profile_data.get("phone", ""), "phone"),
            (LOCATION, profile_data.get("location", ""), "location"),
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

        # Upload resume
        if resume_path and Path(resume_path).exists():
            if _upload_if_exists(page, RESUME_UPLOAD, resume_path):
                result["fields_filled"].append("resume")

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

        # Handle custom questions
        result["custom_answers"] = _handle_custom_questions(
            page, responses, job_content, profile_data,
        )

        result["success"] = True

    except Exception as e:
        result["error"] = str(e)

    return result
