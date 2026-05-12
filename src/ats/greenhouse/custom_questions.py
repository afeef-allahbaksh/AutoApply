"""Claude-backed answers for Greenhouse's free-text and dropdown questions.

Three layers of strategy per field:
  1. Canned response keyword match (`responses` dict)
  2. Smart skip for optional fields user lacks data for (URLs without website,
     referral without referrer info, always-skip personal preferences)
  3. Claude call (`answer_custom_question` or `answer_select_question`)
"""
import time

from playwright.sync_api import Page

from src.api import create_message
from src.ats.applicant_context import build_applicant_context

from .fields import CUSTOM_QUESTION
from .selectors import fuzzy_match_options


def answer_custom_question(
    question_text: str,
    job_content: str,
    profile_data: dict,
    resume_data: dict | None = None,
    responses: dict | None = None,
) -> str:
    """Use Claude to answer a custom free-text question."""
    context = build_applicant_context(profile_data, resume_data, responses or {})

    message = create_message(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": f"""Answer this job application question using the applicant's real data below.

The question text may include section headers, subtitles, or surrounding UI text scraped from the form. Identify the ACTUAL question being asked and answer ONLY that. Ignore decorative headings like "Personal Preferences", "Additional Information", etc.

Question (may include surrounding text): {question_text}

Applicant data:
{context}

Job description:
{job_content[:2000]}

Rules:
- BE CONCISE. Give the shortest accurate answer possible. A human filling this form would write brief answers, not essays.
- For dates (start date, availability, graduation): return ONLY the date (e.g. "June 2026")
- For yes/no questions: return ONLY "Yes" or "No"
- For factual questions (name pronunciation, university, GPA, location): return ONLY the fact
- For URL/link fields: return ONLY the bare URL
- For open-ended questions: answer in 1-2 sentences MAX. Be direct, no filler.
- Use EXACT data from the applicant profile — never fabricate
- If the question matches a pre-set response, use that exact value
- Do NOT mention being excited, passionate, or enthusiastic — sounds like AI
- Do NOT reference the job description or company name unless the question specifically asks about it
- If the text is not a real question, or you don't have the data to answer, return ONLY the single word SKIP — nothing else
- NEVER explain why you can't answer. NEVER say "I don't see a question". Just return SKIP.
- Return ONLY the answer text, no quotes or labels""",
        }],
    )
    result = message.content[0].text.strip()
    if result.upper() == "SKIP":
        return ""
    return result


def answer_select_question(
    question_text: str,
    options: list[str],
    job_content: str,
    profile_data: dict,
    resume_data: dict | None = None,
    responses: dict | None = None,
) -> str:
    """Use Claude to pick the best option from a dropdown for a custom question."""
    context = build_applicant_context(profile_data, resume_data, responses or {})
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


def _get_question_text(page: Page, q, q_id: str) -> str:
    """Extract the actual question text for a form field.

    Greenhouse forms have a pattern where the label can be a section header
    (e.g. "(Optional) Personal Preferences") and the real question is in a
    description div (e.g. "How do you pronounce your name?"). This function
    checks the description div first, then falls back to the label.
    """
    label_text = ""
    description_text = ""

    label_el = page.locator(f'label[for="{q_id}"]')
    if label_el.count() > 0:
        label_text = label_el.first.inner_text().strip()
    else:
        parent = q.locator("..").first
        label_text = parent.inner_text().strip()[:200]

    if q_id:
        desc_el = page.locator(f'#{q_id}-description')
        if desc_el.count() > 0:
            try:
                description_text = desc_el.first.inner_text().strip()
            except Exception:
                pass

    if description_text and label_text:
        # Section headers tend to be vague labels without a question mark
        label_lower = label_text.lower()
        is_section_header = any(kw in label_lower for kw in [
            "personal preference", "additional info", "optional",
            "supplemental", "general info",
        ]) and "?" not in label_text
        if is_section_header:
            return description_text

    if description_text and label_text:
        return f"{label_text}: {description_text}"

    return label_text or description_text


def handle_custom_questions(
    page: Page,
    responses: dict,
    job_content: str,
    profile_data: dict,
    resume_data: dict | None = None,
    skip_ids: set[str] | None = None,
) -> list[dict]:
    """Find and answer custom questions on the form.

    Checks canned responses first, then falls back to Claude with full context.
    Skips any field whose id is in skip_ids (e.g. demographics already filled).
    Returns a list of {question, answer, method} dicts for logging.
    """
    answered = []
    skip_ids = skip_ids or set()
    questions = page.locator(CUSTOM_QUESTION).all()

    for q in questions:
        try:
            q_id = q.get_attribute("id") or ""
            if q_id and q_id in skip_ids:
                continue
            label_text = _get_question_text(page, q, q_id)

            if not label_text:
                print(f"  [custom-q] skip {q_id}: no label/description")
                continue

            tag = q.evaluate("el => el.tagName.toLowerCase()")

            # Canned-response match — try both the raw key and a space-normalized
            # variant ("visa_sponsorship" → "visa sponsorship") because labels use
            # natural language and responses.json keys are snake_case.
            answer = None
            method = "canned"
            label_lower = label_text.lower()
            for key, value in responses.items():
                kl = key.lower()
                if kl in label_lower or kl.replace("_", " ") in label_lower:
                    answer = value
                    break

            role = q.get_attribute("role") or ""

            if role == "combobox":
                # React Select — open, gather options, pick or claude-answer.
                #
                # CRITICAL: scope the [role="option"] query to `.select__menu`.
                # Greenhouse boards often include an intl-tel-input phone
                # country picker that renders 240+ <li role="option"> country
                # elements at page load. A page-wide `[role="option"]` query
                # picks up all of them, polluting our option list and breaking
                # the "find matching option by text" click logic.
                q.click()
                time.sleep(0.5)
                # `.select__menu` only exists when the React Select is open.
                # The visa dropdown's menu is the only one open at this point.
                menu = page.locator('.select__menu').first
                if menu.count() == 0 or not menu.is_visible(timeout=500):
                    # Click didn't open the menu (some React Selects need a
                    # control-wrapper click, not an input click). Try again
                    # via the control wrapper.
                    control = q.locator('xpath=ancestor::div[contains(@class, "select__control")]').first
                    if control.count() > 0:
                        control.click()
                        time.sleep(0.5)
                        menu = page.locator('.select__menu').first
                option_els = menu.locator('[role="option"]').all() if menu.count() > 0 else []
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
                        answer = answer_select_question(
                            label_text, option_labels, job_content,
                            profile_data, resume_data=resume_data, responses=responses,
                        )
                    else:
                        answer = answer_custom_question(
                            label_text, job_content, profile_data,
                            resume_data=resume_data, responses=responses,
                        )
                    method = "claude"

                matched = False
                # Re-scope the click query too — same reason as above.
                if menu.count() > 0:
                    for opt_el in menu.locator('[role="option"]').all():
                        if opt_el.inner_text().strip() == answer:
                            opt_el.click()
                            matched = True
                            break
                if not matched:
                    # Type and select first match — React Select filters by typed input
                    q.fill("")
                    q.type(answer, delay=50)
                    time.sleep(0.8)
                    first_opt = menu.locator('[role="option"]').first if menu.count() > 0 else page.locator('.select__menu [role="option"]').first
                    try:
                        if first_opt.is_visible(timeout=1000):
                            first_opt.click()
                        else:
                            q.press("ArrowDown")
                            q.press("Enter")
                    except Exception:
                        q.press("Enter")
                print(f"  [custom-q] {q_id} ({label_text[:40]}…) → {answer[:30]!r} via {method}")
                answered.append({"question": label_text[:100], "answer": answer[:100], "method": method})
                q.press("Escape")

            elif tag in ("input", "textarea") and q.get_attribute("type") != "file":
                is_required = q.get_attribute("aria-required") == "true"
                if answer is None and not is_required:
                    # Always skip — purely personal, no impact on application
                    always_skip = ["pronounce", "preference", "pronoun", "nickname"]
                    if any(kw in label_lower for kw in always_skip):
                        continue

                    # Skip only if user doesn't have the data
                    url_keywords = ["website", "portfolio", "personal site", "blog",
                                    "publication", "scholar"]
                    if any(kw in label_lower for kw in url_keywords) and not profile_data.get("website"):
                        continue
                    referral_keywords = ["referral", "referred by", "how did you hear"]
                    if any(kw in label_lower for kw in referral_keywords) and not responses.get("referral"):
                        continue

                if answer is None:
                    answer = answer_custom_question(
                        label_text, job_content, profile_data,
                        resume_data=resume_data, responses=responses,
                    )
                    method = "claude"

                # Required-field fallback: if Claude punted on a question that's
                # clearly asking for the applicant's location, fill with the
                # profile location rather than leave a required field blank.
                # The form will reject submission otherwise.
                if (not answer or not answer.strip()) and is_required:
                    location_keywords = ["city", "state", "country", "based",
                                         "where do you", "where will you",
                                         "intend to work", "work location"]
                    if any(kw in label_lower for kw in location_keywords):
                        loc = profile_data.get("location", "").strip()
                        if loc:
                            answer = loc
                            method = "profile_fallback"
                            print(f"  [custom-q] {q_id}: claude skipped required location field → filled with profile.location ({loc})")

                if not answer or not answer.strip():
                    print(f"  [custom-q] skip {q_id} ({label_text[:50]}): empty/SKIP answer{' (REQUIRED)' if is_required else ''}")
                    continue

                # React-controlled inputs (Remix, Next.js, etc.) sometimes
                # don't reconcile `q.fill()` — Playwright sets element.value
                # directly but the component's state isn't updated, so React
                # re-renders with the old empty value and overwrites our fill.
                # Verify the value stuck; if not, fall back to click + type
                # (each keystroke fires a real input event that React reliably
                # consumes through its onChange flow).
                q.fill(answer)
                try:
                    stuck = q.input_value() == answer
                except Exception:
                    stuck = False
                if not stuck:
                    try:
                        q.click()
                        q.fill("")
                        q.type(answer, delay=20)
                    except Exception as e:
                        print(f"  [custom-q] {q_id}: click+type fallback failed: {e}")

                # Verify final state before declaring success
                try:
                    final_value = q.input_value()
                except Exception:
                    final_value = ""
                if final_value != answer:
                    print(f"  [custom-q] {q_id}: value did not stick — got {final_value[:30]!r}, wanted {answer[:30]!r}")
                    continue

                print(f"  [custom-q] {q_id} ({label_text[:40]}…) → {answer[:50]!r} via {method}")
                answered.append({"question": label_text[:100], "answer": answer[:100], "method": method})

            elif tag == "select":
                option_els = q.locator("option").all()
                option_labels = [o.inner_text().strip() for o in option_els
                                 if o.inner_text().strip() and not o.inner_text().strip().startswith("Select")]

                if answer is not None and option_labels:
                    # Canned response matched, but for selects we need to pick the closest option
                    # e.g., canned "Yes, I am authorized..." should pick "Yes" from a Yes/No dropdown
                    answer_lower = answer.lower().strip()
                    if answer not in option_labels:
                        for opt in option_labels:
                            if opt.lower() == answer_lower or opt.lower() in answer_lower or answer_lower in opt.lower():
                                answer = opt
                                break

                if answer is None:
                    if option_labels:
                        answer = answer_select_question(
                            label_text, option_labels, job_content,
                            profile_data, resume_data=resume_data, responses=responses,
                        )
                    else:
                        answer = answer_custom_question(
                            label_text, job_content, profile_data,
                            resume_data=resume_data, responses=responses,
                        )
                    method = "claude"
                try:
                    q.select_option(label=answer)
                    answered.append({"question": label_text[:100], "answer": answer[:100], "method": method})
                except Exception:
                    if fuzzy_match_options(q, answer):
                        answered.append({"question": label_text[:100], "answer": answer[:100], "method": method})

        except Exception as e:
            # One bad field shouldn't kill the rest. Log so we can diagnose
            # later instead of silently dropping. If we're inside a combobox
            # branch with the dropdown still open, close it before moving on
            # — otherwise the next field's selectors get confused by a
            # phantom open dropdown.
            qid_for_log = q_id if 'q_id' in locals() else '?'
            label_for_log = label_text[:50] if 'label_text' in locals() and label_text else '?'
            print(f"  [custom-q] FAILED {qid_for_log} ({label_for_log}): {type(e).__name__}: {e}")
            try:
                q.press("Escape")
            except Exception:
                pass
            continue

    return answered
