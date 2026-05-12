"""Claude-backed answers for Ashby's custom application questions.

Same three-strategy approach as the Greenhouse handler:
  1. Canned response keyword match against `responses.json`
  2. Smart skip for optional URL / preference / referral fields
  3. Claude fallback (`answer_custom_question` / `answer_select_question`)

Ashby's form structure is more uniform than Greenhouse's: custom fields are
non-`_systemfield_*` inputs/textareas/selects with associated `<label>`
elements. We discover them by walking the form once and filtering out the
standard fields by name.
"""
import time

from playwright.sync_api import Page

from src.api import create_message
from src.ats.applicant_context import build_applicant_context

from .fields import SYSTEMFIELD_NAMES


def answer_custom_question(
    question_text: str,
    job_content: str,
    profile_data: dict,
    resume_data: dict | None = None,
    responses: dict | None = None,
) -> str:
    """Use Claude to answer a free-text custom question."""
    context = build_applicant_context(profile_data, resume_data, responses or {})

    message = create_message(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": f"""Answer this job application question using the applicant's real data below.

Question: {question_text}

Applicant data:
{context}

Job description:
{job_content[:2000]}

Rules:
- BE CONCISE. Give the shortest accurate answer possible.
- For dates / yes-no / factual / URL questions: return ONLY the value, no preamble.
- For open-ended questions: 1-2 sentences MAX.
- Use EXACT data from the applicant profile — never fabricate.
- If the question matches a pre-set response, use that exact value.
- Avoid AI tells: "excited", "passionate", "this aligns perfectly", "hope this finds you well".
- If the text isn't a real question or you lack the data, return ONLY the single word SKIP.
- Return ONLY the answer text, no quotes or labels.""",
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
    """Use Claude to pick the best option for a dropdown."""
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
- Return ONLY the exact text of one of the available options, nothing else.
- Pick the option that best matches the applicant's data.
- If unsure, pick the most neutral/safe option.""",
        }],
    )
    return message.content[0].text.strip()


def _label_for_input(page: Page, el) -> str:
    """Find the question text for an input element.

    Tries `aria-label`, then `<label for="{id}">`, then walks up to a wrapping
    `<div>` and grabs its first text node.
    """
    try:
        aria = el.get_attribute("aria-label") or ""
        if aria:
            return aria.strip()
    except Exception:
        pass
    try:
        el_id = el.get_attribute("id") or ""
        if el_id:
            label = page.locator(f'label[for="{el_id}"]')
            if label.count() > 0:
                return label.first.inner_text().strip()
    except Exception:
        pass
    try:
        parent = el.locator("xpath=..").first
        text = parent.inner_text().strip()
        return text[:200]
    except Exception:
        return ""


def handle_custom_questions(
    page: Page,
    responses: dict,
    job_content: str,
    profile_data: dict,
    resume_data: dict | None = None,
    skip_ids: set[str] | None = None,
) -> list[dict]:
    """Walk every input/textarea/select on the form, skip standard + already-
    filled fields, answer the rest via canned response → smart-skip → Claude.

    Returns a list of `{question, answer, method}` dicts for logging.
    """
    answered: list[dict] = []
    skip_ids = skip_ids or set()

    for tag in ("input", "textarea", "select"):
        elements = page.locator(f"{tag}:visible").all()
        for el in elements:
            try:
                el_id = el.get_attribute("id") or ""
                name = el.get_attribute("name") or ""
                input_type = el.get_attribute("type") or ""

                # Skip standard fields, file uploads, and already-handled.
                if name in SYSTEMFIELD_NAMES:
                    continue
                if input_type in ("file", "hidden", "submit", "button", "checkbox", "radio"):
                    continue
                if el_id and el_id in skip_ids:
                    continue
                # Standard LinkedIn / similar already filled by the standard
                # pass via `aria-label` match — its element id ends up in
                # skip_ids when the orchestrator passes filled_ids through.
                # If not, defensive: also skip by visible name match.
                if any(kw in (name + " " + el_id).lower() for kw in ("linkedin", "github")):
                    continue

                label_text = _label_for_input(page, el)
                if not label_text:
                    print(f"  [ashby-q] skip {el_id or name}: no label")
                    continue
                label_lower = label_text.lower()

                # Canned response — both raw key and snake→space form
                answer = None
                method = "canned"
                for key, value in responses.items():
                    kl = key.lower()
                    if kl in label_lower or kl.replace("_", " ") in label_lower:
                        answer = value
                        break

                # Determine if this is a select-style field
                is_select = tag == "select"
                option_labels: list[str] = []
                if is_select:
                    opts = el.locator("option").all()
                    option_labels = [
                        o.inner_text().strip() for o in opts
                        if o.inner_text().strip() and not o.inner_text().strip().lower().startswith("select")
                    ]

                # Smart-skip for non-required optional URL / preference fields
                is_required = el.get_attribute("aria-required") == "true" or el.get_attribute("required") is not None
                if answer is None and not is_required:
                    always_skip = ["pronoun", "preference", "pronounce", "nickname"]
                    if any(kw in label_lower for kw in always_skip):
                        continue
                    url_keywords = ["website", "portfolio", "personal site", "blog", "publication", "scholar"]
                    if any(kw in label_lower for kw in url_keywords) and not profile_data.get("website"):
                        continue
                    referral_keywords = ["referral", "referred by", "how did you hear"]
                    if any(kw in label_lower for kw in referral_keywords) and not responses.get("referral"):
                        continue

                # Claude fallback
                if answer is None:
                    try:
                        if is_select and option_labels:
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
                    except Exception as e:
                        print(f"  [ashby-q] {el_id or name}: Claude call failed ({type(e).__name__}); falling through")
                        answer = ""
                        method = "claude_failed"

                # Required-field fallback for location-shaped questions
                if (not answer or not answer.strip()) and is_required:
                    location_keywords = [
                        "city", "state", "country", "based",
                        "where do you", "where will you",
                        "intend to work", "work location",
                    ]
                    if any(kw in label_lower for kw in location_keywords):
                        loc = profile_data.get("location", "").strip()
                        if loc:
                            answer = loc
                            method = "profile_fallback"
                            print(f"  [ashby-q] {el_id or name}: claude skipped required location → profile.location ({loc})")

                if not answer or not answer.strip():
                    print(f"  [ashby-q] skip {el_id or name} ({label_text[:40]}…): empty/SKIP{' (REQUIRED)' if is_required else ''}")
                    continue

                if is_select:
                    try:
                        el.select_option(label=answer)
                    except Exception:
                        # Fuzzy fallback — pick by substring
                        ans_lower = answer.lower()
                        for opt in option_labels:
                            if opt.lower() == ans_lower or ans_lower in opt.lower() or opt.lower() in ans_lower:
                                el.select_option(label=opt)
                                answer = opt
                                break
                        else:
                            continue
                else:
                    el.fill(answer)
                    # Verify it stuck; fall back to click+type if React-controlled.
                    try:
                        if el.input_value() != answer:
                            el.click()
                            el.fill("")
                            el.type(answer, delay=20)
                    except Exception:
                        pass

                print(f"  [ashby-q] {el_id or name} ({label_text[:40]}…) → {answer[:50]!r} via {method}")
                answered.append({"question": label_text[:100], "answer": answer[:100], "method": method})

            except Exception as e:
                qid_for_log = el.get_attribute("id") if el else "?"
                print(f"  [ashby-q] FAILED {qid_for_log}: {type(e).__name__}: {e}")
                continue

    return answered
