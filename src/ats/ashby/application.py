"""Top-level Ashby orchestrator.

Walks every [data-field-path] container on the form, detects the widget
shape inside, and routes to the matching filler. Source priority:

  1. Known system field (`_systemfield_name/email/location/resume/eeoc_*`)
     → direct profile/responses lookup
  2. Label-keyword match (phone, linkedin, github, school, graduation date, ...)
     → direct profile/resume_data lookup
  3. Pronouns → responses.json["pronouns"] if set, else skip
  4. EEO → responses.json values mapped to form option labels
  5. Otherwise → Claude, shaped to the widget (radio/checkbox/yes-no/text)
"""
import re
import time
from pathlib import Path

from playwright.sync_api import Page

from . import custom_questions as cq
from .fields import (
    APPLY_BUTTON,
    FIELD_ENTRY,
    LABEL_ROUTES,
    SYSTEMFIELD_EEOC_DISABILITY,
    SYSTEMFIELD_EEOC_GENDER,
    SYSTEMFIELD_EEOC_RACE,
    SYSTEMFIELD_EEOC_VETERAN,
    SYSTEMFIELD_EMAIL,
    SYSTEMFIELD_LOCATION,
    SYSTEMFIELD_NAME,
    SYSTEMFIELD_RESUME,
)
from .selectors import (
    fill_checkbox_group,
    fill_combobox,
    fill_date,
    fill_radio_group,
    fill_single_checkbox,
    fill_text,
    fill_yesno,
    get_label_text,
    get_option_labels,
    upload_file,
    wait_for_form,
)
from .shapes import (
    CHECKBOX_GROUP,
    COMBOBOX,
    DATEPICKER,
    FILE_UPLOAD,
    RADIO_GROUP,
    SINGLE_CHECKBOX,
    TEXT_INPUT,
    TEXTAREA,
    YESNO_TOGGLE,
    detect_shape,
    is_multi_select,
    is_required,
)

# Keywords that mark a question as visa/sponsorship-related.
# When detected, we short-circuit to responses.visa_sponsorship instead of
# letting Claude guess (it tends to over-index on visa-type option lists and
# pick a specific visa even when the applicant doesn't need any).
_SPONSORSHIP_KEYWORDS = (
    "sponsorship", "sponsor", "visa", "h1b", "h-1b",
    " opt ", " opt?", "(opt", "opt ",
)

# Option-label patterns that mean "no sponsorship needed / not applicable".
_NO_SPONSORSHIP_PATTERNS = (
    "none", "not applicable", "n/a", "no sponsorship",
    "do not require", "don't require", "not require",
)

# Default EEO option labels when the user's response value doesn't match an
# option verbatim. "Prefer not to say" → "Decline to self-identify".
_EEO_FALLBACK_LABEL = {
    SYSTEMFIELD_EEOC_GENDER: "Decline to self-identify",
    SYSTEMFIELD_EEOC_RACE: "Decline to self-identify",
    SYSTEMFIELD_EEOC_VETERAN: "I decline to self-identify for protected veteran status",
    SYSTEMFIELD_EEOC_DISABILITY: "I do not want to answer",
}

# Map system-field path → responses.json key
_EEO_RESPONSE_KEY = {
    SYSTEMFIELD_EEOC_GENDER: "gender",
    SYSTEMFIELD_EEOC_RACE: "ethnicity",
    SYSTEMFIELD_EEOC_VETERAN: "veteran_status",
    SYSTEMFIELD_EEOC_DISABILITY: "disability",
}

_MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
    "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def _format_grad_date(end_date: str) -> str:
    """Convert resume end_date strings to MM/DD/YYYY (the format react-datepicker
    parses by default). Defaults the day to the 15th when only month+year are given.

    Accepts: 'June 2026', 'Jun 2026', '2026-06', '06/2026', '06/15/2026'.
    Returns '' if the format isn't recognized.
    """
    if not end_date:
        return ""
    s = end_date.strip()

    m = re.match(r"([A-Za-z]+)\s+(\d{4})", s)
    if m:
        mon = _MONTHS.get(m.group(1)[:3].lower())
        if mon:
            return f"{mon}/15/{m.group(2)}"

    m = re.match(r"(\d{4})-(\d{1,2})", s)
    if m:
        return f"{int(m.group(2)):02d}/15/{m.group(1)}"

    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{m.group(3)}"

    m = re.match(r"(\d{1,2})/(\d{4})$", s)
    if m:
        return f"{int(m.group(1)):02d}/15/{m.group(2)}"

    return ""


_EEO_STOPWORDS = {
    "a", "an", "the", "i", "am", "is", "are", "was", "were", "to", "of", "or",
    "and", "for", "in", "on", "as", "be", "it",
}


def _normalize_eeo_answer(answer: str, options: list[str], field_path: str) -> str:
    """Match user response value against form option labels.

    Cascade: exact → decline/prefer-not-to-say synonyms → substring →
    word-overlap. The word-overlap step handles cases like the user saying
    'I am not a veteran' against Notion's 'I am not a protected veteran' —
    neither is a substring of the other, but they share 5 of 6 content words.
    """
    if not options:
        return answer
    if not answer:
        return _EEO_FALLBACK_LABEL.get(field_path, "")

    al = answer.lower().strip()
    for o in options:
        if o.lower() == al:
            return o

    if any(kw in al for kw in ("prefer not", "decline", "not say", "rather not")):
        for o in options:
            if any(kw in o.lower() for kw in ("decline", "prefer not", "do not want", "rather not")):
                return o

    for o in options:
        if al in o.lower() or o.lower() in al:
            return o

    answer_words = {w for w in re.findall(r"\w+", al) if w not in _EEO_STOPWORDS}
    if answer_words:
        best_score = 0
        best_option = answer
        for o in options:
            opt_words = {w for w in re.findall(r"\w+", o.lower()) if w not in _EEO_STOPWORDS}
            if not opt_words:
                continue
            overlap = len(answer_words & opt_words)
            if overlap > best_score:
                best_score = overlap
                best_option = o
        if best_score >= 1:
            return best_option

    return answer


def _source_value(source: str, key: str, profile_data: dict,
                  resume_data: dict | None, responses: dict) -> str:
    """Look up a value from the named source. Normalizes LinkedIn/GitHub
    usernames to full URLs, formats graduation dates to MM/DD/YYYY."""
    if source == "profile":
        v = profile_data.get(key, "") or ""
        if key == "linkedin" and v and not v.startswith("http"):
            return f"https://linkedin.com/in/{v}"
        if key == "github" and v and not v.startswith("http"):
            return f"https://github.com/{v}"
        return v
    if source == "responses":
        return responses.get(key, "") or ""
    if source == "resume_edu" and resume_data:
        edu = resume_data.get("education") or []
        if not edu:
            return ""
        e = edu[0]
        if key == "end_date":
            return _format_grad_date(e.get("end_date", ""))
        return e.get(key, "") or ""
    return ""


def _route_label(label: str) -> tuple[str, str] | None:
    """Match a custom-question label against LABEL_ROUTES.
    Returns (source, key) or None."""
    ll = label.lower()
    for keywords, source, key in LABEL_ROUTES:
        if any(kw in ll for kw in keywords):
            return (source, key)
    return None


def _is_sponsorship_question(label: str) -> bool:
    """True if the question is about visa sponsorship needs."""
    ll = (" " + label.lower() + " ")
    return any(kw in ll for kw in _SPONSORSHIP_KEYWORDS)


