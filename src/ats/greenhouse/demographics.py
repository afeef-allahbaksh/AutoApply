"""EEO / demographic field handling.

Defaults to "Decline to self-identify" when no canned response is set, so
required EEO fields pass validation while preserving privacy. Works for both
native `<select>` and React Select comboboxes regardless of ID convention.
"""
import time

from playwright.sync_api import Page

from .selectors import fuzzy_match_options

DEMOGRAPHIC_KEYWORDS = {
    "gender": "gender",
    "race": "ethnicity",
    "ethnicity": "ethnicity",
    "hispanic": "ethnicity",
    "latino": "ethnicity",
    "veteran": "veteran_status",
    "disability": "disability",
}

DECLINE_PATTERNS = [
    "decline to self-identify",
    "decline to identify",
    "prefer not",
    "decline to answer",
    "i don't wish",
    "do not wish to disclose",
    "do not wish to answer",
    "no answer",
    "rather not",
]


def is_decline_option(text: str) -> bool:
    t = text.lower()
    return any(p in t for p in DECLINE_PATTERNS)


def _demographic_key_for_label(label_text: str) -> str | None:
    t = label_text.lower()
    for kw, key in DEMOGRAPHIC_KEYWORDS.items():
        if kw in t:
            return key
    return None


def _select_decline_in_native(field) -> bool:
    """Pick a 'decline' / 'prefer not to' option from a native <select>."""
    try:
        for opt in field.locator("option").all():
            text = opt.inner_text().strip()
            if text and is_decline_option(text):
                field.select_option(label=text)
                return True
    except Exception:
        pass
    return False


def _fill_combobox_with_decline(page: Page, field, answer: str) -> bool:
    """Open a React Select combobox, pick canned answer if it matches an option,
    otherwise pick a 'decline' option. Returns True if anything was selected.

    Scopes the `[role="option"]` query to `.select__menu` — the page may have
    ambient `role="option"` elements from other widgets (e.g. the intl-tel-input
    phone country picker renders 240+ options at page load) that would
    otherwise poison the option list.
    """
    try:
        field.click()
        time.sleep(0.4)
        menu = page.locator('.select__menu').first
        if menu.count() == 0:
            field.press("Escape")
            return False
        option_els = menu.locator('[role="option"]').all()
        option_labels = [o.inner_text().strip() for o in option_els if o.inner_text().strip()]
        if not option_labels:
            field.press("Escape")
            return False

        target = None
        if answer:
            ans_lower = answer.lower().strip()
            for opt in option_labels:
                opt_lower = opt.lower()
                if opt == answer or opt_lower == ans_lower or ans_lower in opt_lower or opt_lower in ans_lower:
                    target = opt
                    break
        if not target:
            for opt in option_labels:
                if is_decline_option(opt):
                    target = opt
                    break
        if not target:
            field.press("Escape")
            return False

        for opt_el in menu.locator('[role="option"]').all():
            try:
                if opt_el.inner_text().strip() == target:
                    opt_el.click()
                    return True
            except Exception:
                continue
        field.press("Escape")
        return False
    except Exception:
        return False


def fill_demographics(page: Page, responses: dict) -> tuple[list[str], set[str]]:
    """Fill demographic/EEO fields by matching labels to demographic keywords.

    Returns (filled_field_names, set_of_handled_field_ids). Caller passes the
    ID set into the custom-question handler so demographic fields aren't
    re-processed.
    """
    filled: list[str] = []
    handled_ids: set[str] = set()
    seen_keys: set[str] = set()

    try:
        labels = page.locator("label").all()
    except Exception:
        return filled, handled_ids

    for label in labels:
        try:
            text = label.inner_text().strip()
            if not text:
                continue
            key = _demographic_key_for_label(text)
            if not key or key in seen_keys:
                continue

            for_id = label.get_attribute("for") or ""
            if not for_id:
                continue
            field = page.locator(f'#{for_id}').first
            if not field.is_visible(timeout=500):
                continue

            tag = field.evaluate("el => el.tagName.toLowerCase()")
            role_attr = field.get_attribute("role") or ""
            answer = (responses.get(key, "") or "").strip()

            ok = False
            if tag == "select":
                if answer and fuzzy_match_options(field, answer):
                    ok = True
                else:
                    ok = _select_decline_in_native(field)
            elif role_attr == "combobox" or tag == "input":
                ok = _fill_combobox_with_decline(page, field, answer)

            if ok:
                filled.append(f"demographic_{key}")
                handled_ids.add(for_id)
                seen_keys.add(key)
        except Exception:
            continue

    return filled, handled_ids
