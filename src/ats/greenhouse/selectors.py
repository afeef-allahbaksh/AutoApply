"""Generic Playwright helpers — fill, upload, fuzzy-match. Used by every other
greenhouse submodule, so this is the leaf of the dependency graph (no imports
from sibling greenhouse modules)."""
import re

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from .fields import FIRST_NAME


def wait_for_form(page: Page, timeout: int = 15000) -> bool:
    """Wait for the Greenhouse application form to load."""
    try:
        page.wait_for_selector(FIRST_NAME, timeout=timeout)
        return True
    except PlaywrightTimeout:
        return False


def fill_if_exists(page: Page, selector: str, value: str) -> bool:
    """Fill a field if it exists on the page."""
    try:
        el = page.locator(selector).first
        if el.is_visible(timeout=1000):
            el.fill(value)
            return True
    except (PlaywrightTimeout, Exception):
        pass
    return False


def upload_if_exists(page: Page, selector: str, file_path: str) -> bool:
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


def fuzzy_match_options(el, target: str) -> bool:
    """Core fuzzy matching logic for a select element.

    Tries exact match, then substring, then normalized word-overlap.
    """
    try:
        el.select_option(label=target)
        return True
    except Exception:
        pass

    options = el.locator("option").all()
    option_texts = [(opt.inner_text().strip(), opt.get_attribute("value")) for opt in options]

    target_lower = target.lower()

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


def select_option_fuzzy(page: Page, selector: str, target: str) -> bool:
    """Select a dropdown option using fuzzy text matching."""
    try:
        el = page.locator(selector).first
        if not el.is_visible(timeout=1000):
            return False
        return fuzzy_match_options(el, target)
    except Exception:
        pass
    return False


def select_option_fuzzy_el(el, target: str) -> bool:
    """Like select_option_fuzzy but takes a Playwright Locator directly."""
    try:
        if not el.is_visible(timeout=1000):
            return False
        return fuzzy_match_options(el, target)
    except Exception:
        pass
    return False
