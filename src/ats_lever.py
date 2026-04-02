import time
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from src.api import create_message

# Selectors
NAME = 'input[name="name"]'
EMAIL = 'input[name="email"]'
PHONE = 'input[name="phone"]'
ORG = 'input[name="org"]'
URLS = 'input[name="urls"]'
LINKEDIN_URL = 'input[name="urls[LinkedIn]"], input[placeholder*="LinkedIn" i]'
GITHUB_URL = 'input[name="urls[GitHub]"], input[placeholder*="GitHub" i]'
RESUME_UPLOAD = 'input[type="file"][name="resume"]'
RESUME_BUTTON = 'button:has-text("Attach"), button:has-text("Upload"), label:has-text("Attach"), label:has-text("Upload resume")'
COMMENTS = 'textarea[name="comments"]'
SUBMIT_BUTTON = 'button:has-text("Submit")'


def _wait_for_form(page: Page, timeout: int = 15000) -> bool:
    """Wait for the Lever application form to load."""
    try:
        page.wait_for_selector(NAME, timeout=timeout)
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


def _answer_custom_question(
    question_text: str,
    job_content: str,
    profile_data: dict,
    resume_data: dict | None = None,
    responses: dict | None = None,
) -> str:
    """Use Claude to answer a custom question with full applicant context."""
    # Reuse the same context builder from greenhouse handler
    from src.ats_greenhouse import _build_applicant_context
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


def _handle_custom_fields(
    page: Page,
    responses: dict,
    job_content: str,
    profile_data: dict,
    resume_data: dict | None = None,
) -> list[dict]:
    """Find and fill custom fields beyond the standard ones.

    Lever custom questions often appear as additional input/textarea/radio elements.
    Returns a list of {question, answer, method} dicts for logging.
    """
    answered = []
    standard_names = {"name", "email", "phone", "org", "resume", "urls", "comments"}

    # Find all visible inputs and textareas that aren't standard fields
    for tag in ("input", "textarea"):
        elements = page.locator(f"{tag}:visible").all()
        for el in elements:
            try:
                name = el.get_attribute("name") or ""
                input_type = el.get_attribute("type") or "text"

                if name in standard_names or input_type in ("file", "hidden", "submit"):
                    continue
                if "url" in name.lower() and ("linkedin" in name.lower() or "github" in name.lower()):
                    continue

                # Get label text
                el_id = el.get_attribute("id") or ""
                label_el = page.locator(f'label[for="{el_id}"]') if el_id else None
                if label_el and label_el.count() > 0:
                    label_text = label_el.first.inner_text().strip()
                else:
                    placeholder = el.get_attribute("placeholder") or ""
                    label_text = placeholder or name

                if not label_text:
                    continue

                # Check canned responses
                answer = None
                method = "canned"
                label_lower = label_text.lower()
                for key, value in responses.items():
                    if key.lower() in label_lower:
                        answer = value
                        break

                if answer is None:
                    answer = _answer_custom_question(
                        label_text, job_content, profile_data,
                        resume_data=resume_data, responses=responses,
                    )
                    method = "claude"

                el.fill(answer)
                answered.append({"question": label_text[:100], "answer": answer[:100], "method": method})

            except Exception:
                continue

    return answered


def fill_lever_application(
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
    """Navigate to a Lever job posting and fill out the application form.

    The form is typically at the bottom of the job page.
    Returns a status dict: {success, fields_filled, custom_answers, error}
    """
    result = {
        "success": False,
        "fields_filled": [],
        "custom_answers": [],
        "error": None,
    }

    try:
        # Lever apply URL is typically the posting URL + /apply
        apply_url = job_url.rstrip("/") + "/apply"
        page.goto(apply_url, wait_until="networkidle", timeout=30000)
        time.sleep(2)

        if not _wait_for_form(page):
            # Try scrolling to bottom on the main page
            page.goto(job_url, wait_until="networkidle", timeout=30000)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2)
            if not _wait_for_form(page):
                result["error"] = "Application form did not load"
                return result

        # Fill standard fields
        field_map = [
            (NAME, profile_data.get("name", ""), "name"),
            (EMAIL, profile_data.get("email", ""), "email"),
            (PHONE, profile_data.get("phone", ""), "phone"),
        ]

        # LinkedIn
        linkedin = profile_data.get("linkedin", "")
        if linkedin:
            if not linkedin.startswith("http"):
                linkedin = f"https://linkedin.com/in/{linkedin}"
            field_map.append((LINKEDIN_URL, linkedin, "linkedin"))

        # GitHub
        github = profile_data.get("github", "")
        if github:
            if not github.startswith("http"):
                github = f"https://github.com/{github}"
            field_map.append((GITHUB_URL, github, "github"))

        for selector, value, name in field_map:
            if value and _fill_if_exists(page, selector, value):
                result["fields_filled"].append(name)

        # Upload resume — click upload button first if needed, then set file
        if resume_path and Path(resume_path).exists():
            uploaded = False
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

            if not uploaded:
                try:
                    file_inputs = page.locator('input[type="file"]')
                    if file_inputs.count() > 0:
                        file_inputs.first.set_input_files(resume_path)
                        result["fields_filled"].append("resume")
                except Exception:
                    pass

        # Cover letter — only fill the comments field if it looks like a cover letter
        # or "additional information" field, not a generic "how did you hear about us" textarea
        comments_el = page.locator(COMMENTS)
        if comments_el.count() > 0 and resume_data:
            # Check the label to make sure it's appropriate for a cover letter
            label_text = ""
            try:
                label_el = page.locator('label[for="comments"], label:has-text("Additional")')
                if label_el.count() > 0:
                    label_text = label_el.first.inner_text().strip().lower()
            except Exception:
                pass
            # Only fill if the label suggests additional info / cover letter, or if no label found (default Lever behavior)
            if not label_text or any(kw in label_text for kw in ("additional", "cover", "comments", "anything else")):
                from src.cover_letter import generate_cover_letter
                cover = generate_cover_letter(profile_data, resume_data, company, role, job_content)
                comments_el.first.fill(cover)
                result["fields_filled"].append("cover_letter")

        # Handle custom questions — pass full resume data for intelligent answering
        result["custom_answers"] = _handle_custom_fields(
            page, responses, job_content, profile_data,
            resume_data=resume_data,
        )

        result["success"] = True

    except Exception as e:
        result["error"] = str(e)

    return result