def _pick_no_sponsorship_option(options: list[str]) -> str:
    """From a list of sponsorship options, pick the one meaning 'none needed'."""
    for o in options:
        ol = o.lower().strip()
        if ol == "none":
            return o
    for o in options:
        ol = o.lower().strip()
        if any(p in ol for p in _NO_SPONSORSHIP_PATTERNS):
            return o
    return ""


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
    """Navigate to an Ashby job posting and fill the application form.

    Returns {success, fields_filled, custom_answers, error}.
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

        apply_btn = page.locator(APPLY_BUTTON)
        if apply_btn.count() > 0:
            try:
                apply_btn.first.click()
                time.sleep(2)
            except Exception:
                pass

        if not wait_for_form(page):
            result["error"] = "Application form did not load"
            return result

        containers = page.locator(FIELD_ENTRY).all()
        print(f"  [ashby] found {len(containers)} field containers")

        for container in containers:
            field_path = "?"
            try:
                field_path = container.get_attribute("data-field-path") or "?"
                _process_container(
                    page, container, field_path, profile_data, responses,
                    resume_path, resume_data, job_content, result,
                )
            except Exception as e:
                print(f"  [ashby] field {field_path}: {type(e).__name__}: {e}")

        result["success"] = True

    except Exception as e:
        result["error"] = str(e)

    return result


def _process_container(page, container, field_path, profile_data, responses,
                       resume_path, resume_data, job_content, result):
    """Route one [data-field-path] container to the right filler."""
    label = get_label_text(container)
    shape = detect_shape(container)
    required = is_required(container)

    # ---- 1. Known system fields ----
    if field_path == SYSTEMFIELD_NAME:
        if fill_text(container, profile_data.get("name", "")):
            result["fields_filled"].append("name")
            print(f"  [ashby] {field_path} → name")
        return

    if field_path == SYSTEMFIELD_EMAIL:
        if fill_text(container, profile_data.get("email", "")):
            result["fields_filled"].append("email")
            print(f"  [ashby] {field_path} → email")
        return

    if field_path == SYSTEMFIELD_LOCATION:
        loc = profile_data.get("location", "")
        if fill_combobox(page, container, loc):
            result["fields_filled"].append("location")
            print(f"  [ashby] {field_path} → location ({loc})")
        return

    if field_path == SYSTEMFIELD_RESUME or shape == FILE_UPLOAD:
        if not resume_path:
            print(f"  [ashby] {field_path}: resume_path is empty — skipping upload")
            return
        if not Path(resume_path).exists():
            print(f"  [ashby] {field_path}: resume_path does not exist: {resume_path}")
            return
        if upload_file(page, container, resume_path):
            result["fields_filled"].append("resume")
            print(f"  [ashby] {field_path} → resume ({Path(resume_path).name})")
        return

    if field_path in _EEO_FALLBACK_LABEL:
        resp_key = _EEO_RESPONSE_KEY.get(field_path, "")
        user_answer = responses.get(resp_key, "") if resp_key else ""
        options = get_option_labels(container)
        target = _normalize_eeo_answer(user_answer, options, field_path)
        if shape == RADIO_GROUP and fill_radio_group(container, target):
            result["fields_filled"].append(f"{field_path}:{target}")
            print(f"  [ashby] {field_path} → {target}")
        return

    # ---- 2. UUID-id'd fields routed by label keyword ----
    routed = _route_label(label)
    if routed and shape in (TEXT_INPUT, TEXTAREA, DATEPICKER):
        source, key = routed
        value = _source_value(source, key, profile_data, resume_data, responses)
        if value:
            ok = (fill_date(container, value) if shape == DATEPICKER
                  else fill_text(container, value))
            if ok:
                result["fields_filled"].append(f"{field_path}:{key}")
                print(f"  [ashby] {field_path} → {key} ({value[:30]})")
                return

    # ---- 3. Pronouns: from responses.json if set, else "Prefer not to say"
    # when required, else skip. The fuzzy match in fill_radio_group also
    # catches "Decline" / "Rather not say" variants.
    if "pronoun" in label.lower():
        pron = (responses.get("pronouns") or "").strip()
        if not pron and required:
            pron = "Prefer not to say"
        if not pron:
            print(f"  [ashby] {field_path} ({label[:30]}): skip pronouns — optional + not in responses")
            return
        if shape == RADIO_GROUP and fill_radio_group(container, pron):
            result["fields_filled"].append(f"{field_path}:pronouns")
            print(f"  [ashby] {field_path} → pronouns ({pron})")
            return
        # Fuzzy fallback if exact "Prefer not to say" doesn't match
        if shape == RADIO_GROUP:
            for fallback in ("Prefer not to say", "Decline to self-identify",
                              "Rather not say", "Not represented here"):
                if fill_radio_group(container, fallback):
                    result["fields_filled"].append(f"{field_path}:pronouns")
                    print(f"  [ashby] {field_path} → pronouns ({fallback}) [fallback]")
                    return
        return

    # ---- 4. Skip purely subjective optional questions ----
    if not required:
        ll = label.lower()
        if any(kw in ll for kw in ("pronounce", "nickname", "preference")):
            return

    # ---- 5. Sponsorship/visa short-circuit ----
    # When the user has explicitly said they don't need sponsorship, route
    # directly without asking Claude (which tends to over-index on the visa
    # types in the option list and pick a specific visa anyway).
    if _is_sponsorship_question(label):
        user_visa = (responses.get("visa_sponsorship") or "").strip().lower()
        if user_visa in ("no", "false", "n", ""):
            if shape == YESNO_TOGGLE:
                if fill_yesno(container, False):
                    _log_custom(result, label, "No", shape)
                return
            if shape == SINGLE_CHECKBOX:
                if fill_single_checkbox(container, False):
                    _log_custom(result, label, "unchecked", shape)
                return
            if shape == RADIO_GROUP:
                options = get_option_labels(container)
                target = _pick_no_sponsorship_option(options)
                if target and fill_radio_group(container, target):
                    _log_custom(result, label, target, shape + " [no-sponsor]")
                    return
                # No "None" option present — fall through to Claude as last resort

    # ---- 6. Claude fallback per widget shape ----
    try:
        if shape == YESNO_TOGGLE:
            answer = cq.answer_yesno(label, profile_data, resume_data, responses, job_content)
            if fill_yesno(container, answer):
                _log_custom(result, label, "Yes" if answer else "No", shape)
            return

        if shape == SINGLE_CHECKBOX:
            answer = cq.answer_yesno(label, profile_data, resume_data, responses, job_content)
            if fill_single_checkbox(container, answer):
                _log_custom(result, label, "checked" if answer else "unchecked", shape)
            return

        if shape == RADIO_GROUP:
            options = get_option_labels(container)
            if not options:
                return
            picked = cq.pick_radio(
                label, options, profile_data, resume_data, responses, job_content,
            )
            if fill_radio_group(container, picked):
                _log_custom(result, label, picked, shape)
            return

        if shape == CHECKBOX_GROUP:
            options = get_option_labels(container)
            if not options:
                return
            multi = is_multi_select(label)
            picked = cq.pick_checkboxes(
                label, options, multi, profile_data, resume_data, responses, job_content,
            )
            if fill_checkbox_group(container, picked):
                _log_custom(result, label, ",".join(picked), shape)
            return

        if shape == COMBOBOX:
            answer = cq.answer_text(label, profile_data, resume_data, responses, job_content)
            if answer and fill_combobox(page, container, answer):
                _log_custom(result, label, answer, shape)
            return

        if shape == DATEPICKER:
            answer = cq.answer_text(label, profile_data, resume_data, responses, job_content)
            if answer and fill_date(container, answer):
                _log_custom(result, label, answer, shape)
            return

        if shape in (TEXT_INPUT, TEXTAREA):
            answer = cq.answer_text(label, profile_data, resume_data, responses, job_content)
            if answer and fill_text(container, answer):
                _log_custom(result, label, answer, shape)
            return

    except Exception as e:
        print(f"  [ashby] {field_path} Claude/fill failed: {type(e).__name__}: {e}")
        return

    print(f"  [ashby] {field_path} ({label[:30]}): unhandled shape={shape}")


def _log_custom(result: dict, question: str, answer: str, shape: str) -> None:
    result["custom_answers"].append({
        "question": question[:100],
        "answer": answer[:100],
        "shape": shape,
    })
